"""Vista de un segmento de negocio (mapa ?vista=aplicaciones&segmento=…): campana de alertas y botón Skills, igual que en
las fichas de máquina, OpenShift e hipervisor.

Alertas (nivel servicio de negocio, no de máquina): tipo «segmento» del catálogo aap/files/alertas-objetos.yml (G01…G07).
El segmento se presenta a app/alertas_objeto.py como un objeto «seg-<código>» cuyo único «agente» es el blackbox_exporter del
anfitrión: FaceOps arma sus sondas desde la arquitectura aprobada (rol vip = entrada.url por el balanceador; backend = nodos
detrás del balanceador; componente = cada máquina de producción en el puerto de su plataforma) y «Monitoreo · Configurar
alertas del equipo» las escribe en Prometheus (objetivos/seg-<código>.yml, job faceops-segmentos). Alertmanager las entrega
a EDA y FaceOps las sigue igual que las de una máquina («Probar», «Activando · ticket»). Lo que el segmento no tiene cómo
medir (sin URL de entrada, entrada HTTP, sin balanceador) queda «requiere medición» con su motivo; nada se inventa.

Skills: datos/habilidades.yml con categoria: segmento (Ámbito «Segmento»). Cada una crea un ticket con aprobación sobre las
máquinas del segmento; las que no tienen acción publicada, no aplican al segmento o no pasaron su plan de prueba
(app/pruebas.py) aparecen deshabilitadas con su motivo."""
import json
import re

from flask import abort, current_app, g, render_template, request, session, url_for

from . import requiere_login
from .bd import auditar, ej, j, q, q1
from .vistas import bp, equipos_visibles

MARCA = "Skill · "
ABIERTOS = ("pendiente_aprobacion", "en_cola", "en_ejecucion", "en curso", "ejecutando", "esperando")


# ------------------------------------------------------------------ objeto de monitoreo del segmento
def _puertos(x):
    p = x.get("puerto")
    return re.findall(r"\d{1,5}", " ".join(map(str, p)) if isinstance(p, list) else str(p if p is not None else ""))


def _replicas(c, amb):
    r = c.get("replicas")
    if isinstance(r, dict):
        return int(r.get(amb) or 0)
    try:
        return int(r or 0)
    except (TypeError, ValueError):
        return 0


