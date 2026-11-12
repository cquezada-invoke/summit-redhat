"""Ficha de una plataforma (/plataformas/<ID>) con el esquema de la ficha de OpenShift y de la máquina: cabecera con un solo
«Editar conexión», barra de salud (su flotante muestra solo lo remediable, cada punto con su acción en segundo plano), caja
Encendido (el servicio de la plataforma; «Consola» arriba a la derecha si la tiene), campana de alertas y botón Skills, y las
secciones componentes, recursos, máquinas donde corre, segmentos de negocio y acciones.

La salud sale de indicadores propios de cada tecnología (datos/plataformas-ficha.yml) medidos SIN agente: «Plataforma · Medir
salud» entra por SSH/WinRM y lee las APIs locales (S3, Kafka, Keycloak…); los equipos de red usan su inventario de red y una
plataforma en un clúster, la recolección del clúster. Nada se inventa: lo no medido queda «sin medir» y no castiga.
Lo medido se publica como métricas (/metricas/plataformas) que Prometheus lee para las alertas de la plataforma
(Prometheus → Alertmanager → EDA → FaceOps). Toda acción va por AAP con ticket y aprobación."""
import json
import os
import time
from datetime import datetime, timezone

import yaml
from flask import Response, abort, g, redirect, render_template, request, session, url_for

from . import requiere_login
from .bd import ahora, ej, j, q1
from .vistas import bp, equipos_visibles

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "plataformas-ficha.yml")
MEDIR = "Plataforma · Medir salud"
VIGENCIA_H = 24
_cache = {}


def catalogo():
    m = os.path.getmtime(RUTA)
    if _cache.get("m") != m:
        _cache.update(m=m, d=(yaml.safe_load(open(RUTA, encoding="utf-8")) or {}).get("plataformas") or {})
    return _cache["d"]


def _edad_h(iso):
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


# ───────────────────────────────────────────── lo medido (sin inventar)
def _de_red(d):
    """Indicadores de un equipo de red desde su inventario de red (lo deja «Red · Inventariar equipo de red»)."""
    r = d.get("red") or {}
    ifs = [x for x in r.get("interfaces") or [] if isinstance(x, dict)]
    adm = [x for x in ifs if (x.get("admin") or "up") == "up"]
    bgp = [x for x in r.get("bgp") or [] if isinstance(x, dict)]
    v = {"interfaces": len(ifs), "if_admin": len(adm), "if_up": sum(1 for x in adm if x.get("estado") == "up"),
         "if_errores": sum(1 for x in ifs if (x.get("errores") or 0) > 0), "subredes": len(r.get("subredes") or []),
         "rutas": len((r.get("rutas") or {}).get("lista") or []) if isinstance(r.get("rutas"), dict) else len(r.get("rutas") or []),
         "gestion_ssh": 1 if ((r.get("gestion") or {}).get("acceso") or "").lower() == "ssh" else 0}
    if bgp:
        ok = [x for x in bgp if (x.get("estado") or "").lower() == "established"]
        v.update(bgp_total=len(bgp), bgp_ok=len(ok), bgp_sin_prefijos=sum(1 for x in ok if not x.get("prefijos_recibidos")))
    comps = [{"n": x.get("nombre"), "ok": x.get("estado") == "up", "d": ", ".join(x.get("ips") or []) or x.get("descripcion") or ""} for x in ifs]
    comps += [{"n": f"BGP {x.get('vecino')}", "ok": (x.get("estado") or "").lower() == "established",
               "d": f"AS {x.get('as_remoto')} · {x.get('descripcion') or ''} · {x.get('prefijos_recibidos', 0)} prefijos"} for x in bgp]
    return {"valores": v, "texto": {"version": r.get("version") or "", "rol": r.get("rol") or ""}, "componentes": comps,
            "medido": r.get("recolectado") or d.get("recolectado"), "fuente": "inventario de red (AAP por SSH)"}


