"""Consola FaceOps: panel, buscador, automatizaciones, tickets, inventario, segmentos y mapa."""
import os
from urllib.parse import quote
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from . import requiere_login
from .aap import ErrorAAP
from .bd import ahora, auditar, ej, j, q, q1
from .buscador import buscar
from .inventario import cumplimiento, normal, politicas_de, recolectar, segmentos_de, sincronizar, skills_de, sistema_de
from .operacion import resolver_aprobacion, segmentos_aplicables, equipos_elegibles, sincronizar_catalogo, solicitar
from .permisos import equipo_visible, puede_aprobar, puede_ejecutar, puede_ver_segmento, segmentos_visibles

bp = Blueprint("consola", __name__)
SEV = {"alta": 3, "media": 2, "baja": 1}


# ------------------------------------------------------------------ tipos de periférico (agrupación del inventario)
PERIFERICOS = [("servidores", "Servidores", "servidor"), ("routers", "Routers", "router"), ("switches", "Switches", "switch"),
               ("firewalls", "Firewalls", "firewall"), ("balanceadores", "Balanceadores", "balanceador"),
               ("storage", "Storage", "almacenamiento"), ("clusters", "Clústeres de contenedores", "clúster openshift"),
               ("estaciones", "Estaciones de trabajo", "estación de trabajo"), ("otros", "Otros", "")]


CLASES = [("maquinas", "Máquinas"), ("redes", "Equipos de red"), ("almacenamiento", "Almacenamiento"), ("plataformas", "Plataformas")]


def clase_eq(e):
    """Máquinas (servidores, VM, estaciones) · equipos de red (routers, switches, firewalls, balanceadores) · almacenamiento."""
    gp = grupo_periferico(e)
    if gp == "clusters":
        return "plataformas"
    return "redes" if gp in ("routers", "switches", "firewalls", "balanceadores") else ("almacenamiento" if gp == "storage" else "maquinas")


def ambito_eq(e, amb_ubi):
    """Nube u on-premise: por el proveedor del equipo (gcp, aws, azure) o por la infraestructura donde vive."""
    if (e.get("proveedor") or "").lower() in ("gcp", "aws", "azure", "nube"):
        return "nube"
    return amb_ubi.get(e.get("ubicacion") or "", "nube" if "cloud" in (e.get("ubicacion") or "").lower() else "on-premise")


def grupo_periferico(e):
    c = (e.get("categoria") or "").lower()
    if c in ("", "servidor") and {"nginx", "haproxy", "f5-bigip"} & set(e.get("skills") or []):
        return "balanceadores"   # un servidor que cumple el rol de balanceador (p. ej. Nginx delante de los JBoss)
    if c.startswith("clúster") or c in ("kubernetes", "openshift"):
        return "clusters"   # OpenShift, Kubernetes y cualquier otro clúster de contenedores
    return next((k for k, _, cat in PERIFERICOS if cat and c == cat), "otros")


# ------------------------------------------------------------------ datos visibles para el usuario actual
def equipos_visibles():
    cid = g.cliente["id"]
    todos_segs = q("SELECT * FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", cid)
    out = []
    for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' ORDER BY nombre", cid):
        e["segmentos"] = segmentos_de(e, todos_segs)
        e["skills"] = skills_de(e)
        if equipo_visible(g.perfil, e, e["segmentos"]):
            out.append(e)
    return out


def autos_visibles():
    return q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1 ORDER BY categoria, nombre", g.cliente["id"])


CATEGORIAS_RED = ("router", "switch", "firewall", "balanceador")   # equipos de red: se vinculan con el usuario y la clave que se entregan


def tickets_visibles(limite=5000):
    """Los tickets que ve el usuario: por segmento de negocio y, si tiene área, solo los de su área."""
    from .aprobacion_areas import filtrar_tickets
    ts = q("SELECT t.*, a.nombre auto_nombre, a.categoria, u.nombre solicitante FROM tickets t "
           "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id LEFT JOIN usuarios u ON u.id=t.usuario_id "
           "WHERE t.cliente_id=? ORDER BY t.id DESC LIMIT ?", g.cliente["id"], limite)
    ts = [t for t in ts if not t["segmento"] or puede_ver_segmento(g.usuario, g.cliente["id"], t["segmento"])]
    return filtrar_tickets(ts, g.usuario, g.cliente["id"])


def hallazgos_visibles(equipos):
    ids = {e["id"]: e for e in equipos}
    hs = q("SELECT * FROM hallazgos WHERE cliente_id=?", g.cliente["id"])
    out = []
    for h in hs:
        if h["equipo_id"] in ids:
            h["equipo"] = ids[h["equipo_id"]]["nombre"]
            out.append(h)
    return sorted(out, key=lambda h: (-SEV.get(h["severidad"], 0), h["equipo"]))


def auto_sugerida(sugerencia, autos):
    if not sugerencia or sugerencia in ("recolectar", "apagar"):
        return None
    s = normal({"registrar": "registrar en red hat"}.get(sugerencia, sugerencia))
    return next((a for a in autos if s in normal(a["nombre_aap"]) or s in normal(a["nombre"])), None)


# ------------------------------------------------------------------ sesión
def _entrar(u, sig=""):
    from .doble_factor import limpiar
    session.clear()
    session["uid"] = u["id"]
    session.permanent = True
    ej("UPDATE usuarios SET ultimo_ingreso=? WHERE id=?", ahora(), u["id"])
    limpiar(u["usuario"])
    auditar(u["usuario"], None, "ingresar", "sesión", {"ip": request.headers.get("X-Real-IP") or request.remote_addr,
                                                        "acceso": "público" if request.headers.get("X-FaceOps-Acceso") == "publico" else "interno"})
    if sig.startswith("/") and not sig.startswith("//") and sig.rstrip("?") not in ("/", ""):
        return redirect(request.script_root + sig)
    return redirect(url_for("consola.inicio"))


@bp.route("/ingresar", methods=["GET", "POST"])
def ingresar():
    from .doble_factor import bloqueado, es_publico, fallo, ip_cliente
    if request.method == "POST":
        nombre = request.form.get("usuario", "").strip().lower()
        ip = ip_cliente(request)
        if bloqueado(ip, nombre):
            auditar(nombre[:60], None, "ingreso bloqueado", "sesión", {"ip": ip})
            flash("Demasiados intentos fallidos. Espera 15 minutos.", "error")
            return render_template("ingresar.html"), 429
        u = q1("SELECT * FROM usuarios WHERE usuario=? AND activo=1", nombre)
        if u and u.get("expira") and u["expira"] < ahora():
            auditar(nombre[:60], None, "ingreso rechazado", "sesión", {"ip": ip, "motivo": "usuario temporal vencido"})
            flash("Tu acceso temporal venció. Pide uno nuevo a un administrador.", "error")
            return render_template("ingresar.html"), 403
        if u and check_password_hash(u["clave_hash"], request.form.get("clave", "")):
            if u.get("totp_activo"):
                session.clear()
                session["pre_uid"], session["pre_t"] = u["id"], datetime.now(timezone.utc).timestamp()
                return redirect(url_for("consola.ingresar_codigo", siguiente=request.args.get("siguiente", "")))
            if es_publico(request):
                auditar(u["usuario"], None, "ingreso rechazado", "sesión", {"ip": ip, "motivo": "acceso público sin doble factor"})
                flash("Para entrar desde internet necesitas el doble factor. Actívalo una vez desde la red interna (tu usuario → Doble factor).", "error")
                return render_template("ingresar.html"), 403
            return _entrar(u, request.args.get("siguiente", ""))
        fallo(ip, nombre)
        auditar(nombre[:60], None, "ingreso fallido", "sesión", {"ip": ip})
        flash("Usuario o clave incorrectos", "error")
    return render_template("ingresar.html")


@bp.route("/ingresar/codigo", methods=["GET", "POST"])
def ingresar_codigo():
    from .doble_factor import bloqueado, fallo, ip_cliente, verificar
    uid, t0 = session.get("pre_uid"), session.get("pre_t", 0)
    if not uid or datetime.now(timezone.utc).timestamp() - t0 > 300:
        session.clear()
        flash("Vuelve a ingresar tu usuario y clave", "error")
        return redirect(url_for("consola.ingresar"))
    u = q1("SELECT * FROM usuarios WHERE id=? AND activo=1", uid)
    if request.method == "POST" and u:
        ip = ip_cliente(request)
        if bloqueado(ip, u["usuario"]):
            session.clear()
            flash("Demasiados intentos fallidos. Espera 15 minutos.", "error")
            return redirect(url_for("consola.ingresar"))
        if verificar(u["totp_secreto"], request.form.get("codigo", "")):
            return _entrar(u, request.args.get("siguiente", ""))
        fallo(ip, u["usuario"])
        auditar(u["usuario"], None, "código de doble factor incorrecto", "sesión", {"ip": ip})
        flash("Código incorrecto", "error")
    return render_template("ingresar_codigo.html")


@bp.route("/ingresar/activar/<tok>", methods=["GET", "POST"])
def activar(tok):
    """Primer ingreso desde internet con el enlace de activación: usuario + clave, luego escanea su QR y confirma un código."""
    import hashlib
    from .doble_factor import bloqueado, fallo, ip_cliente, nuevo_secreto, uri, verificar
    h = hashlib.sha256(tok.encode()).hexdigest()
    u = q1("SELECT * FROM usuarios WHERE invita_hash=? AND activo=1", h)
    if not u or (u.get("invita_expira") or "") < ahora() or (u.get("expira") and u["expira"] < ahora()):
        flash("El enlace de activación no es válido o venció. Pide uno nuevo a un administrador.", "error")
        return redirect(url_for("consola.ingresar"))
    ip = ip_cliente(request)
    enrolar = None
    if request.method == "POST":
        if bloqueado(ip, u["usuario"]):
            flash("Demasiados intentos fallidos. Espera 15 minutos.", "error")
            return render_template("activar.html"), 429
        if request.form.get("accion") == "credenciales":
            if request.form.get("usuario", "").strip().lower() == u["usuario"] and check_password_hash(u["clave_hash"], request.form.get("clave", "")):
                session["act_uid"], session["act_sec"] = u["id"], nuevo_secreto()
            else:
                fallo(ip, u["usuario"])
                flash("Usuario o clave incorrectos", "error")
        elif request.form.get("accion") == "codigo" and session.get("act_uid") == u["id"]:
            if verificar(session["act_sec"], request.form.get("codigo", "")):
                ej("UPDATE usuarios SET totp_secreto=?, totp_activo=1, invita_hash='', invita_expira='' WHERE id=?", session["act_sec"], u["id"])
                auditar(u["usuario"], None, "doble factor activado con enlace", "sesión", {"ip": ip})
                flash("Doble factor activado. Desde ahora FaceOps te pedirá el código de tu aplicación.", "ok")
                return _entrar(u)
            fallo(ip, u["usuario"])
            flash("El código no coincide. Revisa la hora del celular y prueba con el siguiente.", "error")
    if session.get("act_uid") == u["id"]:
        enrolar = {"secreto": session["act_sec"], "uri": uri(session["act_sec"], u["usuario"])}
    return render_template("activar.html", enrolar=enrolar, nombre=u["nombre"])


@bp.route("/cuenta/doble-factor", methods=["GET", "POST"])
def doble_factor():
    from .doble_factor import es_publico, nuevo_secreto, uri, verificar
    if not g.get("usuario"):
        return redirect(url_for("consola.ingresar"))
    u = q1("SELECT * FROM usuarios WHERE id=?", g.usuario["id"])
    if request.method == "POST":
        accion = request.form.get("accion")
        if accion == "activar" and session.get("totp_nuevo"):
            if verificar(session["totp_nuevo"], request.form.get("codigo", "")):
                ej("UPDATE usuarios SET totp_secreto=?, totp_activo=1 WHERE id=?", session.pop("totp_nuevo"), u["id"])
                auditar(u["usuario"], None, "doble factor activado", "sesión")
                flash("Doble factor activado: desde ahora FaceOps te pedirá el código de tu aplicación.", "ok")
                return redirect(url_for("consola.doble_factor"))
            flash("El código no coincide. Revisa la hora del celular y prueba con el código siguiente.", "error")
        elif accion == "desactivar" and u["totp_activo"]:
            if es_publico(request):
                flash("El doble factor solo se desactiva desde la red interna.", "error")
            elif verificar(u["totp_secreto"], request.form.get("codigo", "")):
                ej("UPDATE usuarios SET totp_secreto='', totp_activo=0 WHERE id=?", u["id"])
                auditar(u["usuario"], None, "doble factor desactivado", "sesión")
                flash("Doble factor desactivado.", "ok")
                return redirect(url_for("consola.doble_factor"))
            else:
                flash("Código incorrecto", "error")
    enrolar = None
    if not u["totp_activo"]:
        if es_publico(request):
            flash("El doble factor se activa desde la red interna.", "error")
        else:
            session.setdefault("totp_nuevo", nuevo_secreto())
            enrolar = {"secreto": session["totp_nuevo"], "uri": uri(session["totp_nuevo"], u["usuario"])}
    return render_template("doble_factor.html", activo=bool(u["totp_activo"]), enrolar=enrolar)


@bp.route("/salir")
def salir():
    session.clear()
    return redirect(url_for("consola.ingresar"))


@bp.route("/cliente/<int:cid>")
@requiere_login
def cambiar_cliente(cid):
    if any(c["id"] == cid for c in g.clientes):
        session["cliente_id"] = cid
    ir = request.args.get("ir", "")
    return redirect(ir if ir.startswith("/") and not ir.startswith("//") else (request.referrer or url_for("consola.inicio")))