def medicion(seg, arq, eqs):
    """(sondas, {alerta: motivo de «requiere medición»}, N nodos esperados tras el balanceador)."""
    from .arquitectura import asignacion
    m = arq["m"] if arq else {}
    objs, sin, vistos = [], {}, set()

    def agregar(tg, mod, rol, comp):
        if (tg, rol) not in vistos:
            vistos.add((tg, rol))
            objs.append({"target": tg, "modulo": mod, "rol": rol, "componente": comp})
    en = m.get("entrada") or {}
    url = str(en.get("url") or "").strip()
    ambs = m.get("ambientes") or ["produccion"]
    amb = "produccion" if "produccion" in ambs else ambs[0]
    if url.startswith(("http://", "https://")):
        agregar(url, "http_ok", "vip", "entrada")
        if url.startswith("http://"):
            sin["G04"] = "requiere medición: la entrada del segmento es HTTP (sin TLS), no presenta certificado"
    else:
        r_ = ("el segmento no declara la URL de su entrada (entrada.url de su arquitectura)" if arq else
              "el segmento no tiene arquitectura aprobada con la URL de su entrada")
        for k in ("G01", "G02", "G04", "G07"):
            sin[k] = "requiere medición: " + r_
    comps = {c["id"]: c for c in m.get("componentes") or []}
    pto = {}
    if en.get("hacia") in comps and _puertos(en):
        pto[en["hacia"]] = _puertos(en)[0]
    for f in m.get("flujos") or []:
        if f.get("hacia") in comps and _puertos(f):
            pto.setdefault(f["hacia"], _puertos(f)[0])
    asig = asignacion(m, eqs) if m else {}
    maq = []
    if m:
        for cid, por in asig.items():
            maq += [(e, pto.get(cid, "22")) for e in por.get(amb) or [] if e.get("ip")]
    else:
        maq = [(e, "22") for e in eqs if seg["nombre"] in (e.get("segmentos") or []) and e.get("ip")
               and (e.get("ambiente") or "produccion") == "produccion"]
    for e, p in maq:
        agregar(f"{e['ip']}:{p}", "tcp_connect", "componente", e["nombre"])
    if not maq:
        ocp = any(c.get("plataforma") == "openshift" for c in comps.values())
        sin["G06"] = ("requiere medición: sus componentes corren en OpenShift (los vigila el monitoreo del clúster; la entrada la cubre la sonda del VIP)"
                      if ocp else "requiere medición: el segmento no tiene máquinas con IP en producción")
    lb = next((c for c in comps.values() if c.get("tipo") == "balanceador"), None)
    n = 0
    if lb:
        for f in m.get("flujos") or []:
            c = comps.get(f.get("hacia"))
            if f.get("desde") != lb["id"] or not c or c.get("plataforma") == "openshift" or not _puertos(f):
                continue
            n += _replicas(c, amb)
            for e in (asig.get(c["id"]) or {}).get(amb) or []:
                if e.get("ip"):
                    agregar(f"{e['ip']}:{_puertos(f)[0]}", "tcp_connect", "backend", e["nombre"])
    if not lb:
        sin["G05"] = "requiere medición: la arquitectura del segmento no declara un balanceador"
    elif not any(o["rol"] == "backend" for o in objs):
        sin["G05"] = "requiere medición: el balanceador no tiene nodos con IP asignados en producción"
    return objs, sin, max(n, sum(1 for o in objs if o["rol"] == "backend"))


def objeto(cliente_id, seg, eqs=None):
    """El segmento como objeto de monitoreo para app/alertas_objeto.py (nombre seg-<código>, IP = blackbox del anfitrión)."""
    from .arquitectura import vigente
    eqs = equipos_visibles() if eqs is None else eqs
    arq = vigente(cliente_id, seg["nombre"])
    cod = re.sub(r"[^A-Za-z0-9._-]", "", str((arq["m"].get("codigo") if arq else "") or seg["id"]))
    objs, sin, n = medicion(seg, arq, eqs)
    d = dict(j(seg.get("datos"), {}) or {}, objetivos=objs, sin_medicion=sin, n_backends=n)
    return {"id": seg["id"], "nombre": f"seg-{cod}", "categoria": "segmento", "ip": "127.0.0.1", "so": "", "estado": "activo",
            "datos": json.dumps(d, ensure_ascii=False), "segmento": seg["nombre"], "cliente_id": cliente_id}