def _de_ocp(d, cod):
    """La plataforma corriendo en un clúster: sus pods en el namespace (recolección del clúster por su API)."""
    o = d.get("ocp") or {}
    clave = {"data-lake": "lake"}.get(cod, cod)
    ns = next((x for x in o.get("namespaces_app") or [] if clave in (x.get("ns") or "")), None)
    if not ns:
        return {"valores": {}, "texto": {}, "componentes": [], "medido": o.get("recolectado"), "fuente": "recolección del clúster (API)",
                "notas": [f"el clúster no tiene un namespace de {cod}"]}
    pods = ns.get("l_pods") or []
    serv = [p for p in pods if p.get("fase") not in ("Succeeded", "Failed") and not (p.get("n") or "").endswith("-build")]
    listos = [p for p in serv if p.get("fase") == "Running" and (p.get("listos") or "0/1").split("/")[0] == (p.get("listos") or "0/1").split("/")[-1]]
    duenos = {p.get("dueno") for p in listos}
    v = {"pods_activos": len(serv), "pods_listos": len(listos), "cargas_fallidas": sum(1 for p in pods if p.get("fase") == "Failed"),
         "reinicios_max": max([p.get("reinicios") or 0 for p in serv] or [0]), "rutas": len(ns.get("rutas") or [])}
    if cod == "data-lake":
        v.update(trino_ok=1 if "trino" in duenos else 0, colector_ok=1 if "colector" in duenos else 0)
    comps = [{"n": p.get("n"), "ok": p in listos, "d": f"{p.get('fase')} · {p.get('listos')} listos · {p.get('reinicios') or 0} reinicios"} for p in serv]
    return {"valores": v, "texto": {"namespace": ns.get("ns"), "rutas": ", ".join(ns.get("rutas") or [])}, "componentes": comps,
            "medido": o.get("recolectado"), "fuente": f"recolección del clúster (API) · namespace {ns.get('ns')}"}