# ------------------------------------------------------------------ panel
def valor_y_efectividad(ts, autos, dias=14):
    """Datos de los gráficos del panel: horas ahorradas por día, ahorro por servicio, manual vs automatizado y resultados."""
    from datetime import timedelta
    por_auto = {a["id"]: a for a in autos}
    hoy = datetime.now(timezone.utc).date()
    ok = [t for t in ts if t["estado"] == "exitoso"]
    serie = []
    for i in range(dias - 1, -1, -1):
        d = (hoy - timedelta(days=i)).isoformat()
        h = sum((t["valor_min"] or 0) for t in ok if (t["creado"] or "").startswith(d)) / 60
        serie.append({"dia": d[8:10] + "/" + d[5:7], "h": round(h, 1)})
    maxh = max([s["h"] for s in serie] + [0.1])
    servicios = defaultdict(lambda: {"h": 0.0, "n": 0})
    for t in ok:
        a = por_auto.get(t["automatizacion_id"]) or {}
        k = (a.get("demo") or a.get("categoria") or "Otras").split(" · ", 1)[-1]
        servicios[k]["h"] += (t["valor_min"] or 0) / 60
        servicios[k]["n"] += 1
    servicios = sorted(({"nombre": k, "h": round(v["h"], 1), "n": v["n"]} for k, v in servicios.items()), key=lambda x: -x["h"])
    efect = defaultdict(lambda: {"n": 0, "eje": 0, "apr": 0})
    for t in ts:
        if t["dur_ejecucion_s"] is None:
            continue
        e = efect[t["automatizacion_id"]]
        e["n"] += 1
        e["eje"] += t["dur_ejecucion_s"] or 0
        e["apr"] += t["dur_aprobacion_s"] or 0
    filas = []
    for aid, v in efect.items():
        a = por_auto.get(aid)
        if not a:
            continue
        filas.append({"nombre": a["nombre"], "n": v["n"], "manual": (a["minutos_manual"] or 0) * 60,
                      "eje": v["eje"] // v["n"], "apr": v["apr"] // v["n"]})
    filas.sort(key=lambda x: -x["manual"])
    escala = max([max(f["manual"], f["eje"] + f["apr"]) for f in filas] + [1])
    est = Counter(t["estado"] for t in ts if t["estado"] not in ("en_cola", "en_ejecucion", "pendiente_aprobacion"))
    total = sum(est.values()) or 1
    # productividad: del tiempo que tomaría a mano lo ejecutado con éxito, cuánto se eliminó (manual − ejecución en AAP)
    man_tot = eje_tot = 0
    for t in ok:
        a = por_auto.get(t["automatizacion_id"]) or {}
        m = (a.get("minutos_manual") or 0) * 60
        if m:
            man_tot += m
            eje_tot += min(t["dur_ejecucion_s"] or 0, m)
    prod = round(100 * (man_tot - eje_tot) / man_tot) if man_tot else 0
    horas_total = round(sum((t["valor_min"] or 0) for t in ok) / 60, 1)
    return {"productividad": prod, "ejec_total": len(ok), "horas_total": horas_total,
            "valor_total": round(horas_total * (g.cliente["costo_hora"] or 0)),
            "serie": serie, "maxh": maxh, "servicios": servicios[:8], "maxs": max([s["h"] for s in servicios] + [0.1]),
            "efect": filas[:8], "escala": escala, "resultados": est, "total": total,
            "exito": round(100 * est.get("exitoso", 0) / total)}


@bp.route("/")
@requiere_login
def inicio():
    eqs = equipos_visibles()
    hs = hallazgos_visibles(eqs)
    ts = tickets_visibles()
    autos = autos_visibles()
    mes = datetime.now(timezone.utc).strftime("%Y-%m")
    del_mes = [t for t in ts if (t["creado"] or "").startswith(mes)]
    ok_mes = [t for t in del_mes if t["estado"] == "exitoso"]
    pendientes = [t for t in ts if t["estado"] == "pendiente_aprobacion"] if puede_aprobar(g.usuario, g.cliente["id"]) else []
    sugerencias = []
    vistos = set()
    for h in hs:
        a = auto_sugerida(h["sugerencia"], autos)
        if a and (a["id"], h["titulo"]) not in vistos:
            vistos.add((a["id"], h["titulo"]))
            sugerencias.append({"hallazgo": h, "auto": a, "n": sum(1 for x in hs if x["titulo"] == h["titulo"])})
    segs = []
    for s in segmentos_visibles(g.usuario, g.cliente["id"]):
        es = [e for e in eqs if s["nombre"] in e["segmentos"]]
        segs.append({"s": s, "equipos": len(es), "hallazgos": sum(1 for h in hs if h["equipo_id"] in {e["id"] for e in es})})
    graficos = valor_y_efectividad(ts, autos)
    # Hallazgos como mapa: un recuadro por infraestructura; al hacer clic, el inventario con sus equipos con hallazgos
    peor = {}
    for h in hs:
        peor[h["equipo_id"]] = max(peor.get(h["equipo_id"], 0), SEV.get(h["severidad"], 0))
    mapa = []
    grupos = [(u, "", [e for e in eqs if (e["ubicacion"] or "Sin centro de datos") == u])
              for u in sorted({e["ubicacion"] or "Sin centro de datos" for e in eqs})]
    for n, d, es in grupos:
        if not es:
            continue
        con = [e for e in es if peor.get(e["id"])]
        mapa.append({"nombre": n, "desc": d, "n": len(es), "alta": sum(1 for e in es if peor.get(e["id"]) == 3),
                     "media": sum(1 for e in es if peor.get(e["id"]) == 2), "baja": sum(1 for e in es if peor.get(e["id"]) == 1),
                     "ok": len(es) - len(con), "q": (n if n != "Sin centro de datos" else "") + " con hallazgos",
                     "titulos": Counter(h["titulo"] for h in hs if h["equipo_id"] in {e["id"] for e in es}).most_common(3),
                     "skills": Counter(k for e in es for k in e["skills"]).most_common(6),
                     "ambientes": sorted({e["ambiente"] for e in es if e["ambiente"]}),
                     "segmentos": len({s for e in es for s in e["segmentos"]})})
    mapa.sort(key=lambda m: (-m["alta"], -m["media"], m["nombre"]))
    from .arquitectura import costo, vigentes
    visibles = {x["nombre"] for x in segmentos_visibles(g.usuario, g.cliente["id"])}
    costos_seg = sorted(({"nombre": n, **costo(r["m"], eqs)} for n, r in vigentes(g.cliente["id"]).items() if n in visibles),
                        key=lambda x: -x["mensual"])
    from .seguridad_panel import ESTADOS_POL, SEVS, completitud
    try:
        seg_ = completitud(g.cliente["id"], eqs)
    except Exception as e_:  # noqa: BLE001 - el panel no se cae si una fuente de seguridad falla
        current_app.logger.warning("completitud de seguridad: %s", e_)
        seg_ = None
    from .panel_extra import creditos as creditos_panel, productividad as productividad_area
    pa = productividad_area(g.cliente["id"], ts, eqs, autos)
    from .panel_extra import aprobaciones_pendientes
    try:
        apr = aprobaciones_pendientes(g.cliente["id"], ts)
    except Exception as e_:  # noqa: BLE001
        current_app.logger.warning("aprobaciones pendientes del panel: %s", e_)
        apr = None
    try:
        cr = creditos_panel(g.cliente, eqs)
    except Exception as e_:  # noqa: BLE001
        current_app.logger.warning("créditos del panel: %s", e_)
        cr = None
    return render_template("inicio.html", apr=apr, pa=pa, cr=cr, seg=seg_, estados_pol=ESTADOS_POL, sevs=SEVS, costos_seg=costos_seg, equipos=eqs, hallazgos=hs, tickets=ts[:8], pendientes=pendientes, g_=graficos, mapa=mapa,
                           conteo=dict(Counter(h["severidad"] for h in hs)), ejecuciones_mes=len(del_mes),
                           horas_mes=round(sum(t["valor_min"] or 0 for t in ok_mes) / 60, 1),
                           valor_mes=sum((t["valor_min"] or 0) / 60 * (g.cliente["costo_hora"] or 0) for t in ok_mes),
                           autos=autos, sugerencias=sugerencias[:6], segmentos=segs)


@bp.route("/buscar")
@requiere_login
def buscar_vista():
    # El buscador vive solo en Automatizaciones
    return redirect(url_for("consola.automatizaciones", q=request.args.get("q", "")))


# ------------------------------------------------------------------ automatizaciones
@bp.route("/automatizaciones")
@requiere_login
def automatizaciones():
    autos = autos_visibles()
    tipo = request.args.get("tipo")
    texto = request.args.get("q", "").strip()
    encontradas, filtros_q = None, {}
    if texto:
        filtros_q, res = buscar(texto, g.cliente["id"], autos, [], [], [])
        encontradas = res["automatizaciones"]
    if tipo in ("lectura", "escritura"):
        autos = [a for a in autos if a["tipo"] == tipo]
    usos = Counter(t["automatizacion_id"] for t in q("SELECT automatizacion_id FROM tickets WHERE cliente_id=?", g.cliente["id"]))
    from .areas import AREAS, area_de
    from .economia import estadisticas, metricas
    est_autos = estadisticas(g.cliente["id"])
    por_area = defaultdict(list)
    for a in sorted(autos, key=lambda x: (x.get("demo") or "~", x["orden"] if x.get("orden") is not None else 999, x["nombre"])):
        a["usos"] = usos.get(a["id"], 0)
        a["puede"], a["por_que"] = puede_ejecutar(g.usuario, g.cliente["id"], a)
        a["proposito"] = j(a.get("guia"), {}).get("proposito", "")
        a["area"] = area_de(a)
        a["met"] = metricas(a, est_autos)
        por_area[a["area"]].append(a)
    area = request.args.get("area", "")
    areas = [{"clave": k, "nombre": n, "desc": d_, "autos": por_area[k]} for k, n, d_ in AREAS if por_area.get(k) and (not area or k == area)]
    if encontradas is not None:
        por_id = {a["id"]: a for v in por_area.values() for a in v}
        encontradas = [por_id[a["id"]] for a in encontradas if a["id"] in por_id]
    return render_template("automatizaciones.html", areas=areas, area=area, tipo=tipo, total=len(autos), texto=texto,
                           encontradas=encontradas, fq=filtros_q, conteo={k: len(v) for k, v in por_area.items()}, AREAS=AREAS)


def __met(a):
    from .economia import estadisticas, metricas
    return metricas(a, estadisticas(g.cliente["id"]))


def _cves_equipo(e):
    from .cves import por_equipo
    return por_equipo(g.cliente["id"], [e]).get(e["id"])


def _politicas_equipo(e):
    from .politicas_seg import tablero
    _, por = tablero(g.cliente["id"], [e])
    return por.get(e["id"], [])


def _autos_compatibles(e):
    """Automatizaciones que se pueden ejecutar sobre este equipo, por área (compatibilidad: sistema, tipo, red, producto)."""
    from .areas import AREAS, area_de
    from .compatibilidad import compatible
    por = {}
    for a in autos_visibles():
        if (a["usa_limite"] or a.get("por_inventario")) and compatible(a, e)[0]:
            por.setdefault(area_de(a), []).append(a)
    return [(n, por[k]) for k, n, _ in AREAS if por.get(k)]


def _compatibles(a):
    """Máquinas del inventario (que el usuario ve) sobre las que actúa la automatización. Vacío si crea máquinas nuevas
    o si no opera sobre equipos del inventario (sin criterios de compatibilidad ni selección de equipos)."""
    from .compatibilidad import compatible, de
    c = de(a)
    if c.get("crea") or not (a.get("usa_limite") or any(c.get(k) for k in ("sistemas", "tipos", "red", "skills", "proveedor"))
                             or any(x.get("tipo") in ("equipo", "equipos") for x in j(a.get("campos"), []))):
        return []
    return [e for e in equipos_visibles() if e["estado"] != "retirado" and compatible(a, e)[0]]


def _prod_auto(a):
    """Productividad de la automatización en este cliente: factor (a mano ÷ espera de aprobación + ejecución), tiempos medios y áreas."""
    from .aprobacion_areas import area_de, grupos_de, nombre
    ts = q("SELECT estado, unidades, dur_ejecucion_s, dur_aprobacion_s FROM tickets WHERE cliente_id=? AND automatizacion_id=?", g.cliente["id"], a["id"])
    ok = [t for t in ts if t["estado"] == "exitoso"]
    man = sum((a["minutos_manual"] or 0) * 60 * (t["unidades"] or 1) for t in ok)
    proc = sum((t["dur_ejecucion_s"] or 0) + (t["dur_aprobacion_s"] or 0) for t in ok)
    aps = [t["dur_aprobacion_s"] for t in ts if t["dur_aprobacion_s"] is not None]
    med = sum(aps) / len(aps) if aps else None
    gs = grupos_de(a)
    cadena = " → ".join(" + ".join(nombre(x) for x in g_) for g_ in gs)
    return {"factor": round(man / proc, 1) if proc else None, "n_aprob": len(aps),
            "aprob_txt": "—" if med is None else (f"{med / 60:.0f} min" if med < 3600 else (f"{med / 3600:.1f} h" if med < 86400 else f"{med / 86400:.1f} días")),
            "exito": round(100 * len(ok) / len([t for t in ts if t["estado"] in ("exitoso", "fallido")])) if any(t["estado"] in ("exitoso", "fallido") for t in ts) else None,
            "area": nombre(area_de(a)), "cadena": cadena, "areas": list(dict.fromkeys([nombre(area_de(a))] + [nombre(x) for g_ in gs for x in g_]))}


def _refs_auto(a):
    """Enlaces a la automatización en AAP y a su repositorio Git (y commit) para la ficha."""
    tec = j(a.get("tecnico"), {})
    out = {"aap": None, "git": None, "commit": None, "playbook": None, "inventario": None,
           "readme": git_publico(f"http://192.168.122.1:3000/invoke/faceops-automatizaciones/src/branch/main/{a['readme_ruta']}") if a.get("readme_ruta") else None}
    try:
        from .aap import AAP
        pub = AAP.de_cliente(g.cliente).publica
        if a.get("aap_id") and a["aap_tipo"] in ("job", "workflow"):
            out["aap"] = f"{pub}/execution/templates/{'job-template' if a['aap_tipo'] == 'job' else 'workflow-job-template'}/{a['aap_id']}/details"
    except Exception:  # noqa: BLE001
        pass
    out["partes"] = _partes_auto(a, tec)
    repo = (tec.get("repositorio") or "").strip()
    if not repo.startswith("http"):   # workflow o compuesta: el repositorio de las plantillas que la forman
        repo = next((p_["repo"] for p_ in out["partes"] if p_.get("repo")), "")
    out["repo_txt"] = repo
    if repo.startswith("http"):
        base = git_publico(repo[:-4] if repo.endswith(".git") else repo)
        out["git"] = base
        if tec.get("commit"):
            out["commit"] = f"{base}/commit/{tec['commit']}"
        if tec.get("playbook"):   # el playbook mismo, en la versión que usa AAP
            rama = tec.get("rama") if tec.get("rama") and tec.get("rama") != "por defecto" else "main"
            out["playbook"] = f"{base}/src/{'commit/' + tec['commit'] if tec.get('commit') else 'branch/' + rama}/{tec['playbook']}"
    inv = tec.get("inventario") or a.get("inventario")
    if inv and out["aap"]:
        try:
            from .aap import AAP
            cli = AAP.de_cliente(g.cliente)
            r = cli.get("inventories/", name=inv).get("results") or []
            if r:
                out["inventario"] = f"{cli.publica}/execution/infrastructure/inventories/inventory/{r[0]['id']}/hosts"
        except Exception:  # noqa: BLE001
            pass
    return out


def _partes_auto(a, tec, prof=0):
    """Plantillas de AAP que forman un workflow o una automatización compuesta de FaceOps, con su playbook y repositorio en Git."""
    nombres = []
    if tec.get("compuesta"):
        nombres = [(v.get("automatizacion"), None, k) for k, v in tec["compuesta"].items() if v.get("automatizacion")]
    elif a.get("aap_tipo") == "workflow":
        nombres = [(n["nombre"], n.get("ujt_id"), None) for n in tec.get("nodos") or [] if n.get("tipo") == "plantilla"]
    out, vistos = [], set()
    try:
        from .aap import AAP
        pub = AAP.de_cliente(g.cliente).publica
    except Exception:  # noqa: BLE001
        pub = ""
    for nombre, ujt, accion in nombres:
        if nombre in vistos:
            continue
        vistos.add(nombre)
        b = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre)
        bt = j(b.get("tecnico"), {}) if b else {}
        if b and (bt.get("compuesta") or b["aap_tipo"] == "workflow") and prof < 2:
            out.append({"nombre": nombre, "url": f"{pub}/execution/templates/workflow-job-template/{b['aap_id']}/details" if pub and b["aap_id"] else None,
                        "tipo": "workflow", "auto_id": b["id"], "accion": accion, "nivel": prof})
            out += _partes_auto(b, bt, prof + 1)
            continue
        pid = (b or {}).get("aap_id") or ujt
        repo = (bt.get("repositorio") or "").strip()
        url_pb = None
        if repo.startswith("http") and bt.get("playbook"):
            base = git_publico(repo[:-4] if repo.endswith(".git") else repo)
            url_pb = f"{base}/src/{'commit/' + bt['commit'] if bt.get('commit') else 'branch/main'}/{bt['playbook']}"
        out.append({"nombre": nombre, "tipo": "plantilla", "auto_id": b["id"] if b else None, "repo": repo, "accion": accion, "nivel": prof,
                    "url": f"{pub}/execution/templates/job-template/{pid}/details" if pub and pid else None,
                    "playbook": bt.get("playbook"), "url_playbook": url_pb})
    return out


def git_publico(url):
    """URL de Gitea que abre el navegador del usuario (la interna de la VM se publica por el HTTPS de FaceOps)."""
    pub = os.environ.get("FACEOPS_GIT_PUBLICA", "").rstrip("/")
    if not pub:
        return url
    return re.sub(r"^https?://[^/]+(:\d+)?(/git(?=/))?", pub, url)


@bp.route("/automatizaciones/<int:aid>", methods=["GET", "POST"])
@requiere_login
def automatizacion(aid):
    a = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", aid, g.cliente["id"])
    if not a:
        abort(404)
    if a["nombre_aap"] == "FaceOps · Nuevo segmento de negocio":  # se pide adjuntando la arquitectura
        return redirect(url_for("consola.arquitectura_nueva"))
    puede, por_que = puede_ejecutar(g.usuario, g.cliente["id"], a)
    segs = segmentos_aplicables(g.usuario, g.cliente["id"], a) if a["usa_limite"] else []
    campos = j(a["campos"], [])
    if request.method == "GET":  # prellenado desde otra pantalla (p. ej. el mapa): ?v_<variable>=valor
        for c in campos:
            if request.args.get("v_" + c["var"]):
                c["defecto"] = request.args.get("v_" + c["var"])
    elegibles = equipos_elegibles(g.usuario, g.cliente["id"], a) if any(c["tipo"] in ("equipo", "equipos") for c in campos) else []
    jboss_eqs = []
    if any(c["tipo"] in ("jboss_origen", "jboss_destino") for c in campos):   # servidores con JBoss (inventario recolectado)
        from .identidad import codigo as _cod
        from .salud import encendido as _enc
        for e_ in equipos_visibles():
            jb_ = j(e_["datos"], {}).get("jboss") or []
            if jb_ and _enc(e_) == "encendido":   # solo servidores encendidos: la evaluación y la migración entran a ellos
                jboss_eqs.append({"nombre": e_["nombre"], "cod": _cod(e_), "ip": e_.get("ip") or "", "ambiente": e_.get("ambiente") or "",
                                  "origen": [x for x in jb_ if x.get("familia") != "eap8"], "eap8": [x for x in jb_ if x.get("familia") == "eap8"]})
    if request.method == "POST":
        params, secretos = {}, {}
        validos = {e["nombre"] for e in elegibles}
        for c in campos:
            v = request.form.get("v_" + c["var"], "").strip()
            if c["tipo"] in ("multiselect", "equipos", "jboss_apps"):
                v = [x for x in request.form.getlist("v_" + c["var"]) if x]
            if c["tipo"] == "jboss_origen" and v and v not in {x["nombre"] for x in jboss_eqs if x["origen"]}:
                flash(f"«{c['etiqueta']}»: elige un servidor con JBoss de tu inventario", "error")
                return redirect(request.url)
            if c["tipo"] == "jboss_destino" and v and v not in {x["nombre"] for x in jboss_eqs if x["eap8"]}:
                flash(f"«{c['etiqueta']}»: el destino debe tener JBoss EAP 8", "error")
                return redirect(request.url)
            if c["tipo"] == "jboss_destino" and not v and request.form.get("v_mj_modo") == "migrar":
                flash("Para migrar elige el servidor de destino (JBoss EAP 8)", "error")
                return redirect(request.url)
            if c["tipo"] in ("equipo", "equipos") and any(x not in validos for x in ([v] if isinstance(v, str) and v else v or [])):
                flash(f"«{c['etiqueta']}»: elige equipos de tus segmentos", "error")
                return redirect(request.url)
            if c["tipo"] == "segmento" and v:
                s_ok = {s["nombre"]: s for s in segmentos_visibles(g.usuario, g.cliente["id"])}
                fam = "windows" if str(request.form.get("v_hab_so", "")).startswith("windows") else "linux"
                if v not in s_ok or (s_ok[v].get("sistema") and s_ok[v]["sistema"] != fam):
                    flash(f"«{c['etiqueta']}»: elige un segmento tuyo y compatible con el sistema", "error")
                    return redirect(request.url)
            if c["tipo"] == "ip" and v and not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?", v):
                flash(f"«{c['etiqueta']}» debe ser una IP (ej. 10.10.10.10)", "error")
                return redirect(request.url)
            if c["tipo"] == "integer" and v != "":
                v = int(v)
            if c["tipo"] == "infraestructura" and v and not q1("SELECT 1 FROM infraestructuras WHERE cliente_id=? AND nombre=?", g.cliente["id"], v):
                flash(f"«{c['etiqueta']}»: elige una infraestructura incorporada", "error")
                return redirect(request.url)
            if c["tipo"] == "manifiesto" and v:
                import yaml as _yaml
                try:
                    list(_yaml.safe_load_all(v))
                except _yaml.YAMLError as e:
                    flash(f"«{c['etiqueta']}»: el YAML no es válido ({str(e).splitlines()[0][:160]})", "error")
                    return redirect(request.url)
                if len(v) > 200_000 or re.search(r'"auths"|pullSecret\s*:\s*\S|BEGIN [A-Z ]*PRIVATE KEY|client_secret|clientSecret', v):
                    flash(f"«{c['etiqueta']}»: no incluya el pull secret, llaves ni claves (vienen de credenciales cifradas de AAP) y que pese menos de 200 KB", "error")
                    return redirect(request.url)
            if v not in ("", []) and c["tipo"] == "password":
                secretos[c["var"]] = v
                params[c["var"]] = "••••••"
            elif v not in ("", []):
                params[c["var"]] = v
            elif c.get("requerido"):
                flash(f"Falta «{c['etiqueta']}»", "error")
                return redirect(request.url)
        if params.get("hab_infraestructura"):   # crear máquinas: la infraestructura elegida define el destino; nace sin segmento
            inf_ = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre=?", g.cliente["id"], params["hab_infraestructura"])
            if not inf_ or inf_["tipo"] not in ("kvm", "gcp", "vmware"):
                flash("En esa infraestructura FaceOps aún no crea máquinas (hoy: KVM, VMware vSphere y Cloud GCP)", "error")
                return redirect(request.url)
            params["hab_destino"] = {"gcp": "gcp", "vmware": "vmware"}.get(inf_["tipo"], "onpremise")
            params["hab_ubicacion"] = inf_["ubicacion"]
            if inf_["tipo"] == "vmware":   # el puerto del vCenter vive en la infraestructura (la credencial de AAP solo trae la dirección)
                from .infraestructuras import _vc
                params["hab_vc_puerto"] = str(_vc(j(inf_.get("detalle"), {}).get("host", ""))[1])
            params.pop("hab_segmento", None)
        try:
            t = solicitar(g.usuario, g.cliente, a, request.form.get("segmento"), params, request.form.get("motivo", "").strip(), secretos,
                          {"ambientes": [x for x in request.form.getlist("f_ambiente") if x][:1], "infraestructura": request.form.getlist("f_infra")})
            from .operacion import requiere_aprobacion
            amb = (j(t["filtros"], {}).get("ambientes") or [None])[0]
            flash(f"{t['numero']} creado y enviado a AAP" + (" (esperará la aprobación en AAP)" if requiere_aprobacion(a, amb) else ""), "ok")
            return redirect(url_for("consola.ticket", tid=t["id"]))
        except PermissionError as e:
            flash(str(e), "error")
        except ErrorAAP as e:
            flash(f"AAP rechazó la ejecución: {e}", "error")
    from .operacion import conf_servicenow
    sn = conf_servicenow(a["nombre_aap"]) if g.cliente.get("tickets_tipo") == "servicenow" else None
    from .economia import beneficio
    from .flujo import plan as plan_flujo, uso_mensual
    from .objetos_aap import de as objetos_aap
    return render_template("automatizacion.html", jboss_eqs=jboss_eqs, objetos=objetos_aap(g.cliente, a), flujo_a=plan_flujo(a, g.cliente["id"]), prod=_prod_auto(a), uso=uso_mensual(g.cliente["id"], aid), refs=_refs_auto(a),
                           compat_eqs=_compatibles(a), met=__met(a), ben=beneficio(a), a=a, campos=campos, puede=puede, por_que=por_que, elegibles=elegibles,
                           segs_negocio=segmentos_visibles(g.usuario, g.cliente["id"]),
                           infras_cli=q("SELECT nombre, tipo, region, ubicacion FROM infraestructuras WHERE cliente_id=? ORDER BY id", g.cliente["id"]) if any(c["tipo"] == "infraestructura" for c in campos) else [],
                           guia=j(a.get("guia"), {}), tec=j(a.get("tecnico"), {}), sn=sn,
                           segmentos=segs)


# ------------------------------------------------------------------ tickets
@bp.route("/tickets")
@requiere_login
def tickets():
    ts = tickets_visibles(500)
    est = request.args.get("estado")
    if est:
        ts = [t for t in ts if t["estado"] == est]
    ids = {int(x) for x in request.args.get("ids", "").split(",") if x.strip().isdigit()}
    if ids:   # desde el control de deuda: los tickets de una actividad
        ts = [t for t in ts if t["id"] in ids]
    texto = request.args.get("q", "").strip()
    ent = {}
    if texto:
        from .buscador import buscar_tickets
        ent, ts = buscar_tickets(texto, ts)
    if request.headers.get("X-Requested-With") == "fetch":
        return render_template("tickets.html", tickets=ts, estado=est, texto=texto, ent=ent, solo_tabla=True)
    return render_template("tickets.html", tickets=ts, estado=est, texto=texto, ent=ent)


def _maquinas_ticket(t):
    """Máquinas del inventario que toca el ticket (para enlazarlas): las del límite y las que nombran sus parámetros
    o su resultado (p. ej. las que creó «Habilitar servidor»)."""
    eqs = q("SELECT id, nombre, ip, datos, estado FROM equipos WHERE cliente_id=?", g.cliente["id"])
    lim = [x for x in (t.get("limite") or "").split(":") if x and x != "*"]
    texto = " ".join(str(t.get(k) or "") for k in ("parametros", "resultado", "motivo"))
    out = []
    for e in eqs:
        if e["nombre"] in lim or (len(e["nombre"]) > 3 and re.search(r"(?<![\w-])" + re.escape(e["nombre"]) + r"(?![\w-])", texto)):
            out.append(e)
    orden = {n: i for i, n in enumerate(lim)}
    return sorted(out, key=lambda e: (orden.get(e["nombre"], 999), e["nombre"]))


def _flujo_ticket(t, a, detalle=False):
    if not a:
        return None
    from .flujo import en_vivo, plan
    cli, avance, vig = None, None, None
    if t.get("aap_job_id") and (t["estado"] in ("en_ejecucion", "pendiente_aprobacion", "en_cola") or (detalle and t["estado"] in ("fallido", "exitoso", "cancelado"))):
        try:
            from .aap import AAP
            from .avance import repartir, vigilancia
            cli = AAP.de_cliente(g.cliente)
            if t["estado"] in ("en_ejecucion", "fallido", "exitoso", "cancelado"):
                avance = repartir(cli, t, a, plan(a))
            if t["estado"] == "en_ejecucion":
                vig = vigilancia(cli, t)
        except Exception:  # noqa: BLE001
            cli = cli or None
    return en_vivo(t, a, cli, avance, vig)


@bp.route("/atajos", methods=["POST"])
@requiere_login
def atajos_guardar():
    """Guarda los atajos de teclado del usuario (o los restablece)."""
    if request.form.get("_csrf") != session.get("csrf"):
        abort(400)
    from .atajos import validar
    if request.form.get("restablecer"):
        ej("UPDATE usuarios SET atajos='{}' WHERE id=?", g.usuario["id"])
        return {"ok": True}
    try:
        d = validar(json.loads(request.form.get("atajos") or "{}"))
    except (ValueError, TypeError) as x:
        return {"error": str(x)}, 400
    ej("UPDATE usuarios SET atajos=? WHERE id=?", json.dumps(d), g.usuario["id"])
    return {"ok": True}


@bp.route("/tickets/<int:tid>/flujo")
@requiere_login
def ticket_flujo(tid):
    """Flujo del ticket en vivo (lo pide la página del ticket cada pocos segundos)."""
    t = q1("SELECT * FROM tickets WHERE id=? AND cliente_id=?", tid, g.cliente["id"])
    from .aprobacion_areas import filtrar_tickets
    if not t or not filtrar_tickets([t], g.usuario, g.cliente["id"]):
        abort(404)
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    mac = current_app.jinja_env.get_template("_macros.html").module
    from .aprobacion_areas import puede_aprobar as puede_aprobar_etapa
    aprobable = bool(a) and t["estado"] == "pendiente_aprobacion" and bool(puede_aprobar_etapa(g.usuario, g.cliente["id"], t, a))
    return {"estado": t["estado"], "aprobable": aprobable, "html": str(mac.flujo(_flujo_ticket(t, a) or [], True))}


@bp.route("/tickets/<int:tid>/caja/<pos>")
@requiere_login
def ticket_caja(tid, pos):
    """Log de una caja del flujo del ticket (panel flotante a la derecha; se refresca mientras el ticket avanza)."""
    t = q1("SELECT t.*, u.nombre solicitante FROM tickets t LEFT JOIN usuarios u ON u.id=t.usuario_id WHERE t.id=? AND t.cliente_id=?", tid, g.cliente["id"])
    from .aprobacion_areas import filtrar_tickets
    if not t or not filtrar_tickets([t], g.usuario, g.cliente["id"]):
        abort(404)
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    cols = _flujo_ticket(t, a, detalle=True) or []
    n = next((x for c_ in cols for x in c_ if x.get("pos") == pos), None)
    if not n:
        abort(404)
    pasos, info, vig, web_aap, jobs = [], [], {}, "", []
    from .aap import AAP
    try:
        cli = AAP.de_cliente(g.cliente)
        web_aap = cli.publica
    except Exception:  # noqa: BLE001
        cli = None
    if n["tipo"] == "inicio":
        info = [("Ticket", t["numero"]), ("Creado", (t["creado"] or "")[:19].replace("T", " ")), ("Solicitante", t.get("solicitante") or "—"),
                ("Motivo", t["motivo"] or "—"), ("Equipos", (t["limite"] or "los que define la automatización").replace(":", ", "))]
    elif n["tipo"] == "aprobacion":
        from .aprobacion_areas import dias, etapas
        f = next((x for x in etapas(t, a) if x["area"] == n.get("area")), None)
        if f:
            info = [("Área", n["titulo"].replace("Aprueba ", "")), ("Estado", f["estado"]), ("Quién", f.get("quien") or "aún nadie"),
                    ("Desde", (f.get("inicio") or "")[:19].replace("T", " ")), ("Espera", f"{dias(f) * 24:.1f} h")]
        info.append(("Quiénes pueden aprobar", n.get("roles") or "—"))
        if t["estado"] == "pendiente_aprobacion":
            info.append(("Nota", "El ticket espera solo esta aprobación: es lo único que puede detenerlo. Las aprobaciones no vencen."))
    elif n["tipo"] == "tarea" and cli and t.get("aap_job_id"):
        from .avance import repartir, vigilancia
        from .flujo import plan
        av = repartir(cli, t, a, plan(a))
        if av.get("por_nodo"):
            x = av["por_nodo"].get(n.get("nodo_aap") or "", {})
            pasos, jobs = x.get("pasos", []), [x["job"]] if x.get("job") else []
        elif av.get("tramos"):
            tr = av["tramos"][min(n.get("_i", 0), len(av["tramos"]) - 1)]
            pasos, jobs = tr["pasos"], av.get("jobs", [])
        if t["estado"] == "en_ejecucion" and n.get("estado") == "curso":
            vig = vigilancia(cli, t)
    elif n["tipo"] == "fin":
        info = [("Estado", t["estado"].replace("_", " ")), ("Terminó", (t["fin"] or "—")[:19].replace("T", " ")), ("Resultado", t["resultado"] or "—")]
    resp = make_response(render_template("_caja.html", n=n, t=t, pasos=pasos, info=info, vig=vig, web_aap=web_aap, jobs=jobs))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/tickets/<int:tid>", methods=["GET", "POST"])
@requiere_login
def ticket(tid):
    t = q1("SELECT t.*, u.nombre solicitante, u.usuario solicitante_usuario, ap.nombre aprobador FROM tickets t "
           "LEFT JOIN usuarios u ON u.id=t.usuario_id LEFT JOIN usuarios ap ON ap.id=t.aprobado_por "
           "WHERE t.id=? AND t.cliente_id=?", tid, g.cliente["id"])
    if not t or (t["segmento"] and not puede_ver_segmento(g.usuario, g.cliente["id"], t["segmento"])):
        abort(404)
    from .aprobacion_areas import filtrar_tickets
    if not filtrar_tickets([t], g.usuario, g.cliente["id"]):   # de otra área
        abort(404)
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    from .solicitud_segmento import SEGMENTO_NUEVO
    es_segmento = bool(a) and a["nombre_aap"] == SEGMENTO_NUEVO
    if request.method == "POST":
        if request.form.get("_csrf") != session.get("csrf"):
            abort(400)
        aprobar_ = request.form.get("accion") == "aprobar"
        if es_segmento:  # doble aprobación: cada etapa la responde su rol; Infraestructura deja su decisión de recursos
            from .aap import AAP
            from .permisos import tiene_rol
            from .solicitud_segmento import de_ticket, decision_de_formulario, etapa
            et = etapa(AAP.de_cliente(g.cliente), t)
            if not et or not tiene_rol(g.usuario, g.cliente["id"], et):
                abort(403)
            if not aprobar_ and not request.form.get("comentario", "").strip():
                flash("Al rechazar, deja el motivo en el comentario", "error")
                return redirect(url_for("consola.ticket", tid=tid))
            if et == "infraestructura" and aprobar_:
                r = de_ticket(t)
                d = decision_de_formulario(r["m"], request.form)
                ej("UPDATE arquitecturas SET decision=? WHERE id=?", json.dumps(d, ensure_ascii=False), r["id"])
                auditar(g.usuario["usuario"], g.cliente["id"], "decisión de infraestructura", t["numero"], d)
        else:
            from .aprobacion_areas import puede_aprobar as puede_aprobar_etapa
            if not puede_aprobar_etapa(g.usuario, g.cliente["id"], t, a):
                abort(403)
            if not aprobar_ and not request.form.get("comentario", "").strip():
                flash("Al rechazar, deja el motivo en el comentario", "error")
                return redirect(url_for("consola.ticket", tid=tid))
        try:
            resolver_aprobacion(g.usuario, tid, aprobar_, request.form.get("comentario", ""))
            flash("Respuesta registrada", "ok")
        except ErrorAAP as e:
            flash(f"AAP no aceptó la respuesta: {e}", "error")
        return redirect(url_for("consola.ticket", tid=tid))
    url_aap = url_aprob = None
    bitacora, web_aap = [], ""
    if t["aap_job_id"]:
        from .aap import AAP
        from .bitacora import construir
        try:
            cli = AAP.de_cliente(g.cliente)
            url_aap = cli.url_trabajo(t["aap_job_tipo"], t["aap_job_id"])
            url_aprob = cli.url_trabajo(t["aap_job_tipo"], t["aap_job_id"])
            web_aap = cli.publica
            bitacora = construir(cli, t)
        except ErrorAAP:
            pass
    from .operacion import conector
    url_sn = conector(g.cliente).enlace(t["externo_ref"])
    from .operacion import Odoo as _Odoo
    url_odoo = _Odoo(g.cliente).enlace(t.get("odoo_ref")) if (j(g.cliente.get("tickets_conf"), {}).get("odoo") or {}).get("url") else None
    traza = q("SELECT * FROM auditoria WHERE cliente_id=? AND objeto=? ORDER BY id", g.cliente["id"], t["numero"])
    sol = None
    from .aprobacion_areas import actual as etapa_actual, dias as dias_etapa, nombre as nombre_area, puede_aprobar as puede_aprobar_etapa, etapas as etapas_ticket
    aprobar_ok = t["estado"] == "pendiente_aprobacion" and puede_aprobar_etapa(g.usuario, g.cliente["id"], t, a)
    et_act = etapa_actual(t, a) if t["estado"] == "pendiente_aprobacion" else None
    et_todas = [dict(x, area_n=nombre_area(x["area"]), dias=int(dias_etapa(x))) for x in etapas_ticket(t, a)]
    if es_segmento:
        from .aap import AAP
        from .arquitectura import costo, plan
        from .permisos import tiene_rol
        from .solicitud_segmento import ETAPAS, aplicar_decision, candidatos, de_ticket, etapa
        r = de_ticket(t)
        if r:
            eqs = equipos_visibles()
            et = etapa(AAP.de_cliente(g.cliente), t) if t["estado"] == "pendiente_aprobacion" else None
            todos_segs = q("SELECT * FROM segmentos WHERE cliente_id=?", g.cliente["id"])
            final = r["m"] if r["estado"] == "aprobada" else aplicar_decision(r["m"], r["decision_d"], eqs, r["segmento"])
            pasadas = [x for x in traza if x["accion"] in ("aprobado en AAP", "rechazado en AAP", "aprobar en AAP", "rechazar en AAP")]
            sol = {"r": r, "m": r["m"], "k": costo(final, eqs), "etapa": et, "ETAPAS": ETAPAS,
                   "puede": bool(et) and tiene_rol(g.usuario, g.cliente["id"], et),
                   "cand": candidatos(r["m"], eqs, lambda e: segmentos_de(e, todos_segs)) if et == "infraestructura" else {},
                   "plan": plan(g.cliente, final, eqs), "aprobaciones": pasadas}
        aprobar_ok = False  # la respuesta va en el panel de la solicitud, por etapa y rol
    from .confianza import URL as URL_VALIDADOR, de_ticket as validacion_de
    return render_template("ticket.html", t=t, a=a, url_aap=url_aap, url_aprob=url_aprob, url_sn=url_sn, url_odoo=url_odoo, traza=traza,
                           maquinas_t=_maquinas_ticket(t), bitacora=bitacora, web_aap=web_aap, sol=sol, flujo_t=_flujo_ticket(t, a), aprobar=aprobar_ok, et_act=dict(et_act, area_n=nombre_area(et_act["area"])) if et_act else None, et_todas=et_todas, validacion=validacion_de(t["id"]),
                           url_validador=URL_VALIDADOR)


# ------------------------------------------------------------------ inventario
@bp.route("/inventario", methods=["GET", "POST"])
@requiere_login
def inventario():
    if request.method == "POST":
        if g.perfil["modo"] != "escritura":
            abort(403)
        try:
            if request.form.get("accion") == "recolectar":
                n = recolectar(g.cliente, g.usuario["usuario"])
                flash(f"Recolector lanzado en AAP ({len(n)} trabajos). En unos minutos usa «Sincronizar».", "ok")
            else:
                n = sincronizar(g.cliente, g.usuario["usuario"])
                m = sincronizar_catalogo(g.cliente, g.usuario["usuario"])
                flash(f"Inventario sincronizado desde AAP: {n} equipos · catálogo: {m} automatizaciones", "ok")
        except ErrorAAP as e:
            flash(str(e), "error")
        return redirect(url_for("consola.inventario"))
    eqs = equipos_visibles()
    hs = hallazgos_visibles(eqs)
    por_eq = defaultdict(list)
    for h in hs:
        por_eq[h["equipo_id"]].append(h)
    texto = request.args.get("q", "").strip()
    entendido = {}
    if texto:
        from .buscador import buscar_equipos
        entendido, eqs = buscar_equipos(texto, g.cliente["id"], eqs, por_eq)
    filtros = {k: request.args.get(k, "") for k in ("infra", "ambiente", "segmento", "skill", "estado", "tipo")}
    fi_ = request.args.get("infra", "")
    from .infraestructuras import ambito as ambito_infra
    amb_ubi = {x["ubicacion"]: ambito_infra(x["tipo"]) for x in q("SELECT ubicacion, tipo FROM infraestructuras WHERE cliente_id=?", g.cliente["id"])}
    f_amb, f_clase = request.args.get("ambito", ""), request.args.get("clase", "")
    ocp_infra = {x["ubicacion"]: (x["detalle"] or "") for x in q("SELECT ubicacion, detalle FROM infraestructuras WHERE cliente_id=? AND tipo='openshift'", g.cliente["id"])}
    n_clusters = sum(1 for e in equipos_visibles() if grupo_periferico(e) == "clusters")

    def en_infra(e, u):   # un clúster pertenece a su infraestructura OpenShift aunque viva en un centro de datos
        return e["ubicacion"] == u or (u in ocp_infra and grupo_periferico(e) == "clusters" and (n_clusters == 1 or (e.get("ip") and e["ip"] in ocp_infra[u])))
    tipos_todos = Counter(grupo_periferico(e) for e in equipos_visibles() if (not fi_ or en_infra(e, fi_))
                          and (not f_amb or ambito_eq(e, amb_ubi) == f_amb) and (not f_clase or clase_eq(e) == f_clase))
    eqs = [e for e in eqs if (not f_amb or ambito_eq(e, amb_ubi) == f_amb) and (not f_clase or clase_eq(e) == f_clase)]
    if filtros["tipo"]:
        eqs = [e for e in eqs if grupo_periferico(e) == filtros["tipo"]]
    if filtros["infra"]:
        eqs = [e for e in eqs if en_infra(e, filtros["infra"])]
    if filtros["segmento"]:
        eqs = [e for e in eqs if filtros["segmento"] in e["segmentos"]]
    f_error = request.args.get("error", "")
    if f_error.isdigit():   # máquinas a las que aplica un error conocido (desde Incidentes › Aplicabilidad)
        ids_ = set(_aplicabilidad_errores().get(int(f_error), {}).get("equipos", []))
        eqs = [e for e in eqs if e["id"] in ids_]
    sin_segmento = request.args.get("sin_segmento") == "1"
    if sin_segmento:   # máquinas huérfanas: no están en ningún segmento de negocio (se asignan en el mapa)
        eqs = [e for e in eqs if not e["segmentos"]]
    if filtros["skill"]:
        eqs = [e for e in eqs if filtros["skill"] in e["skills"]]
    if filtros["ambiente"]:
        eqs = [e for e in eqs if e["ambiente"] == filtros["ambiente"]]
    if filtros["estado"] == "con-hallazgos":
        eqs = [e for e in eqs if por_eq.get(e["id"])]
    todos = equipos_visibles()
    try:   # paginado: solo se calcula la salud y el acceso de la página visible
        por_pag = max(5, min(200, int(request.args.get("n", 10))))
    except ValueError:
        por_pag = 10
    n_filtrados = len(eqs)
    paginas = max(1, -(-n_filtrados // por_pag))
    try:
        pag = max(1, min(paginas, int(request.args.get("p", 1))))
    except ValueError:
        pag = 1
    eqs = eqs[(pag - 1) * por_pag: pag * por_pag]
    from .consola import disponible, tipo_consola
    from .inventario import sistema_eq
    for e in eqs:
        e["consola"] = tipo_consola(e, sistema_eq(e)) if disponible() and g.perfil["modo"] == "escritura" else None
        if (e.get("categoria") or "") == "clúster openshift":   # acceso: la consola web de OpenShift (la publicada, si la infraestructura la declara)
            inf_ = q1("SELECT detalle FROM infraestructuras WHERE cliente_id=? AND tipo='openshift' AND ubicacion=?", g.cliente["id"], e.get("ubicacion") or "")
            e["ocp_web"] = j((inf_ or {}).get("detalle"), {}).get("consola") or (j(e.get("datos"), {}).get("ocp") or {}).get("consola") or ""
    from .infraestructuras import lista as lista_infra
    infras = lista_infra(g.cliente["id"], todos)
    from .boveda import disponible as bov_ok, maquinas_con_credencial
    bov_conectada = bov_ok()
    vinc = maquinas_con_credencial([e["nombre"] for e in eqs]) if bov_conectada else set()
    from .inventariado import antiguedad as antig_inv
    for e in eqs:
        e["inv"] = antig_inv(e)
    for e in eqs:
        e["ambito"], e["clase"] = ambito_eq(e, amb_ubi), clase_eq(e)
        e["energia_ok"] = energia_posible(e) and g.perfil["modo"] == "escritura"
        e["vinculada"] = e["nombre"] in vinc if bov_conectada else None   # credencial propia en la bóveda conectada a AAP
    # tickets en curso que cambian el estado o el vínculo (bolita amarilla en la lista)
    en_curso = q("SELECT t.id, t.numero, t.parametros, t.motivo, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
                 "WHERE t.cliente_id=? AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion') AND a.nombre_aap IN (?, ?) "
                 "ORDER BY t.estado != 'pendiente_aprobacion', t.id",
                 g.cliente["id"], ENERGIA, "FaceOps · Vincular máquina a la bóveda")
    # último ticket de encender/apagar y de vincular de cada máquina (se muestra al lado de su bolita)
    ultimos = q("SELECT t.id, t.numero, t.estado, t.parametros, t.motivo, t.creado, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
                "WHERE t.cliente_id=? AND a.nombre_aap IN (?, ?) ORDER BY t.id DESC LIMIT 2000", g.cliente["id"], ENERGIA, "FaceOps · Vincular máquina a la bóveda")
    ult_en, ult_vi = {}, {}
    for t in ultimos:
        if t["nombre_aap"] == ENERGIA:
            ult_en.setdefault(j(t["parametros"], {}).get("en_equipo"), t)
        else:
            m_ = re.match(r"Vincular (.+?) a la bóveda", t["motivo"] or "")
            if m_:
                ult_vi.setdefault(m_.group(1), t)
    for e in eqs:
        e["ult_energia"], e["ult_vinculo"] = ult_en.get(e["nombre"]), ult_vi.get(e["nombre"])
        e["vinc_no"] = "Esta máquina no se vincula a la bóveda (anfitrión de la plataforma)" \
            if "kvm" in (e.get("skills") or []) else \
            ("Sin IP todavía: se podrá vincular cuando el hipervisor le asigne una dirección" if not e.get("ip") and (e.get("categoria") or "") != "clúster openshift" else None)
    for e in eqs:
        e["proc_energia"] = next((t for t in en_curso if t["nombre_aap"] == ENERGIA and j(t["parametros"], {}).get("en_equipo") == e["nombre"]), None)
        e["proc_vinculo"] = next((t for t in en_curso if t["nombre_aap"] != ENERGIA and f"Vincular {e['nombre']} a" in (t["motivo"] or "")), None)
    try:   # riesgo ante fallas de cada máquina (errores conocidos de su tecnología, activos y prevenidos)
        from .riesgo import evaluar as evaluar_riesgo
        from .salud import de_equipo as salud_de
        from . import kb as kb_
        prods_ = kb_.q("SELECT codigo, nombre, skill, detecta FROM productos") if kb_.disponible() else []
        try:   # CVE y políticas de la página visible (para la salud)
            from .cves import por_equipo as _cves_por
            from .politicas_seg import tablero as _tablero
            cves_pag = _cves_por(g.cliente["id"], eqs)
            pols_pag = _tablero(g.cliente["id"], eqs)[1]
        except Exception:  # noqa: BLE001
            cves_pag, pols_pag = {}, {}
        for e in eqs:
            r_ = evaluar_riesgo(g.cliente["id"], e) if e["estado"] == "con datos" else None
            e["riesgo"] = {k: r_[k] for k in ("riesgo", "nivel", "activos", "prevenidos", "en_deuda")} | {"n": len(r_["errores"])} if r_ and r_["errores"] else None
            nom_p = {p_["codigo"]: p_["nombre"] for p_ in prods_}
            e["plats"] = [nom_p.get(c_, c_) for c_ in (r_ or {}).get("productos", []) if c_ != "rhel"]   # las mismas que su ficha
            from .identidad import plataformas as _plats
            e["plats"] += [x for x in _plats(e) if not any(x.lower() in y.lower() or y.lower() in x.lower() for y in e["plats"])]
            if r_:
                s_ = salud_de(e, r_, prods_, {"cves": cves_pag.get(e["id"]), "politicas": pols_pag.get(e["id"], [])})
                e["salud"] = {"salud": s_["salud"], "nivel": s_["nivel"], "peor": (s_.get("peor") or {}).get("nombre"),
                              "comps": [(c["nombre"], c["salud"]) for c in s_["componentes"]]}
    except Exception:  # noqa: BLE001
        pass
    cuenta_amb = Counter(ambito_eq(e, amb_ubi) for e in todos)
    cuenta_clase = Counter(clase_eq(e) for e in todos if not f_amb or ambito_eq(e, amb_ubi) == f_amb)
    vault_auto = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Vault · Preparar servidor'", g.cliente["id"])
    auto_crear = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "FaceOps · Habilitar servidor")
    infras_crear = q("SELECT nombre, tipo, region, ubicacion FROM infraestructuras WHERE cliente_id=? AND tipo IN ('kvm','vmware','gcp') ORDER BY id", g.cliente["id"])
    return render_template("inventario.html", auto_crear=auto_crear, infras_crear=infras_crear, sin_segmento=sin_segmento, bov_conectada=bov_conectada, vault_auto=vault_auto, pag=pag, paginas=paginas, por_pag=por_pag, n_filtrados=n_filtrados, infras=infras, f_amb=f_amb, f_clase=f_clase, cuenta_amb=cuenta_amb, cuenta_clase=cuenta_clase,
                           clases=CLASES, tipos=[(k, n, tipos_todos.get(k, 0)) for k, n, _ in PERIFERICOS], tipo_sel=filtros["tipo"], equipos=eqs, por_eq=por_eq, filtros=filtros, texto=texto, ent=entendido, total=len(todos),
                           opciones={"infra": sorted({e["ubicacion"] for e in todos if e["ubicacion"]}),
                                     "segmento": sorted({s for e in todos for s in e["segmentos"]}),
                                     "skill": sorted({s for e in todos for s in e["skills"]}),
                                     "ambiente": sorted({e["ambiente"] for e in todos if e["ambiente"]})},
                           ultima=max([e["ultima_recoleccion"] or "" for e in todos] or [""]))


@bp.route("/inventario/<int:eid>/ejecutar/<int:aid>", methods=["POST"])
@requiere_login
def ejecutar_en_equipo(eid, aid):
    """Lanza una automatización sobre este equipo sin salir de su detalle (varias a la vez). Responde JSON."""
    from .operacion import solicitar_en_equipo
    if request.form.get("_csrf") != session.get("csrf"):
        abort(400)
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", aid, g.cliente["id"])
    if not e or not a:
        abort(404)
    params, secretos = {}, {}
    for c in j(a["campos"], []):
        if c["tipo"] in ("equipo", "equipos"):
            params[c["var"]] = e["nombre"] if c["tipo"] == "equipo" else [e["nombre"]]
            continue
        v = request.form.getlist("v_" + c["var"]) if c["tipo"] == "multiselect" else request.form.get("v_" + c["var"], "").strip()
        if v in ("", []) and ("v_" + c["var"]) not in request.form and c.get("defecto") not in (None, ""):
            v = c["defecto"]  # lanzado desde el mapa o un hallazgo sin formulario: va con el valor por defecto
        if c["tipo"] == "integer" and v != "":
            v = int(v)
        if v in ("", []):
            if c.get("requerido"):
                return {"error": f"Falta «{c['etiqueta']}»"}, 400
            continue
        if c["tipo"] == "password":
            secretos[c["var"]], params[c["var"]] = v, "••••••"
        else:
            params[c["var"]] = v
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params, request.form.get("motivo", "").strip() or f"Lanzado desde el detalle de {e['nombre']}", secretos)
    except (PermissionError, ErrorAAP) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/tickets/<int:tid>/estado")
@requiere_login
def ticket_estado(tid):
    t = q1("SELECT numero, estado, segmento, dur_ejecucion_s, resultado FROM tickets WHERE id=? AND cliente_id=?", tid, g.cliente["id"])
    if not t or (t["segmento"] and not puede_ver_segmento(g.usuario, g.cliente["id"], t["segmento"])):
        abort(404)
    return {"numero": t["numero"], "estado": t["estado"], "ejecucion_s": t["dur_ejecucion_s"],
            "resultado": (t["resultado"] or "")[:300]}


def _proxy_web(e, w, sub, prefijo, transparente=False, auth=None):
    """Proxy simple a la consola web interna de un equipo (reescribe enlaces y redirecciones salvo en modo transparente,
    cuando la aplicación ya sabe que vive bajo el prefijo, como Redis Insight con RI_PROXY_PATH)."""
    import re
    import requests
    base = w["url"].split("/", 3)
    origen = "/".join(base[:3])                      # esquema://ip:puerto
    destino = origen + (((request.script_root or os.environ.get("FACEOPS_PREFIJO", "/faceops")) + request.path) if transparente else "/" + (sub or (base[3] if len(base) > 3 else "")))
    socket_io = "socket.io" in (sub or "").split("/")[:2]
    if transparente and socket_io:   # Redis Insight atiende socket.io en la raíz (no bajo su ruta de proxy); el long-polling espera ~25 s
        destino = origen + "/" + sub
    if not sub:
        auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola web", e["nombre"], {"url": w["url"]})
    cab = {k: v for k, v in request.headers if k.lower() in ("accept", "accept-language", "content-type", "user-agent")}
    if w.get("host"):
        cab["Host"] = w["host"]
        cab["Referer"] = w.get("referer") or f"{origen.split(':')[0]}://{w['host']}/"
    galletas = {k: v for k, v in request.cookies.items() if k != current_app.config.get("SESSION_COOKIE_NAME", "session")}
    try:
        r = requests.request(request.method, destino, params=request.args, data=request.get_data(), headers=cab, cookies=galletas,
                             timeout=60 if socket_io else 10, verify=False, allow_redirects=False, auth=auth)
    except requests.RequestException as x:
        return render_template("app_caida.html", segmento=e["nombre"], url=destino, error=str(x).split(":")[0][:160]), 502
    tipo = r.headers.get("Content-Type", "application/octet-stream")
    cuerpo = r.content
    if not transparente and ("text/html" in tipo or "text/css" in tipo or "javascript" in tipo):
        txt = cuerpo.decode(r.encoding or "utf-8", "replace").replace(origen, prefijo)
        if w.get("host"):
            txt = txt.replace(f"https://{w['host']}", prefijo).replace(f"http://{w['host']}", prefijo)
        txt = re.sub(r'((?:href|src|action)=["\'])/(?!/)', lambda m: m.group(1) + prefijo + "/", txt)
        for ra in w.get("abs") or []:   # rutas absolutas dentro del JavaScript de la aplicación (p. ej. IdM llama a "/ipa/session/json")
            txt = re.sub(r'(["\'])' + re.escape(ra), lambda m: m.group(1) + prefijo + ra, txt)
        if "text/html" in tipo:
            franja = (f'<div style="font:13px Arial,sans-serif;background:#151515;color:#fff;padding:6px 14px;border-bottom:3px solid #ee0000">'
                      f'Consola web de <b>{e["nombre"]}</b> ({w["producto"]}) a través de FaceOps · {w["url"]} · HTTP {r.status_code}</div>')
            i = txt.lower().find("<body")
            j_ = txt.find(">", i) + 1 if i >= 0 else 0
            txt = txt[:j_] + franja + txt[j_:]
        cuerpo, tipo = txt.encode("utf-8"), (tipo.split(";")[0] + "; charset=utf-8")
    resp = make_response(cuerpo, r.status_code)
    resp.headers["Content-Type"] = tipo
    resp.headers["Cache-Control"] = "no-store"
    if r.headers.get("Location"):
        loc = r.headers["Location"].replace(origen, prefijo)
        if w.get("host"):
            loc = loc.replace(f"https://{w['host']}", prefijo).replace(f"http://{w['host']}", prefijo)
        resp.headers["Location"] = loc if transparente else (prefijo + loc if loc.startswith("/") and not loc.startswith(prefijo) else loc)
    for c in r.raw.headers.getlist("Set-Cookie") if hasattr(r.raw.headers, "getlist") else []:
        resp.headers.add("Set-Cookie", c if transparente else re.sub(r"(?i)path=/[^;]*", f"Path={prefijo}/", c))
    return resp


@bp.route("/inventario/<int:eid>/web/", defaults={"sub": ""}, methods=["GET", "POST"])
@bp.route("/inventario/<int:eid>/web/<path:sub>", methods=["GET", "POST"])
@requiere_login
def web_equipo(eid, sub):
    """Consola web del equipo a través de FaceOps."""
    from .consola import web_de
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    w = web_de(e) if e else None
    if not w or w["producto"] == "Redis":
        abort(404)
    if not sub:
        auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola web", e["nombre"], {"url": w["url"]})
    return _proxy_web(e, w, sub, url_for("consola.web_equipo", eid=eid, sub="").rstrip("/"))


# consola web propia de cada plataforma (solo las que tienen una de verdad); las demás muestran que no tienen
CONSOLAS = {"jboss": ("http", 9990, "/console/", "Consola de administración de JBoss"), "vault": ("http", 8200, "/ui/", "Vault"),
            "keycloak": ("http", 8080, "/admin/", "Keycloak"), "redis": ("http", 5540, "", "Redis Insight"),
            "red-hat-idm": ("https", 443, "/ipa/ui/", "Red Hat IdM")}


FAMILIAS = [("jboss", "jboss"), ("eap", "jboss"), ("wildfly", "jboss"), ("postgres", "postgresql"), ("redis", "redis"), ("valkey", "redis"),
            ("nginx", "nginx"), ("httpd", "httpd"), ("apache", "httpd"), ("tomcat", "tomcat"), ("jws", "tomcat"), ("keycloak", "keycloak"),
            ("rhbk", "keycloak"), ("kafka", "kafka"), ("streams", "kafka"), ("artemis", "activemq"), ("amq", "activemq"), ("vault", "vault"),
            ("mysql", "mysql"), ("mariadb", "mysql")]


def familia(cod):
    """Código del producto en la base de conocimiento (jboss-eap, postgresql-15…) → familia con la que FaceOps lo maneja."""
    c = (cod or "").lower()
    return next((f for k, f in FAMILIAS if k in c), c)


def consola_plataforma(e, cod):
    """{'url', 'producto', 'transparente'} o None. Redis: solo si Redis Insight está instalado en la máquina."""
    cod = familia(cod)
    c = CONSOLAS.get(cod)
    if not c or not e.get("ip"):
        return None
    if cod == "redis" and "redisinsight" not in (e.get("skills") or []):
        return None
    esq, puerto, ruta, prod = c
    w = {"url": f"{esq}://{e['ip']}:{puerto}{ruta}", "producto": prod, "transparente": cod == "redis"}
    if cod == "red-hat-idm":   # IdM solo responde a su nombre (Host y Referer con su FQDN) y redirige a él
        d_ = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
        fq = d_.get("hostname") or e["nombre"]
        if "." not in fq:
            fq = next((x for x in [e.get("nombre")] if x and "." in x), fq)
        w.update(host=fq, referer=f"https://{fq}/ipa/ui/", abs=["/ipa/"], inicio="ipa/ui/")
    return w


@bp.route("/inventario/<int:eid>/consola-web/<cod>/", defaults={"sub": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@bp.route("/inventario/<int:eid>/consola-web/<cod>/<path:sub>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@requiere_login
def consola_web_plataforma(eid, cod, sub):
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    w = consola_plataforma(e, cod) if e and g.perfil["modo"] == "escritura" else None
    if not w:
        abort(404)
    if not sub and request.method == "GET":
        auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola web", e["nombre"], {"plataforma": cod, "url": w["url"]})
    prefijo = url_for("consola.consola_web_plataforma", eid=eid, cod=cod, sub="").rstrip("/")
    if w.get("inicio") and not sub and request.method == "GET":   # la aplicación espera vivir en su ruta (rutas relativas)
        return redirect(f"{prefijo}/{w['inicio']}")
    auth = None
    if familia(cod) == "jboss":
        # La consola de JBoss (HAL) busca la API de administración en /management de su mismo origen: se le indica la de
        # FaceOps con ?connect= y FaceOps entra con la credencial de administración guardada en la bóveda (el usuario no la ve)
        if request.method == "GET" and sub in ("", "console", "console/", "console/index.html") and not request.args.get("connect"):
            return redirect(f"{prefijo}/console/index.html?connect=" + quote(request.host_url.rstrip("/") + prefijo, safe=""))
        from .boveda import web as cred_web
        from requests.auth import HTTPDigestAuth
        cr = cred_web(e["nombre"], "jboss") or {}
        if cr.get("usuario") and cr.get("clave") and sub.startswith(("management", "logout")):
            auth = HTTPDigestAuth(cr["usuario"], cr["clave"])
    return _proxy_web(e, w, sub, prefijo, w["transparente"], auth)


@bp.route("/inventario/<int:eid>/redis-insight", methods=["POST"])
@requiere_login
def equipo_redis_insight(eid):
    """Instala Redis Insight en la máquina (ticket en AAP); queda publicado bajo la ruta de su consola en FaceOps."""
    from .operacion import solicitar_en_equipo
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Redis · Instalar Redis Insight'", g.cliente["id"])
    if not e or not a:
        flash("Falta «Redis · Instalar Redis Insight» en el catálogo (sincroniza con AAP)", "error")
        return redirect(url_for("consola.equipo", eid=eid) + "#p-redis")
    pre = request.script_root or os.environ.get("FACEOPS_PREFIJO", "/faceops")   # la ruta pública con la que el navegador la pide
    ruta = pre + url_for("consola.consola_web_plataforma", eid=eid, cod="redis", sub="")[len(request.script_root):].rstrip("/")
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, {"ri_ruta": ruta}, f"Instalar Redis Insight en {e['nombre']}")
        flash(f"Instalando Redis Insight en {e['nombre']} ({t['numero']}): al terminar, el icono de Redis abre su consola", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid) + "#p-redis")


SERVICIOS_PLATAFORMA = {"jboss": ["eap8-standalone", "eap7-standalone", "jboss-eap", "wildfly", "jboss"], "nginx": ["nginx"],
                       "postgresql": ["postgresql", "postgresql-16", "postgresql-15", "postgresql-13"], "redis": ["redis", "valkey"],
                       "httpd": ["httpd"], "tomcat": ["tomcat", "jws6-tomcat", "jws5-tomcat"], "keycloak": ["keycloak", "rhbk"],
                       "kafka": ["kafka", "amq-streams"], "activemq": ["artemis", "amq-broker", "activemq"], "mysql": ["mysqld", "mariadb"],
                       # (una unidad que termina en «@» toma cualquier instancia: dirsrv@slapd-dc1…)
                       "389-directory-server": ["dirsrv@"], "red-hat-idm": ["ipa"], "vault": ["vault"], "midpoint": ["midpoint"],
                       "certificate-system": ["pki-tomcatd@"], "gitea": ["gitea"], "registro-paquetes": ["gitea"],
                       "aap": ["automation-controller-web", "automation-controller"], "automation-hub": ["automation-hub-api", "pulpcore-api"],
                       "frrouting": ["frr"], "vyos-firewall": ["vyos-router"], "rabbitmq": ["rabbitmq-server"], "openshift": ["kubelet"],
                       # Windows (servicios de Windows)
                       "active-directory": ["NTDS"], "iis": ["W3SVC"]}


def servicio_estado(e, cod):
    """En ejecución | detenido | None (no se sabe) según los servicios recolectados del equipo."""
    uds = SERVICIOS_PLATAFORMA.get(familia(cod))
    serv = [x.lower() for x in j(e.get("servicios"), [])] if isinstance(e.get("servicios"), str) else [x.lower() for x in (e.get("servicios") or [])]
    if not uds or not serv:
        return None
    return "en ejecución" if any(u.lower().rstrip("@") in x for u in uds for x in serv) else "detenido"


@bp.route("/tickets/<int:tid>/bitacora")
@requiere_login
def ticket_bitacora(tid):
    """La bitácora del ticket en vivo (fragmento HTML que la ficha refresca mientras el trabajo corre)."""
    t = q1("SELECT * FROM tickets WHERE id=? AND cliente_id=?", tid, g.cliente["id"])
    if not t:
        abort(404)
    from .aprobacion_areas import filtrar_tickets
    if not filtrar_tickets([t], g.usuario, g.cliente["id"]):
        abort(404)
    bitacora, web_aap = [], ""
    if t["aap_job_id"]:
        from .aap import AAP
        from .bitacora import construir
        try:
            cli = AAP.de_cliente(g.cliente)
            web_aap, bitacora = cli.publica, construir(cli, t)
        except ErrorAAP:
            pass
    resp = make_response(render_template("_bitacora.html", bitacora=bitacora, web_aap=web_aap, t=t))
    resp.headers["X-Ticket-Terminado"] = "0" if t["estado"] in ("en_ejecucion", "en_cola", "pendiente_aprobacion") else "1"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/tickets/<int:tid>/cancelar", methods=["POST"])
@requiere_login
def ticket_cancelar(tid):
    """Cancela una solicitud que aún no termina (esperando aprobación, en cola o en ejecución): la cancela también en AAP."""
    _escritura()
    t = q1("SELECT * FROM tickets WHERE id=? AND cliente_id=?", tid, g.cliente["id"])
    if not t or t["estado"] not in ("pendiente_aprobacion", "en_cola", "en_ejecucion"):
        abort(404)
    if t["usuario_id"] != g.usuario["id"] and not puede_aprobar(g.usuario, g.cliente["id"]):
        abort(403)
    motivo = (request.form.get("motivo") or "").strip()[:200]
    if t.get("aap_job_id"):
        try:
            from .aap import AAP
            AAP.de_cliente(g.cliente).post(f"{'workflow_jobs' if t['aap_job_tipo'] == 'workflow' else 'jobs'}/{t['aap_job_id']}/cancel/")
        except Exception as x:  # noqa: BLE001 - si AAP ya lo terminó, igual se cierra en FaceOps
            print("Cancelar en AAP:", str(x)[:200])
    ej("UPDATE tickets SET estado='cancelado', fin=?, resultado=? WHERE id=?", ahora(),
       f"Cancelado por {g.usuario['usuario']}" + (f": {motivo}" if motivo else ""), tid)
    ej("UPDATE ticket_etapas SET estado='cancelada', fin=? WHERE ticket_id=? AND estado='pendiente'", ahora(), tid)
    auditar(g.usuario["usuario"], g.cliente["id"], "cancelar ticket", t["numero"], {"motivo": motivo})
    flash(f"{t['numero']} cancelado", "ok")
    return redirect(request.form.get("volver") or url_for("consola.ticket", tid=tid))


@bp.route("/inventario/<int:eid>/vincular", methods=["POST"])
@requiere_login
def equipo_vincular(eid):
    """Solicitud para vincular la máquina a la bóveda (credencial propia guardada en la bóveda conectada a AAP)."""
    from .inventario import sistema_eq
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Vincular máquina a la bóveda'", g.cliente["id"])
    if not e:
        return {"error": "Equipo no encontrado"}, 404
    if not a:
        return {"error": "Falta «FaceOps · Vincular máquina a la bóveda» en el catálogo (sincroniza con AAP)"}, 400
    tipo = "openshift" if (e.get("categoria") or "") == "clúster openshift" else \
        ("windows" if sistema_eq(e) == "windows" else ("red" if (e.get("categoria") or "servidor") in CATEGORIAS_RED else "linux"))
    try:
        # aplica a toda máquina del inventario (servidor, red, Windows, con o sin recolección): no pasa por las reglas
        # de compatibilidad del catálogo, solo por el permiso de ambiente del perfil
        from .permisos import ambiente_permitido
        if not ambiente_permitido(g.perfil, e.get("ambiente")):
            raise PermissionError("Tu perfil no opera el ambiente de esta máquina")
        if "kvm" in (e.get("skills") or []):
            raise ValueError("Esta máquina no se vincula a la bóveda (anfitrión de la plataforma)")
        ya = q1("SELECT numero FROM tickets WHERE cliente_id=? AND automatizacion_id=? AND motivo=? AND estado IN ('pendiente_aprobacion','en_cola','en_ejecucion')",
                g.cliente["id"], a["id"], f"Vincular {e['nombre']} a la bóveda")
        if ya:
            raise ValueError(f"Ya hay una vinculación en curso para {e['nombre']} ({ya['numero']}): apruébala o cancélala desde ese ticket")
        if e.get("energia") == "apagado":
            raise ValueError(f"{e['nombre']} está apagada: enciéndela (bolita de Estado › Encender) y luego vincúlala")
        params, secretos = {"vm_tipo": tipo, "vm_usuario": "faceops"}, None
        if tipo == "red":   # los equipos de red no crean usuario: se guarda en la bóveda el usuario y la clave que se entregan
            usu, cla = (request.form.get("vm_usuario") or "").strip(), request.form.get("vm_clave") or ""
            if not usu or not cla:
                raise ValueError("Para un equipo de red indica el usuario y la clave de administración que se guardarán en la bóveda")
            params, secretos = {"vm_tipo": tipo, "vm_usuario": usu, "vm_clave": "••••••"}, {"vm_clave": cla}
        if tipo == "openshift":   # un token de administrador, una sola vez: crea la cuenta de servicio faceops y en la bóveda queda su token
            tok = (request.form.get("vm_clave") or "").strip()
            if not tok:
                raise ValueError("Para vincular el clúster indica un token de administrador (oc whoami -t): se usa una vez para crear la cuenta de servicio de FaceOps")
            inf_ = q1("SELECT detalle FROM infraestructuras WHERE cliente_id=? AND tipo='openshift' AND (ubicacion=? OR ?='')", g.cliente["id"], e.get("ubicacion") or "", e.get("ubicacion") or "")
            api_ = (j(inf_["detalle"], {}) if inf_ else {}).get("api", "")
            params, secretos = {"vm_tipo": tipo, "vm_usuario": "faceops", "vm_clave": "••••••", **({"vm_api": api_} if api_ else {})}, {"vm_clave": tok}
        t = solicitar(g.usuario, g.cliente, a, None, params, f"Vincular {e['nombre']} a la bóveda",
                      secretos, {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/inventario/<int:eid>/inventariar", methods=["POST"])
@requiere_login
def equipo_inventariar(eid):
    """Inventario completo de la máquina (sistema, servicios, paquetes): ticket en AAP; FaceOps contrasta el resultado."""
    from .operacion import solicitar
    from .inventariado import AUTOMATIZACION
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], AUTOMATIZACION)
    if not e:
        abort(404)
    nodo_ = j(e["datos"], {}).get("nodo_ocp") or {}
    if (e.get("categoria") or "") == "clúster openshift" or nodo_:   # un clúster (o uno de sus nodos) se inventaría por la API de OpenShift
        if nodo_:
            e = next((x for x in equipos_visibles() if x["id"] == nodo_.get("cluster_id")), e)
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='OpenShift · Recolectar clúster'", g.cliente["id"])
        if not a:
            return {"error": "Falta «OpenShift · Recolectar clúster» en el catálogo (sincroniza con AAP)"}, 400
    if not a:
        flash(f"Falta «{AUTOMATIZACION}» en el catálogo (sincroniza con AAP)", "error")
        return redirect(url_for("consola.equipo", eid=eid) + "#inventario-maq")
    fetch_ = request.headers.get("X-Requested-With") == "fetch"
    try:
        t = solicitar(g.usuario, g.cliente, a, None, {"pq_respaldar": "si"}, f"Inventariar {e['nombre']}", None,
                      {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
        if fetch_:
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]),
                    "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}
        return redirect(url_for("consola.ticket", tid=t["id"]))
    except (PermissionError, ErrorAAP, ValueError) as x:
        if fetch_:
            return {"error": str(x)}, 400
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid) + "#inventario-maq")


