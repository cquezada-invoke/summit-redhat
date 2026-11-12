"""Flotante de salud del segmento en el mapa: la misma lógica que el de la máquina (_salud_acciones.html). Solo lo que
falta reparar, agrupado por pilar en el orden de siempre y, dentro de cada pilar, lo que más sube primero. Cada situación
se abre caso a caso (una máquina, un flujo) y cada caso lleva su botón: un ticket individual con aprobación en AAP
(«Encender», «Habilitar respaldo», la acción sugerida del hallazgo, «Replicar máquina», abrir el flujo o emitir su
certificado). FaceOps nunca toca la máquina directo.

El estado del botón sale de los tickets («En curso · número» mientras está abierto, «Aplicada · número» al terminar bien),
así se mantiene al recargar y al cambiar de ambiente. Se arma con lo ya calculado (salud del segmento, inventario y tabla
de tickets): sin consultas en vivo en la carga."""
import re

from flask import g, render_template, request, url_for

from . import requiere_login
from .bd import j, q, q1
from .salud_acciones import ABIERTOS, MARCA
from .vistas import bp, equipos_visibles

PEN = {"alta": 15, "media": 6}
ENERGIA = "Infra · Encender o apagar equipo"
ACCESO_RED = "FaceOps · Acceso de red"
CERTIFICADO = "Certificados · Emitir para servicio"
RECARGA = {"nginx": "systemctl reload nginx", "httpd": "systemctl reload httpd", "jboss": "systemctl restart jboss", "jboss-eap": "systemctl restart jboss",
           "tomcat": "systemctl restart tomcat", "postgres": "systemctl restart postgresql", "keycloak": "systemctl restart keycloak",
           "redis": "systemctl restart redis", "kafka": "systemctl restart kafka"}


def _auto(nombre_aap):
    return q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre_aap)


def _bloqueo(a):
    """Motivo por el que la regla de prueba no deja lanzarla (None si está lista)."""
    from .pruebas import estado, exceptuada
    ep = estado(dict(a, cliente_id=g.cliente["id"]))["estado"]
    if ep == "certificada" or exceptuada(a["nombre_aap"]):
        return None
    return ("cambió desde su última prueba y debe probarse de nuevo" if ep == "cambiada" else
            "su última prueba falló y debe pasar de nuevo" if ep == "fallida" else "aún no tiene su plan de prueba ejecutado y aprobado")


def _pide_datos(a, extra=()):
    """¿La acción exige datos que no tienen valor por defecto? (entonces se pide desde la ficha, no con un clic)."""
    return any(c.get("requerido") and c["tipo"] not in ("equipo", "equipos") and c.get("defecto") in (None, "") and c["var"] not in extra
               for c in j(a["campos"], []))