def medicion(e, cod):
    """{valores, texto, componentes, medido, fuente, ticket, notas} de la plataforma en la máquina; vacío si nunca se midió."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    defn = catalogo().get(cod) or {}
    if defn.get("fuente") == "red":
        return _de_red(d)
    if (e.get("categoria") or "") == "clúster openshift":
        return _de_ocp(d, cod)
    m = (d.get("salud_plat") or {}).get(cod) or {}
    return dict(m, fuente=m.get("fuente") or f"«{MEDIR}» (AAP por {'WinRM' if 'windows' in (e.get('so') or '').lower() else 'SSH'})")


def indicadores(e, cod):
    defn = catalogo().get(cod) or {}
    if (e.get("categoria") or "") == "clúster openshift" and defn.get("indicadores_ocp"):
        return defn["indicadores_ocp"]
    return defn.get("indicadores") or []


def pct(ind, v):
    """% de cumplimiento de un indicador con lo medido; None si no se midió (no castiga)."""
    rg = ind.get("regla")
    if isinstance(rg, dict) and "ratio" in rg:
        a, b = (v.get(k) for k in rg["ratio"])
        if a is None or b is None:
            return None
        return 100 if not b else max(0, min(100, round(100 * a / b)))
    x = v.get(ind.get("clave"))
    if x is None:
        return None
    if rg == "si":
        return 100 if x else 0
    if rg == "no":
        return 0 if x else 100
    if isinstance(rg, dict) and "min" in rg:
        return 100 if x >= rg["min"] else max(0, round(100 * x / rg["min"])) if rg["min"] else 0
    if isinstance(rg, dict) and "max" in rg:
        return 100 if x <= rg["max"] else (0 if not rg["max"] else max(0, round(100 * rg["max"] / x)))
    return None


def valor_txt(ind, v):
    rg = ind.get("regla")
    if isinstance(rg, dict) and "ratio" in rg:
        a, b = (v.get(k) for k in rg["ratio"])
        return "" if a is None or b is None else f"{a:g} de {b:g}"
    x = v.get(ind.get("clave"))
    if x is None:
        return "sin medir"
    if rg in ("si", "no"):
        return "sí" if x else "no"
    return f"{x:g}"


# ───────────────────────────────────────────── acciones (todas por AAP con ticket y aprobación)
def _acc_skill(e, hid, esc):
    from .habilidades import skill_plataforma
    s = skill_plataforma(e, hid, esc) if hid else None
    if not s:
        return {"bloqueo": "sin acción automática para este punto todavía", "auto": "", "escritura": esc, "ticket": None}
    return {"aid": s["aid"], "auto": s["auto"], "bloqueo": s["bloqueo"], "escritura": esc, "ticket": s["ticket"], "hid": hid}


def _auto(nombre):
    return q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre)


def _bloqueo_auto(a, nombre):
    from .pruebas import estado, exceptuada
    if not a:
        return f"«{nombre}» aún no está publicada en AAP"
    ep = estado(dict(a, cliente_id=g.cliente["id"]))["estado"]
    if ep != "certificada" and not exceptuada(nombre):
        return {"cambiada": "cambió desde su última prueba; debe probarse de nuevo", "fallida": "su última prueba falló"}.get(ep, "sin plan de prueba aprobado")
    return ""


def _ticket_medir(e, cod, aid):
    """Último ticket de la medición de esta plataforma en esta máquina: abierto («En curso») o exitoso («Aplicada»)."""
    from .bd import q
    from .salud_acciones import ABIERTOS
    cache = g.setdefault("_tk_medir", {})
    if aid not in cache:
        cache[aid] = q("SELECT id, numero, estado, limite, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 80",
                       g.cliente["id"], aid)
    for t in cache[aid]:
        p_ = j(t["parametros"], {}) or {}
        if p_.get("pm_plataforma") == cod and e["nombre"] in (t["limite"] or "").split(":"):
            if t["estado"] in ABIERTOS:
                return dict(t, abierto=True)
            return dict(t, abierto=False) if t["estado"] == "exitoso" else None
    return None


def acc_medir(e, cod, esc):
    """Acción «Medir ahora» (el punto «Medición vigente»): según la fuente, la medición de la plataforma, el inventario de red o
    la recolección del clúster."""
    defn = catalogo().get(cod) or {}
    if defn.get("fuente") == "red":
        nombre = "Red · Inventariar equipo de red"
    elif (e.get("categoria") or "") == "clúster openshift":
        nombre = "OpenShift · Recolectar clúster"
    else:
        nombre = MEDIR
    a = _auto(nombre)
    b = _bloqueo_auto(a, nombre)
    if not b and e.get("energia") == "apagado":
        b = "la máquina que la corre está apagada"
    if not b and nombre == MEDIR and cod not in ("data-lake", "kafka", "keycloak", "jboss-eap", "redis", "postgresql", "activemq", "nginx", "vault",
                                                  "satellite", "red-hat-idm", "389-directory-server", "iis", "active-directory"):
        b = "esta plataforma aún no tiene medición propia"
    return {"aid": (a or {}).get("id"), "auto": (a or {}).get("nombre") or nombre, "bloqueo": b, "escritura": esc,
            "ticket": _ticket_medir(e, cod, a["id"]) if a and nombre == MEDIR else None, "medir": nombre}


def radar(e, cod, m, esc):
    """Salud de la plataforma con los 6 pilares de FaceOps: sus indicadores propios + «Medición vigente». Cada punto que no se
    cumple lleva su acción (la skill que lo remedia o «Medir ahora»)."""
    from .radar import _armar
    v = m.get("valores") or {}
    crit = []
    for ind in indicadores(e, cod):
        p_ = pct(ind, v)
        crit.append({"id": ind["id"], "principio": ind["principio"], "nombre": ind["nombre"], "faltan": [], "n_faltan": 0,
                     "explica": f"{ind.get('explica', '')} · hoy: {valor_txt(ind, v)}", "pct": p_, "acc": _acc_skill(e, ind.get("skill"), esc) if p_ is not None and p_ < 100 else None})
    ed = _edad_h(m.get("medido"))
    p_m = None if not indicadores(e, cod) else (0 if ed is None else (100 if ed <= VIGENCIA_H else (50 if ed <= 3 * VIGENCIA_H else 0)))
    if p_m is not None:
        crit.append({"id": "PM0", "principio": "Excelencia operativa", "nombre": f"Medición vigente (menos de {VIGENCIA_H} h)", "faltan": [], "n_faltan": 0,
                     "explica": (f"última {m['medido'][:16].replace('T', ' ')} UTC" if m.get("medido") else "nunca se ha medido") + f" · {m.get('fuente', '')}",
                     "pct": p_m, "acc": acc_medir(e, cod, esc) if p_m < 100 else None})
    if e.get("energia") == "apagado":   # la máquina que la corre está apagada: nada de lo medido vale hoy
        for c in crit:
            if c["principio"] == "Disponibilidad" and c["pct"] is not None:
                c["pct"], c["explica"] = 0, c["explica"] + " · la máquina está apagada"
    r = _armar(crit, 1, g.cliente)
    r["remediables"] = sorted([c for c in crit if c["pct"] is not None and c["pct"] < 100], key=lambda c: c["pct"])
    return r


# ───────────────────────────────────────────── contexto de la ficha
def contexto(e, rsk, cod, pid):
    from .vistas import _instancia, _plataformas_de, _riesgo_parque, id_plataforma, familia, SERVICIOS_PLATAFORMA, servicio_estado, consola_plataforma
    from . import alertas_objeto as ao
    esc = g.perfil["modo"] == "escritura"
    defn = catalogo().get(cod) or {}
    p = _instancia(e, rsk, cod)
    m = medicion(e, cod)
    r = radar(e, cod, m, esc)
    v, tx = m.get("valores") or {}, m.get("texto") or {}
    # Encendido: el servicio de la plataforma (Plataforma · Controlar servicio); en un clúster lo mantiene el clúster
    fam = familia(cod)
    unidades = SERVICIOS_PLATAFORMA.get(fam) or ([defn["unidad"]] if defn.get("unidad") else [])
    est_s = servicio_estado(e, cod) if fam in SERVICIOS_PLATAFORMA else None
    if est_s is None and v.get("servicio_activo") is not None:
        est_s = "en ejecución" if v["servicio_activo"] else "detenido"
    if e.get("energia") == "apagado":
        est_s = "detenido"
    a_srv = _auto("Plataforma · Controlar servicio")
    motivo_e = ""
    if (e.get("categoria") or "") == "clúster openshift":
        motivo_e = "Corre en el clúster: la mantienen sus despliegues (se escala o se detiene desde OpenShift)"
    elif defn.get("fuente") == "red":
        motivo_e = "Es el sistema del equipo de red: se enciende o apaga con el equipo"
    elif fam not in SERVICIOS_PLATAFORMA:
        motivo_e = "Aún no disponible: su servicio no está en el control de servicios de FaceOps"
    elif e.get("energia") == "apagado":
        motivo_e = "La máquina que la corre está apagada: enciéndela desde su ficha"
    else:
        motivo_e = _bloqueo_auto(a_srv, "Plataforma · Controlar servicio")
    if not motivo_e and not esc:
        motivo_e = "Tu perfil es de solo lectura"
    # alertas: la campana global de la plataforma (su ámbito en la máquina que la corre)
    amb = ao.ambito_de_plataforma(fam)
    try:
        al = ao.contexto(g.cliente["id"], e, esc, amb, defn.get("nombre") or p["nombre"]) if amb else None
    except Exception:  # noqa: BLE001  la ficha no se cae si el monitoreo no responde
        al = None
    # máquinas donde corre: la suya y las otras instancias de la misma plataforma en el parque
    otras = []
    for e_, r_ in _riesgo_parque():
        if e_["id"] != e["id"] and cod in _plataformas_de(e_, r_):
            otras.append({"pid": id_plataforma(cod, e_["id"]), "nombre": e_["nombre"], "ip": e_.get("ip") or "", "so": e_.get("so") or "",
                          "energia": e_.get("energia") or "", "categoria": e_.get("categoria") or "", "eid": e_["id"]})
    comps = m.get("componentes") or []
    if not comps:   # sin medir: los componentes conocidos de la plataforma, sin estado inventado
        comps = [{"n": c, "ok": None, "d": "sin medir"} for c in defn.get("componentes") or []] + \
                [{"n": c, "ok": None, "d": "componente interno"} for c in p.get("internos") or []]
    recursos = [{"t": x["t"], "v": v.get(x["k"], tx.get(x["k"]))} for x in defn.get("recursos") or []]
    if (e.get("categoria") or "") == "clúster openshift":
        recursos = [{"t": "Pods activos", "v": v.get("pods_activos")}, {"t": "Pods listos", "v": v.get("pods_listos")},
                    {"t": "Rutas", "v": v.get("rutas")}, {"t": "Namespace", "v": tx.get("namespace")}]
    consola = p.get("entrar")
    if not consola and cod == "data-lake":
        from .topologia import LAKE
        consola = {"url": LAKE["web"], "t": "Consola del data lake"} if LAKE.get("web") else None
    from .vistas import CONSOLAS
    d_ = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    editable = fam in CONSOLAS and (e.get("categoria") or "") != "clúster openshift"
    return {"puerto_editable": editable, "puerto_defecto": CONSOLAS[fam][1] if editable else "", "puerto_actual": (d_.get("puertos_web") or {}).get(fam),
            "inventarios": j(e.get("inventarios"), []) if isinstance(e.get("inventarios"), str) else (e.get("inventarios") or []),
            "p": p, "defn": defn, "m": m, "radar_p": r, "esc": esc, "al": al, "otras": sorted(otras, key=lambda x: x["nombre"]),
            "comps": comps, "recursos_p": recursos, "est_s": est_s, "motivo_e": motivo_e, "unidades": unidades, "consola": consola,
            "acc_medir": acc_medir(e, cod, esc), "e": e, "cod": cod, "pid": pid, "ambito_txt": defn.get("ambito") or p["nombre"]}


# ───────────────────────────────────────────── rutas
def _de_pid(pid):
    from .vistas import _plataforma_de_id
    return _plataforma_de_id(pid)


def _json_ticket(t, a):
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": (a or {}).get("nombre", "")}


@bp.route("/plataformas/<pid>/medir", methods=["POST"])
@requiere_login
def plataforma_medir(pid):
    """«Medir ahora»: ticket (con aprobación en AAP) de la medición de la plataforma; solo lectura, sin agente."""
    from .aap import ErrorAAP
    from .operacion import solicitar_en_equipo
    from .vistas import puerto_web, familia
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e, _, cod = _de_pid(pid)
    ac = acc_medir(e, cod, True)
    js = request.headers.get("X-Requested-With") == "fetch"
    vuelta = request.form.get("volver") or url_for("consola.plataforma_detalle", pid=pid)
    if ac.get("ticket") and ac["ticket"].get("abierto"):
        t = ac["ticket"]
        return dict(_json_ticket(t, None), existente=True) if js else redirect(vuelta)
    try:
        if ac["bloqueo"]:
            raise ValueError(ac["bloqueo"])
        a = _auto(ac["medir"])
        params = {}
        if ac["medir"] == MEDIR:
            params["pm_plataforma"] = cod
            if familia(cod) == "keycloak":
                params["pm_puerto"] = int(puerto_web(e, "keycloak", 8080))
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params, f"Medir salud de {pid} en {e['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as x:
        if js:
            return {"error": str(x)}, 400
        from flask import flash
        flash(str(x), "error")
        return redirect(vuelta)
    if js:
        return _json_ticket(t, a)
    from flask import flash
    flash(f"{t['numero']}: medición de {pid}" + (" (espera aprobación)" if t["estado"] == "pendiente_aprobacion" else ""), "ok")
    return redirect(vuelta)


@bp.route("/plataformas/<pid>/conexion", methods=["POST"])
@requiere_login
def plataforma_conexion(pid):
    """«Editar conexión» de la plataforma: el puerto HTTP propio (si no usa el de fábrica) queda registrado en FaceOps y se
    vuelve a medir por AAP (solo lectura). Los secretos nunca pasan por aquí: viven en AAP o en la bóveda."""
    from flask import flash
    from .vistas import CONSOLAS, familia, _escritura
    _escritura()
    e, _, cod = _de_pid(pid)
    fam = familia(cod)
    pt = (request.form.get("puerto") or "").strip()
    if fam not in CONSOLAS or (pt and not (pt.isdigit() and 1 <= int(pt) <= 65535)):
        flash("Puerto no válido (1 a 65535)", "error")
        return redirect(url_for("consola.plataforma_detalle", pid=pid) + "?editar=1")
    x = q1("SELECT datos FROM equipos WHERE id=? AND cliente_id=?", e["id"], g.cliente["id"])
    d = j((x or {}).get("datos"), {})
    pw = d.setdefault("puertos_web", {})
    if pt:
        pw[fam] = int(pt)
    else:
        pw.pop(fam, None)
    ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), e["id"])
    from .bd import auditar
    auditar(g.usuario["usuario"], g.cliente["id"], "conexión de plataforma", pid, {"puerto": pt or "de fábrica"})
    return plataforma_medir(pid)


@bp.route("/plataformas/<pid>/salud-accion", methods=["POST"])
@requiere_login
def plataforma_salud_accion(pid):
    """Acción de un punto de salud (flotante de salud): la skill que lo remedia, como ticket con aprobación en segundo plano."""
    from .habilidades import ejecutar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e, _, cod = _de_pid(pid)
    sid = request.form.get("sid") or ""
    if sid == "PM0":
        return plataforma_medir(pid)
    ind = next((x for x in indicadores(e, cod) if x["id"] == sid), None)
    if not ind or not ind.get("skill"):
        return {"error": "Este punto aún no tiene una acción automática"}, 400
    return ejecutar(e, ind["skill"], request.form)


def registrar_medicion(cliente_id, art, numero):
    """Hook al terminar «Plataforma · Medir salud»: lo medido queda en la ficha (datos.salud_plat del equipo)."""
    for equipo, por in ((art or {}).get("faceops_salud_plat") or {}).items():
        e = q1("SELECT id, datos FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente_id, equipo)
        if not e or not isinstance(por, dict):
            continue
        d = j(e["datos"], {})
        for cod, x in por.items():
            if isinstance(x, dict) and cod in catalogo():
                d.setdefault("salud_plat", {})[cod] = {"valores": {k: v for k, v in (x.get("valores") or {}).items() if isinstance(v, (int, float))},
                                                       "texto": {k: str(v)[:200] for k, v in (x.get("texto") or {}).items()},
                                                       "componentes": (x.get("componentes") or [])[:40], "notas": (x.get("notas") or [])[:10],
                                                       "medido": x.get("medido") or ahora(), "ticket": numero}
        ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), e["id"])


def _metricas():
    """Texto de exposición de Prometheus con los indicadores medidos de cada plataforma (todos los clientes)."""
    from .bd import q
    lin = ["# HELP faceops_plataforma_indicador Indicador propio de una plataforma, medido sin agente por FaceOps (AAP por SSH/API)",
           "# TYPE faceops_plataforma_indicador gauge",
           "# HELP faceops_plataforma_medicion_edad_segundos Segundos desde la última medición de la plataforma",
           "# TYPE faceops_plataforma_medicion_edad_segundos gauge"]
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')   # noqa: E731
    cods = {c: v for c, v in catalogo().items()}
    from .inventario import skills_de
    for e in q("SELECT e.*, c.codigo FROM equipos e JOIN clientes c ON c.id=e.cliente_id WHERE e.estado!='retirado'"):
        d = j(e["datos"], {})
        try:
            sk = set(skills_de(e))
        except Exception:  # noqa: BLE001
            sk = set()
        fuentes = [(cod, medicion(e, cod)) for cod in (d.get("salud_plat") or {}) if cod in cods and (e["categoria"] or "") != "clúster openshift"]
        for cod, defn in cods.items():
            if defn.get("fuente") == "red" and cod in sk and d.get("red"):
                fuentes.append((cod, _de_red(d)))
            elif defn.get("indicadores_ocp") and (e["categoria"] or "") == "clúster openshift" and d.get("ocp"):
                m_ = _de_ocp(d, cod)
                if m_.get("valores"):
                    fuentes.append((cod, m_))
        for cod, m in fuentes:
            base = f'faceops_cliente="{esc((e["codigo"] or "").lower())}",faceops_equipo="{esc(e["nombre"])}",faceops_plataforma="{esc(cod)}"'
            for k, v in sorted((m.get("valores") or {}).items()):
                if isinstance(v, (int, float)):
                    lin.append(f'faceops_plataforma_indicador{{{base},indicador="{esc(k)}"}} {float(v):g}')
            ed = _edad_h(m.get("medido"))
            if ed is not None:
                lin.append(f"faceops_plataforma_medicion_edad_segundos{{{base}}} {round(ed * 3600)}")
    return "\n".join(lin) + "\n"


def _registrar_metricas(st):
    """/metricas/plataformas: solo desde el propio anfitrión (Prometheus local contra la dirección en que escucha FaceOps, sin
    pasar por el proxy) y sin sesión."""
    def metricas_plataformas():
        if request.headers.get("X-Forwarded-For") or request.remote_addr not in ("127.0.0.1", "::1", request.environ.get("SERVER_NAME")):
            abort(404)
        return Response(_metricas(), mimetype="text/plain; version=0.0.4")
    st.app.add_url_rule("/metricas/plataformas", "metricas_plataformas", metricas_plataformas)


bp.record_once(_registrar_metricas)


# ───────────────────────────────────────────── medición programada (schedule de AAP cada 15 minutos)
PROGRAMAR = "Plataforma · Programar medición"
MEDIBLES = ("data-lake", "kafka", "keycloak", "jboss-eap", "redis", "postgresql", "activemq", "nginx", "vault", "satellite",
            "red-hat-idm", "389-directory-server", "iis", "active-directory")
FRECUENCIA_MIN = 15


def grupos_programables(cliente, aap):
    """[{inventario_id, inventario, credenciales, limite, objetivos}]: todas las plataformas medibles por SSH/WinRM del cliente,
    agrupadas por el inventario y la credencial de AAP con que se llega a sus máquinas (un schedule por grupo)."""
    from .operacion import acceso_equipo
    from .vistas import _plataformas_de, _riesgo_parque, familia, puerto_web
    grupos = {}
    for e, r in _riesgo_parque():
        if (e.get("categoria") or "") in ("clúster openshift", "router", "switch", "firewall", "balanceador") or not e.get("ip"):
            continue
        cods = [c for c in dict.fromkeys(_plataformas_de(e, r)) if c in MEDIBLES]
        if not cods:
            continue
        try:
            inv_id, inv_n, creds = acceso_equipo(aap, cliente, e)
        except Exception:  # noqa: BLE001  sin acceso en AAP: no se programa (su ficha lo muestra al medir)
            continue
        k = (inv_id, inv_n, tuple(creds))
        x = grupos.setdefault(k, {"inventario_id": inv_id, "inventario": inv_n, "credenciales": list(creds), "equipos": [], "objetivos": []})
        x["equipos"].append(e["nombre"])
        for c in cods:
            o = {"equipo": e["nombre"], "plataforma": c}
            if familia(c) == "keycloak":
                o["puerto"] = str(puerto_web(e, "keycloak", 8080))
            x["objetivos"].append(o)
    return [dict(v, limite=":".join(v.pop("equipos"))) for v in grupos.values()]


@bp.route("/plataformas/medicion/programar", methods=["POST"])
@requiere_login
def plataformas_programar():
    """Programa la medición sin agente de todas las plataformas cada 15 minutos: UN ticket con aprobación; aprobado, FaceOps deja
    en AAP un schedule de «Plataforma · Medir salud» por inventario y credencial (solo lectura)."""
    from .aap import AAP, ErrorAAP
    from .operacion import solicitar
    from .vistas import _escritura
    _escritura()
    a = _auto(PROGRAMAR)
    try:
        if not a:
            raise ValueError(f"«{PROGRAMAR}» aún no está publicada en AAP")
        gr = grupos_programables(g.cliente, AAP.de_cliente(g.cliente))
        if not gr:
            raise ValueError("No hay plataformas medibles con acceso en AAP")
        t = solicitar(g.usuario, g.cliente, a, "", {"pm_frecuencia": FRECUENCIA_MIN, "pm_grupos": gr},
                      f"Medir la salud de {sum(len(x['objetivos']) for x in gr)} plataformas cada {FRECUENCIA_MIN} minutos")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return _json_ticket(t, a)


def programar_al_terminar(aap, c, t):
    """Hook del ticket aprobado y exitoso de «Plataforma · Programar medición»: un schedule por grupo (sin duplicar)."""
    p = j(t["parametros"], {}) or {}
    med = q1("SELECT aap_id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", c["id"], MEDIR)
    if not med:
        return "\nFalta «Plataforma · Medir salud» en el catálogo"
    frec = int(p.get("pm_frecuencia") or FRECUENCIA_MIN)
    out = []
    for gr in p.get("pm_grupos") or []:
        nom = f"Medición de plataformas · {gr['inventario']}" + (f" · credencial {'-'.join(str(x) for x in gr['credenciales'])}" if gr.get("credenciales") else "")
        datos = {"name": nom, "rrule": f"DTSTART;TZID=America/Santiago:20261112T000000 RRULE:FREQ=MINUTELY;INTERVAL={frec}",
                 "inventory": gr["inventario_id"], "limit": gr["limite"], "extra_data": {"pm_objetivos": gr["objetivos"]},
                 "description": f"Programado desde {t['numero']} (solo lectura, sin agente)", "enabled": True}
        ya = aap.get(f"job_templates/{med['aap_id']}/schedules/", name=nom).get("results") or []
        if ya:
            s = aap.patch(f"schedules/{ya[0]['id']}/", datos)
        else:
            s = aap.post(f"job_templates/{med['aap_id']}/schedules/", datos)
        tiene = {x["id"] for x in (aap.get(f"schedules/{s['id']}/credentials/").get("results") or [])}
        for cr in gr.get("credenciales") or []:
            if cr not in tiene:
                aap.post(f"schedules/{s['id']}/credentials/", {"id": cr})
        out.append(f"{nom}: {len(gr['objetivos'])} plataformas cada {frec} min")
    return "\nProgramado en AAP: " + " · ".join(out)


_ult_recogida = {}


def recoger_programadas(c):
    """Lo que midieron los trabajos programados (schedules) queda en las fichas y en sus métricas. Cada 60 s como máximo."""
    from .aap import AAP
    from .bd import con
    if time.time() - _ult_recogida.get(c["id"], 0) < 60:
        return 0
    _ult_recogida[c["id"]] = time.time()
    med = q1("SELECT aap_id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", c["id"], MEDIR)
    if not med or not med["aap_id"]:
        return 0
    con().execute("CREATE TABLE IF NOT EXISTS plat_medicion_ult (cliente_id INTEGER PRIMARY KEY, job INTEGER)")
    ult = (q1("SELECT job FROM plat_medicion_ult WHERE cliente_id=?", c["id"]) or {}).get("job") or 0
    aap = AAP.de_cliente(c)
    jobs = aap.get("jobs/", job_template=med["aap_id"], launch_type="scheduled", id__gt=ult, order_by="id",
                   status__in="successful,failed", page_size=50).get("results") or []
    for jb in jobs:
        try:
            registrar_medicion(c["id"], aap.get(f"jobs/{jb['id']}/").get("artifacts") or {}, f"programada · trabajo {jb['id']}")
        except Exception:  # noqa: BLE001
            pass
        ult = jb["id"]
    if jobs:
        ej("INSERT INTO plat_medicion_ult(cliente_id, job) VALUES (?, ?) ON CONFLICT(cliente_id) DO UPDATE SET job=excluded.job", c["id"], ult)
    return len(jobs)