@bp.route("/inventario/<int:eid>/paquetes", methods=["POST"])
@requiere_login
def equipo_paquetes(eid):
    """Inventario de paquetes (con su fuente) y respaldo de los RPM en el registro propio: ticket en AAP, con aprobación."""
    from .operacion import solicitar
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Sistema · Paquetes · inventario y respaldo'", g.cliente["id"])
    if not e:
        abort(404)
    if not a:
        flash("Falta «Sistema · Paquetes · inventario y respaldo» en el catálogo (sincroniza con AAP)", "error")
        return redirect(url_for("consola.equipo", eid=eid) + "#paquetes")
    try:
        t = solicitar(g.usuario, g.cliente, a, None, {"pq_respaldar": "si"}, f"Inventariar y respaldar paquetes de {e['nombre']}", None,
                      {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
        return redirect(url_for("consola.ticket", tid=t["id"]))
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid) + "#paquetes")


@bp.route("/inventario/<int:eid>/plataforma/<cod>/servicio", methods=["POST"])
@requiere_login
def plataforma_servicio(eid, cod):
    """Encender, apagar o reiniciar la plataforma (su servicio) sin tocar la máquina: ticket en AAP, con aprobación."""
    from .operacion import solicitar_en_equipo
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    accion = request.form.get("accion", "")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Plataforma · Controlar servicio'", g.cliente["id"])
    if not e or familia(cod) not in SERVICIOS_PLATAFORMA or accion not in ("encender", "apagar", "reiniciar"):
        abort(404)
    if not a:
        flash("Falta «Plataforma · Controlar servicio» en el catálogo (sincroniza con AAP)", "error")
        return redirect(url_for("consola.equipo", eid=eid) + f"#p-{cod}")
    nom = request.form.get("nombre") or cod
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, {"sp_unidades": SERVICIOS_PLATAFORMA[familia(cod)], "sp_accion": accion, "sp_plataforma": nom},
                                f"{accion.capitalize()} {nom} en {e['nombre']}")
        flash(f"{accion.capitalize()} {nom}: {t['numero']} creado (espera su aprobación)", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid) + f"#p-{cod}")


PAQUETES_PLATAFORMA = {"jboss": ["eap8*", "eap7*", "jboss-eap*"], "nginx": ["nginx*"], "postgresql": ["postgresql-server*", "postgresql*-server", "postgresql-contrib*"],
                       "redis": ["redis*", "valkey*"], "httpd": ["httpd"], "tomcat": ["tomcat*", "jws6-tomcat*", "jws5-tomcat*"],
                       "keycloak": ["rhbk*", "keycloak*"], "kafka": ["kafka*", "amq-streams*"], "activemq": ["amq-broker*", "artemis*"],
                       "mysql": ["mysql-server*", "mariadb-server*"],
                       "389-directory-server": ["389-ds-base*"], "red-hat-idm": ["ipa-server*", "freeipa-server*"], "vault": ["vault"],
                       "certificate-system": ["pki-ca", "pki-kra", "redhat-pki*"], "gitea": ["gitea"], "registro-paquetes": ["gitea"],
                       "automation-hub": ["automation-hub*"], "aap": ["automation-controller*"], "frrouting": ["frr"], "vyos-firewall": ["vyos*"],
                       "rabbitmq": ["rabbitmq-server*"]}
# lo que no se quita con paquetes: contenedores (podman) y roles de Windows; IdM antes desinstala el servidor
CONTENEDORES_PLATAFORMA = {"keycloak": ["keycloak"], "midpoint": ["midpoint"], "redisinsight": ["redisinsight"], "vault": ["vault"]}
WINDOWS_PLATAFORMA = {"active-directory": ["AD-Domain-Services"], "iis": ["Web-Server"]}
PREVIO_PLATAFORMA = {"red-hat-idm": "ipa-server-install --uninstall -U"}


def desinstalable(cod):
    f = familia(cod)
    return f in PAQUETES_PLATAFORMA or f in CONTENEDORES_PLATAFORMA or f in WINDOWS_PLATAFORMA


@bp.route("/inventario/<int:eid>/operador/<cod>", methods=["POST"])
@requiere_login
def operador_accion(eid, cod):
    """Reiniciar o desinstalar un operador de OpenShift: ticket en AAP con aprobación."""
    from .operacion import solicitar
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    op = next((o for o in ((j(e["datos"], {}).get("ocp") or {}).get("operadores") or []) if "op-" + o["n"] == cod), None) if e else None
    accion = request.form.get("accion", "")
    if not op or accion not in ("reiniciar", "desinstalar"):
        return {"error": "Operador o acción no válidos"}, 400
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='OpenShift · Operador'", g.cliente["id"])
    if not a:
        return {"error": "Falta «OpenShift · Operador» en el catálogo (sincroniza con AAP)"}, 400
    try:
        t = solicitar(g.usuario, g.cliente, a, None, {"op_ns": op["ns"], "op_sub": op.get("sub") or op["n"], "op_csv": op.get("csv") or "", "op_accion": accion},
                      f"{accion.capitalize()} el operador {op.get('nombre') or op['n']} en {e['nombre']}", None,
                      {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/inventario/<int:eid>/plataforma/<cod>/desinstalar", methods=["POST"])
@requiere_login
def plataforma_desinstalar(eid, cod):
    """Desinstalar una plataforma de la máquina: ticket en AAP con aprobación; al terminar sale de la ficha."""
    from .operacion import solicitar_en_equipo
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    fam = familia(cod)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Plataforma · Desinstalar'", g.cliente["id"])
    if not e or not desinstalable(cod):
        abort(404)
    if not a:
        return {"error": "Falta «Plataforma · Desinstalar» en el catálogo (sincroniza con AAP)"}, 400
    nom = request.form.get("nombre") or cod
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, {"dp_plataforma": nom, "dp_familia": fam, "dp_unidades": SERVICIOS_PLATAFORMA.get(fam, []),
                                                             "dp_paquetes": PAQUETES_PLATAFORMA.get(fam, []),
                                                             "dp_contenedores": CONTENEDORES_PLATAFORMA.get(fam, []), "dp_windows": WINDOWS_PLATAFORMA.get(fam, []),
                                                             "dp_previo": PREVIO_PLATAFORMA.get(fam, "")}, f"Desinstalar {nom} de {e['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/inventario/<int:eid>/redis", methods=["GET", "POST"])
@requiere_login
def redis_equipo(eid):
    """Consola de Redis: FaceOps entra con la credencial de la bóveda (el usuario nunca la ve)."""
    from . import redis_consola as rc
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    try:
        r = rc.conectar(e)
        if request.method == "POST":
            if request.form.get("_csrf") != session.get("csrf"):
                abort(400)
            accion = request.form.get("accion")
            if accion == "valor":
                return rc.valor(r, request.form.get("clave", ""))
            if accion == "comando":
                try:
                    res, escribe = rc.comando(r, request.form.get("texto", ""), g.perfil["modo"] == "escritura")
                except (ValueError, PermissionError) as x:
                    return {"error": str(x)}, 400
                auditar(g.usuario["usuario"], g.cliente["id"], "comando Redis" + (" (escritura)" if escribe else ""), e["nombre"],
                        {"comando": request.form.get("texto", "")[:200]})
                return {"resultado": res}
            abort(400)
        patron = request.args.get("patron", "*")
        cursor, ks = rc.claves(r, patron, int(request.args.get("cursor", 0) or 0))
        if not request.args:
            auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola Redis", e["nombre"], {})
        return render_template("redis.html", e=e, info=rc.resumen(r), claves=ks, cursor=cursor, patron=patron, error=None)
    except (LookupError, Exception) as x:  # noqa: BLE001 - conexión, autenticación o bóveda
        if request.method == "POST":
            return {"error": str(x)[:300]}, 502
        return render_template("redis.html", e=e, info=None, claves=[], cursor=0, patron="*", error=str(x)[:300])


# ------------------------------------------------------------------ respaldo continuo: historial de versiones y recuperación
VOLVER, RECUPERAR = "FaceOps · Volver a una versión", "FaceOps · Recuperar servidor"


def _gitea_respaldo(ruta, **params):
    import os
    import requests
    base = "http://192.168.122.1:3000/api/v1/repos/repositorio/respaldo-servidores"
    tok = os.environ.get("FACEOPS_RESPALDO_TOKEN", "")
    r = requests.get(base + ruta, params=params, timeout=10, headers={"Authorization": f"token {tok}"} if tok else {})
    return r.json() if r.ok else None


def _gitea_respaldo_archivo(rama, ruta):
    import requests
    tok = os.environ.get("FACEOPS_RESPALDO_TOKEN", "")
    r = requests.get(f"http://192.168.122.1:3000/api/v1/repos/repositorio/respaldo-servidores/raw/{ruta}", params={"ref": rama}, timeout=10,
                     headers={"Authorization": f"token {tok}"} if tok else {})
    return r.text if r.ok else None


@bp.route("/inventario/<int:eid>/aplicaciones")
@requiere_login
def aplicaciones_equipo(eid):
    """Aplicaciones desplegadas en el equipo (registro que guarda el respaldo continuo en Git) y las versiones anteriores."""
    import yaml as _y
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    txt = _gitea_respaldo_archivo(e["nombre"], "meta/aplicaciones.txt") or ""
    apps = []
    for bloque in txt.split("--- ")[1:]:
        nombre, _, cuerpo = bloque.partition("\n")
        cuerpo = "\n".join(l for l in cuerpo.splitlines() if not l.startswith("archivo:"))
        try:
            d = _y.safe_load(cuerpo) or {}
        except _y.YAMLError:
            d = {}
        d["app"] = d.get("app") or nombre.strip()
        apps.append(d)
    hist = {}   # versiones anteriores de cada aplicación: de los commits del respaldo que la cambiaron
    for c in _gitea_respaldo("/commits", sha=e["nombre"], limit=50, stat="false", files="false") or []:
        msg = c["commit"]["message"]
        if "Aplicaciones:" not in msg:
            continue
        bloque = msg.split("Aplicaciones:", 1)[1]
        v = re.search(r"version:\s*\"?([\w.\-]+)", bloque)
        cm = re.search(r"commit:\s*\"?([0-9a-f]{6,40})", bloque)
        ap = re.search(r"app:\s*([\w.\-]+)", bloque)
        if v:
            k = ap.group(1) if ap else "app-jboss"
            hist.setdefault(k, []).append({"version": v.group(1), "commit": cm.group(1) if cm else "", "fecha": c["commit"]["author"]["date"],
                                            "respaldo": c["sha"][:7], "respaldo_url": c["html_url"]})
    for a in apps:
        a["historial"] = [h for h in hist.get(a["app"], []) if h["version"] != str(a.get("version"))][:10]
        if a.get("repo") and a.get("commit"):
            a["commit_url"] = f"{a['repo'].rstrip('/').removesuffix('.git')}/commit/{a['commit']}"
    return {"aplicaciones": apps, "puede": g.perfil["modo"] == "escritura", "desplegar": url_for("consola.desplegar_version_app", eid=eid)}


@bp.route("/inventario/<int:eid>/aplicaciones/desplegar", methods=["POST"])
@requiere_login
def desplegar_version_app(eid):
    """Vuelve a desplegar una versión anterior de una aplicación (ticket; aprobación en producción y QA)."""
    from .operacion import solicitar_en_equipo
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='JBoss · Desplegar app'", g.cliente["id"])
    ver = re.sub(r"[^\w.\-]", "", request.form.get("version", ""))[:40]
    com = re.sub(r"[^0-9a-f]", "", request.form.get("commit", ""))[:40]
    if not e or not a or not ver:
        return {"error": "Falta la versión o la automatización «JBoss · Desplegar app»"}, 400
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, {"app_version": ver, "app_commit": com},
                                f"Volver a la versión {ver} de la aplicación en {e['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/inventario/<int:eid>/versiones")