def registrar_alertas(cliente_id, x, numero):
    """Hook al terminar el ticket de alertas: el reflejo de lo aplicado queda en el segmento (segmentos.datos)."""
    from .bd import ahora
    for s in q("SELECT * FROM segmentos WHERE cliente_id=?", cliente_id):
        try:
            if objeto(cliente_id, s, [])["nombre"] != x.get("equipo"):
                continue
        except Exception:  # noqa: BLE001
            continue
        d = j(s.get("datos"), {}) or {}
        d["alertas"] = {"tipo": "segmento", "alertas": x.get("alertas") or [], "resumen": x.get("resumen") or [], "actualizado": ahora(), "ticket": numero}
        ej("UPDATE segmentos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), s["id"])
        return


def _seg(sid):
    from .permisos import puede_ver_segmento
    s = q1("SELECT * FROM segmentos WHERE cliente_id=? AND id=?", g.cliente["id"], sid)
    if not s or not puede_ver_segmento(g.usuario, g.cliente["id"], s["nombre"]):
        abort(404)
    return s


@bp.app_template_global("alertas_segmento")
def alertas_segmento(seg):
    """Contexto del flotante de alertas del segmento (mismo macro que la máquina, con sus propias rutas)."""
    from . import alertas_objeto as ao
    if not seg or not seg.get("id"):
        return None
    try:
        e = objeto(g.cliente["id"], q1("SELECT * FROM segmentos WHERE id=?", seg["id"]) or seg)
        ctx = ao.contexto(g.cliente["id"], e, g.perfil["modo"] == "escritura")
    except Exception as x:  # noqa: BLE001
        current_app.logger.warning("alertas del segmento %s: %s", seg.get("nombre"), x)
        return None
    if not ctx:
        return None
    d = j(e["datos"], {})
    n = d.get("n_backends") or 0
    for xs in ctx["grupos"].values():   # «menos de N nodos sanos»: N = réplicas de producción tras el balanceador
        for a in xs:
            if a["id"] == "G05" and not a.get("en_reflejo") and not a.get("pendiente") and n:
                a["valor_aviso"], a["valor_critico"] = (n if n > 1 else None), 1
    ctx.update(url_regla=url_for("consola.segmento_alerta_regla", sid=seg["id"]), url_probar=url_for("consola.segmento_alerta_probar", sid=seg["id"]),
               medidas=sum(1 for xs in ctx["grupos"].values() for a in xs if not a.get("pendiente")), nombre_mon=e["nombre"])
    return ctx


@bp.route("/mapa/segmento/<int:sid>/alertas", methods=["POST"])
@requiere_login
def segmento_alertas(sid):
    """Campana del segmento: ticket a «Monitoreo · Configurar alertas del equipo» con sus alertas y sus sondas."""
    from . import alertas_objeto as ao
    from .vistas import _alertas_pedir, _escritura
    _escritura()
    s = _seg(sid)
    e = objeto(g.cliente["id"], s)
    ctx = ao.contexto(g.cliente["id"], e, True)
    if not ctx or not ctx["disponible"]:
        return {"error": (ctx or {}).get("por_que") or "Sin alertas para este segmento"}, 400
    f = request.form.to_dict()
    n_ = sum(1 for k in f if k.startswith("on_"))
    f["motivo"] = f.get("motivo") or (f"Alertas del segmento {s['nombre']}: {n_} activa{'s' if n_ != 1 else ''}" if n_ else f"Retirar las alertas del segmento {s['nombre']}")
    try:
        t, a = _alertas_pedir(e, "maquina", f)
    except (ValueError, PermissionError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/mapa/segmento/<int:sid>/alertas/probar", methods=["POST"])
@requiere_login
def segmento_alerta_probar(sid):
    """«Probar»: alerta sintética del segmento por la cadena real (Alertmanager → EDA → aviso), sin tocar nada."""
    from .alerta_prueba import lanzar
    from .vistas import _escritura
    _escritura()
    e = objeto(g.cliente["id"], _seg(sid))
    try:
        pid = lanzar(current_app._get_current_object(), g.cliente, e, "maquina", (request.form.get("aid") or "").strip(), g.usuario)
    except ValueError as x:
        return {"error": str(x)}, 400
    return {"id": pid, "url": url_for("consola.segmento_alerta_prueba", sid=sid, pid=pid)}


@bp.route("/mapa/segmento/<int:sid>/alertas/prueba/<int:pid>")
@requiere_login
def segmento_alerta_prueba(sid, pid):
    from .alerta_prueba import leer
    e = objeto(g.cliente["id"], _seg(sid), [])
    p_ = leer(pid)
    if not p_ or p_["cliente_id"] != g.cliente["id"] or p_["equipo"] != e["nombre"]:
        abort(404)
    return {"estado": p_["estado"], "pasos": p_["pasos"], "inicio": p_["inicio"]}


@bp.route("/mapa/segmento/<int:sid>/alertas/regla")
@requiere_login
def segmento_alerta_regla(sid):
    """La regla de una alerta del segmento tal como está cargada en Prometheus (solo lectura)."""
    from . import alertas_objeto as ao
    s = _seg(sid)
    e = objeto(g.cliente["id"], s, [])
    aid = (request.args.get("aid") or "").strip()
    a = next((x for x in ao.catalogo()["alertas"] if x["id"] == aid and "segmento" in x.get("tipos", [])), None)
    if not a:
        abort(404)
    mon = ao.reglas_monitoreo(e, "maquina", "segmento")
    return render_template("alerta_regla.html", e=e, a=a, aid=aid, ambito="maquina", mon=mon, d=(mon or {}).get(aid),
                           grafana=ao.url_grafana(e["nombre"], aid), grupo=ao.grupo_monitoreo(e), archivo=f"{ao.REGLAS}/{e['nombre']}.yml",
                           volver=url_for("consola.mapa", vista="aplicaciones", segmento=s["nombre"]))


# ------------------------------------------------------------------ skills del segmento
def _contexto_skill(seg, arq, eqs):
    """Lo que tiene el segmento para decidir si una skill aplica: entrada, HTTPS, balanceador, máquinas y sus plataformas."""
    from .inventario import skills_de
    from .vistas import familia
    m = arq["m"] if arq else {}
    url = str((m.get("entrada") or {}).get("url") or "")
    mios = [e for e in eqs if seg["nombre"] in (e.get("segmentos") or [])]
    return {"https": url.startswith("https://"), "entrada": url.startswith("http"),
            "balanceador": any(c.get("tipo") == "balanceador" for c in m.get("componentes") or []),
            "maquinas": bool(mios), "vm": bool(mios) and not (m and all(c.get("plataforma") == "openshift" for c in m.get("componentes") or [])),
            "fams": {familia(s_) for e in mios for s_ in skills_de(e)}, "mios": mios}


REQ = {"https": "la entrada del segmento no es HTTPS (no hay certificado de VIP que renovar)",
       "entrada": "el segmento no declara la URL de su entrada",
       "balanceador": "la arquitectura del segmento no declara un balanceador",
       "maquinas": "el segmento no tiene máquinas en el inventario",
       "vm": "el segmento ya corre en OpenShift (no tiene máquinas virtuales que mover)"}


def _skill(h, cx):
    """(automatización o None, motivo de bloqueo) de una skill del segmento."""
    from .habilidades import _faltan_datos
    from .pruebas import estado, exceptuada
    if h.get("requiere") and not cx.get(h["requiere"]):
        return None, REQ.get(h["requiere"], h["requiere"])
    nombres = list(h.get("nombre_aap") or [])
    if h.get("por_plataforma"):
        nombres = [v for k, v in h["por_plataforma"].items() if k in cx["fams"]]
        if not nombres:
            return None, h.get("sin_accion") or "ninguna plataforma del segmento tiene una acción del catálogo para esto"
    if not nombres:
        return None, h.get("sin_accion") or "aún no hay una acción del catálogo para esta skill"
    a = next((x for n_ in nombres for x in [q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], n_)] if x), None)
    if not a:
        return None, f"aún no disponible: «{nombres[0]}» no está publicada en AAP"
    a = dict(a)
    ep = estado(dict(a, cliente_id=g.cliente["id"]))["estado"]
    if ep != "certificada" and not exceptuada(a["nombre_aap"]):
        return a, {"cambiada": "cambió desde su última prueba; debe probarse de nuevo", "fallida": "su última prueba falló"}.get(ep, "sin plan de prueba aprobado")
    if h.get("parametros_de") != "flujos":
        f_ = _faltan_datos(a, h)
        if f_:
            return a, "pide datos (" + ", ".join(f_[:2]) + "): lánzala desde Acciones"
    return a, ""