def _casos(it, por_nombre, autos):
    """Casos de una situación: [{clave, etiqueta, boton, aid, auto, firma, eid, params, tipo, motivo_no}]."""
    from .operacion import motivo_no_aplica
    from .vistas import auto_sugerida, energia_posible
    k, out = it.get("accion_k"), []
    maqs = [por_nombre[n] for n in it.get("maquinas") or [] if n in por_nombre]
    if k == "encender":
        a = autos.get(ENERGIA)
        for e in maqs:
            c = {"clave": f"enc:{e['id']}", "etiqueta": f"Encender {e['nombre']}", "boton": "Encender", "tipo": "energia", "eid": e["id"],
                 "firma": f"Encender {e['nombre']}", "a": a}
            if not a:
                c["motivo_no"] = f"falta «{ENERGIA}» en el catálogo"
            elif not energia_posible(e):
                c["motivo_no"] = "no se enciende desde FaceOps (solo máquinas virtuales del anfitrión KVM)"
            out.append(c)
    elif k in ("auto", "hallazgo"):
        for e in maqs:
            if k == "auto":
                a = next((autos[n] for n in it.get("autos") or [] if autos.get(n)), None)
                boton = it.get("boton") or (a or {}).get("nombre") or ""
            else:
                a = auto_sugerida(it.get("sugerencia"), [x for x in autos.values() if x.get("visible")])
                boton = (a or {}).get("nombre") or ""
            c = {"clave": f"{k}:{e['id']}:{(a or {}).get('id', 0)}", "etiqueta": f"{e['nombre']}", "boton": boton, "tipo": "equipo", "eid": e["id"], "a": a,
                 "firma": f"{MARCA}{(a or {}).get('nombre', '')} en {e['nombre']}"}
            if not a:
                c["motivo_no"] = "sin acción automática aún"
            else:
                no_ = motivo_no_aplica(g.usuario, g.cliente["id"], a, e)
                if no_ and "ya tiene" not in no_:
                    c["motivo_no"] = no_
                elif _pide_datos(a):
                    c["motivo_no"], c["url"] = "pide datos: lánzala desde la ficha", url_for("consola.equipo", eid=e["id"]) + "#codigo"
            out.append(c)
    elif k in ("abrir", "tls"):
        for f in it.get("flujos") or []:
            o_, d_ = por_nombre.get(f["desde"]), por_nombre.get(f["hacia"])
            pto = (re.findall(r"\d{2,5}", f.get("puerto") or "") or [""])[0]
            if k == "abrir":
                a = autos.get(ACCESO_RED)
                c = {"clave": f"abrir:{f['desde']}:{f['hacia']}:{pto}", "etiqueta": f"{f['desde']} → {f['hacia']}:{f.get('puerto')}", "boton": "Abrir flujo",
                     "tipo": "segmento", "a": a, "firma": f"{MARCA}abrir {f['desde']} → {f['hacia']} ({f.get('puerto')})",
                     "params": {"red_accion": "abrir", "origen": (o_ or {}).get("ip") or "", "destino": (d_ or {}).get("ip") or "",
                                "puerto": (f.get("puerto") or "").replace(" · ", ", ")}}
                if not (o_ and d_ and o_.get("ip") and d_.get("ip")):
                    c["motivo_no"] = "sin IP de origen o destino en el inventario"
            else:
                a = autos.get(CERTIFICADO)
                sk = next((s for s in (d_ or {}).get("skills") or [] if s in RECARGA), "")
                c = {"clave": f"tls:{f['desde']}:{f['hacia']}:{pto}", "etiqueta": f"{f['desde']} → {f['hacia']}", "boton": "Emitir certificado",
                     "tipo": "equipo", "eid": (d_ or {}).get("id"), "a": a, "firma": f"{MARCA}{(a or {}).get('nombre', '')} en {f['hacia']}",
                     "params": {"cert_nombre": (sk or "servicio").replace("jboss-eap", "jboss"), "cert_puerto": pto, "cert_recargar": RECARGA.get(sk, ""),
                                "cert_formato": "pkcs12" if sk in ("jboss", "jboss-eap", "tomcat", "kafka", "keycloak") else "pem"}}
                if not d_:
                    c["motivo_no"] = "el destino no está en el inventario"
            if not a:
                c["motivo_no"] = f"falta «{ACCESO_RED if k == 'abrir' else CERTIFICADO}» en el catálogo"
            out.append(c)
    elif "accion" in it:
        # regla de réplicas y paridad (app/paridad.py): sus máquinas vienen nombradas en el detalle
        ac = it.get("accion") or {}
        a = autos.get(ac.get("nombre_aap") or "") or (q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", ac["aid"], g.cliente["id"]) if ac.get("aid") else None)
        nombres = [n for n in re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]+", (it.get("detalle") or "").split(":", 1)[-1]) if n in por_nombre]
        replica = bool(a and a["nombre_aap"] == "Infra · Replicar máquina")
        for e in [por_nombre[n] for n in dict.fromkeys(nombres)] or [None]:
            if e is None:
                out.append({"clave": "", "etiqueta": "", "boton": "", "a": None, "motivo_no": "sin acción automática aún", "url": it.get("url") or ""})
                continue
            c = {"clave": f"{'rep' if replica else 'par'}:{e['id']}:{(a or {}).get('id', 0)}", "etiqueta": e["nombre"], "eid": e["id"], "a": a,
                 "boton": "Replicar máquina" if replica else (a or {}).get("nombre") or "", "tipo": "replica" if replica else "equipo",
                 "firma": f"Replicar {e['nombre']} en " if replica else f"{MARCA}{(a or {}).get('nombre', '')} en {e['nombre']}"}
            if not a:
                c["motivo_no"] = "sin acción automática aún"
            elif not replica and _pide_datos(a):
                c["motivo_no"], c["url"] = "pide datos: lánzala desde la ficha", url_for("consola.equipo", eid=e["id"]) + "#skills"
            out.append(c)
    return out


def _tickets(aids):
    """Últimos tickets del cliente de esas acciones (una sola consulta a la base de FaceOps)."""
    aids = [a for a in aids if a]
    if not aids:
        return []
    return q(f"SELECT id, numero, estado, motivo, fin, automatizacion_id FROM tickets WHERE cliente_id=? AND automatizacion_id IN ({','.join('?' * len(aids))}) "
             "ORDER BY id DESC LIMIT 800", g.cliente["id"], *aids)


def _reciente(fin, minutos=30):
    """Terminó hace poco: el caso sigue en la lista hasta que el inventario se actualice, mostrado como «Aplicada». Si
    terminó hace más y el problema sigue, se puede volver a pedir."""
    from datetime import datetime, timedelta, timezone
    try:
        f_ = datetime.fromisoformat(str(fin).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - (f_ if f_.tzinfo else f_.replace(tzinfo=timezone.utc)) < timedelta(minutes=minutos)
    except (TypeError, ValueError):
        return False


def _ticket(ts, aid, firma):
    for t in ts:
        m_ = t["motivo"] or ""
        if t["automatizacion_id"] == aid and (m_ == firma or m_.startswith(firma if firma.endswith(" ") else firma + " ")):
            if t["estado"] in ABIERTOS:
                return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": True}
            if t["estado"] == "exitoso" and _reciente(t.get("fin")):   # aplicada y el inventario aún no lo refleja
                return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": False}
            return None   # el último falló o se canceló: se puede volver a pedir
    return None


def filas(r, eqs, segmento, ambiente):
    """[{area, pct, items:[{titulo, detalle, sube, casos:[...]}]}] con solo lo que falta reparar (orden de los pilares de
    la salud; dentro, lo que más sube primero)."""
    from .topologia import PRINCIPIOS_SALUD
    por_nombre = {e["nombre"]: e for e in eqs}
    autos = {a["nombre_aap"]: a for a in q("SELECT * FROM automatizaciones WHERE cliente_id=?", g.cliente["id"])}
    escritura = g.perfil["modo"] == "escritura"
    pct = {a["area"]: a["pct"] for a in r.get("salud_areas") or []}
    enc = r.get("salud_encendido") or {}
    grupos = {}
    for it in r.get("salud") or []:
        if it.get("nivel") not in ("alta", "media") or it.get("area") not in pct:
            continue
        sube = min(PEN[it["nivel"]], 100 - pct[it["area"]])
        if it.get("comp_apagado") and enc.get("componentes"):   # componente sin instancias encendidas: vale 0 en la salud
            sube = round(100 / enc["componentes"])
        grupos.setdefault(it["area"], []).append({"titulo": it["titulo"], "detalle": it.get("detalle") or "", "remedio": it.get("remedio") or "",
                                                  "nivel": it["nivel"], "sube": sube or None, "casos": _casos(it, por_nombre, autos)})
    casos = [c for xs in grupos.values() for it in xs for c in it["casos"]]
    ts = _tickets({c["a"]["id"] for c in casos if c.get("a")})
    url = url_for("consola.salud_segmento_accion", segmento=segmento, ambiente=ambiente)
    for c in casos:
        a = c.pop("a", None)
        if not a:
            continue
        c.update(aid=a["id"], auto=a["nombre"], url_post=url, escritura=escritura, ticket=_ticket(ts, a["id"], c["firma"]))
        if not c.get("motivo_no") and not c["ticket"]:
            c["bloqueo"] = _bloqueo(a)
    out = []
    for area in PRINCIPIOS_SALUD:
        if area in grupos:
            out.append({"area": area, "pct": pct[area], "items": sorted(grupos[area], key=lambda x: (-(x["sube"] or 0), x["nivel"] != "alta"))})
    return out


@bp.route("/mapa/salud/flotante")
@requiere_login
def salud_segmento_flotante():
    """El flotante de salud del segmento en el ambiente pedido. Se trae al abrirlo (no en la carga del mapa) y siempre con
    el ambiente que se está viendo: así no queda pegado el de otro ambiente si se cambió sin recargar la página."""
    from .permisos import puede_ver_segmento
    from .topologia import resumen_segmento
    from .vistas import hallazgos_visibles
    seg, amb = request.args.get("segmento", ""), request.args.get("ambiente") or "produccion"
    if not puede_ver_segmento(g.usuario, g.cliente["id"], seg):
        return {"error": "Segmento no encontrado"}, 404
    eqs = equipos_visibles()
    r = resumen_segmento(g.cliente, eqs, hallazgos_visibles(eqs), seg, amb, indicadores=False)
    gr = filas(r, eqs, seg, amb)
    return {"nombre": seg, "ambiente": amb, "salud_pct": r["salud_pct"], "salud_areas": r["salud_areas"], "salud_meta": r.get("salud_meta"),
            "salud_encendido": r.get("salud_encendido"), "equipos": r["equipos"],
            "pendientes": sum(len([c for c in it["casos"] if c.get("clave")]) or 1 for x in gr for it in x["items"]),
            "html": render_template("_salud_segmento.html", grupos=gr)}


@bp.route("/mapa/salud/accion", methods=["POST"])
@requiere_login
def salud_segmento_accion():
    """Un caso del flotante de salud del segmento → su ticket con aprobación en AAP. El caso se vuelve a calcular en el
    servidor (no se confía en lo que manda el navegador); si ya hay un ticket abierto para ese caso, devuelve ese."""
    from flask import session
    from .aap import ErrorAAP
    from .operacion import solicitar, solicitar_en_equipo
    from .permisos import puede_ver_segmento
    from .topologia import resumen_segmento
    from .vistas import hallazgos_visibles
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    seg, amb, clave = request.args.get("segmento", ""), request.args.get("ambiente", "produccion"), request.form.get("sid", "")
    if not puede_ver_segmento(g.usuario, g.cliente["id"], seg):
        return {"error": "Segmento no encontrado"}, 404
    eqs = equipos_visibles()
    r = resumen_segmento(g.cliente, eqs, hallazgos_visibles(eqs), seg, amb, indicadores=False)
    c = next((c for gr in filas(r, eqs, seg, amb) for it in gr["items"] for c in it["casos"] if c.get("clave") == clave), None)
    if not c or not c.get("aid"):
        return {"error": "Ese caso ya no está pendiente en este ambiente (recarga el mapa)"}, 400
    if c.get("ticket") and c["ticket"]["abierto"]:
        return dict(c["ticket"], url=url_for("consola.ticket", tid=c["ticket"]["id"]), existente=True)
    if c.get("motivo_no"):
        return {"error": c["motivo_no"]}, 400
    if c.get("bloqueo"):
        return {"error": f"«{c['auto']}» está bloqueada: {c['bloqueo']}"}, 400
    a = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", c["aid"], g.cliente["id"])
    e = next((x for x in eqs if x["id"] == c.get("eid")), None)
    try:
        if c["tipo"] == "energia":   # como «Encender» de la ficha: un clúster se enciende por sus nodos
            from .vistas import nodos_equipo_cluster
            for x_ in (nodos_equipo_cluster(e) if (e.get("categoria") or "") == "clúster openshift" else [e]):
                t = solicitar(g.usuario, g.cliente, a, (x_["segmentos"] or e["segmentos"] or [seg])[0], {"en_equipo": x_["nombre"], "en_accion": "encender"},
                              c["firma"] + (f" (nodo {x_['nombre']})" if x_ is not e else ""),
                              filtros={"ambientes": [e["ambiente"]]} if e.get("ambiente") else None)
        elif c["tipo"] == "replica":
            from .replicar_maquina import solicitar_replica
            t = solicitar_replica(g.usuario, g.cliente, e, eqs, {})
        elif c["tipo"] == "segmento":
            t = solicitar(g.usuario, g.cliente, a, seg, c["params"], c["firma"], filtros={"ambientes": [amb]})
        else:
            params = dict(c.get("params") or {})
            for f_ in j(a["campos"], []):
                if f_["tipo"] in ("equipo", "equipos"):
                    params[f_["var"]] = e["nombre"] if f_["tipo"] == "equipo" else [e["nombre"]]
                elif f_["var"] not in params and f_.get("defecto") not in (None, ""):
                    params[f_["var"]] = f_["defecto"]
            t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params, c["firma"])
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"]}