@requiere_login
def versiones_equipo(eid):
    """Historial de versiones del equipo (cada commit de su respaldo continuo: qué cambió y qué comandos se ejecutaron)."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    cs = _gitea_respaldo("/commits", sha=e["nombre"], limit=int(request.args.get("n", 25)), stat="false", files="false")
    if cs is None:
        return {"versiones": [], "error": "Este equipo aún no tiene respaldo continuo en Git (instala la skill «Respaldo continuo en Git»)."}
    out = []
    for i, c in enumerate(cs if isinstance(cs, list) else []):
        lineas = c["commit"]["message"].strip().splitlines()
        sec, partes = "archivos", {"archivos": [], "paquetes": [], "plataformas": [], "aplicaciones": [], "comandos": []}
        for ln in lineas[1:]:
            t_ = ln.strip()
            if t_ in ("Paquetes:", "Plataformas:", "Aplicaciones:", "Comandos:"):
                sec = t_[:-1].lower()
                continue
            if t_:
                partes[sec].append(t_.lstrip("·$ ").strip())
        out.append({"sha": c["sha"], "corto": c["sha"][:7], "fecha": c["commit"]["author"]["date"], "autor": c["commit"]["author"]["name"],
                    "titulo": lineas[0] if lineas else "", "detalle": "\n".join(lineas[1:]).strip(),
                    "url": git_publico(c["html_url"]), "actual": i == 0,
                    **partes})
    return {"versiones": out, "rama": e["nombre"], "historial": out[0]["url"].split("/commit/")[0] + f"/commits/branch/{e['nombre']}" if out else "",
            "puede": g.perfil["modo"] == "escritura", "volver": url_for("consola.volver_equipo", eid=eid),
            "recuperar": url_for("consola.recuperar_equipo", eid=eid)}


@bp.route("/inventario/<int:eid>/versiones/<sha>")
@requiere_login
def version_equipo(eid, sha):
    """Un cambio del respaldo continuo de la máquina (el repositorio es privado: FaceOps lo muestra sin entrar a Gitea)."""
    import requests
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        abort(404)
    c = _gitea_respaldo(f"/git/commits/{sha}", stat="true", files="true")
    if not c:
        abort(404)
    tok = os.environ.get("FACEOPS_RESPALDO_TOKEN", "")
    r = requests.get(f"http://192.168.122.1:3000/api/v1/repos/repositorio/respaldo-servidores/git/commits/{sha}.diff", timeout=15,
                     headers={"Authorization": f"token {tok}"} if tok else {})
    diff = r.text[:400_000] if r.ok else ""
    return render_template("version_equipo.html", e=e, c=c, diff=diff, sha=sha)


def _ticket_respaldo(nombre_aap, e, params, motivo):
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre_aap)
    if not a:
        return {"error": f"Falta «{nombre_aap}» en el catálogo (sincroniza con AAP)"}, 400
    try:
        t = solicitar(g.usuario, g.cliente, a, (e["segmentos"] or [""])[0], params, motivo)
    except (PermissionError, ErrorAAP) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/inventario/<int:eid>/volver", methods=["POST"])
@requiere_login
def volver_equipo(eid):
    """Volver a una versión del historial: ticket con aprobación en AAP; restaura sobre la misma máquina."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    sha = request.form.get("sha", "")
    if not e or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return {"error": "Versión no válida"}, 400
    return _ticket_respaldo(VOLVER, e, {"rec_equipo": e["nombre"], "rec_version": sha, "rec_ip": e["ip"]},
                            f"Volver {e['nombre']} a la versión {sha[:7]} de su respaldo en Git")


def _monitoreo_url(e):
    """Enlace a la máquina en la herramienta de monitoreo del cliente (conf. del cliente «monitoreo_url», con {nombre} e {ip})."""
    conf = j(g.cliente.get("tickets_conf"), {}) if g.get("cliente") else {}
    plantilla = conf.get("monitoreo_url") or os.environ.get("FACEOPS_MONITOREO_URL", "")
    if not plantilla:
        return None
    return {"url": plantilla.replace("{nombre}", quote(e["nombre"], safe="")).replace("{ip}", e.get("ip") or ""),
            "nombre": conf.get("monitoreo_nombre") or os.environ.get("FACEOPS_MONITOREO_NOMBRE", "monitoreo")}


LOGS_PLATAFORMA = {"jboss": r"eap|wildfly|jboss", "httpd": r"/httpd/", "389-directory-server": r"/dirsrv/", "red-hat-idm": r"ipa|krb5|kdc|/pki/|sssd",
                   "nginx": r"/nginx/", "postgresql": r"pgsql|postgres", "redis": r"/redis/", "tomcat": r"tomcat", "keycloak": r"keycloak",
                   "vault": r"/vault/", "midpoint": r"midpoint", "certificate-system": r"/pki/", "gitea": r"gitea"}


def _plat_de_servicio(n):
    for fam, us in SERVICIOS_PLATAFORMA.items():
        for u in us:
            if (u.endswith("@") and n.startswith(u)) or n == u or n.startswith(u + "-"):
                return fam
    return None


def servicios_de(e, c):
    """Servicios de un componente: los de la plataforma, o los del sistema operativo (los que no son de ninguna plataforma)."""
    det = j(e["datos"], {}).get("servicios_det") or []
    fam = None if c.get("so") else familia(c["codigo"])
    xs = [x for x in det if (_plat_de_servicio(x["n"]) == fam) if True] if fam else [x for x in det if not _plat_de_servicio(x["n"])]
    orden = {"error": 0, "detenido": 1, "arriba": 2, "inactivo": 3}
    return sorted(xs, key=lambda x: (orden.get(x["estado"], 9), x["n"]))


def logs_de(e, c):
    import re as _re
    lg = j(e["datos"], {}).get("logs") or []
    if c.get("so"):
        todos = "|".join(LOGS_PLATAFORMA.values())
        return [x for x in lg if not _re.search(todos, x["ruta"])]
    rx = LOGS_PLATAFORMA.get(familia(c["codigo"]))
    return [x for x in lg if rx and _re.search(rx, x["ruta"])]


def _ns_segmento(ns):
    """Segmento de negocio de FaceOps desplegado en ese namespace de OpenShift (si lo hay)."""
    try:
        return q1("SELECT segmento, ambiente FROM segmentos_ocp WHERE cliente_id=? AND namespace=?", g.cliente["id"], ns)
    except Exception:  # noqa: BLE001
        return None


def _nodo_equipo(n):
    """La ficha del inventario de un nodo de OpenShift (por nombre o por IP), si existe."""
    for x in equipos_visibles():
        if (x.get("categoria") or "") == "clúster openshift":
            continue
        if x["nombre"].split(".")[0] == (n.get("nombre") or "").split(".")[0] or (n.get("ip") and x.get("ip") == n.get("ip")):
            return x
    return None


def _ids_seguridad(autos):
    """Ids de las automatizaciones de seguridad (área «seguridad»), para separarlas de la operación general."""
    from .areas import area_de
    return [a["id"] for a in autos if area_de(a) == "seguridad"]


AGREGAR_DISCO = "FaceOps · Agregar disco"


@bp.route("/inventario/<int:eid>/disco", methods=["POST"])
@requiere_login
def equipo_disco(eid):
    """Agregar un disco a la máquina: del hipervisor (pool de libvirt) o del storage (volumen de la SAN). Ticket con aprobación:
    AAP crea el volumen, lo conecta a la VM y lo prepara dentro del sistema."""
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or not energia_posible(e):
        return {"error": "Solo se agregan discos a máquinas virtuales del anfitrión KVM"}, 400
    f = request.form
    origen = f.get("origen", "hipervisor")
    nombre = f.get("nombre", "datos").strip().lower()
    gb = f.get("gb", "20")
    if origen not in ("hipervisor", "storage") or not re.fullmatch(r"[a-z0-9-]{2,30}", nombre) or not gb.isdigit() or not 1 <= int(gb) <= 2000:
        return {"error": "Datos del disco no válidos"}, 400
    vm = e["nombre"].split(".")[0]
    params = {"ad_equipo": vm, "ad_gb": gb, "ad_origen": origen, "ad_nombre": nombre, "ad_formatear": bool(f.get("formatear")),
              "ad_serial": f"fo{int(time.time()) % 10**8}"}
    if origen == "storage":
        sts = [x["nombre"] for x in equipos_visibles() if "storage-emulado" in (x.get("skills") or [])]
        if f.get("storage") not in sts:
            return {"error": "Storage no válido"}, 400
        params.update(ad_storage=f["storage"], ad_volumen=f"{vm}-{nombre}", ad_st_pool=f.get("st_pool") if f.get("st_pool") in ("oro", "plata") else "plata")
    else:
        params["ad_pool"] = "images"
    if "windows" in (e.get("so") or "").lower():
        params["ad_letra"] = f.get("letra", "E") if f.get("letra", "E") in "EFGHIJ" else "E"
    else:
        m = f.get("montaje", "/datos")
        if not re.fullmatch(r"/[A-Za-z0-9/_.-]{1,60}", m) or m in ("/", "/boot", "/usr", "/etc", "/var"):
            return {"error": "Punto de montaje no válido"}, 400
        params["ad_montaje"] = m
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], AGREGAR_DISCO)
    if not a:
        return {"error": f"Falta «{AGREGAR_DISCO}» en el catálogo (sincroniza con AAP)"}, 400
    desde = f"del storage {params.get('ad_storage')}" if origen == "storage" else "del hipervisor"
    try:
        t = solicitar(g.usuario, g.cliente, a, (e["segmentos"] or [""])[0], params, f"Agregar disco de {gb} GB {desde} a {e['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/asistente", methods=["POST"])
@requiere_login
def asistente():
    """Chat del asistente: interpreta lo pedido, completa los datos y, al confirmar, crea el ticket."""
    from .asistente import responder
    d = request.get_json(silent=True) or {}
    if d.get("_csrf") != session.get("csrf"):
        return {"error": "Sesión vencida: recarga la página"}, 400
    if d.get("reiniciar"):
        session.pop("asistente", None)
        return {"mensajes": [], "estado": {}}
    autos = q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1", g.cliente["id"])
    msgs, est = responder(g.usuario, g.cliente, g.perfil, session.get("asistente") or {}, (d.get("mensaje") or "")[:600],
                          equipos_visibles(), autos)
    session["asistente"] = est
    for x in msgs:
        for t_ in x.get("lista") or []:
            t_["url"] = url_for("consola.ticket", tid=t_["id"])
        if x.get("ticket"):
            x["url"] = url_for("consola.ticket", tid=x["ticket"])
            t_ = q1("SELECT externo_ref, odoo_ref FROM tickets WHERE id=?", x["ticket"]) or {}
            x["refs"] = [r for r in ((t_.get("externo_ref") or "").split("|")[0], ("Odoo #" + t_["odoo_ref"]) if (t_.get("odoo_ref") or "").isdigit() else "") if r and r not in ("pendiente", "propio")]
    return {"mensajes": msgs, "estado": {"paso": est.get("paso", "")}}


@bp.route("/inventario/<int:eid>/disco/ampliar", methods=["POST"])
@requiere_login
def equipo_disco_ampliar(eid):
    """Ampliar un disco de la máquina: ticket con aprobación; AAP agranda el disco virtual en el anfitrión y luego crece
    la partición, el LVM y el sistema de archivos del montaje dentro de la máquina."""
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or not energia_posible(e):
        return {"error": "Solo se amplían discos de máquinas virtuales del anfitrión KVM"}, 400
    f = request.form
    disco, gb, actual = f.get("disco", "").replace("/dev/", ""), f.get("gb", ""), f.get("actual", "0")
    montaje = f.get("montaje", "").strip()
    if not re.fullmatch(r"(vd|sd|xvd|hd)[a-z]", disco) or not gb.isdigit() or not 2 <= int(gb) <= 4000:
        return {"error": "Disco o tamaño no válidos"}, 400
    try:
        if int(gb) <= float(actual):
            return {"error": f"El tamaño nuevo debe ser mayor que el actual ({actual} GB): los discos no se achican"}, 400
    except ValueError:
        pass
    if montaje and not re.fullmatch(r"/[A-Za-z0-9/_.-]{0,60}", montaje):
        return {"error": "Punto de montaje no válido"}, 400
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "FaceOps · Ampliar disco")
    if not a:
        return {"error": "Falta «FaceOps · Ampliar disco» en el catálogo (sincroniza con AAP)"}, 400
    vm = e["nombre"].split(".")[0]
    params = {"ae_equipo": vm, "ae_disco": disco, "ae_gb": gb, "ae_montaje": montaje}
    try:
        t = solicitar(g.usuario, g.cliente, a, (e["segmentos"] or [""])[0], params,
                      f"Ampliar disco {disco} de {e['nombre']} a {gb} GB" + (f" ({montaje})" if montaje else ""))
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/inventario/<int:eid>/puntos", methods=["POST"])
@requiere_login
def punto_crear(eid):
    """Crea un punto de restauración con el estado completo actual de la máquina."""
    from .puntos import crear
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    p = crear(g.cliente, e, origen="manual", usuario=(g.usuario or {}).get("nombre", ""), nota=request.form.get("nota", "")[:200])
    flash(f"Punto de restauración {p['codigo']} creado.", "ok")
    return redirect(url_for("consola.equipo", eid=eid) + "#puntos")


@bp.route("/inventario/<int:eid>/nodo/<nombre>")
@requiere_login
def nodo_ocp(eid, nombre):
    """Detalle de un nodo de un clúster OpenShift (lo que informa el clúster por su API)."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    nds = ((j(e["datos"], {}).get("ocp") or {}).get("nodos") or []) if e else []
    n = next((x for x in nds if x.get("nombre") == nombre), None)
    if not n:
        abort(404)
    return render_template("nodo.html", e=e, n=n, eq=_nodo_equipo(n))


@bp.route("/inventario/<int:eid>/namespace/<ns>")
@requiere_login
def namespace_ocp(eid, ns):
    """Detalle de un namespace de aplicaciones de un clúster OpenShift: cargas, pods, volúmenes y rutas."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    ocp = (j(e["datos"], {}).get("ocp") or {}) if e else {}
    n = next((x for x in ocp.get("namespaces_app") or [] if x.get("ns") == ns), None)
    if not n:
        abort(404)
    return render_template("namespace.html", e=e, n=n, sg=_ns_segmento(ns), recolectado=ocp.get("recolectado", ""), r=_resumen_ns(n, ocp))


def _cant(v):
    """«500m» → 0.5 cores · «512Mi» → 0.5 GB (para cuotas y requests)."""
    v = str(v or "").strip()
    try:
        if v.endswith("m"):
            return float(v[:-1]) / 1000
        for suf, f in (("Ki", 1 / 1048576), ("Mi", 1 / 1024), ("Gi", 1), ("Ti", 1024), ("K", 1e3 / 1073741824), ("M", 1e6 / 1073741824), ("G", 1e9 / 1073741824)):
            if v.endswith(suf):
                return float(v[:-len(suf)]) * f
        return float(v) if v else None
    except ValueError:
        return None


def _resumen_ns(n, ocp):
    """Vista consolidada de un namespace: salud, uso frente a lo reservado y a la cuota, cargas con sus pods, gobierno."""
    pods = n.get("l_pods") or []
    cargas = []
    for c in n.get("l_cargas") or []:
        ps = [p for p in pods if (p.get("dueno") or "") == c["n"] or (c["tipo"] == "StatefulSet" and (p.get("dueno") or "") == c["n"])]
        cpu = sum((p.get("uso") or {}).get("cpu") or 0 for p in ps)
        mem = sum((p.get("uso") or {}).get("mem_mb") or 0 for p in ps) / 1024
        img = (c.get("imagenes") or [""])[0]
        cargas.append(dict(c, pods=ps, cpu=round(cpu, 3), mem=round(mem, 2), img_corta=img.split("/")[-1][:60],
                           req_cpu=_cant((c.get("req") or {}).get("cpu")), req_mem=_cant((c.get("req") or {}).get("memory")),
                           ok=c.get("listos") == c.get("rep")))
    sueltos = [p for p in pods if not any(p in c["pods"] for c in cargas)]
    reinicios = [p for p in pods if int(p.get("reinicios") or 0) > 5]
    mal = [p for p in pods if p.get("fase") not in ("Running", "Succeeded")]
    req_cpu = sum((c["req_cpu"] or 0) * (c.get("rep") or 0) for c in cargas)
    req_mem = sum((c["req_mem"] or 0) * (c.get("rep") or 0) for c in cargas)
    cuota = {}
    for q_ in n.get("l_cuotas") or []:
        for k, v in (q_.get("hard") or {}).items():
            cuota[k] = {"hard": v, "used": (q_.get("used") or {}).get(k, "0")}
    gitops = [a for a in ((ocp.get("respaldo") or {}).get("gitops") or []) if a.get("ns_destino") == n["ns"]]
    motivos = []
    if mal:
        motivos.append((f"{len(mal)} pod(s) fuera de Running", 30))
    if [c for c in cargas if not c["ok"]]:
        motivos.append((f"{len([c for c in cargas if not c['ok']])} carga(s) sin todas sus réplicas listas", 25))
    if reinicios:
        motivos.append((f"{len(reinicios)} pod(s) con más de 5 reinicios", 10))
    if n.get("l_eventos"):
        motivos.append((f"{len(n['l_eventos'])} advertencia(s) recientes", 5))
    if not cuota:
        motivos.append(("sin ResourceQuota (T-07)", 5))
    if not n.get("limitranges"):
        motivos.append(("sin LimitRange (T-07)", 5))
    if [c for c in cargas if c["req_cpu"] is None]:
        motivos.append(("cargas sin requests de CPU declarados", 5))
    salud = max(0, 100 - sum(d for _, d in motivos))
    atencion = mal + [p for p in sueltos if p not in mal]
    return {"cargas": cargas, "sueltos": sueltos, "atencion": atencion, "reinicios": reinicios, "mal": mal, "req_cpu": round(req_cpu, 2), "req_mem": round(req_mem, 2),
            "cuota": cuota, "gitops": gitops, "motivos": motivos, "salud": salud,
            "nivel": "verde" if salud >= 85 else ("amarillo" if salud >= 60 else "rojo")}


@bp.route("/inventario/<int:eid>/puntos/<cod>")
@requiere_login
def punto(eid, cod):
    """Un punto de restauración: qué vuelve exactamente si se restaura, con el enlace a la fuente de cada eslabón."""
    from .puntos import lista as _pr_lista
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    ps = _pr_lista(g.cliente["id"], eid) if e else []
    p = next((x for x in ps if x["codigo"] == cod), None)
    if not p:
        abort(404)
    f = p["f"]
    reg = ((f.get("sistema") or {}).get("origen") or {}).get("registro") or {}
    conf = f.get("configuracion") or {}
    G = "http://x/git/"
    enlaces = {"imagen": git_publico(G + f"repositorio/-/packages/generic/{reg.get('artefacto', '')}/{reg.get('version', '')}") if reg else "",
               "rpm": git_publico(G + "repositorio/-/packages?type=rpm"),
               "rpm_paq": git_publico(G + "repositorio/-/packages?type=rpm&q="),
               "arbol": git_publico(G + f"repositorio/respaldo-servidores/src/commit/{conf.get('commit', '')}") if conf.get("commit") else "",
               "git_punto": git_publico(G + f"{__import__('app.puntos', fromlist=['x']).dueno_git()}/puntos-restauracion/src/branch/main/{e['nombre']}/{cod}.yml")}
    i_ = next(i for i, x in enumerate(ps) if x["codigo"] == cod)
    ant = ps[i_ + 1] if i_ + 1 < len(ps) else None
    fa = ant["f"] if ant else None
    k3 = lambda x: "|".join(x.split("|")[:3])
    pa = {x.split("|")[0]: x for x in ((fa or {}).get("paquetes") or {}).get("lista") or []}
    pb = {x.split("|")[0]: x for x in (f.get("paquetes") or {}).get("lista") or []}
    # sin inventario de paquetes en el punto anterior no hay con qué comparar: no se marca nada como cambiado
    paq_cambio = {n: ("nuevo" if n not in pa else "versión") for n, x in pb.items() if pa and (n not in pa or k3(pa[n]) != k3(x))}
    paq_quitados = [pa[n] for n in sorted(set(pa) - set(pb))] if pa and pb else []
    pla_ant = {x["plataforma"]: x for x in (fa or {}).get("plataformas") or []}
    app_ant = {x["aplicacion"]: x for x in (fa or {}).get("aplicaciones") or []}
    return render_template("punto.html", eslabones_ocp=__import__("app.puntos", fromlist=["x"]).ESLABONES_OCP, ant=ant, fa=fa, paq_cambio=paq_cambio, paq_quitados=paq_quitados, pla_ant=pla_ant, app_ant=app_ant, url_repo=__import__("app.paquetes", fromlist=["x"]).url_repo, e=e, p=p, enlaces=enlaces, ultimo=ps[0]["codigo"] == cod)


@bp.route("/inventario/<int:eid>/puntos/frecuencia", methods=["POST"])
@requiere_login
def punto_frecuencia(eid):
    """Cada cuánto FaceOps crea solo un punto de restauración de la máquina (cada hora, cada día o solo manual)."""
    from .puntos import FRECUENCIAS
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    fr = request.form.get("frecuencia", "")
    campo = "inventario_frecuencia" if request.form.get("que") == "inventario" else "puntos_frecuencia"
    validas = ("dia", "no") if campo == "inventario_frecuencia" else FRECUENCIAS
    if not e or fr not in validas or request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "No válido"}, 400
    d = j(q1("SELECT datos FROM equipos WHERE id=?", eid)["datos"], {})
    d[campo] = fr
    ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), eid)
    auditar(g.usuario["usuario"], g.cliente["id"], "frecuencia de " + ("inventario" if campo == "inventario_frecuencia" else "puntos de restauración"), e["nombre"], {"frecuencia": fr})
    return {"ok": True}


@bp.route("/inventario/<int:eid>/puntos/<cod>/restaurar", methods=["POST"])
@requiere_login
def punto_restaurar(eid, cod):
    """Restaurar un punto: FaceOps orquesta la vuelta del conjunto (configuración, paquetes, plataformas y aplicaciones
    del commit del punto) con un ticket con aprobación en AAP. La máquina no se toca desde FaceOps."""
    from .bd import q1 as _q1
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    p = _q1("SELECT * FROM puntos_restauracion WHERE codigo=? AND equipo_id=? AND cliente_id=?", cod, eid, g.cliente["id"]) if e else None
    if not p or request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(404)
    f = j(p["ficha"], {})
    sha = (f.get("configuracion") or {}).get("commit") or ""
    if not sha:
        flash("Ese punto no tiene configuración en Git para restaurar.", "error")
        return redirect(url_for("consola.equipo", eid=eid) + "#puntos")
    r = _ticket_respaldo(VOLVER, e, {"rec_equipo": e["nombre"], "rec_version": sha, "rec_ip": e["ip"]},
                         f"Restaurar {e['nombre']} al punto {cod} (configuración {sha[:7]}, paquetes, plataformas y aplicaciones)")
    x = r[0] if isinstance(r, tuple) else r
    try:
        x = x.get_json() if hasattr(x, "get_json") else x
        if x.get("url"):
            return redirect(x["url"])
        flash(x.get("error") or "No se pudo crear el ticket", "error")
    except Exception:  # noqa: BLE001
        pass
    return redirect(url_for("consola.equipo", eid=eid) + "#puntos")


@bp.route("/inventario/<int:eid>/recuperar", methods=["POST"])
@requiere_login
def recuperar_equipo(eid):
    """Recuperar servidor: recrea la máquina con su mismo nombre y la reconstruye desde el respaldo (ticket con aprobación)."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    r = _recursos(e)
    params = {"hab_so": "rhel9", "hab_destino": "onpremise", "hab_nombre_exacto": e["nombre"], "hab_reemplazar": True,
              "hab_nombre": "recuperado", "hab_replicas": "1", "hab_cpu": str(r["cpu"]), "hab_ram_gb": str(r["ram_gb"]),
              "hab_disco_gb": str(max(40, r["disco_gb"])), "hab_ambiente": e.get("ambiente") or "produccion",
              "rec_equipo": e["nombre"], "rec_version": request.form.get("sha", "")}
    return _ticket_respaldo(RECUPERAR, e, params, f"Recuperar {e['nombre']} desde su respaldo en Git y el registro de paquetes")


@bp.route("/inventario/<int:eid>/credencial-web", methods=["POST"])
@requiere_login
def credencial_web(eid):
    """Credencial de la consola web del equipo, leída de la bóveda (queda en la auditoría quién la vio)."""
    from .boveda import web
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    c = (web(e["nombre"], request.form.get("plataforma") or None) or web(e["nombre"])) if e else None
    if not c:
        return {"error": "No hay credencial web en la bóveda para este equipo"}, 404
    auditar(g.usuario["usuario"], g.cliente["id"], "ver credencial web (bóveda)", e["nombre"], {"sitio": c.get("sitio", "")})
    return {"usuario": c.get("usuario", ""), "clave": c.get("clave", ""), "sitio": c.get("sitio", "")}


ELIMINAR = "FaceOps · Eliminar servidor"


def eliminable(e):
    """Solo servidores creados por FaceOps (inventario «Servidores habilitados»): los nodos base del laboratorio no."""
    return "Servidores habilitados" in j(e.get("inventarios"), []) and e.get("estado") != "retirado"


@bp.route("/inventario/<int:eid>/eliminar", methods=["POST"])
@requiere_login
def eliminar_equipo(eid):
    """Eliminar servidor: ticket con aprobación en AAP (lo saca del balanceo, apaga y retira la VM; disco y Git se conservan)."""
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    eqs = equipos_visibles()
    e = next((x for x in eqs if x["id"] == eid), None)
    if not e or not eliminable(e):
        return {"error": "Solo se pueden eliminar servidores creados por FaceOps"}, 400
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], ELIMINAR)
    if not a:
        return {"error": f"Falta «{ELIMINAR}» en el catálogo (sincroniza con AAP)"}, 400
    bal = [{"nombre": x["nombre"], "ip": x["ip"]} for x in eqs if "nginx" in x["skills"] and x.get("ip") and x["id"] != e["id"]]
    params = {"el_equipo": e["nombre"], "el_ip": e["ip"], "el_balanceadores": bal, "hab_replicas_nuevas": 1}
    try:
        t = solicitar(g.usuario, g.cliente, a, (e["segmentos"] or [""])[0], params, f"Eliminar {e['nombre']} ({e['ip']})")
    except (PermissionError, ErrorAAP) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/inventario/<int:eid>/consola")
@requiere_login
def consola_equipo(eid):
    """Abre la consola (SSH) o el escritorio remoto (RDP) del equipo en el navegador, vía Guacamole."""
    from .consola import disponible, tipo_consola, url_consola
    from .inventario import sistema_eq
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or g.perfil["modo"] != "escritura" or not disponible():
        abort(403)
    tipo = tipo_consola(e, sistema_eq(e))
    if not tipo:
        abort(404)
    auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola" if tipo == "ssh" else "abrir escritorio remoto", e["nombre"],
            {"ip": e["ip"], "protocolo": tipo})
    comando = None
    ruta = request.args.get("log", "")
    if ruta and tipo == "ssh":   # solo archivos de log que la recolección encontró en la máquina
        if ruta not in {x["ruta"] for x in j(e["datos"], {}).get("logs") or []}:
            abort(404)
        import shlex
        comando = f"sudo less -R +G {shlex.quote(ruta)}; exec bash -l"
        auditar(g.usuario["usuario"], g.cliente["id"], "ver log", e["nombre"], {"archivo": ruta})
    destino = url_consola(g.usuario, e, tipo, comando)
    # Guacamole reutiliza la sesión anterior (otra máquina) y responde «no existe»: se cierra antes de abrir esta
    return f"""<!doctype html><meta charset="utf-8"><title>Abriendo {e['nombre']}…</title>