def _ticket(seg, aid):
    for t in q("SELECT id, numero, estado, motivo FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 40", g.cliente["id"], aid):
        if not (t["motivo"] or "").startswith(MARCA) or not (t["motivo"] or "").endswith(f" en {seg['nombre']}"):
            continue
        if t["estado"] in ABIERTOS:
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": True}
        return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": False} if t["estado"] == "exitoso" else None
    return None


@bp.app_template_global("habilidades_segmento")
def habilidades_segmento(seg):
    """Skills de la vista del segmento (habilidades.yml, categoria: segmento), mismas columnas y estados que las de la máquina."""
    from .arquitectura import vigente
    from .habilidades import es_skill, mapa
    if not seg:
        return []
    escritura = g.perfil["modo"] == "escritura"
    cx = _contexto_skill(seg, vigente(g.cliente["id"], seg["nombre"]), equipos_visibles())
    out = []
    for h in mapa():
        if h.get("categoria") != "segmento" or not es_skill(h):   # solo lo diferenciado: las operaciones viven en la vista del segmento
            continue
        a, motivo = _skill(h, cx)
        out.append({"id": h["id"], "tipo": h.get("tipo") or "quick win", "grupo": h.get("grupo") or "", "nombre": h["nombre"], "valor": h.get("valor") or "",
                    "ambito": h.get("ambito") or "Segmento", "auto": (a or {}).get("nombre") or ((h.get("nombre_aap") or [""])[0] or h["nombre"]),
                    "aid": (a or {}).get("id"), "ui": "", "bloqueo": motivo, "ticket": _ticket(seg, a["id"]) if a else None, "escritura": escritura,
                    "url": url_for("consola.segmento_skill", sid=seg["id"]), "campos": []})
    out.sort(key=lambda x: (x["tipo"] != "diferenciador", bool(x["bloqueo"])))
    return out


@bp.route("/mapa/segmento/<int:sid>/skill", methods=["POST"])
@requiere_login
def segmento_skill(sid):
    """Ticket (con aprobación en AAP) de una skill del segmento, sobre sus máquinas; si ya hay uno abierto, devuelve ese."""
    from .aap import ErrorAAP
    from .arquitectura import vigente
    from .habilidades import mapa
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    s = _seg(sid)
    h = next((x for x in mapa() if x.get("id") == request.form.get("hid") and x.get("categoria") == "segmento"), None)
    if not h:
        return {"error": "Skill no válida"}, 400
    eqs = equipos_visibles()
    cx = _contexto_skill(s, vigente(g.cliente["id"], s["nombre"]), eqs)
    a, motivo = _skill(h, cx)
    if not a or motivo:
        return {"error": f"«{h['nombre']}»: {motivo}"}, 400
    t0 = _ticket(s, a["id"])
    if t0 and t0["abierto"]:
        return dict(t0, url=url_for("consola.ticket", tid=t0["id"]), existente=True)
    params = {}
    for c in j(a.get("campos"), []):
        if c.get("defecto") not in (None, ""):
            params[c["var"]] = int(c["defecto"]) if c.get("tipo") == "integer" and str(c["defecto"]).lstrip("-").isdigit() else c["defecto"]
    params.update(h.get("parametros") or {})
    if h.get("parametros_de") == "flujos":   # verificar de punta a punta: los tramos de este segmento
        from .topologia import flujos_verificables
        noms = {e["nombre"] for e in cx["mios"]}
        params["faceops_flujos"] = [f for f in flujos_verificables(g.cliente, eqs) if f.get("desde") in noms]
        if not params["faceops_flujos"]:
            return {"error": "El segmento no tiene tramos verificables (el origen debe ser un servidor con datos del recolector)"}, 400
    try:
        t = solicitar(g.usuario, g.cliente, a, s["nombre"], params, f"{MARCA}{h['nombre']} en {s['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    auditar(g.usuario["usuario"], g.cliente["id"], "skill de segmento", s["nombre"], {"skill": h["id"], "ticket": t["numero"]})
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"]}