<body style="font-family:sans-serif;color:#555;padding:24px">Abriendo la consola de {e['nombre']}…
<script>(async function(){{
  const toks=[];
  for (const st of [localStorage, sessionStorage]) {{ try {{ for (const k of Object.keys(st)) if (/guac/i.test(k)) {{ toks.push(st.getItem(k)); st.removeItem(k); }} }} catch(e) {{}} }}
  for (const t of toks) {{ const v=(t||'').replace(/^"|"$/g,''); if (v && v.length < 200) {{ try {{ await fetch('/consola/api/session', {{method:'DELETE', headers:{{'Guacamole-Token': v}}}}); }} catch(e) {{}} }} }}
  location.replace({json.dumps(destino)});
}})();</script>"""


@bp.route("/inventario/<int:eid>", methods=["GET", "POST"])
@requiere_login
def equipo(eid):
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    if request.method == "POST":
        if g.perfil["modo"] != "escritura":
            abort(403)
        if request.form.get("accion") == "apagar":
            from .operacion import solicitar_apagado
            try:
                t = solicitar_apagado(g.usuario, g.cliente, e, request.form.get("motivo", "").strip())
                if request.headers.get("X-Inline"):
                    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}
                flash(f"{t['numero']}: apagado enviado a AAP; espera la aprobación", "ok")
                return redirect(url_for("consola.ticket", tid=t["id"]))
            except (PermissionError, ErrorAAP) as x:
                if request.headers.get("X-Inline"):
                    return {"error": str(x)}, 400
                flash(str(x), "error")
                return redirect(url_for("consola.equipo", eid=eid))
        manual = sorted({s.strip().lower() for s in request.form.get("skills", "").split(",") if s.strip()})
        ej("UPDATE equipos SET skills_manual=? WHERE id=?", json.dumps(manual), eid)
        auditar(g.usuario["usuario"], g.cliente["id"], "editar skills", e["nombre"], {"skills_manual": manual})
        from .inventario import calcular
        calcular(g.cliente)
        flash("Skills actualizadas; políticas y hallazgos recalculados", "ok")
        return redirect(url_for("consola.equipo", eid=eid))
    autos = autos_visibles()
    hs = [h for h in q("SELECT * FROM hallazgos WHERE equipo_id=?", eid)]
    from .operacion import aplica_a_equipo, requiere_aprobacion
    for h in hs:  # cada hallazgo trae su remediación lista para generar el ticket ahí mismo
        a = auto_sugerida(h["sugerencia"], autos)
        if a and aplica_a_equipo(g.usuario, g.cliente["id"], a, e):
            a = dict(a, campos_l=[c for c in j(a["campos"], []) if c["tipo"] not in ("equipo", "equipos")],
                     aprob=requiere_aprobacion(a, e.get("ambiente")), puede=puede_ejecutar(g.usuario, g.cliente["id"], a)[0])
        else:
            a = None
        h["auto"] = a
    acciones = []
    for a in sorted(autos, key=lambda x: (0 if x.get("demo") or x["categoria"] == "Skills" else 1, x.get("orden") or 999, x["nombre"])):
        if aplica_a_equipo(g.usuario, g.cliente["id"], a, e):
            a["campos_l"] = [c for c in j(a["campos"], []) if c["tipo"] not in ("equipo", "equipos")]
            a["aprob"] = requiere_aprobacion(a, e.get("ambiente"))
            a["puede"] = puede_ejecutar(g.usuario, g.cliente["id"], a)[0]
            acciones.append(a)
    from .consola import disponible, tipo_consola
    from .inventario import sistema_eq
    consola_tipo = tipo_consola(e, sistema_eq(e)) if disponible() and g.perfil["modo"] == "escritura" else None
    from .consola import web_de as _web_de
    _w = _web_de(e)
    web_eq = dict(_w, abrir=url_for("consola.redis_equipo", eid=e["id"]) if _w["producto"] == "Redis"
                  else _w["url"] if _w["producto"].startswith("Consola de OpenShift")  # OAuth propio: se abre directo, no por el proxy
                  else url_for("consola.web_equipo", eid=e["id"], sub="")) if _w else None
    from .boveda import web as _web_b, disponible as _boveda_ok
    if web_eq and web_eq["producto"] != "Redis" and g.perfil["modo"] == "escritura" and _boveda_ok() and _web_b(e["nombre"]):
        web_eq["credencial"] = url_for("consola.credencial_web", eid=e["id"])
    redis_eq = (url_for("consola.redis_equipo", eid=e["id"]) if ("redis" in e["skills"] or bool(_web_b(e["nombre"], "redis")))
                and not (web_eq and web_eq["producto"] == "Redis") else None)
    try:
        from .riesgo import evaluar as evaluar_riesgo
        riesgo_eq = evaluar_riesgo(g.cliente["id"], e)
    except Exception:  # noqa: BLE001 - sin la base de errores conocidos la ficha sigue
        riesgo_eq = None
    componentes = []
    if riesgo_eq:   # cada plataforma instalada por separado: sus accesos, su integración y sus errores conocidos
        from . import kb as kb_
        try:
            nombres = {p_["codigo"]: p_["nombre"] for p_ in kb_.productos()}
            integ = {x["producto"]: x for x in kb_.integraciones(g.cliente["id"]) if x["equipo"] == e["nombre"]}
        except Exception:  # noqa: BLE001
            nombres, integ = {}, {}
        for cod in riesgo_eq["productos"]:
            errs = [f for f in riesgo_eq["errores"] if f["producto"] == nombres.get(cod)]
            nom = nombres.get(cod, cod)
            acc = []
            cw = consola_plataforma(e, cod)
            if cw and g.perfil["modo"] == "escritura":
                acc.append({"t": cw["producto"], "url": url_for("consola.consola_web_plataforma", eid=e["id"], cod=cod, sub=""), "ic": "abrir", "nueva": True})
            elif cod == "openshift" and web_eq:
                acc.append({"t": "Consola de OpenShift", "url": web_eq["abrir"], "ic": "abrir", "nueva": True})
            if acc and web_eq and web_eq.get("credencial") and web_eq.get("producto", "").lower() in nom.lower():
                acc.append({"t": "Usuario y clave", "cred": web_eq["credencial"], "ic": "llave"})
            from .versiones import de as version_de
            ver = version_de(e, cod) if cod != "rhel" else None
            if ver and ver["version"] not in nom:
                nom = f"{nom} · versión {ver['version']}"
            componentes.append({"codigo": cod, "nombre": nom, "so": cod == "rhel", "errores": len(errs), "version": ver,
                                "activos": sum(1 for f in errs if f["activa"]),
                                "prevenidos": sum(1 for f in errs if f["cobertura"].get("estado") == "cubierto"),
                                "integracion": (integ.get(cod) or {}).get("estado"), "acciones": acc, "lista": errs})
        componentes.sort(key=lambda c: (not c["so"], c["nombre"]))
    from .identidad import NOMBRES_PLAT, plataformas as _plats
    for nom_ in _plats(e):   # plataformas que corre la máquina aunque la base de conocimiento aún no las tenga (p. ej. Keycloak recién incorporado)
        if not any(nom_.lower() in c["nombre"].lower() or c["nombre"].lower() in nom_.lower() for c in componentes):
            cod_ = next((k for k, v in NOMBRES_PLAT.items() if v == nom_), nom_.lower())
            cw_ = consola_plataforma(e, cod_)
            componentes.append({"codigo": cod_, "nombre": nom_, "so": False, "errores": 0, "activos": 0, "prevenidos": 0, "integracion": None,
                                "acciones": [{"t": cw_["producto"], "url": url_for("consola.consola_web_plataforma", eid=e["id"], cod=cod_, sub=""), "ic": "abrir", "nueva": True}]
                                if cw_ and g.perfil["modo"] == "escritura" else [], "lista": []})
    if not any(c["so"] for c in componentes) and (e.get("categoria") or "servidor") != "clúster openshift":
        # todas las fichas con el mismo formato: el sistema del equipo (Windows, VyOS, FortiOS…) es su primera «plataforma»
        componentes.insert(0, {"codigo": "so", "nombre": e.get("so") or (e.get("categoria") or "Sistema").capitalize(), "so": True, "sintetico": True,
                               "errores": 0, "activos": 0, "prevenidos": 0, "integracion": None, "acciones": [], "lista": []})
    if (e.get("categoria") or "") == "clúster openshift":   # OpenShift es el «sistema» del clúster (arriba, como RHEL en una máquina)
        for c in componentes:
            if c["codigo"] == "openshift":
                c["so"] = True
                c["ocp_base"] = True
    if (e.get("categoria") or "") == "clúster openshift":   # en un clúster, cada operador instalado es una plataforma
        for o_ in sorted((j(e["datos"], {}).get("ocp") or {}).get("operadores") or [], key=lambda x: (x.get("nombre") or x["n"]).lower()):
            if any(c["codigo"] == "op-" + o_["n"] for c in componentes):
                continue
            componentes.append({"codigo": "op-" + o_["n"], "nombre": o_.get("nombre") or o_["n"], "so": False, "op": o_, "errores": 0, "activos": 0,
                                "prevenidos": 0, "integracion": None, "acciones": [], "lista": [], "salud": None, "version": None})
    nodo_c = j(e["datos"], {}).get("nodo_ocp") or {}
    if nodo_c and not any(c["codigo"] == "openshift-nodo" for c in componentes):   # un nodo de OpenShift corre la plataforma OpenShift
        cl_ = q1("SELECT id, nombre, so FROM equipos WHERE id=?", nodo_c.get("cluster_id")) if nodo_c.get("cluster_id") else None
        componentes.append({"codigo": "openshift-nodo", "nombre": (cl_["so"] if cl_ and cl_["so"] else "OpenShift"), "so": False, "nodo": nodo_c,
                            "cluster": dict(cl_) if cl_ else None, "errores": 0, "activos": 0, "prevenidos": 0, "integracion": None, "lista": [],
                            "acciones": [{"t": f"Clúster {cl_['nombre']} en FaceOps", "url": url_for("consola.equipo", eid=cl_["id"]), "ic": "abrir"}] if cl_ else [],
                            "salud": None, "version": None})
    for c in componentes:   # icono para entrar a cada plataforma: consola SSH/RDP del sistema operativo, consola web, Redis…
        ent = None
        if c["so"] or (c["codigo"] == "windows" and consola_tipo):
            if consola_tipo:
                ent = {"url": url_for("consola.consola_equipo", eid=e["id"]), "t": "Consola SSH" if consola_tipo == "ssh" else "Escritorio remoto", "nueva": True,
                       "ic": consola_tipo}
        if not ent:
            a_ = next((a for a in c["acciones"] if a.get("url")), None)
            if a_:
                ent = {"url": a_["url"], "t": a_["t"], "nueva": True, "ic": "web"}
        c["entrar"] = ent
    comp_alias = []   # para clasificar cada cambio del historial: sistema operativo o la plataforma a la que pertenece
    try:
        from . import kb as kb_
        info_p = {p_["codigo"]: p_ for p_ in kb_.q("SELECT codigo, nombre, skill, detecta FROM productos")} if kb_.disponible() else {}
    except Exception:  # noqa: BLE001
        info_p = {}
    for c in componentes:
        p_ = info_p.get(c["codigo"], {})
        al = {x.lower() for x in (p_.get("detecta") or [])} | ({p_["skill"].lower()} if p_.get("skill") else set()) | {c["codigo"].lower()}
        comp_alias.append({"codigo": c["codigo"], "nombre": c["nombre"], "so": c["so"], "alias": sorted(a for a in al if len(a) >= 3)})
    def _de_comp(a):   # a qué plataforma pertenece una automatización (por su nombre); si no es de ninguna, al sistema operativo
        t = f"{a.get('nombre') or ''} {a.get('nombre_aap') or ''}".lower()
        m = [c["codigo"] for c in comp_alias if not c["so"] and any(x in t for x in c["alias"])]
        return m or [next((c["codigo"] for c in comp_alias if c["so"]), None)]
    from .inventario import segmentos_candidatos
    e["seg_cand"] = [x for x in segmentos_candidatos(e, q("SELECT * FROM segmentos WHERE cliente_id=?", g.cliente["id"])) if x not in e["segmentos"]]
    compat_all = _autos_compatibles(e)
    # plataformas que se pueden incorporar a esta máquina: automatizaciones compatibles que habilitan una skill que aún no tiene
    # plataformas en incorporación: tickets en curso sobre esta máquina que habilitan una skill que aún no tiene
    incorporando = [dict(t, nombre_auto=t["anombre"]) for t in q(
        "SELECT t.id, t.numero, t.estado, a.nombre anombre, a.habilita_skill FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
        "WHERE t.cliente_id=? AND t.limite=? AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion') AND COALESCE(a.habilita_skill,'')!='' "
        "ORDER BY t.id", g.cliente["id"], e["nombre"]) if t["habilita_skill"] not in (e.get("skills") or [])]
    desinstalando = {j(t["parametros"], {}).get("dp_familia"): t for t in q(
        "SELECT t.id, t.numero, t.estado, t.parametros FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.limite=? "
        "AND a.nombre_aap='Plataforma · Desinstalar' AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion')", g.cliente["id"], e["nombre"])}
    plat_nuevas, vistos_ = [], {t["habilita_skill"] for t in incorporando}
    d_ = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    sin_registro = (e.get("so") or "").lower().startswith(("redhat", "red hat")) and d_.get("registrado") is False   # sin repositorios de Red Hat
    reg_auto = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='DC · Registrar en Red Hat'", g.cliente["id"]) if sin_registro else None
    for _ar, autos_ in compat_all:
        for a_ in autos_:
            sk_ = a_.get("habilita_skill")
            if sk_ and sk_ not in (e.get("skills") or []) and sk_ not in vistos_:
                vistos_.add(sk_)
                faltan = [c for c in j(a_.get("campos"), []) if c.get("requerido") and c.get("defecto") in (None, "") and c["tipo"] not in ("equipo", "equipos")]
                plat_nuevas.append({"id": a_["id"], "nombre": a_["nombre"], "skill": sk_, "desc": (j(a_.get("guia"), {}).get("proposito") or a_.get("descripcion") or "")[:180],
                                    "datos": bool(faltan)})
    for c in componentes:
        c["acc"] = [a for a in acciones if c["codigo"] in _de_comp(a)]
        c["compat"] = [(n, xs) for n, xs in ((n, [a for a in autos if c["codigo"] in _de_comp(a)]) for n, autos in compat_all) if xs]
    hist_tickets = [{"numero": t["numero"], "auto": t["auto"] or "", "nombre_aap": t["nombre_aap"] or "", "estado": t["estado"], "autor": t["solicitante"] or "",
                     "fecha": t["fin"] or t["creado"] or "", "inicio": t["inicio"] or t["creado"] or "", "fin": t["fin"] or "",
                     "url": url_for("consola.ticket", tid=t["id"])}
                    for t in q("SELECT t.id, t.numero, t.estado, t.creado, t.inicio, t.fin, a.nombre auto, a.nombre_aap, u.nombre solicitante FROM tickets t "
                               "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id LEFT JOIN usuarios u ON u.id=t.usuario_id "
                               "WHERE t.cliente_id=? AND ((':' || COALESCE(t.limite,'') || ':') LIKE ? OR t.parametros LIKE ?) ORDER BY t.creado DESC LIMIT 100",
                               g.cliente["id"], f"%:{e['nombre']}:%", f'%"{e["nombre"]}"%')]
    salud_eq = None
    try:   # salud por componente y global (reemplaza el puntaje de riesgo como indicador principal)
        from . import kb as kb_
        from .salud import de_equipo as salud_de
        from .evaluacion_so import de_equipo as _ev_de
        _ext = {"cves": _cves_equipo(e), "politicas": _politicas_equipo(e),
                "eval_so": _ev_de(g.cliente["id"], e["nombre"]) if sistema_de(e) == "linux" else None}
        salud_eq = salud_de(e, riesgo_eq, kb_.q("SELECT codigo, nombre, skill, detecta FROM productos") if riesgo_eq else [], _ext)
        sc = {c["codigo"]: c for c in salud_eq["componentes"]}
        for c in componentes:
            if c.get("sintetico"):
                from .salud import componente_sistema
                c.update(componente_sistema(e, _ext))
                continue
            if c.get("op"):
                from .salud import componente_operador
                c.update(componente_operador(c["op"]))
                continue
            if c.get("nodo"):
                from .salud import componente_nodo_ocp
                c.update(componente_nodo_ocp(c["nodo"]))
                continue
            c.update({k: sc.get(c["codigo"], {}).get(k) for k in ("salud", "nivel", "motivos", "secciones", "umbral")})
    except Exception:  # noqa: BLE001
        from .salud import encendido
        salud_eq = {"estado": encendido(e), "salud": None, "nivel": "sin datos", "componentes": []}
    from .evaluacion_so import de_equipo as eval_so_de
    eval_so = eval_so_de(g.cliente["id"], e["nombre"]) if sistema_de(e) == "linux" else None
    from .informe_so import secuencia as secuencia_so_de, ultimo as informe_ultimo
    secuencia_so = secuencia_so_de(e, eval_so) if eval_so is not None else []
    try:
        informe_so = informe_ultimo(g.cliente["id"], e["nombre"])
    except Exception:  # noqa: BLE001
        informe_so = None
    from .paquetes import de_equipo as paquetes_de
    from .inventariado import antiguedad as antig_inv
    try:
        from .puntos import origenes, _nombre_vm, registro_generico
        _org = origenes(g.cliente)
        disco_origen = dict(_org.get(_nombre_vm(e, _org)) or {}) or None
        if disco_origen and disco_origen.get("sha256"):
            _gen = registro_generico().get(disco_origen["sha256"])
            if _gen:
                disco_origen["registro"] = {"artefacto": _gen[0], "version": _gen[1], "igual": True}
    except Exception:  # noqa: BLE001
        disco_origen = None
    try:
        from .puntos import lista as _pr_lista
        puntos_r = _pr_lista(g.cliente["id"], e["id"])
    except Exception:  # noqa: BLE001
        puntos_r = []
    from .puntos import frecuencia as _pr_frec
    puntos_frec = _pr_frec(e)
    from .puntos import dueno_git
    puntos_git = git_publico(f"http://x/git/{dueno_git()}/puntos-restauracion/src/branch/main/" + e["nombre"]) if puntos_r else ""
    inv_frec = j(e["datos"], {}).get("inventario_frecuencia") or "dia"
    storages = [x["nombre"] for x in equipos_visibles() if "storage-emulado" in (x.get("skills") or [])]
    # último cambio de estado de energía: el que vio FaceOps o, si no, el arranque según el tiempo encendido de la última recolección
    ult_estado = e.get("energia_cambio") or ""
    if not ult_estado and e.get("ultima_recoleccion") and (j(e["datos"], {}).get("uptime_dias") is not None):
        try:
            from datetime import datetime as _dt, timedelta as _td
            ult_estado = (_dt.fromisoformat(str(e["ultima_recoleccion"]).replace("Z", "+00:00")) - _td(days=float(j(e["datos"], {})["uptime_dias"]))).isoformat(timespec="seconds")
        except (ValueError, TypeError):
            ult_estado = ""
    monit = _monitoreo_url(e)
    return render_template("equipo.html", uso_eq=uso_equipo(e, j(e["datos"], {}), j(e["discos"], [])), ns_segmento=_ns_segmento, nodo_equipo=_nodo_equipo, servicios_de=servicios_de, logs_de=logs_de, ids_seguridad=_ids_seguridad, url_repo=__import__("app.paquetes", fromlist=["x"]).url_repo, inv_frec=inv_frec, ult_estado=ult_estado, monit=monit, pr_ult=(puntos_r[0]["f"] if puntos_r else None), reg_url=git_publico("http://x/git/repositorio/-/packages"), puntos_frec=puntos_frec, storages=storages, disco_origen=disco_origen, puntos_r=puntos_r, puntos_git=puntos_git, inv_maq=antig_inv(e), paquetes=paquetes_de(j(e["datos"], {}), e.get("skills")), servicio_estado=servicio_estado, servicios_plat=SERVICIOS_PLATAFORMA, familia=familia, hipervisor=_hipervisor(e), uso_seg=_uso_seg(e), energia_ok=energia_posible(e) and g.perfil["modo"] == "escritura", secuencia_so=secuencia_so, informe_so=informe_so, comp_alias=comp_alias, hist_tickets=hist_tickets, salud_eq=salud_eq, riesgo_eq=riesgo_eq, eval_so=eval_so, componentes=componentes, plat_nuevas=plat_nuevas, incorporando=incorporando, sin_registro=sin_registro, reg_auto=reg_auto, desinstalando=desinstalando, paquetes_plat=PAQUETES_PLATAFORMA, desinstalable=desinstalable, redis_eq=redis_eq, web_eq=web_eq, cves_eq=_cves_equipo(e), puede_eliminar=eliminable(e) and g.perfil["modo"] == "escritura", autos_compat=compat_all, pols_eq=_politicas_equipo(e), e=e, datos=j(e["datos"], {}), consola_tipo=consola_tipo, acciones=acciones, discos=j(e["discos"], []),
                           servicios=j(e["servicios"], []), grupos=j(e["grupos"], []), hallazgos=hs,
                           cumplimiento=cumplimiento(e, g.cliente["id"]),
                           manual=j(e["skills_manual"], []))


@bp.route("/segmentos")
@requiere_login
def segmentos():
    return redirect(url_for("consola.mapa"))


def _gobierno_desactivado():   # vista de Gobierno (segmentos, skills, políticas): fuera del menú por ahora
    eqs = equipos_visibles()
    segs = segmentos_visibles(g.usuario, g.cliente["id"])
    from .grupos_segmento import GRUPOS, NOMBRE, grupo_de
    for s in segs:
        s["equipos"] = [e for e in eqs if s["nombre"] in e["segmentos"]]
        s["regla_d"] = j(s["regla"], {})
        s["grupo_ef"] = grupo_de(s)
    segs.sort(key=lambda x: ([k for k, *_ in GRUPOS].index(x["grupo_ef"]), x.get("orden") or 0, x["nombre"]))
    sk = q("SELECT * FROM skills WHERE cliente_id=? ORDER BY nombre", g.cliente["id"])
    autos_por_nombre = {a["nombre_aap"]: a for a in autos_visibles()}
    for s in sk:
        s["auto"] = autos_por_nombre.get(s.get("automatizacion") or "")
        s["equipos"] = sum(1 for e in eqs if s["nombre"] in e["skills"])
        s["regla_d"] = j(s["regla"], {})
    pol = q("SELECT * FROM politicas WHERE cliente_id=? ORDER BY nombre", g.cliente["id"])
    for p in pol:
        p["aplica_d"], p["controles_d"] = j(p["aplica"], {}), j(p["controles"], [])
        p["equipos"] = sum(1 for e in eqs if any(x["id"] == p["id"] for x in politicas_de(e, g.cliente["id"])))
    from .segmentos_archivo import lista as archivados
    return render_template("segmentos.html", segmentos=segs, skills=sk, politicas=pol, archivados=archivados(g.cliente["id"]),
                           grupos=GRUPOS, nombre_grupo=NOMBRE, areas=sorted({s_.get("area") for s_ in segs if s_.get("area")}))


@bp.route("/segmentos/grupo", methods=["POST"])
@requiere_login
def segmento_grupo():
    """Cambia el grupo (entrada del menú principal) de un segmento de negocio."""
    from .grupos_segmento import NOMBRE, grupo_de, normalizar
    if g.perfil["modo"] != "escritura" or request.form.get("_csrf") != session.get("csrf"):
        abort(403)
    seg, gr = request.form.get("segmento", ""), normalizar(request.form.get("grupo"))
    gr = gr or grupo_de(q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], seg) or {})
    if not gr or not puede_ver_segmento(g.usuario, g.cliente["id"], seg):
        abort(400)
    ej("UPDATE segmentos SET grupo=?, area=? WHERE cliente_id=? AND nombre=?", gr, (request.form.get("area") or "").strip()[:60], g.cliente["id"], seg)
    auditar(g.usuario["usuario"], g.cliente["id"], "cambiar grupo de segmento", seg, {"grupo": gr})
    flash(f"«{seg}»: {NOMBRE[gr]}" + (f" · área {request.form.get('area').strip()}" if (request.form.get("area") or "").strip() else ""), "ok")
    return redirect(request.referrer or url_for("consola.mapa"))


@bp.route("/segmentos/archivar", methods=["POST"])
@requiere_login
def segmento_archivar():
    """Archiva un segmento de negocio: sale de las listas y libera sus máquinas (no elimina máquinas virtuales)."""
    from .segmentos_archivo import archivar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    nombre = request.form.get("segmento", "")
    if not puede_ver_segmento(g.usuario, g.cliente["id"], nombre):
        abort(404)
    eqs = [e["nombre"] for e in equipos_visibles() if nombre in e["segmentos"]]
    try:
        archivar(g.usuario, g.cliente, nombre, request.form.get("motivo", ""), eqs)
        flash(f"Segmento «{nombre}» archivado: {len(eqs)} máquina(s) liberada(s), sin eliminar ninguna.", "ok")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(request.referrer or url_for("consola.mapa"))
    return redirect(url_for("consola.mapa") + "#archivados")


@bp.route("/segmentos/archivados/<int:aid>/restaurar", methods=["POST"])
@requiere_login
def segmento_restaurar(aid):
    from .segmentos_archivo import restaurar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    try:
        flash(f"Segmento «{restaurar(g.usuario, g.cliente, aid)}» restaurado.", "ok")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("consola.mapa") + "#archivados")


# ------------------------------------------------------------------ mapa del ecosistema
@bp.route("/mapa")
@requiere_login
def mapa():
    """Mapa vivo: infraestructura → redes (o segmentos de negocio) → equipos, con los flujos de red y el estado de cada uno."""
    vista = request.args.get("vista", "aplicaciones")
    segmento = request.args.get("segmento", "")
    tarjetas, texto, entendido = [], request.args.get("q", "").strip(), {}
    if vista == "aplicaciones" and not segmento:
        from .topologia import resumen_segmentos
        eqs = equipos_visibles()
        hs = hallazgos_visibles(eqs)
        from .grupos_segmento import grupo_de
        grupo = request.args.get("grupo", "")
        tarjetas = resumen_segmentos(g.cliente, eqs, hs, [x for x in segmentos_visibles(g.usuario, g.cliente["id"]) if not grupo or grupo_de(x) == grupo])
        if texto:
            tarjetas, entendido = buscar_segmentos(texto, tarjetas, eqs, hs)
            if len(tarjetas) == 1 and request.args.get("ir") == "1":
                return redirect(url_for("consola.mapa", vista="aplicaciones", segmento=tarjetas[0]["nombre"]))
    seg_info = None
    if segmento:
        from .arquitectura import vigente
        seg_info = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], segmento)
        if seg_info:
            seg_info["ficha_d"] = j(seg_info.get("ficha"), {})
            arq = vigente(g.cliente["id"], segmento)
            seg_info["arq"] = {"version": arq["version"], "entrada": arq["m"].get("entrada") or {}} if arq else None
            # ambientes del segmento (pestañas): los de su arquitectura o los de sus equipos; primero producción
            orden = {"produccion": 0, "qa": 1, "desarrollo": 2}
            ambs = arq["m"]["ambientes"] if arq else sorted({e["ambiente"] for e in equipos_visibles() if segmento in e["segmentos"] and e.get("ambiente")})
            seg_info["ambientes"] = sorted(ambs, key=lambda a: (orden.get(a, 9), a)) or ["produccion"]
            seg_info["ambiente"] = request.args.get("ambiente") if request.args.get("ambiente") in seg_info["ambientes"] else seg_info["ambientes"][0]
    from .grupos_segmento import GRUPOS, NOMBRE, grupo_de
    grupo = request.args.get("grupo") or (grupo_de(seg_info) if seg_info else ("redes" if vista == "infra" else ""))
    from .segmentos_archivo import lista as archivados
    areas = sorted({x["area"] for x in q("SELECT area FROM segmentos WHERE cliente_id=? AND COALESCE(area,'')<>''", g.cliente["id"])})
    return render_template("mapa.html", vista=vista, segmento=segmento, tarjetas=tarjetas, texto=texto, ent=entendido, seg_info=seg_info,
                           grupo=grupo if grupo in NOMBRE else "", grupos=GRUPOS, nombre_grupo=NOMBRE, areas=areas,
                           archivados=archivados(g.cliente["id"]) if vista == "aplicaciones" and not segmento else [])


def buscar_segmentos(texto, tarjetas, eqs, hs):
    """Buscador inteligente de segmentos de negocio: entiende lo mismo que el del inventario (skills, ambientes, sistemas,
    equipos, IPs, hallazgos, encendidos/apagados…) y además «flujos bloqueados» y «latencia». Devuelve los segmentos que
    tienen algún componente que cumple, marcando cuáles coinciden."""
    import re
    from .buscador import buscar_equipos
    from .inventario import normal
    t = normal(texto)
    bloq = bool(re.search(r"bloquead|cerrad|sin conexion|sin comunicacion|flujos? (rot|caid)", t))
    resto = re.sub(r"(flujos?|puertos?|red)? ?(bloquead\w*|cerrad\w*|sin conexion|sin comunicacion)|\bflujos?\b|\blatencia\b", " ", t).strip()
    # el nombre del segmento también cuenta (p. ej. «pagos», «portal»)
    por_nombre = [x for x in tarjetas if resto and all(p in normal(x["nombre"] + " " + x["descripcion"]) for p in resto.split()
                                                    if len(p) > 2)]
    por_eq = defaultdict(list)
    for h in hs:
        por_eq[h["equipo_id"]].append(h)
    if por_nombre:  # «pagos», «identidades»: el nombre del segmento manda
        return [dict(x, coinciden=[]) for x in por_nombre if not bloq or x["bloqueados"]], {"segmentos": [x["nombre"] for x in por_nombre], "bloqueados": bloq}
    entendido, cumplen = buscar_equipos(resto, g.cliente["id"], eqs, por_eq) if resto else ({}, eqs)
    ids = {e["id"] for e in cumplen}
    out = []
    for x in tarjetas:
        coinciden = [e["nombre"] for e in x["componentes"] if e["id"] in ids]
        if x not in por_nombre and not coinciden:
            continue
        if len(coinciden) == len(x["componentes"]):
            coinciden = []  # coincide el segmento completo: no hace falta marcar componentes
        if bloq and not x["bloqueados"]:
            continue
        out.append(dict(x, coinciden=coinciden))
    entendido = dict(entendido or {}, bloqueados=bloq)
    return out, entendido


@bp.route("/mapa/verificar", methods=["POST"])
@requiere_login
def mapa_verificar():
    from .topologia import verificar_flujos
    if g.perfil["modo"] != "escritura" or request.form.get("_csrf") != session.get("csrf"):
        abort(403)
    try:
        f = request.form
        solo = {(f["desde"], f["hacia"])} if f.get("desde") and f.get("hacia") else None
        n, jobs = verificar_flujos(g.cliente, equipos_visibles(), g.usuario["usuario"], solo=solo)
        return {"flujos": n, "trabajos": jobs}
    except ErrorAAP as e:
        return {"error": str(e)}, 400


@bp.route("/mapa/flujo", methods=["POST"])
@requiere_login
def mapa_flujo():
    """Abre o cierra un flujo de red desde el mapa: crea el ticket de «Acceso de red» de inmediato (aprobación en AAP)."""
    from .operacion import solicitar
    f = request.form
    if f.get("_csrf") != session.get("csrf") or f.get("accion") not in ("abrir", "bloquear"):
        abort(400)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Acceso de red'", g.cliente["id"])
    if not a:
        return {"error": "Falta «Acceso de red» en el catálogo"}, 400
    params = {"red_accion": f["accion"], "origen": f.get("origen", ""), "destino": f.get("destino", ""), "puerto": f.get("puerto", "")}
    if not all(params.values()):
        return {"error": "Faltan origen, destino o puerto"}, 400
    try:
        t = solicitar(g.usuario, g.cliente, a, None, params,
                      f"Mapa: {'abrir' if f['accion'] == 'abrir' else 'cerrar'} {f.get('desde')} → {f.get('hacia')} ({params['puerto']})")
    except (PermissionError, ErrorAAP) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


REPLICAR = "FaceOps · Replicar con alta disponibilidad"
HA_PRODUCTOS = {"jboss": 8080, "nginx": 80}   # productos sin estado que se replican y balancean tal cual


def _recursos(e):
    cpu = max(1, min(8, int(e.get("cpu") or 2)))
    ram = max(2, min(16, round((e.get("ram_mb") or 4096) / 1024)))
    disco = max(20, min(200, int(max([d.get("gb", 0) for d in j(e.get("discos"), [])] or [40]))))
    return {"cpu": cpu, "ram_gb": ram, "disco_gb": disco}


def datos_ha(e, els, eqs, segmento):
    """Lo que necesita el botón «Replicar con HA» de la caja: producto, balanceador actual o candidatos para habilitarlo."""
    prod = next((p for p in HA_PRODUCTOS if p in e["skills"]), None)
    nube = "cloud" in (e.get("ubicacion") or "").lower() or "gcp" in (e.get("proveedor") or "").lower()
    if not prod or not e.get("ip") or nube or "windows" in (e.get("so") or "").lower():
        return None
    por_id = {"e:" + x["nombre"]: x for x in eqs}
    bal = None
    for el in els:  # quién le llega: un equipo Nginx que le entrega tráfico es su balanceador
        d = el["data"]
        if d.get("target") == "e:" + e["nombre"] and d.get("source") in por_id and "nginx" in por_id[d["source"]]["skills"]:
            b = por_id[d["source"]]
            bal = {"nombre": b["nombre"], "ip": b["ip"]}
            break
    cand = [{"nombre": x["nombre"], "ip": x["ip"]} for x in eqs if "nginx" in x["skills"] and x["id"] != e["id"] and x.get("ip")
            and (x.get("ambiente") or "") == (e.get("ambiente") or "") and "cloud" not in (x.get("ubicacion") or "").lower()]
    if prod == "nginx" and bal is None:
        cand = [c for c in cand if c["nombre"] != e["nombre"]]
    return {"producto": prod, "puerto": HA_PRODUCTOS[prod], "balanceador": bal, "candidatos": cand, "recursos": _recursos(e),
            "segmento": segmento, "url": url_for("consola.mapa_replicar")}


@bp.route("/mapa/replicar", methods=["POST"])
@requiere_login
def mapa_replicar():
    """Replicar con alta disponibilidad desde la caja del mapa: un ticket con aprobación en AAP antes de crear nada."""
    from .arquitectura import asignacion, vigente
    from .operacion import solicitar
    f = request.form
    if f.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], REPLICAR)
    if not a:
        return {"error": "Falta «Replicar con alta disponibilidad» en el catálogo (sincroniza con AAP)"}, 400
    eqs = equipos_visibles()
    e = next((x for x in eqs if str(x["id"]) == f.get("eid")), None)
    if not e:
        return {"error": "Equipo no encontrado"}, 404
    segmento = f.get("segmento", "")
    from .topologia import construir
    els, _ = construir(g.cliente, eqs, hallazgos_visibles(eqs), "segmento", segmento, "negocio", e.get("ambiente") or "")
    ha = datos_ha(e, els, eqs, segmento)
    if not ha:
        return {"error": "Este equipo no se puede replicar con HA (solo JBoss o Nginx en Linux on-premise)"}, 400
    n = max(1, min(3, int(f.get("replicas") or 1)))
    nuevo = None
    if not ha["balanceador"]:
        nuevo = next((c for c in ha["candidatos"] if c["nombre"] == f.get("nuevo")), None)
        if not nuevo:
            return {"error": "Elige el equipo Nginx donde se habilita el balanceo"}, 400
    # nombre de las réplicas: srv-<codigo>-<componente>-NN, así quedan solas en su segmento y componente
    base, ruta = re.sub(r"[^a-z0-9-]", "-", e["nombre"].lower())[:14] + "-ha", "/"
    arq = vigente(g.cliente["id"], segmento) if segmento else None
    if arq:
        m = arq["m"]
        asg = asignacion(m, eqs)
        comp = next((cid for cid, por in asg.items() for xs in por.values() for x in xs if x["nombre"] == e["nombre"]), None)
        if comp:
            base = f"{m['codigo']}-{comp}"[:20]
        url_e = (m.get("entrada") or {}).get("url") or ""
        ruta = "/" + url_e.split("/", 3)[3] if url_e.count("/") >= 3 else "/"
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,19}", base):
        base = "ha-" + str(e["id"])
    ya = len([x for x in eqs if x["nombre"].startswith(f"srv-{base}-")])
    r = ha["recursos"]
    params = {"hab_so": "rhel9", "hab_destino": "onpremise", "hab_nombre": base, "hab_replicas": str(min(5, ya + n)),
              "hab_cpu": str(r["cpu"]), "hab_ram_gb": str(r["ram_gb"]), "hab_disco_gb": str(r["disco_gb"]),
              "hab_ambiente": e.get("ambiente") or "desarrollo", "hab_segmento": segmento,
              "ha_origen": e["nombre"], "ha_origen_ip": e["ip"], "ha_producto": ha["producto"], "ha_puerto": ha["puerto"],
              "ha_balanceador": (ha["balanceador"] or {}).get("nombre", ""), "ha_balanceador_ip": (ha["balanceador"] or {}).get("ip", ""),
              "ha_replicas_nuevas": n, "hab_replicas_nuevas": n,
              "ha_nuevo": (nuevo or {}).get("nombre", ""), "ha_nuevo_ip": (nuevo or {}).get("ip", ""), "ha_puerto_lb": 8008, "ha_ruta": ruta}
    if ya + n > 5:
        return {"error": f"Ya hay {ya} réplicas de este componente; el máximo por solicitud es 5"}, 400
    try:
        t = solicitar(g.usuario, g.cliente, a, segmento, params,
                      f"Mapa: replicar {e['nombre']} ×{n} con HA · balanceo round robin en "
                      + (ha["balanceador"]["nombre"] if ha["balanceador"] else f"{nuevo['nombre']} (nuevo, puerto 8008)"))
    except (PermissionError, ErrorAAP) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/mapa/datos")
@requiere_login
def mapa_datos():
    from .topologia import construir
    eqs = equipos_visibles()
    amb = request.args.get("ambiente", "")
    els, miga = construir(g.cliente, eqs, hallazgos_visibles(eqs), request.args.get("nivel", "global"),
                          request.args.get("valor", ""), request.args.get("vista", "infra"), amb)
    acceso = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Acceso de red' AND visible=1", g.cliente["id"])
    # por qué está en rojo o amarillo cada máquina y con qué se repara (para resolverlo desde el mapa)
    from .operacion import aplica_a_equipo, requiere_aprobacion
    autos, por_eq_e = autos_visibles(), {e["id"]: e for e in eqs}
    ids_mapa = {el["data"].get("eid") for el in els if el["data"].get("eid")}
    por_equipo_h = defaultdict(list)
    sev_n = {"alta": 3, "media": 2, "baja": 1}
    for h in hallazgos_visibles(eqs):
        if h["equipo_id"] not in ids_mapa:
            continue
        e = por_eq_e[h["equipo_id"]]
        a = auto_sugerida(h["sugerencia"], autos)
        rem = None
        if a and g.perfil["modo"] == "escritura" and aplica_a_equipo(g.usuario, g.cliente["id"], a, e):
            req = [c for c in j(a["campos"], []) if c.get("requerido") and c["tipo"] not in ("equipo", "equipos") and c.get("defecto") in (None, "")]
            rem = {"id": a["id"], "nombre": a["nombre"], "aprob": requiere_aprobacion(a, e.get("ambiente")), "pide_datos": bool(req),
                   "url": url_for("consola.equipo", eid=e["id"]) + "#codigo"}
        por_equipo_h[h["equipo_id"]].append({"titulo": h["titulo"], "detalle": h["detalle"], "severidad": h["severidad"],
                                             "n": sev_n.get(h["severidad"], 0), "remediacion": rem,
                                             "apagar": h["sugerencia"] == "apagar" and g.perfil["modo"] == "escritura"})
    for v_ in por_equipo_h.values():
        v_.sort(key=lambda x: -x["n"])
    from .politicas_seg import tablero as tablero_pol
    _, pol_eq = tablero_pol(g.cliente["id"], eqs)
    from .cves import por_equipo as cves_por_equipo
    cve_eq = cves_por_equipo(g.cliente["id"], eqs)
    from .consola import disponible, tipo_consola, web_de
    from .inventario import sistema_eq
    consola_ok = disponible()
    replicar_ok = bool(q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], REPLICAR))
    for el in els:
        d = el["data"]
        if acceso and d.get("origen_ip") and d.get("tipo") == "flujo":
            comun = dict(aid=acceso["id"], v_origen=d["origen_ip"], v_destino=d["destino_ip"], v_puerto=d["puerto"].replace(" · ", ", "))
            if d.get("estado_flujo") != "abierto":
                d["desbloquear"] = url_for("consola.automatizacion", v_red_accion="abrir", **comun,
                                           motivo=f"Mapa: abrir {d['desde']} → {d['hacia']} ({d['puerto']})")
            if d.get("estado_flujo") != "bloqueado":
                d["bloquear"] = url_for("consola.automatizacion", v_red_accion="bloquear", **comun,
                                        motivo=f"Mapa: bloquear {d['desde']} → {d['hacia']} ({d['puerto']})")
        if d.get("eid"):
            d["url"] = url_for("consola.equipo", eid=d["eid"])
            d["acciones"] = url_for("consola.equipo", eid=d["eid"]) + "#acciones"
            d["ejecutar"] = url_for("consola.inventario") + f"/{d['eid']}/ejecutar/"
            d["apagar"] = url_for("consola.equipo", eid=d["eid"])
            d["hallazgos"] = por_equipo_h.get(d["eid"], [])
            e_ = por_eq_e.get(d["eid"])
            if e_:  # accesos: consola web (vía FaceOps) y SSH o escritorio remoto (vía Guacamole)
                w = web_de(e_)
                d["web"] = dict(w, abrir=url_for("consola.web_equipo", eid=e_["id"], sub="")) if w else None
                t_ = tipo_consola(e_, sistema_eq(e_)) if consola_ok and g.perfil["modo"] == "escritura" else None
                from .boveda import acceso as acceso_b, web as web_b, disponible as boveda_ok
                cred_ = acceso_b(e_["nombre"]) if boveda_ok() else None
                d["ssh"] = {"tipo": t_, "url": url_for("consola.consola_equipo", eid=e_["id"]),
                            "destino": (f"{(cred_ or {}).get('usuario') or 'ansible'}@{e_['ip']}" if t_ == "ssh" else ((cred_ or {}).get("usuario", "") + "@" if cred_ else "") + e_["ip"]),
                            "boveda": bool(cred_)} if t_ else None
                if d.get("web") is not None and boveda_ok() and web_b(e_["nombre"]):
                    d["web"]["credencial"] = url_for("consola.credencial_web", eid=e_["id"])
                d["politicas"] = [{"codigo": x["codigo"], "nombre": x["nombre"], "estado": x["estado"], "version": x["version"], "vigente": x["vigente"],
                                   "url": url_for("consola.politica", codigo=x["codigo"]),
                                   "solicitar": url_for("consola.politica_solicitar", codigo=x["codigo"])} for x in pol_eq.get(e_["id"], [])]
                cx = cve_eq.get(e_["id"]) or {}
                d["cves"] = dict({k: cx.get(k) for k in ("revisado", "repos", "pendientes", "criticos", "importantes", "cubiertos")},
                                 top=[x["cve"] for x in cx.get("top", [])[:3]], url=url_for("consola.equipo", eid=e_["id"]) + "#cves") if cx.get("revisado") else None
                d["eliminar"] = url_for("consola.eliminar_equipo", eid=e_["id"]) if g.perfil["modo"] == "escritura" and eliminable(e_) else None
                d["respaldo"] = {"versiones": url_for("consola.versiones_equipo", eid=e_["id"])} if "respaldo-git" in e_["skills"] else None
                d["ha"] = datos_ha(e_, els, eqs, request.args.get("valor", "") if request.args.get("nivel") == "segmento" else "") \
                    if g.perfil["modo"] == "escritura" and replicar_ok else None
    seg = None
    if request.args.get("nivel") == "segmento" and request.args.get("valor"):
        from .topologia import resumen_segmento
        seg = resumen_segmento(g.cliente, eqs, hallazgos_visibles(eqs), request.args["valor"], amb)
        seg["url"] = url_for("consola.arquitectura", segmento=seg["nombre"])
        seg["inventario"] = url_for("consola.inventario", q=seg["nombre"])
    return {"elementos": els, "miga": miga, "segmento": seg}


@bp.route("/auditoria")
@requiere_login
def auditoria():
    if not puede_aprobar(g.usuario, g.cliente["id"]):
        abort(403)
    return render_template("auditoria.html", filas=q("SELECT * FROM auditoria WHERE cliente_id=? OR cliente_id IS NULL "
                                                      "ORDER BY id DESC LIMIT 300", g.cliente["id"]))


# ------------------------------------------------------------------ arquitectura de los segmentos de negocio
def _arq_vigente(segmento):
    from .arquitectura import vigente
    if not segmento or not puede_ver_segmento(g.usuario, g.cliente["id"], segmento):
        abort(404)
    return vigente(g.cliente["id"], segmento)


def _escritura():
    """Perfil con escritura; en POST además el token CSRF (un GET que solo muestra una pantalla no lo trae)."""
    if g.perfil["modo"] != "escritura" or (request.method != "GET" and request.form.get("_csrf") != session.get("csrf")):
        abort(403)


@bp.route("/arquitectura")
@requiere_login
def arquitectura():
    """Ficha del segmento de negocio, su arquitectura aprobada y el plan contra el inventario."""
    from .arquitectura import FICHA, costo, plan, ultimo_recorrido
    segmento = request.args.get("segmento", "")
    a = _arq_vigente(segmento)
    versiones = q("SELECT id, version, estado, origen, autor, creado, aprobado_por, aprobado_en, ticket_id FROM arquitecturas "
                  "WHERE cliente_id=? AND segmento=? ORDER BY version DESC", g.cliente["id"], segmento)
    seg = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], segmento)
    eqs = equipos_visibles()
    p = plan(g.cliente, a["m"], eqs) if a else None
    return render_template("arquitectura.html", segmento=segmento, a=a, m=a["m"] if a else None, p=p, seg=seg, versiones=versiones,
                           k=costo(a["m"], eqs) if a else None,
                           FICHA=FICHA, ficha=(a["m"]["ficha"] if a else j(seg["ficha"] if seg else "{}", {})),
                           recorrido=ultimo_recorrido(g.cliente["id"], segmento),
                           puede_aprobar=puede_aprobar(g.usuario, g.cliente["id"]))


@bp.route("/arquitectura/version/<int:aid>.yml")
@requiere_login
def arquitectura_yaml(aid):
    a = q1("SELECT * FROM arquitecturas WHERE id=? AND cliente_id=?", aid, g.cliente["id"])
    if not a or not puede_ver_segmento(g.usuario, g.cliente["id"], a["segmento"]):
        abort(404)
    nombre = re.sub(r"[^a-z0-9]+", "-", normal(a["segmento"])).strip("-")
    original = request.args.get("original") and a.get("original")  # el adjunto tal como lo pidió el proyecto
    return (a["original"] if original else a["contenido"]), 200, {
        "Content-Type": "text/yaml; charset=utf-8",
        "Content-Disposition": f'attachment; filename="{nombre}-v{a["version"]}{"-solicitud" if original else ""}.yml"'}


@bp.route("/arquitectura/nueva", methods=["GET", "POST"])
@requiere_login
def arquitectura_nueva():
    """Nuevo segmento (o nueva versión) desde su arquitectura: se pega o sube el YAML, se revisa y queda como propuesta."""
    from .arquitectura import DIR, costo, leer, plan, proponer
    texto, m, errores, p, k = "", None, [], None, None
    if request.method == "POST":
        _escritura()
        f = request.files.get("archivo")
        texto = (f.read().decode("utf-8", "replace") if f and f.filename else request.form.get("texto", "")).strip()
        m, errores = leer(texto) if texto else (None, ["Pega o sube el manifiesto"])
        if m and not errores and m["segmento"] and not puede_ver_segmento(g.usuario, g.cliente["id"], m["segmento"]) \
                and q1("SELECT id FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], m["segmento"]):
            errores = ["No tienes acceso a ese segmento"]
        if m and not errores:
            eqs = equipos_visibles()
            p, k = plan(g.cliente, m, eqs), costo(m, eqs)
            if request.form.get("accion") == "proponer":
                from .solicitud_segmento import solicitar as solicitar_segmento
                try:
                    t = solicitar_segmento(g.usuario, g.cliente, texto, request.form.get("motivo", "").strip())
                    flash(f"{t['numero']}: solicitud enviada a doble aprobación en AAP (Arquitectura → Infraestructura)", "ok")
                    return redirect(url_for("consola.ticket", tid=t["id"]))
                except (ValueError, PermissionError, ErrorAAP) as e:
                    errores = [str(e)]
    elif request.args.get("desde"):
        a = _arq_vigente(request.args["desde"])
        texto = a["contenido"] if a else ""
    else:
        try:
            texto = open(f"{DIR}/ejemplo-ventas-en-linea.yml.ejemplo", encoding="utf-8").read().split("\n", 1)[1].strip()
        except (OSError, IndexError):
            texto = ""
        from .grupos_segmento import normalizar as norm_grupo
        gr = norm_grupo(request.args.get("grupo"))
        if gr and texto:
            import re as _re
            texto = _re.sub(r"(?m)^grupo:.*\n", "", texto)
            texto = _re.sub(r"(?m)^(segmento:.*)$", r"\1\ngrupo: " + gr, texto, count=1)
    return render_template("arquitectura_nueva.html", texto=texto, m=m, errores=errores, p=p, k=k)


@bp.route("/arquitectura/<int:aid>/<accion>", methods=["POST"])
@requiere_login
def arquitectura_decidir(aid, accion):
    from .arquitectura import aprobar, rechazar
    _escritura()
    if not puede_aprobar(g.usuario, g.cliente["id"]) or accion not in ("aprobar", "rechazar"):
        abort(403)
    a = q1("SELECT * FROM arquitecturas WHERE id=? AND cliente_id=?", aid, g.cliente["id"])
    if not a:
        abort(404)
    if a.get("ticket_id"):
        flash("Esta versión se aprueba en su ticket (doble aprobación en AAP)", "error")
        return redirect(url_for("consola.ticket", tid=a["ticket_id"]))
    try:
        if accion == "aprobar":
            aprobar(g.cliente, aid, g.usuario["usuario"])
            from .inventario import calcular
            calcular(g.cliente)
            flash(f"Arquitectura v{a['version']} aprobada: el segmento quedó definido por ella", "ok")
        else:
            rechazar(g.cliente, aid, g.usuario["usuario"])
            flash(f"Arquitectura v{a['version']} rechazada", "ok")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("consola.arquitectura", segmento=a["segmento"]))


@bp.route("/arquitectura/paso", methods=["POST"])
@requiere_login
def arquitectura_paso():
    """Ejecuta uno o todos los pasos pendientes del plan: crea los tickets de una vez (JSON)."""
    from .arquitectura import ejecutar_paso, plan
    _escritura()
    a = _arq_vigente(request.form.get("segmento", ""))
    if not a:
        return {"error": "El segmento no tiene una arquitectura aprobada"}, 400
    eqs = equipos_visibles()
    p = plan(g.cliente, a["m"], eqs)
    clave = request.form.get("clave", "")
    pasos = [x for x in p["pasos"] if not x.get("ticket") and x.get("posible") and x["tipo"] != "verificar"
             and (clave == "*" or x["clave"] == clave)]
    if not pasos:
        return {"error": "No hay pasos pendientes (o ya tienen ticket en curso)"}, 400
    out = []
    for x in pasos:
        try:
            t = ejecutar_paso(g.usuario, g.cliente, dict(a["m"], version=a["version"]), x, eqs)
            out.append({"clave": x["clave"], "numero": t["numero"], "estado": t["estado"], "id": t["id"],
                        "url": url_for("consola.ticket", tid=t["id"])})
        except (ValueError, PermissionError, ErrorAAP) as e:
            out.append({"clave": x["clave"], "error": str(e)})
    return {"tickets": out}


@bp.route("/arquitectura/recorrido", methods=["GET", "POST"])
@requiere_login
def arquitectura_recorrido():
    """POST: prueba el recorrido de punta a punta (entrada HTTP + cada tramo en AAP). GET: estado para refrescar."""
    from .arquitectura import plan, probar_recorrido, ultimo_recorrido
    segmento = request.values.get("segmento", "")
    a = _arq_vigente(segmento)
    if not a:
        return {"error": "El segmento no tiene una arquitectura aprobada (sin entrada ni tramos declarados)"}, 400
    eqs = equipos_visibles()
    if request.method == "POST":
        _escritura()
        probar_recorrido(g.usuario, g.cliente, a["m"], eqs)
    r = ultimo_recorrido(g.cliente["id"], segmento)
    p = plan(g.cliente, a["m"], eqs)
    tramos = [{"desde": f["desde"], "hacia": f["hacia"], "puerto": f["puerto"], "estado": f["estado"], "lat": f.get("lat", ""),
               "texto": f["texto"], "orden": f["orden"]} for f in p["flujos"] if f["estado"] != "entrada"]
    return {"cuando": r["cuando"] if r else None, "entrada": (r["r"].get("entrada") if r else None), "error": (r["r"].get("error") if r else ""),
            "decl_entrada": a["m"].get("entrada") or {}, "tramos": tramos,
            "verificando": any(t["estado"] == "verificando" for t in tramos)}


@bp.route("/arquitectura/app")
@requiere_login
def arquitectura_app():
    """Abre la aplicación del segmento a través de FaceOps (su URL de entrada vive en la red interna del cliente):
    lo que se ve es la respuesta real de la app, incluida su conexión a la base de datos."""
    import requests
    a = _arq_vigente(request.args.get("segmento", ""))
    url = ((a or {}).get("m") or {}).get("entrada", {}).get("url") if a else None
    if not url:
        abort(404)
    auditar(g.usuario["usuario"], g.cliente["id"], "abrir aplicación", a["segmento"], {"url": url})
    try:
        r = requests.get(url, timeout=8, verify=False)
    except requests.RequestException as e:
        return render_template("app_caida.html", segmento=a["segmento"], url=url, error=str(e).split(":")[0][:160]), 502
    tipo = r.headers.get("Content-Type", "text/html; charset=utf-8")
    cuerpo = r.content
    if "text/html" in tipo:  # una franja arriba deja claro que se ve a través de FaceOps
        franja = (f'<div style="font:13px Arial,sans-serif;background:#ee0000;color:#fff;padding:6px 14px">Vista a través de FaceOps · '
                  f'{a["segmento"]} · {url} · HTTP {r.status_code} en {r.elapsed.total_seconds() * 1000:.0f} ms · '
                  f'<a style="color:#fff" href="{url_for("consola.arquitectura", segmento=a["segmento"])}">volver al segmento</a></div>').encode()
        txt = cuerpo.decode(r.encoding or "utf-8", "replace")
        i = txt.lower().find("<body")
        j_ = txt.find(">", i) + 1 if i >= 0 else 0
        cuerpo = (txt[:j_] + franja.decode() + txt[j_:]).encode("utf-8")
        tipo = "text/html; charset=utf-8"
    return cuerpo, r.status_code, {"Content-Type": tipo, "Cache-Control": "no-store"}


# ------------------------------------------------------------------ políticas de seguridad (automatizaciones versionadas)
@bp.route("/politicas")
@requiere_login
def politicas():
    from .politicas_seg import CATEGORIAS, DESC_CAT, ETIQUETA, tablero
    pols, _ = tablero(g.cliente["id"], equipos_visibles())
    cat = request.args.get("categoria", "")
    conteo = {k: len([p for p in pols if p["categoria"] == k]) for k, _ in CATEGORIAS}
    total = {k: sum(p["cuenta"][k] for p in pols) for k in ETIQUETA}
    orden = [k for k, _ in CATEGORIAS]
    pols.sort(key=lambda p: (orden.index(p["categoria"]) if p["categoria"] in orden else 99, p["codigo"]))
    # cajas por categoría (todas, aunque no tengan políticas: desde cada una se agrega la primera)
    cajas = [{"codigo": k, "nombre": n, "pols": [p for p in pols if p["categoria"] == k], "desc": DESC_CAT.get(k, ""),
              "cuenta": {e: sum(p["cuenta"][e] for p in pols if p["categoria"] == k) for e in ETIQUETA}} for k, n in CATEGORIAS]
    return render_template("politicas.html", pols=[p for p in pols if not cat or p["categoria"] == cat], categorias=CATEGORIAS, cat=cat,
                           conteo=conteo, total=total, etiqueta=ETIQUETA, cajas=cajas, cat_nombre=dict(CATEGORIAS).get(cat, ""))


@bp.route("/politicas/nueva", methods=["GET", "POST"])
@requiere_login
def politica_nueva():
    """Política nueva dentro de una categoría: se escribe en YAML (con plantilla), se valida y queda en Git como versión 1."""
    import yaml
    from .politicas_seg import CATEGORIAS, PLANTILLA, codigo_nuevo, editar, una, validar, _normalizar
    if g.perfil["modo"] != "escritura":
        abort(403)
    cat = request.values.get("categoria", "")
    if cat not in dict(CATEGORIAS):
        abort(404)
    codigo = (request.form.get("codigo") or codigo_nuevo(g.cliente["id"], cat)).strip().upper()
    texto = request.form.get("texto") or yaml.safe_dump(PLANTILLA, allow_unicode=True, sort_keys=False, width=140)
    if request.method == "POST":
        if request.form.get("_csrf") != session.get("csrf"):
            abort(400)
        try:
            p = _normalizar(yaml.safe_load(texto) or {})
        except yaml.YAMLError as x:
            flash(f"YAML no válido: {str(x)[:200]}", "error")
            p = None
        if p is not None:
            p.update(codigo=codigo, categoria=cat)
            err = validar(p)
            if not re.match(r"^[A-Z]{2,4}-[0-9]{2,3}$", codigo):
                err.append("el código debe ser como CON-03")
            if una(g.cliente["id"], codigo):
                err.append(f"ya existe la política {codigo}")
            if err:
                flash("No se guardó: " + "; ".join(err), "error")
            else:
                try:
                    v = editar(g.cliente["id"], p, g.usuario["usuario"], request.form.get("motivo", "") or "política nueva en FaceOps")
                    auditar(g.usuario["usuario"], g.cliente["id"], "nueva política", codigo, {"categoria": cat, "version": v})
                    flash(f"Política {codigo} creada en Git (versión {v}); se asigna sola a los equipos que cumplen sus condiciones", "ok")
                    return redirect(url_for("consola.politica", codigo=codigo))
                except RuntimeError as x:
                    flash(str(x), "error")
    return render_template("politica_nueva.html", cat=cat, cat_nombre=dict(CATEGORIAS)[cat], codigo=codigo, texto=texto)


@bp.route("/politicas/<codigo>", methods=["GET", "POST"])
@requiere_login
def politica(codigo):
    import yaml
    from .politicas_seg import ETIQUETA, editar, historial, tablero, validar, _normalizar
    if request.method == "POST":
        if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
            abort(400)
        try:
            p = _normalizar(yaml.safe_load(request.form.get("texto", "")) or {})
        except yaml.YAMLError as x:
            flash(f"YAML no válido: {str(x)[:200]}", "error")
            return redirect(url_for("consola.politica", codigo=codigo))
        p["codigo"] = codigo
        err = validar(p)
        if err:
            flash("No se guardó: " + "; ".join(err), "error")
        else:
            try:
                v = editar(g.cliente["id"], p, g.usuario["usuario"], request.form.get("motivo", "") or "edición en FaceOps")
                auditar(g.usuario["usuario"], g.cliente["id"], "editar política", codigo, {"version": v})
                flash(f"Política {codigo} guardada en Git como versión {v}: los equipos que la tenían quedan desactualizados", "ok")
            except RuntimeError as x:
                flash(str(x), "error")
        return redirect(url_for("consola.politica", codigo=codigo))
    pols, _ = tablero(g.cliente["id"], equipos_visibles())
    p = next((x for x in pols if x["codigo"] == codigo), None)
    if not p:
        abort(404)
    texto = yaml.safe_dump({k: v for k, v in p.items() if k in ("nombre", "categoria", "severidad", "descripcion", "aplica", "reglas", "recargar")},
                           allow_unicode=True, sort_keys=False, width=140)
    return render_template("politica.html", p=p, texto=texto, historial=historial(g.cliente["id"], codigo), etiqueta=ETIQUETA)


@bp.route("/politicas/<codigo>/eliminar", methods=["POST"])
@requiere_login
def politica_eliminar(codigo):
    """Retira la política: se borra en Git (queda el historial) y deja de asignarse; lo aplicado en las máquinas se mantiene."""
    from .politicas_seg import eliminar, una
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    p = una(g.cliente["id"], codigo)
    if not p:
        abort(404)
    try:
        eliminar(g.cliente["id"], codigo, g.usuario["usuario"], request.form.get("motivo", "") or "retirada en FaceOps")
        auditar(g.usuario["usuario"], g.cliente["id"], "eliminar política", codigo, {"categoria": p.get("categoria")})
        flash(f"Política {codigo} eliminada: ya no se asigna a los equipos (su historial queda en Git)", "ok")
    except RuntimeError as x:
        flash(str(x), "error")
    return redirect(url_for("consola.politicas", categoria=p.get("categoria")))


@bp.route("/politicas/<codigo>/solicitar", methods=["POST"])
@requiere_login
def politica_solicitar(codigo):
    """Aplicar o verificar la política: en los pendientes y desactualizados, en todos o en un equipo (ticket por inventario)."""
    from .politicas_seg import solicitar as solicitar_pol, tablero
    f = request.form
    if f.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    pols, _ = tablero(g.cliente["id"], equipos_visibles())
    p = next((x for x in pols if x["codigo"] == codigo), None)
    if not p:
        abort(404)
    modo, alcance = ("verificar" if f.get("modo") == "verificar" else "aplicar"), f.get("alcance", "pendientes")
    es = [x["equipo"] for x in p["equipos"] if (alcance == "todos" or (alcance == "pendientes" and x["estado"] != "al_dia")
                                                 or (alcance.isdigit() and x["equipo"]["id"] == int(alcance)))]
    if not es:
        msg = "No hay equipos pendientes ni desactualizados para esta política"
        if request.headers.get("X-Inline"):
            return {"error": msg}, 400
        flash(msg, "info")
        return redirect(request.referrer or url_for("consola.politicas"))
    try:
        ts = solicitar_pol(g.usuario, g.cliente, p, es, modo)
    except (PermissionError, ErrorAAP) as x:
        if request.headers.get("X-Inline"):
            return {"error": str(x)}, 400
        flash(str(x), "error")
        return redirect(request.referrer or url_for("consola.politicas"))
    if request.headers.get("X-Inline"):
        return {"tickets": [{"numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])} for t in ts]}
    flash(f"{'Verificación' if modo == 'verificar' else 'Aplicación'} de {codigo} en {len(es)} equipo(s): " + ", ".join(t["numero"] for t in ts), "ok")
    return redirect(request.referrer or url_for("consola.politicas"))


# ------------------------------------------------------------------ seguridad: CVE cubiertos y pendientes por equipo
@bp.route("/seguridad/cves")
@requiere_login
def cves():
    from .compatibilidad import compatible
    from .cves import REVISAR, por_equipo, resumen
    eqs = equipos_visibles()
    a_rev = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], REVISAR)
    evaluables = [e for e in eqs if a_rev and compatible(a_rev, e)[0]]
    pe = por_equipo(g.cliente["id"], evaluables)
    vista = request.args.get("vista", "pendientes")
    pend = resumen(g.cliente["id"], evaluables, "pendiente")
    kpi = {"pendientes": len(pend), "criticos": len([x for x in pend if x["severidad"] == "crítica"]),
           "importantes": len([x for x in pend if x["severidad"] == "importante"]),
           "afectados": len([1 for x in pe.values() if x["pendientes"]]), "cubiertos": sum(x["cubiertos"] for x in pe.values()),
           "sin_evaluar": len([1 for x in pe.values() if not x["repos"]]), "equipos": len(evaluables)}
    ent_sel = request.args.get("entorno", "")
    conteo_ent = Counter(en for x in pend for en in x["entornos"])
    if ent_sel:
        pend = [x for x in pend if ent_sel in x["entornos"]]
    return render_template("cves.html", vista=vista, kpi=kpi, pend=pend, conteo_ent=conteo_ent, ent_sel=ent_sel, cub=resumen(g.cliente["id"], evaluables, "cubierto") if vista == "cubiertos" else [],
                           equipos=[(e, pe[e["id"]]) for e in evaluables])


@bp.route("/seguridad/cves/<cve>")
@requiere_login
def cve_detalle(cve):
    """Ficha de un CVE: referencia oficial (Red Hat, NVD, MITRE), entorno que impacta y las máquinas afectadas y protegidas."""
    from .compatibilidad import compatible
    from .cves import REVISAR, entorno, oficial
    if not re.match(r"^CVE-\d{4}-\d{4,7}$", cve):
        abort(404)
    eqs = {e["id"]: e for e in equipos_visibles()}
    a_rev = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], REVISAR)
    filas = q("SELECT * FROM cves_equipo WHERE cliente_id=? AND cve=?", g.cliente["id"], cve)
    if not filas:
        abort(404)
    afect, prot = {}, {}
    for r in filas:
        e = eqs.get(r["equipo_id"])
        if not e or (a_rev and not compatible(a_rev, e)[0]):
            continue
        (afect if r["estado"] == "pendiente" else prot).setdefault(e["id"], {"e": e, "paquetes": set(), "visto": r["visto"]})["paquetes"].add(r["paquete"])
    sev = filas[0]["severidad"]
    paquetes = sorted({(r["paquete"] or "").rsplit("-", 2)[0] for r in filas if r["paquete"]})
    entornos = sorted({entorno(r["paquete"]) for r in filas})
    return render_template("cve.html", cve=cve, sev=sev, paquetes=paquetes, entornos=entornos, of=oficial(cve),
                           afect=sorted(afect.values(), key=lambda x: x["e"]["nombre"]), prot=sorted(prot.values(), key=lambda x: x["e"]["nombre"]))


@bp.route("/seguridad/cves/solicitar", methods=["POST"])
@requiere_login
def cves_solicitar():
    """Revisar CVEs (lectura) o aplicar parches de seguridad (con aprobación en AAP), en todos, un equipo o los afectados por un CVE."""
    from .compatibilidad import compatible
    from .cves import PARCHAR, REVISAR, solicitar as solicitar_cve
    f = request.form
    if f.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    nombre = PARCHAR if f.get("accion") == "parchar" else REVISAR
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre)
    eqs = [e for e in equipos_visibles() if a and compatible(a, e)[0]]
    alc, cve = f.get("alcance", "todos"), f.get("cve", "")
    if alc.isdigit():
        eqs = [e for e in eqs if e["id"] == int(alc)]
    elif cve:
        ids = {r["equipo_id"] for r in q("SELECT equipo_id FROM cves_equipo WHERE cliente_id=? AND cve=? AND estado='pendiente'", g.cliente["id"], cve)}
        eqs = [e for e in eqs if e["id"] in ids]
    if not eqs:
        flash("No hay equipos compatibles para esa acción", "info")
        return redirect(request.referrer or url_for("consola.cves"))
    params = {"cves": cve} if cve else {}
    motivo = (f"Parchar {cve or 'vulnerabilidades pendientes'}" if nombre == PARCHAR else "Revisar CVE") + f" en {len(eqs)} equipo(s)"
    try:
        ts = solicitar_cve(g.usuario, g.cliente, nombre, eqs, params, motivo)
        flash(motivo + ": " + ", ".join(t["numero"] for t in ts) + (" · requiere aprobación en AAP" if nombre == PARCHAR else ""), "ok")
    except (PermissionError, ErrorAAP) as x:
        flash(str(x), "error")
    return redirect(request.referrer or url_for("consola.cves"))


# ------------------------------------------------------------------ facturación: créditos del plan, consumo y tabla de créditos
@bp.route("/facturacion")
@requiere_login
def facturacion():
    from .areas import AREAS, area_de
    from .economia import UNIDADES, estadisticas, metricas, regla, resumen
    est_t = estadisticas(g.cliente["id"])
    mes = request.args.get("mes") or datetime.now(timezone.utc).strftime("%Y-%m")
    ts = tickets_visibles(5000)
    r = resumen(g.cliente, mes, ts)
    from .creditos import TIPOS, estado as estado_creditos, movimientos
    r["cr"], r["movimientos"] = estado_creditos(g.cliente), movimientos(g.cliente["id"], mes=mes)
    meses = sorted({(t["creado"] or "")[:7] for t in ts if t["creado"]} | {mes}, reverse=True)[:12]
    tabla = []
    for a in autos_visibles() + q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=0", g.cliente["id"]):
        rg = regla(a)
        tabla.append(dict(rg, nombre=a["nombre"], nombre_aap=a["nombre_aap"], area=area_de(a), minutos=a["minutos_manual"], id=a["id"], met=metricas(a, est_t)))
    nombres = {k: n for k, n, _ in AREAS}
    tabla.sort(key=lambda x: ([k for k, _, _ in AREAS].index(x["area"]) if x["area"] in nombres else 99, -x["creditos"], x["nombre"]))
    return render_template("facturacion.html", r=r, mes=mes, meses=meses, tabla=tabla, areas=nombres, tipos_mov=TIPOS,
                           vista=request.args.get("vista", "consumo"), unidades=UNIDADES)


@bp.route("/alertas")
@requiere_login
def alertas():
    from .alertas import leer
    dias = request.args.get("dias", "7")
    dias = int(dias) if dias in ("1", "7", "30") else 7
    try:
        r = leer(g.cliente, dias)
    except ErrorAAP as e:
        r = {"alertas": [], "errores": [str(e)], "cuenta": {}}
    origen = request.args.get("origen", "")
    lista = [x for x in r["alertas"] if not origen or x["origen"].startswith(origen)]
    por_origen = {o: sum(1 for x in r["alertas"] if x["origen"].startswith(o)) for o in ("", "eda", "trabajo", "aprobacion")}
    from .alertas import clave as clave_alerta
    for x in lista:
        x["clave"] = clave_alerta(x)
    return render_template("alertas.html", r=r, lista=lista, dias=dias, origen=origen, por_origen=por_origen)


@bp.route("/alertas/eliminar", methods=["POST"])
@requiere_login
def alertas_eliminar():
    """Elimina alertas de la vista (no borra nada en AAP): si vuelve a fallar después, la alerta reaparece."""
    from .alertas import descartar
    _escritura()
    claves = [k for k in request.form.getlist("clave") if k][:500]
    descartar(g.cliente["id"], claves, g.usuario["usuario"])
    auditar(g.usuario["usuario"], g.cliente["id"], "eliminar alertas", f"{len(claves)} alerta(s)", {"claves": claves[:20]})
    flash(f"{len(claves)} alerta{'s' if len(claves) != 1 else ''} eliminada{'s' if len(claves) != 1 else ''}: si vuelven a fallar, reaparecen", "ok")
    return redirect(request.referrer or url_for("consola.alertas"))


@bp.route("/certificados")
@requiere_login
def certificados():
    from .certificados_panel import leer
    r = leer(g.cliente, equipos_visibles())
    vista = "todos"
    filas = r["filas"]
    renovar = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "Certificados · Renovar por vencer")
    emitir = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "Certificados · Emitir para servicio")
    maq_cert = sorted([e for e in equipos_visibles() if (e["categoria"] or "servidor") == "servidor" and e["ip"] and e["estado"] != "retirado"
                       and "windows" not in (e["so"] or "").lower()], key=lambda e: e["nombre"])
    return render_template("certificados.html", maq_cert=maq_cert, r=r, filas=filas, vista=vista, renovar=renovar, emitir=emitir,
                           cuenta={"servicios": sum(1 for f in r["filas"] if f["tipo"] == "servidor" and f["estado"] not in ("reemplazado", "revocado")),
                                   "historial": sum(1 for f in r["filas"] if f["estado"] in ("reemplazado", "revocado")), "todos": len(r["filas"])})


@bp.route("/certificados/<serie>")
@requiere_login
def certificado(serie):
    from .certificados_panel import leer, buscar_serie, afectadas
    eqs = equipos_visibles()
    r = leer(g.cliente, eqs)
    f = buscar_serie(r, serie)
    if not f:
        abort(404)
    renovar = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "Certificados · Renovar por vencer")
    emitir = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "Certificados · Emitir para servicio")
    return render_template("certificado.html", f=f, r=r, maquinas=afectadas(f, eqs), renovar=renovar, emitir=emitir)


@bp.route("/equipos/<int:eid>/certificados")
@requiere_login
def equipo_certificados(eid):
    from .certificados_panel import leer, de_equipo
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    try:
        r = leer(g.cliente, equipos_visibles())
    except Exception:  # noqa: BLE001
        return {"certificados": [], "error": "No se pudo leer el inventario de certificados", "solicitar": _solicitar_certificado(e)}
    return {"certificados": [{"cn": f["cn"], "servicio": f["servicio"], "estado": f["estado"], "dias": f["dias"], "nivel": f["nivel"],
                                  "url": url_for("consola.certificado", serie=f["serie"])} for f in de_equipo(r, e["nombre"])],
            "solicitar": _solicitar_certificado(e)}


def _solicitar_certificado(e):
    """Cómo pedir un certificado para este equipo: OpenShift lo emite directo (rutas *.apps); una máquina abre el formulario
    de «Emitir para servicio» (hay que decir para qué servicio y puerto). None si no aplica o el perfil no puede."""
    from .operacion import aplica_a_equipo
    if g.perfil["modo"] != "escritura":
        return None
    for nombre, directo in (("Certificados · OpenShift", True), ("Certificados · Emitir para servicio", False)):
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=? AND visible=1", g.cliente["id"], nombre)
        if a and aplica_a_equipo(g.usuario, g.cliente["id"], a, e):
            return {"nombre": nombre, "directo": directo, "url": url_for("consola.automatizacion", aid=a["id"]),
                    "ejecutar": url_for("consola.ejecutar_en_equipo", eid=e["id"], aid=a["id"]) if directo else None,
                    "detalle": "Certificado de la CA interna para las rutas *.apps del clúster (consola, OAuth y aplicaciones)" if directo
                    else "Certificado de la CA interna para un servicio de la máquina (se indica el servicio y su puerto)"}
    return None


@bp.route("/ciclo-vida")
@requiere_login
def ciclo_vida():
    from .ciclo_vida import CLASES, FASES, panel
    eqs = [dict(e, _datos=j(e["datos"], {})) for e in equipos_visibles()]
    r = panel(eqs, cliente_id=g.cliente["id"])
    fase, clase = request.args.get("fase", ""), request.args.get("clase", "")
    base = [x for x in r["grupos"] if not clase or x["clase"] == clase]
    cuenta_fase = {k: sum(1 for x in base if x["fase"] == k or (k == "riesgo" and x["riesgo"])) for k in ("riesgo", "fuera", "extendido", "mantencion", "completo", "desconocido")}
    grupos = [x for x in base if not fase or x["fase"] == fase or (fase == "riesgo" and x["riesgo"])]
    return render_template("ciclo_vida.html", r=r, grupos=grupos, fase=fase, clase=clase, CLASES=CLASES, FASES=FASES, cuenta_fase=cuenta_fase, n_base=len(base))


@bp.route("/ciclo-vida/<clase>/<producto>/<path:version>")
@requiere_login
def ciclo_vida_version(clase, producto, version):
    from .ciclo_vida import CLASES, FASES, panel, ruta_actualizacion, datos as datos_cv
    eqs = [dict(e, _datos=j(e["datos"], {})) for e in equipos_visibles()]
    r = panel(eqs, cliente_id=g.cliente["id"])
    infra = request.args.get("infra", "")
    gr = next((x for x in r["grupos"] if x["clase"] == clase and x["producto"] == producto and str(x["version"]) == version and x["infra"] == infra), None)
    if not gr:
        abort(404)
    prod = datos_cv().get(producto, {})
    fechas = (prod.get("versiones") or {}).get(gr["version"]) or {}
    return render_template("ciclo_vida_version.html", gr=gr, r=r, FASES=FASES, fechas=fechas, clase_nombre=dict(CLASES).get(clase, ""),
                           ruta=ruta_actualizacion(producto, gr["version"]),
                           otras=sorted(((k, v) for k, v in (prod.get("versiones") or {}).items()), key=lambda kv: [int(x) for x in re.findall(r"\d+", str(kv[0]))]))


@bp.route("/ciclo-vida/<clase>/<producto>/<path:version>/evaluar", methods=["POST"])
@requiere_login
def ciclo_vida_evaluar(clase, producto, version):
    """«Evaluar destino» en una caja de la ruta: un ticket por máquina del grupo con la automatización que corresponde a la
    plataforma (Leapp para RHEL, JBoss → EAP 8, versión del clúster para OpenShift o la evaluación genérica de destino)."""
    from .ciclo_vida import EVALUAR_DESTINO, panel, ruta_actualizacion
    from .operacion import solicitar_en_equipo
    _escritura()
    eqs = [dict(e, _datos=j(e["datos"], {})) for e in equipos_visibles()]
    infra = request.form.get("infra", "")
    gr = next((x for x in panel(eqs, cliente_id=g.cliente["id"])["grupos"] if x["clase"] == clase and x["producto"] == producto
               and str(x["version"]) == version and x["infra"] == infra), None)
    destino = request.form.get("destino", "")
    ruta = ruta_actualizacion(producto, version) if gr else None
    vuelta = url_for("consola.ciclo_vida_version", clase=clase, producto=producto, version=version, infra=infra or None)
    if not ruta or destino not in {x["version"] for x in ruta.get("versiones", []) if x["alcanzable"]}:
        flash("Ese destino no es alcanzable desde esta versión en un salto", "error")
        return redirect(vuelta)
    nombre, params = EVALUAR_DESTINO.get(producto, ("Ciclo de vida · Evaluar destino",
                                                    lambda v: {"cv_producto": producto, "cv_actual": version, "cv_destino": v}))
    params = params(destino)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre)
    if not a:
        flash(f"Falta «{nombre}» en el catálogo del cliente", "error")
        return redirect(vuelta)
    hechos, errores = [], []
    for e in gr["equipos"]:
        try:
            t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params, f"Evaluar {gr['nombre']} {version} → {destino} · {e['nombre']}")
            hechos.append(t["numero"])
        except (PermissionError, ErrorAAP, ValueError) as x:
            errores.append(f"{e['nombre']}: {x}")
    if hechos:
        flash(f"Evaluación de {gr['nombre']} {destino} con «{a['nombre']}»: {len(hechos)} ticket(s) · " + ", ".join(hechos), "ok")
    if errores:
        flash("No se pidió en: " + " · ".join(errores[:5]), "error")
    return redirect(vuelta)


@bp.route("/certificados/actualizar", methods=["POST"])
@requiere_login
def certificados_actualizar():
    """Pide a AAP una lectura nueva («Certificados · Inventario»); FaceOps no habla con la CA."""
    from .aap import AAP
    from .certificados_panel import INVENTARIO, _cache
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    try:
        aap = AAP.de_cliente(g.cliente)
        jt = (aap.get("job_templates/", name=INVENTARIO).get("results") or [None])[0]
        if not jt:
            raise ErrorAAP(f"No existe «{INVENTARIO}» en AAP")
        jid = aap.lanzar("job", jt["id"], {"cert_avisar": True})
        auditar(g.usuario["usuario"], g.cliente["id"], "actualizar inventario de certificados", INVENTARIO, {"job": jid})
        _cache.pop(g.cliente["id"], None)
        flash(f"Lectura pedida a AAP (trabajo {jid}): en ~1 minuto se actualiza esta vista.", "ok")
    except ErrorAAP as e:
        flash(str(e), "error")
    return redirect(url_for("consola.certificados"))


@bp.route("/manifiestos/<nombre>.yml")
@requiere_login
def manifiesto_plantilla(nombre):
    import os
    if nombre not in ("openshift",):
        abort(404)
    r = make_response(open(os.path.join(os.path.dirname(__file__), "..", "datos", f"{nombre}-manifiesto.yml"), encoding="utf-8").read())
    r.headers["Content-Type"] = "text/yaml; charset=utf-8"
    r.headers["Content-Disposition"] = f'attachment; filename="{nombre}-manifiesto.yml"'
    return r


# ------------------------------------------------------------------ infraestructuras (on-premise y nubes)
@bp.route("/infraestructuras", methods=["GET", "POST"])
@requiere_login
def infraestructuras():
    from .infraestructuras import PROVEEDORES, incorporar, lista
    if request.method == "POST":
        if g.perfil["modo"] != "escritura":
            abort(403)
        try:
            if request.form.get("tipo") == "fisico":   # servidores físicos de a uno, agrupados por fabricante
                from .infraestructuras import agregar_fisico
                n, nuevo = agregar_fisico(g.usuario, g.cliente, request.form)
                flash(f"{request.form.get('srv_nombre')} agregado a «{n}»" + (" (grupo nuevo: credencial e inventario creados en AAP)" if nuevo else ""), "ok")
                return redirect(url_for("consola.infraestructuras"))
            n = incorporar(g.usuario, g.cliente, request.form)
            flash(f"Infraestructura «{n}» incorporada: credencial e inventario creados en AAP. Usa «Probar acceso».", "ok")
        except (ValueError, ErrorAAP) as e:
            flash(str(e), "error")
        return redirect(url_for("consola.infraestructuras"))
    from .infraestructuras import AMBITOS
    from .ejecucion import plano
    eqs_ = equipos_visibles()
    infras = lista(g.cliente["id"], eqs_)
    eventos = []
    try:
        from .aap import AAP
        cli_ = AAP.de_cliente(g.cliente)
        instancias = cli_.todos("instances/")
        try:   # herramientas conectadas a AAP que mandan eventos (activaciones de Event-Driven Ansible)
            from .alertas import _eda
            from .ejecucion import fuentes_eventos
            eventos = fuentes_eventos(_eda(cli_, "activations/", page_size=200).get("results", []))
        except Exception:  # noqa: BLE001
            eventos = []
    except ErrorAAP:
        instancias = []
    pl = plano(g.cliente, eqs_, infras, instancias, eventos)
    con = request.args.get("conectar", type=int)
    inc_ = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Incorporar máquinas'", g.cliente["id"])
    from .infraestructuras import NATURALEZAS, NUBE_PUBLICA
    orden = sorted({x["centro"] for x in infras}, key=lambda c: (c == NUBE_PUBLICA, c))
    from .infraestructuras import FABRICANTES
    grupos_fis = [{"fabricante": j(x["detalle"], {}).get("fabricante", ""), "centro": x.get("centro_datos") or "", "nombre": x["nombre"]}
                  for x in q("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo='fisico'", g.cliente["id"])]
    return render_template("infraestructuras.html", fabricantes=FABRICANTES, grupos_fis=grupos_fis, inc_id=inc_["id"] if inc_ else None, pl=pl, infras=infras, proveedores=PROVEEDORES, ambitos=AMBITOS,
                           centros=orden, naturalezas=NATURALEZAS, nube_publica=NUBE_PUBLICA, boveda_ok=_boveda_disponible(),
                           nuevo=request.args.get("nuevo", ""), conectar=next((x for x in infras if x["id"] == con), None))


@bp.route("/infraestructuras/stack", methods=["GET", "POST"])
@requiere_login
def stack_tecnologico():
    """Evaluación del stack tecnológico: barrido de versiones (ticket en AAP) y nivel de obsolescencia frente al ciclo de vida."""
    from . import stack as S
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], S.AUTOMATIZACION)
    if request.method == "POST":
        _escritura()
        if not a:
            flash(f"Falta «{S.AUTOMATIZACION}» en el catálogo (sincroniza con AAP)", "error")
            return redirect(url_for("consola.stack_tecnologico"))
        from .operacion import solicitar
        try:
            t = solicitar(g.usuario, g.cliente, a, "*", {}, "Barrido del stack tecnológico y su obsolescencia")
            flash(f"{t['numero']} creado: al aprobarse barre todas las máquinas y arma el informe", "ok")
            return redirect(url_for("consola.ticket", tid=t["id"]))
        except (PermissionError, ErrorAAP, ValueError) as x:
            flash(str(x), "error")
        return redirect(url_for("consola.stack_tecnologico"))
    r = S.ultimo(g.cliente["id"])
    if not r:   # sin barrido aún: lo que FaceOps ya sabe del inventario
        r = dict(S.evaluar(g.cliente["id"], {}), preliminar=True)
    return render_template("stack.html", r=r, a=a)


@bp.route("/infraestructuras/stack/<int:sid>.pdf")
@requiere_login
def stack_pdf(sid):
    from . import stack as S
    x = q1("SELECT * FROM stack_eval WHERE id=? AND cliente_id=?", sid, g.cliente["id"])
    p = S.ruta_informe(sid) if x else None
    if not p or not os.path.exists(p):
        abort(404)
    from flask import send_file
    return send_file(p, mimetype="application/pdf", download_name=f"stack-tecnologico-{sid}.pdf")


@bp.route("/infraestructuras/arquitectura")
@requiere_login
def ecosistema():
    """Diagrama de alto nivel del ecosistema del cliente, armado desde el inventario."""
    from .ecosistema import armar
    from .infraestructuras import NUBE_PUBLICA, lista
    eqs_ = equipos_visibles()
    from .operacion import estado_servicenow
    from .flujo_arq import armar as armar_flujo
    return render_template("ecosistema.html", d=armar(eqs_, lista(g.cliente["id"], eqs_), grupo_periferico, clase_eq, NUBE_PUBLICA),
                           flujo=armar_flujo(eqs_, grupo_periferico, set(filter(None, request.args.get("plegar", "").split(",")))), sn=estado_servicenow(g.cliente))


def _boveda_disponible():
    from .boveda import disponible
    try:
        return disponible()
    except Exception:  # noqa: BLE001
        return False


@bp.route("/infraestructuras/<int:iid>/conectar", methods=["POST"])
@requiere_login
def infraestructura_conectar(iid):
    from .infraestructuras import conectar
    if g.perfil["modo"] != "escritura":
        abort(403)
    try:
        n = conectar(g.usuario, g.cliente, iid, request.form)
        flash(f"Credencial de «{n}» guardada cifrada en AAP. Ahora «Probar acceso».", "ok")
    except (ValueError, ErrorAAP) as e:
        flash(str(e), "error")
        return redirect(url_for("consola.infraestructuras", conectar=iid) + "#conectar")
    return redirect(url_for("consola.infraestructuras"))


@bp.route("/infraestructuras/<int:iid>/probar", methods=["POST"])
@requiere_login
def infraestructura_probar(iid):
    from .infraestructuras import probar
    if g.perfil["modo"] != "escritura":
        abort(403)
    try:
        st, msg = probar(g.usuario, g.cliente, iid)
        flash(f"Acceso {'correcto' if st == 'conectada' else 'con error'}: {msg}", "ok" if st == "conectada" else "error")
    except (ValueError, ErrorAAP) as e:
        flash(str(e), "error")
    return redirect(url_for("consola.infraestructuras"))


# ------------------------------------------------------------------ menú lateral (grupos desplegables, como la consola de OpenShift)
DEF_ARGS = {"vista": "aplicaciones"}


def menu_nav():
    if (g.get("perfil") or {}).get("modo") == "ejecutivo":
        return [{"t": "Resumen del contrato", "ic": "tarifas", "url": url_for("consola.resumen"), "abierto": True,
                 "on": request.endpoint == "consola.resumen", "hijos": [
                     {"t": f"Últimos {n} meses" if n > 1 else "Este mes", "url": url_for("consola.resumen", meses=n),
                      "on": request.endpoint == "consola.resumen" and request.args.get("meses", "3") == str(n)} for n in (1, 3, 6, 12)]}]
    from .politicas_seg import CATEGORIAS as CATS_POL
    activo = {"consola.arquitectura": "consola.mapa", "consola.arquitectura_nueva": "consola.mapa", "consola.equipo": "consola.inventario",
              "consola.ticket": "consola.tickets", "consola.incidente": "consola.politicas", "consola.incidente_conocido": "consola.politicas", "consola.incidentes_diagnostico": "consola.politicas", "consola.incidentes": "consola.politicas", "consola.automatizacion": "consola.automatizaciones",
              "consola.cves": "consola.politicas", "consola.politica": "consola.politicas", "consola.certificados": "consola.politicas", "consola.certificado": "consola.politicas", "consola.ciclo_vida": "consola.politicas", "consola.ciclo_vida_version": "consola.politicas", "consola.stack_tecnologico": "consola.infraestructuras", "consola.ecosistema": "consola.infraestructuras"}.get(request.endpoint, request.endpoint)
    from .areas import AREAS, area_de
    con = {area_de(a) for a in autos_visibles()}
    g_autos = [("Todas", {})] + [(n, {"area": k}) for k, n, _ in AREAS if k in con]
    g_inv = [("Todos los equipos", {}), ("Máquinas sin segmento", {"sin_segmento": "1"})]
    from .grupos_segmento import GRUPOS, grupo_de
    segs_v = segmentos_visibles(g.usuario, g.cliente["id"])
    sel = request.args.get("segmento")
    grupo_act = request.args.get("grupo") or (grupo_de(next((x for x in segs_v if x["nombre"] == sel), {})) if sel else "") \
        or ("redes" if request.args.get("vista") == "infra" else "aplicaciones")
    g_seg = [("Todos", {})]
    for gk, gn, gic, _ in GRUPOS:   # categorías dentro de Segmentos de negocio
        g_seg.append((gn, {"grupo": gk, "_titulo": True}))
        g_seg += [(x["nombre"], {"grupo": gk, "segmento": x["nombre"], "_sub": True}) for x in segs_v if grupo_de(x) == gk]
        if gk == "redes":
            g_seg.append(("Centros de datos y redes", {"grupo": gk, "vista": "infra", "_sub": True}))
    if g.perfil["modo"] == "escritura":
        g_seg.append(("+ Solicitar nuevo", {"_ep": "consola.arquitectura_nueva"}))
    secciones = [("consola.inicio", "Panel", "inicio", None),
                 ("consola.automatizaciones", "Automatizaciones", "autos", g_autos),
                 ("consola.tickets", "Tickets", "tickets", [("Todos", {}), ("En ejecución", {"estado": "en_ejecucion"}), ("Exitosos", {"estado": "exitoso"}),
                                                            ("Fallidos", {"estado": "fallido"})]),
                 ("consola.deudas", "Control de deuda", "deuda", [("Todo lo pendiente", {})]),
                 ("consola.infraestructuras", "Infraestructura", "infra", [("Visión global", {}), ("Arquitectura", {"_ep": "consola.ecosistema"}), ("Stack tecnológico", {"_ep": "consola.stack_tecnologico"})]),
                 ("consola.inventario", "Inventario", "inventario", g_inv),
                 ("consola.mapa", "Segmentos de negocio", "segmentos", g_seg),
                 ("consola.politicas", "Seguridad", "gobierno", [("Políticas", {}), ("CVEs", {"_ep": "consola.cves"}),
                                                                ("Certificados", {"_ep": "consola.certificados"}),
                                                                ("Ciclo de vida", {"_ep": "consola.ciclo_vida"}), ("Incidentes", {"_ep": "consola.incidentes"})]),
                 ("consola.alertas", "Alertas", "alertas", [("Todas", {}), ("Event-Driven Ansible", {"origen": "eda"}), ("Trabajos fallidos", {"origen": "trabajo"}),
                                                             ("Aprobaciones pendientes", {"origen": "aprobacion"})]),
                 ("consola.facturacion", "Créditos", "tarifas", [("Saldo y consumo", {}), ("Tabla de créditos", {"vista": "tabla"})])]
    out = []
    for ep, t, ic, hijos in secciones:
        it = {"t": t, "ic": ic, "url": url_for(ep), "abierto": activo == ep, "on": activo == ep, "hijos": []}
        claves = {k for _, a in (hijos or []) for k in a if not k.startswith("_")}
        actuales = {k: v for k in claves if (v := request.args.get(k)) and DEF_ARGS.get(k) != v}
        for ht, args in hijos or []:
            hep = args.get("_ep", ep)
            limpios = {k: v for k, v in args.items() if not k.startswith("_")}
            url = url_for(hep, **limpios) + ("#" + args["_ancla"] if "_ancla" in args else "")
            if hep == ep:
                on = request.endpoint == hep and "_ancla" not in args and {k: v for k, v in limpios.items() if DEF_ARGS.get(k) != v} == actuales
            else:   # hijo de otra página (p. ej. Seguridad › CVEs): calza por sus parámetros
                on = request.endpoint == hep and all(request.args.get(k) == v for k, v in limpios.items())
            it["hijos"].append({"t": ht, "url": url, "on": on, "titulo": args.get("_titulo"), "sub": args.get("_sub")})
        out.append(it)
    return out


# ------------------------------------------------------------------ incidentes: base de conocimiento por producto y versión
def _kb_contexto():
    from . import kb
    contratados = kb.productos_cliente(g.cliente["id"])
    return kb, contratados


@bp.route("/incidentes", methods=["GET", "POST"])
@requiere_login
def incidentes():
    """Errores conocidos como tickets y un buscador inteligente: se pega el log o se describe el error y aparecen los tickets
    con mayor probabilidad de solución. Cada uno trae su probabilidad de ocurrencia y sus acciones preventivas."""
    try:
        kb, contratados = _kb_contexto()
    except Exception as e:
        flash(str(e), "error")
        return render_template("incidentes.html", sin_kb=True, filas=[], texto="", buscando=False, recientes=[], prods=[], autos=[], orden="", prod_nombre={})
    if request.method == "POST":   # registrar un error conocido nuevo
        _escritura()
        f = request.form
        if f.get("producto") in contratados and f.get("titulo", "").strip():
            pid = kb.aprender(f["producto"], f["titulo"].strip(), f.get("sintomas", "")[:3000], f.get("causa", ""), f.get("solucion", ""),
                              f.get("automatizacion", ""), g.usuario["usuario"])
            if pid:
                kb.ej("UPDATE problemas SET prob_ocurrencia=%s, severidad=%s WHERE id=%s", float(f.get("ocurrencia") or 10), f.get("severidad", "media"), pid)
                auditar(g.usuario["usuario"], g.cliente["id"], "nuevo error conocido", f["titulo"], {"producto": f["producto"]})
                return redirect(url_for("consola.incidente_conocido", pid=pid))
            flash("Ya existe un error conocido con ese título en ese producto", "error")
        return redirect(url_for("consola.incidentes"))
    texto = (request.args.get("q") or "").strip()
    orden = request.args.get("orden", "ocurrencia")
    filas = kb.buscar(texto, contratados, limite=100) if texto else kb.conocidos(contratados, orden)
    if texto and len(filas) < 5:   # al escribir: también los que contienen todas las palabras (sin tildes ni mayúsculas)
        import unicodedata
        def _n(x):
            return "".join(ch for ch in unicodedata.normalize("NFD", (x or "").lower()) if unicodedata.category(ch) != "Mn")
        pal = [w for w in re.split(r"\W+", _n(texto)) if len(w) > 2]
        ya = {p_["id"] for p_ in filas}
        for p_ in kb.conocidos(contratados, orden):
            hay = _n(" ".join(str(p_.get(k) or "") for k in ("numero", "titulo", "sintomas", "causa", "solucion", "tipo", "producto_nombre")))
            if p_["id"] not in ya and pal and all(w in hay for w in pal):
                p_.update(coincidencia=50, prob_solucion=round(50 * float(p_.get("confianza") or 0.8)))
                filas.append(p_)
    _seguir_incidentes(kb)
    apl = _aplicabilidad_errores()
    for p_ in filas:   # a qué máquinas aplica y cómo está ese riesgo en el parque (semáforo)
        x_ = apl.get(p_["id"], {"maquinas": 0, "activas": 0, "prevenidas": 0})
        p_.update(apl_n=x_["maquinas"], apl_activas=x_["activas"], apl_prev=x_["prevenidas"],
                  salud="gris" if not x_["maquinas"] else ("rojo" if x_["activas"] else ("verde" if x_["prevenidas"] == x_["maquinas"] else "amarillo")))
    # solo errores de componentes presentes hoy en alguna máquina del inventario: si se desinstala, sale de la lista
    filas = [p_ for p_ in filas if p_["apl_n"]]
    tipos_err = Counter((p_.get("tipo") or "otro") for p_ in filas)
    tipo_sel = request.args.get("tipo", "")
    if tipo_sel:
        filas = [p_ for p_ in filas if (p_.get("tipo") or "otro") == tipo_sel]
    # cajas: plataformas presentes hoy en el inventario (productos y sistemas operativos), agrupadas; clic → detalle
    nombres_ = {p["codigo"]: p["nombre"] for p in kb.productos()}
    nombres_.update({"so:ubuntu": "Ubuntu", "so:windows": "Windows", "so:linux": "Linux (otras distribuciones)"})
    plat = request.args.get("plataforma", "")
    por_plat = defaultdict(list)
    for e_, r_ in _riesgo_parque():
        activos_ = {f["id"] for f in r_.get("errores", []) if f["activa"]}
        for c_ in _plataformas_de(e_, r_):
            por_plat[c_].append({"e": e_, "activos": len([f for f in r_.get("errores", []) if f["activa"] and f["producto"] == nombres_.get(c_)]),
                                 "riesgo": r_.get("riesgo", 0), "ids_activos": activos_})
    cajas = []
    for c_, eqs_ in por_plat.items():
        fs = [p_ for p_ in filas if p_.get("producto") == c_]
        grupo = next((k for k, _, cods in GRUPOS_PLATAFORMA if c_ in cods), "otros")
        cajas.append({"codigo": c_, "nombre": nombres_.get(c_, c_), "grupo": grupo, "maquinas": len(eqs_), "errores": len(fs),
                      "rojo": sum(1 for p_ in fs if p_["salud"] == "rojo"), "amarillo": sum(1 for p_ in fs if p_["salud"] == "amarillo"),
                      "activas": sum(1 for x in eqs_ if x["activos"]), "soporte": c_ in contratados})
    cajas.sort(key=lambda c: (-c["rojo"], -c["amarillo"], c["nombre"]))
    grupos = [(k, n, [c for c in cajas if c["grupo"] == k]) for k, n, _ in GRUPOS_PLATAFORMA + [("otros", "Otras plataformas", set())]]
    grupos = [x for x in grupos if x[2]]
    detalle = None
    if plat:
        filas = [p_ for p_ in filas if p_.get("producto") == plat]
        maq = sorted(por_plat.get(plat, []), key=lambda x: (-x["activos"], -x["riesgo"], x["e"]["nombre"]))
        detalle = {"codigo": plat, "nombre": nombres_.get(plat, plat), "maquinas": maq, "soporte": plat in contratados,
                   "caja": next((c for c in cajas if c["codigo"] == plat), None)}
    vista_cajas = not texto and not plat and not tipo_sel
    if request.headers.get("X-Requested-With") == "fetch":   # buscador en vivo: solo la tabla
        return render_template("incidentes.html", solo_tabla=True, filas=filas, texto=texto, buscando=bool(texto), tipos_err=tipos_err, tipo_sel=tipo_sel, sin_kb=False, prods=[1], autos=[], orden=orden,
                               grupos=grupos, vista_cajas=vista_cajas, detalle=detalle, plat=plat)
    return render_template("incidentes.html", tipos_err=tipos_err, tipo_sel=tipo_sel, sin_kb=False, filas=filas, texto=texto, buscando=bool(texto), orden=orden,
                           grupos=grupos, vista_cajas=vista_cajas, detalle=detalle, plat=plat,
                           recientes=kb.incidentes(g.cliente["id"], 15), prods=[p for p in kb.productos() if p["codigo"] in contratados],
                           prod_nombre={p["codigo"]: p["nombre"] for p in kb.productos()},
                           autos=q("SELECT nombre_aap, nombre FROM automatizaciones WHERE cliente_id=? AND visible=1 ORDER BY nombre", g.cliente["id"]))


# Agrupación de las plataformas del inventario en cajas (incidentes): producto de la base de errores conocidos → grupo
GRUPOS_PLATAFORMA = [
    ("so", "Sistemas operativos", {"rhel", "windows-server", "so:ubuntu", "so:windows", "so:linux"}),
    ("contenedores", "Contenedores y orquestación", {"openshift", "kubernetes", "podman", "docker"}),
    ("middleware", "Servidores de aplicación y web", {"jboss-eap", "iis", "nginx", "tomcat", "httpd", "apache"}),
    ("datos", "Bases de datos y caché", {"postgresql", "redis", "mysql", "mariadb", "oracle", "sqlserver", "mongodb"}),
    ("mensajeria", "Mensajería e integración", {"kafka", "activemq", "rabbitmq"}),
]


def _riesgo_parque():
    """[(equipo, evaluación de riesgo)] de las máquinas con datos: una sola pasada para cajas, aplicabilidad y detalle."""
    if "_riesgo_parque" in g:
        return g._riesgo_parque
    from .riesgo import evaluar as evaluar_riesgo
    out = []
    for e in equipos_visibles():
        if e["estado"] != "con datos":
            continue
        try:
            r = evaluar_riesgo(g.cliente["id"], e)
        except Exception:  # noqa: BLE001
            r = None
        out.append((e, r or {}))
    g._riesgo_parque = out
    return out


def _plataformas_de(e, r):
    """Códigos de plataforma del equipo: los productos de la base de errores y, si su sistema operativo no tiene producto
    propio, el sistema operativo (so:ubuntu, so:windows…) para que también aparezca su caja."""
    ps = list(r.get("productos") or [])
    so = (e.get("so") or "").lower()
    if not any(p in ("rhel", "windows-server") for p in ps) and so:
        ps.append("so:ubuntu" if "ubuntu" in so else ("so:windows" if "windows" in so else
                  ("rhel" if ("red hat" in so or so.startswith("rhel")) else "so:linux")))
    return ps


def _aplicabilidad_errores():
    """{id de error conocido: {maquinas, activas, prevenidas, equipos}} sobre las máquinas visibles del cliente."""
    out = defaultdict(lambda: {"maquinas": 0, "activas": 0, "prevenidas": 0, "equipos": []})
    for e, r in _riesgo_parque():
        for f in r.get("errores", []):
            x = out[f["id"]]
            x["maquinas"] += 1
            x["activas"] += 1 if f["activa"] else 0
            x["prevenidas"] += 1 if (f.get("cobertura") or {}).get("estado") == "cubierto" else 0
            x["equipos"].append(e["id"])
    return out


@bp.route("/incidentes/conocido/<int:pid>", methods=["GET", "POST"])
@requiere_login
def incidente_conocido(pid):
    """Ticket del error conocido: causa, solución (automatización correctiva), probabilidad de ocurrencia y acciones preventivas
    que llevan a tareas automatizadas. Desde aquí se aplica la solución en un equipo (queda la ocurrencia y su ticket)."""
    from .economia import catalogo_tareas
    from .operacion import aplica_a_equipo, solicitar, solicitar_en_equipo
    kb, contratados = _kb_contexto()
    p = kb.conocido(pid)
    if not p or p["producto"] not in contratados:
        abort(404)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], p["automatizacion"]) if p["automatizacion"] else None
    eqs = [e for e in equipos_visibles() if a and (not a["usa_limite"] or aplica_a_equipo(g.usuario, g.cliente["id"], a, e))] if a else []
    if request.method == "POST" and request.form.get("accion") == "prevenir":   # aplicar la prevención (si pide aprobación y no se da, queda como deuda)
        _escritura()
        from .operacion import solicitar_en_equipo
        ap = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", int(request.form.get("auto") or 0), g.cliente["id"])
        e = next((x for x in equipos_visibles() if x["nombre"] == request.form.get("equipo")), None)
        try:
            if not ap or not e:
                raise ValueError("Automatización o equipo no válidos")
            t = solicitar_en_equipo(g.usuario, g.cliente, ap, e, {}, f"Prevención · {p['numero']} {p['titulo']} · {e['nombre']}")
            flash(f"Prevención solicitada: «{ap['nombre']}» en {e['nombre']}" + (" · espera aprobación (si no se aprueba, queda como deuda)" if ap["requiere_aprobacion"] else ""), "ok")
        except (PermissionError, ErrorAAP, ValueError) as x:
            flash(str(x), "error")
        return redirect(url_for("consola.incidente_conocido", pid=pid))
    afectados_ids = set(_aplicabilidad_errores().get(pid, {}).get("equipos", []))
    if request.method == "POST" and request.form.get("accion") == "remediar":   # aplicar la remediación en una máquina afectada
        _escritura()
        e = next((x for x in equipos_visibles() if x["nombre"] == request.form.get("equipo") and x["id"] in afectados_ids), None)
        try:
            if not a or not e:
                raise ValueError("Sin automatización de remediación o la máquina no está afectada por este error")
            t = solicitar_en_equipo(g.usuario, g.cliente, a, e, dict(p.get("parametros") or {}), f"Remediación · {p['numero']} {p['titulo']} · {e['nombre']}")
            kb.ocurrio(pid)
            flash(f"Remediación «{a['nombre']}» solicitada en {e['nombre']} · ticket {t['numero']}"
                  + (" (espera aprobación)" if a["requiere_aprobacion"] else ""), "ok")
        except (PermissionError, ErrorAAP, ValueError) as x:
            flash(str(x), "error")
        return redirect(url_for("consola.incidente_conocido", pid=pid) + "#afectadas")
    if request.method == "POST":   # paso 1: verificar en el equipo que el incidente es este error conocido
        _escritura()
        integrados = {x["equipo"] for x in kb.integraciones(g.cliente["id"]) if x["producto"] == p["producto"] and x["estado"] == "integrado"}
        e = next((x for x in equipos_visibles() if x["nombre"] == request.form.get("equipo") and (x["nombre"] in integrados or x["id"] in afectados_ids)), None)
        if not e:
            flash(f"Elige un equipo con {p['producto_nombre']} integrado al soporte", "error")
            return redirect(url_for("consola.incidente_conocido", pid=pid))
        num = kb.numero(g.cliente["id"], g.cliente["codigo"])
        iid = kb.ocurrencia_nueva(g.cliente["id"], num, pid, e["nombre"], p["producto"], (request.form.get("texto") or p["titulo"]).strip(),
                                  g.usuario["usuario"])
        kb.ocurrio(pid)
        _verificar(kb, iid, p, e)
        return redirect(url_for("consola.incidente", iid=iid))
    tareas, comp = catalogo_tareas()
    del_cliente = {x["nombre_aap"]: x for x in q("SELECT id, nombre, nombre_aap FROM automatizaciones WHERE cliente_id=? AND visible=1", g.cliente["id"])}
    prev = []
    for x in p["preventivas"] or []:
        autos_t = [del_cliente[n] for n, cods in comp.items() if x.get("tarea") in cods and n in del_cliente]
        prev.append({"accion": x.get("accion", ""), "tarea": tareas.get(x.get("tarea"), {}).get("nombre", x.get("tarea", "")), "autos": autos_t[:4]})
    historial = kb.q("SELECT * FROM incidentes WHERE cliente_id=%s AND problema_id=%s ORDER BY creado DESC LIMIT 20", g.cliente["id"], pid)
    integrados = {x["equipo"] for x in kb.integraciones(g.cliente["id"]) if x["producto"] == p["producto"] and x["estado"] == "integrado"}
    from .deuda import cobertura
    cob = {e_: cobertura(g.cliente["id"], e_, p) for e_ in sorted(integrados)}
    # máquinas afectadas (el error aplica por producto y versión), con su estado, para actuar caso a caso
    afectadas = []
    for e_, r_ in _riesgo_parque():
        f_ = next((f for f in r_.get("errores", []) if f["id"] == pid), None)
        if f_:
            afectadas.append({"e": e_, "activa": f_["activa"], "cobertura": f_.get("cobertura") or {}, "exposicion": f_.get("exposicion", 0),
                              "remediable": bool(a) and (not a["usa_limite"] or aplica_a_equipo(g.usuario, g.cliente["id"], a, e_))})
    afectadas.sort(key=lambda x: (not x["activa"], -x["exposicion"], x["e"]["nombre"]))
    return render_template("incidente_conocido.html", cob=cob, afectadas=afectadas, p=p, a=a, eqs=sorted((x for x in equipos_visibles() if x["nombre"] in integrados), key=lambda x: x["nombre"]), prev=prev,
                           historial=historial, rango=kb._rango)


def _verificar(kb, iid, p, e):
    """Lanza la verificación con Ansible (solo lectura) de las revisiones del error conocido en el equipo."""
    from .operacion import solicitar_en_equipo
    if not p["verificacion"]:
        flash("Este error conocido no tiene verificación automática: confírmalo a mano antes de remediar", "info")
        return
    av = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Incidentes · Verificar error conocido'", g.cliente["id"])
    if not av:
        flash("Falta la automatización «Incidentes · Verificar error conocido» en el catálogo del cliente", "error")
        return
    try:
        i = kb.q1("SELECT numero FROM incidentes WHERE id=%s", iid)
        t = solicitar_en_equipo(g.usuario, g.cliente, av, e, {"kb_codigo": p["numero"], "kb_titulo": p["titulo"],
                                                              "kb_verificaciones": p["verificacion"]}, f"{i['numero']} · verificar {p['numero']}")
        kb.verificacion_lanzada(g.cliente["id"], iid, t["id"], t["numero"])
        flash(f"Verificando en {e['nombre']} con Ansible", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")


def _seguir_incidentes(kb):
    from .aap import AAP

    def artefactos(t):
        try:
            return AAP.de_cliente(g.cliente).get(f"jobs/{t['aap_job_id']}/").get("artifacts") or {}
        except ErrorAAP:
            return {}
    kb.seguir_verificaciones(g.cliente["id"], lambda tid: q1("SELECT * FROM tickets WHERE id=?", tid), artefactos)
    kb.seguir_soporte(g.cliente["id"], lambda tid: q1("SELECT * FROM tickets WHERE id=?", tid), artefactos)
    kb.seguir_tickets(g.cliente["id"], lambda tid: (q1("SELECT estado FROM tickets WHERE id=?", tid) or {}).get("estado"))


@bp.route("/incidentes/<int:iid>", methods=["GET", "POST"])
@requiere_login
def incidente(iid):
    """Ocurrencia de un error conocido: 1) Ansible verifica en el equipo, 2) si se confirma, se aplica la remediación, 3) cierre."""
    from .operacion import solicitar, solicitar_en_equipo
    kb, contratados = _kb_contexto()
    _seguir_incidentes(kb)
    i = kb.incidente(g.cliente["id"], iid)
    if not i:
        abort(404)
    p = kb.conocido(i["problema_id"]) if i["problema_id"] else None
    e = next((x for x in equipos_visibles() if x["nombre"] == i["equipo"]), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], p["automatizacion"]) if p and p["automatizacion"] else None
    if request.method == "POST":
        _escritura()
        acc = request.form.get("accion")
        if acc == "verificar" and p and e:
            _verificar(kb, iid, p, e)
        elif acc == "confirmar" and i["estado"] in ("por verificar", "no confirmado"):
            kb.ej("UPDATE incidentes SET estado='confirmado', nota=%s WHERE id=%s", f"Confirmado a mano por {g.usuario['usuario']}", iid)
        elif acc == "aplicar" and i["estado"] == "confirmado" and a:
            motivo = f"{i['numero']} · remediar {p['numero']} {p['titulo']}"
            try:
                t = solicitar_en_equipo(g.usuario, g.cliente, a, e, dict(p["parametros"] or {}), motivo) if (e and a["usa_limite"]) \
                    else solicitar(g.usuario, g.cliente, a, None, dict(p["parametros"] or {}), motivo)
                kb.solucion_lanzada(g.cliente["id"], iid, t["id"], t["numero"])
                flash(f"Remediación en curso: «{a['nombre']}»", "ok")
            except (PermissionError, ErrorAAP, ValueError) as x:
                flash(str(x), "error")
        elif acc in ("resuelto", "no resuelto"):
            kb.cerrar(g.cliente["id"], iid, acc, request.form.get("nota", ""))
        auditar(g.usuario["usuario"], g.cliente["id"], f"incidente · {acc}", i["numero"], {})
        return redirect(url_for("consola.incidente", iid=iid))
    vt = q1("SELECT * FROM tickets WHERE id=?", i["verif_ticket_id"]) if i.get("verif_ticket_id") else None
    tk = q1("SELECT * FROM tickets WHERE id=?", i["ticket_id"]) if i["ticket_id"] else None
    from .deuda import cobertura
    cob = cobertura(g.cliente["id"], i["equipo"], p) if p and i["equipo"] else None
    return render_template("incidente.html", i=i, p=p, a=a, e=e, vt=vt, tk=tk, cob=cob)


# ------------------------------------------------------------------ resumen ejecutivo del contrato (perfil ejecutivo y administradores)
@bp.route("/resumen")
@requiere_login
def resumen():
    from .resumen_ejecutivo import calcular
    n = request.args.get("meses", "3")
    n = int(n) if n in ("1", "3", "6", "12") else 3
    return render_template("resumen.html", r=calcular(g.cliente, n), n=n)


@bp.route("/resumen/reporte.csv")
@requiere_login
def resumen_csv():
    import csv
    import io
    from .resumen_ejecutivo import calcular
    n = request.args.get("meses", "3")
    n = int(n) if n in ("1", "3", "6", "12") else 3
    r = calcular(g.cliente, n)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow([f"Resumen del contrato · {g.cliente['nombre']} · {r['meses'][0]} a {r['meses'][-1]}"])
    w.writerow(["Área", "Segmentos de negocio", "Ejecuciones", "Máquinas", "Créditos", f"Costo ({r['moneda']})", "Horas liberadas",
                f"Ahorro ({r['moneda']})", "Retorno (ahorro ÷ costo)", "% del beneficio"])
    for a in r["areas"]:
        w.writerow([a["area"], ", ".join(a["segmentos"]), a["ejecuciones"], a["maquinas"], str(a["creditos"]).replace(".", ","),
                    str(a["costo"]).replace(".", ","), str(a["horas"]).replace(".", ","), str(a["ahorro"]).replace(".", ","),
                    (f"{a['retorno']:.1f}".replace(".", ",") if a["retorno"] else ""), f"{a['pct']:.1f}".replace(".", ",")])
    w.writerow([])
    w.writerow(["Cargos fijos (base del producto y Managed Service)", "", "", "", str(r["creditos_fijos"]).replace(".", ","),
                str(round(r["creditos_fijos"] * r["precio"], 2)).replace(".", ",")])
    w.writerow(["Total", "", r["exitosos"], "", str(round(r["creditos_ejecuciones"] + r["creditos_fijos"], 2)).replace(".", ","),
                str(r["costo"]).replace(".", ","), str(r["horas"]).replace(".", ","), str(r["ahorro"]).replace(".", ","),
                (f"{r['retorno']:.1f}".replace(".", ",") if r["retorno"] else "")])
    w.writerow([])
    w.writerow(["Mes", "Créditos de automatizaciones", "Créditos fijos", "Horas liberadas", f"Ahorro ({r['moneda']})"])
    for m in r["por_mes"]:
        w.writerow([m["mes"], str(m["creditos"]).replace(".", ","), str(m["fijos"]).replace(".", ","), str(m["horas"]).replace(".", ","),
                    str(m["ahorro"]).replace(".", ",")])
    auditar(g.usuario["usuario"], g.cliente["id"], "descargar reporte del contrato", "resumen", {"meses": n})
    from flask import Response
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=faceops-resumen-{g.cliente['codigo']}-{r['meses'][-1]}.csv"})



# ------------------------------------------------------------------ soporte por producto: integración y diagnóstico con Ansible
def _conf_integraciones():
    import yaml
    try:
        return (yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "integraciones.yml"))) or {}).get("productos") or {}
    except OSError:
        return {}


def _soporte(kb, contratados):
    """Por producto con soporte contratado: equipos que lo tienen y su estado de integración (sin integración no hay soporte)."""
    from .inventario import skills_de
    conf = _conf_integraciones()
    nombres = {p["codigo"]: p["nombre"] for p in kb.productos()}
    integ = {(x["producto"], x["equipo"]): x for x in kb.integraciones(g.cliente["id"])}
    out = []
    for cod in contratados:
        c = conf.get(cod)
        eqs = [e for e in equipos_visibles() if c and c.get("skill") in set(skills_de(e))] if c else []
        out.append({"codigo": cod, "nombre": nombres.get(cod, cod), "disponible": bool(c), "describe": (c or {}).get("describe", ""),
                    "equipos": [{"nombre": e["nombre"], "ip": e.get("ip"), "integ": integ.get((cod, e["nombre"]))} for e in eqs]})
    out.sort(key=lambda x: (not x["disponible"], x["nombre"]))
    return out


def _lanzar_soporte(kb, producto, equipo, cual):
    from .operacion import solicitar_en_equipo
    c = _conf_integraciones().get(producto) or {}
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], c.get(cual, ""))
    e = next((x for x in equipos_visibles() if x["nombre"] == equipo), None)
    if not a or not e:
        raise ValueError("No está disponible la automatización o el equipo")
    return solicitar_en_equipo(g.usuario, g.cliente, a, e, {}, f"Soporte {producto} · {'integración' if cual == 'integrar' else 'diagnóstico'} de {equipo}")


@bp.route("/incidentes/soporte", methods=["POST"])
@requiere_login
def incidentes_soporte():
    _escritura()
    kb, contratados = _kb_contexto()
    f = request.form
    producto, equipo, accion = f.get("producto", ""), f.get("equipo", ""), f.get("accion", "")
    if producto not in contratados:
        abort(403)
    try:
        if accion == "integrar":
            t = _lanzar_soporte(kb, producto, equipo, "integrar")
            kb.integracion_lanzada(g.cliente["id"], producto, equipo, t["id"])
            flash(f"Integrando {producto} en {equipo} con Ansible", "ok")
        elif accion == "diagnosticar":
            integ = next((x for x in kb.integraciones(g.cliente["id"]) if x["producto"] == producto and x["equipo"] == equipo and x["estado"] == "integrado"), None)
            if not integ:
                raise ValueError(f"{producto} no está integrado en {equipo}: intégralo primero")
            t = _lanzar_soporte(kb, producto, equipo, "diagnosticar")
            did = kb.diagnostico_lanzado(g.cliente["id"], producto, equipo, t["id"], g.usuario["usuario"])
            auditar(g.usuario["usuario"], g.cliente["id"], "diagnosticar producto", f"{producto} · {equipo}", {})
            return redirect(url_for("consola.incidentes_diagnostico", did=did))
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.incidentes") + "#soporte")


@bp.route("/incidentes/diagnostico/<int:did>", methods=["GET", "POST"])
@requiere_login
def incidentes_diagnostico(did):
    kb, contratados = _kb_contexto()
    _seguir_incidentes(kb)
    d = kb.diagnostico(g.cliente["id"], did)
    if not d:
        abort(404)
    if request.method == "POST":   # el diagnóstico ya es la verificación en el equipo: la ocurrencia nace confirmada
        _escritura()
        pid = int(request.form.get("problema") or 0)
        h = next((x for x in (d["hallazgos"] or []) if x["id"] == pid), None)
        p = kb.conocido(pid) if h else None
        if p:
            num = kb.numero(g.cliente["id"], g.cliente["codigo"])
            iid = kb.ocurrencia_nueva(g.cliente["id"], num, pid, d["equipo"], d["producto"],
                                      "\n".join(h["senales"] + h["lineas"]) or p["titulo"], g.usuario["usuario"])
            kb.ej("UPDATE incidentes SET estado='confirmado', verif=%s, nota=%s WHERE id=%s",
                  json.dumps({"confirmado": True, "revisiones": [{"revisar": x, "confirma": True, "salida": ""} for x in h["senales"]] +
                              [{"revisar": "Línea del log", "confirma": True, "salida": x} for x in h["lineas"]]}, ensure_ascii=False),
                  "Confirmado por el diagnóstico de Ansible", iid)
            kb.ocurrio(pid)
            return redirect(url_for("consola.incidente", iid=iid))
    tk = q1("SELECT * FROM tickets WHERE id=?", d["ticket_id"]) if d["ticket_id"] else None
    from .deuda import cobertura
    cob = {h["id"]: cobertura(g.cliente["id"], d["equipo"], kb.conocido(h["id"]) or {}) for h in (d["hallazgos"] or [])}
    return render_template("incidentes_diagnostico.html", d=d, tk=tk, cob=cob)



# ------------------------------------------------------------------ deuda de aprobación (stoppers por responsable)
@bp.route("/deudas", methods=["GET", "POST"])
@requiere_login
def deudas():
    """Tickets pendientes de aprobación (no vencen), por área: cada etapa acumula sus días al área que la tiene."""
    from . import aprobacion_areas as AA
    ts = tickets_visibles()
    if request.method == "POST":
        _escritura()
        f = request.form
        t = next((x for x in ts if x["id"] == int(f.get("ticket") or 0)), None)
        if not t or t["estado"] != "pendiente_aprobacion":
            abort(404)
        a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
        if not AA.puede_aprobar(g.usuario, g.cliente["id"], t, a):
            abort(403)
        aprobar_ = f.get("accion") == "aprobar"
        if not aprobar_ and not f.get("comentario", "").strip():
            flash("Al rechazar, deja el motivo", "error")
            return redirect(url_for("consola.deudas"))
        try:
            resolver_aprobacion(g.usuario, t["id"], aprobar_, f.get("comentario", ""))
            flash(f"{t['numero']}: {'aprobado' if aprobar_ else 'rechazado'}", "ok")
        except ErrorAAP as e:
            flash(f"AAP no aceptó la respuesta: {e}", "error")
        return redirect(url_for("consola.deudas"))
    pend = AA.pendientes(g.cliente["id"], ts)
    for p_ in pend:
        p_["area_n"] = AA.nombre(p_["area"])
        p_["puede"] = AA.puede_aprobar(g.usuario, g.cliente["id"], p_["t"])
    areas_ = [dict(x, area_n=AA.nombre(x["area"])) for x in AA.por_area(pend)]
    mi_area = AA.area_usuario(g.usuario, g.cliente["id"])
    return render_template("deudas.html", pend=pend, areas_=areas_, mi_area=AA.nombre(mi_area) if mi_area else "",
                           dias_total=int(sum(x["dias"] for x in pend)))


@bp.route("/inventario/<int:eid>/descripcion", methods=["POST"])
@requiere_login
def equipo_descripcion(eid):
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    d = (request.form.get("descripcion") or "").strip()[:120]
    ej("UPDATE equipos SET descripcion=? WHERE id=?", d, eid)
    auditar(g.usuario["usuario"], g.cliente["id"], "descripción del equipo", e["nombre"], {"descripcion": d})
    return redirect(url_for("consola.equipo", eid=eid))


def _hipervisor(e):
    """Hipervisor o plataforma donde vive la máquina (KVM, VMware, nube, OpenShift) y adónde lleva su enlace."""
    if (e.get("forma") or "") != "virtual":
        return None
    infras = q("SELECT * FROM infraestructuras WHERE cliente_id=?", g.cliente["id"])
    inf = next((x for x in infras if x["ubicacion"] == e.get("ubicacion") and x["tipo"] != "openshift"), None)
    if not inf:
        return None
    if inf["tipo"] == "kvm":   # el anfitrión KVM es también un equipo del inventario: se abre su ficha
        ip = j(inf["detalle"], {}).get("anfitrion")
        host = next((x for x in equipos_visibles() if ip and x.get("ip") == ip and x["id"] != e["id"] and (x.get("categoria") or "servidor") == "servidor"), None)
        if host:
            return {"nombre": f"{inf['nombre']} ({_cod_eq(host)})", "url": url_for("consola.equipo", eid=host["id"])}
    return {"nombre": inf["nombre"], "url": url_for("consola.infraestructuras") + f"#infra-{inf['id']}"}


def _cod_eq(x):
    from .identidad import codigo
    return codigo(x)


def _uso_seg(e):
    try:
        from .uso import por_segmento
        return por_segmento(g.cliente["id"], e)
    except Exception:  # noqa: BLE001
        return {"medido": False}


ENERGIA = "Infra · Encender o apagar equipo"


def uso_equipo(e, datos, discos):
    """Uso de CPU, memoria y discos para la cabecera. Orden: medido en la máquina → informado por el clúster OpenShift →
    EMULADO (valor estable por equipo, marcado como tal) mientras no exista la medición de fondo para ese tipo de equipo."""
    import hashlib
    h = int(hashlib.sha256(e["nombre"].encode()).hexdigest()[:8], 16)
    out = {"cpu_u": None, "carga": None, "ram_u": None, "ram_gb": None, "discos": discos, "emulado": set(), "fuente": {}}
    ocp = datos.get("ocp") or {}
    nodo = datos.get("nodo_ocp") or {}
    rec = ocp.get("recursos") or {}
    carga = datos.get("carga_1m")
    if e.get("cpu") and carga is not None:
        out.update(cpu_u=min(100, round(100 * carga / e["cpu"])), carga=carga)
    else:
        u, t = (nodo.get("cpu_uso"), nodo.get("cpu")) if nodo else (rec.get("cpu_uso"), rec.get("cpu_total"))
        try:
            out.update(cpu_u=min(100, round(100 * float(u) / float(t))), carga=f"{float(u):.2f} cores")
            out["fuente"]["cpu"] = "métricas del clúster OpenShift"
        except (TypeError, ValueError, ZeroDivisionError):
            if e.get("cpu"):
                out.update(cpu_u=12 + h % 38, carga=None)
                out["emulado"].add("cpu")
    ram_t = e.get("ram_mb") or 0
    ram_l = datos.get("ram_disponible_mb") if datos.get("ram_disponible_mb") is not None else datos.get("ram_libre_mb")
    if ram_t and ram_l is not None:
        out.update(ram_u=round(100 * (ram_t - ram_l) / ram_t), ram_gb=round((ram_t - ram_l) / 1024, 1))
    else:
        u, t = (nodo.get("memoria_uso_gb"), nodo.get("memoria_gb")) if nodo else (rec.get("memoria_uso_gb"), rec.get("memoria_total_gb"))
        try:
            out.update(ram_u=round(100 * float(u) / float(t)), ram_gb=round(float(u), 1))
            out["fuente"]["ram"] = "métricas del clúster OpenShift"
        except (TypeError, ValueError, ZeroDivisionError):
            if ram_t:
                pct = 35 + (h >> 8) % 40
                out.update(ram_u=pct, ram_gb=round(ram_t / 1024 * pct / 100, 1))
                out["emulado"].add("ram")
    if not [d for d in discos if d.get("gb")] and (e.get("forma") or "") in ("virtual", "física", "fisica", "") \
            and (e.get("categoria") or "servidor") in ("servidor", "clúster openshift", "estación de trabajo"):
        gb = 120 if nodo or ocp else 50
        out["discos"] = [{"dev": "", "gb": gb, "uso": 30 + (h >> 16) % 45, "punto": "/"}]
        out["emulado"].add("discos")
    return out


def nodos_equipo_cluster(e):
    """Las máquinas del inventario que son los nodos de un clúster OpenShift."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    return [x for x in (_nodo_equipo(n) for n in ((d.get("ocp") or {}).get("nodos") or [])) if x]


def energia_posible(e):
    """Se enciende o apaga desde FaceOps: máquinas virtuales del anfitrión KVM (no vSphere ni equipos físicos o de red).
    Un clúster OpenShift se enciende o apaga a través de sus nodos (todos deben poder)."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    if (e.get("categoria") or "") == "clúster openshift":
        ns = nodos_equipo_cluster(e)
        return bool(ns) and len(ns) == len((d.get("ocp") or {}).get("nodos") or []) and all(energia_posible(x) for x in ns)
    return (e.get("forma") or "") == "virtual" and (e.get("categoria") or "servidor") in ("servidor", "estación de trabajo") \
        and not d.get("vsphere") and "vmware" not in (e.get("ubicacion") or "").lower() and e.get("estado") != "retirado" \
        and "kvm" not in (e.get("skills") or [])   # el anfitrión de la plataforma no se apaga desde aquí


@bp.route("/inventario/<int:eid>/energia", methods=["POST"])
@requiere_login
def equipo_energia(eid):
    """Encender, apagar (ordenado) o reiniciar la VM: ticket en AAP; en producción, con aprobación."""
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    accion = request.form.get("accion", "")
    if not e or accion not in ("encender", "apagar", "reiniciar"):
        return {"error": "Equipo o acción no válidos"}, 400
    if not energia_posible(e):
        return {"error": "Este equipo no se enciende ni apaga desde FaceOps (solo máquinas virtuales del anfitrión KVM)"}, 400
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], ENERGIA)
    if not a:
        return {"error": f"Falta «{ENERGIA}» en el catálogo (sincroniza con AAP)"}, 400
    destinos = nodos_equipo_cluster(e) if (e.get("categoria") or "") == "clúster openshift" else [e]   # un clúster: cada uno de sus nodos
    try:
        for x_ in destinos:
            t = solicitar(g.usuario, g.cliente, a, (x_["segmentos"] or e["segmentos"] or [""])[0], {"en_equipo": x_["nombre"], "en_accion": accion},
                          f"{accion.capitalize()} {x_['nombre']}" + (f" (nodo del clúster {e['nombre']})" if x_ is not e else ""),
                          filtros={"ambientes": [e["ambiente"]]} if e.get("ambiente") else None)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/equipos/<int:eid>/evaluar-so", methods=["GET", "POST"])
@requiere_login
def equipo_evaluar_so(eid):
    """Evalúa ahora el sistema operativo del equipo para una posible actualización (solo lectura)."""
    from .operacion import solicitar_en_equipo
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Migración · Evaluar sistema operativo'", g.cliente["id"])
    if not e or not a:
        abort(404)
    if request.method == "GET":   # revisar antes de crear el ticket
        from .economia import estimar
        from .informe_so import secuencia
        dst = re.sub(r"[^0-9]", "", request.args.get("destino", ""))[:2]
        sec = secuencia(e)
        paso = next((p for p in sec if str(p.get("mayor")) == dst and not p.get("menor")), None) or next((p for p in sec if str(p.get("mayor")) == dst), None)
        camino = [p["version"] for p in sec if p.get("actual") or (p.get("mayor") and int(p["mayor"]) <= int(dst or 0))]
        return render_template("evaluar_so.html", e=e, a=a, guia=j(a.get("guia"), {}), destino=dst, destino_txt=(paso or {}).get("version", dst),
                               camino=camino, est=estimar(a, {}, e["nombre"]), correo=g.usuario.get("correo"))
    try:
        dst = re.sub(r"[^0-9]", "", request.form.get("destino", ""))[:2]
        params = {"mig_politica": request.form.get("politica", "minimo"), "mig_preupgrade": request.form.get("preupgrade", "false")} | ({"mig_destino": dst} if dst else {})
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params, (request.form.get("motivo") or "").strip()[:200]
                                or (f"Evaluar impacto de llevar {e['nombre']} a RHEL {dst}" if dst else f"Evaluar actualización de {e['nombre']}"))
        flash(f"Evaluando el impacto en {e['nombre']} ({t['numero']}): al terminar llega el informe PDF a tu correo y queda en esta ficha", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid) + "#p-rhel")


@bp.route("/equipos/<int:eid>/informe-so/<int:iid>.pdf")
@requiere_login
def equipo_informe_so(eid, iid):
    """Informe PDF de impacto de migración del equipo (el mismo que se envía por correo)."""
    from flask import send_file
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    r = q1("SELECT * FROM informes_so WHERE id=? AND cliente_id=?", iid, g.cliente["id"])
    if not e or not r or r["equipo"] != e["nombre"] or not os.path.exists(r["archivo"] or ""):
        abort(404)
    return send_file(r["archivo"], mimetype="application/pdf", download_name=os.path.basename(r["archivo"]).split("-", 1)[1])
