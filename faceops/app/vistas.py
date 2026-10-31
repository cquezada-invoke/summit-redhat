"""Consola FaceOps: panel, buscador, automatizaciones, tickets, inventario, segmentos y mapa."""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from . import requiere_login
from .aap import ErrorAAP
from .bd import ahora, auditar, ej, j, q, q1
from .buscador import buscar
from .inventario import cumplimiento, normal, politicas_de, recolectar, segmentos_de, sincronizar, skills_de
from .operacion import resolver_aprobacion, segmentos_aplicables, equipos_elegibles, sincronizar_catalogo, solicitar
from .permisos import equipo_visible, puede_aprobar, puede_ejecutar, puede_ver_segmento, segmentos_visibles

bp = Blueprint("consola", __name__)
SEV = {"alta": 3, "media": 2, "baja": 1}


# ------------------------------------------------------------------ tipos de periférico (agrupación del inventario)
PERIFERICOS = [("servidores", "Servidores", "servidor"), ("routers", "Routers", "router"), ("switches", "Switches", "switch"),
               ("firewalls", "Firewalls", "firewall"), ("balanceadores", "Balanceadores", "balanceador"),
               ("storage", "Storage", "almacenamiento"), ("clusters", "Clústeres OpenShift", "clúster openshift"),
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


def tickets_visibles(limite=200):
    ts = q("SELECT t.*, a.nombre auto_nombre, a.categoria, u.nombre solicitante FROM tickets t "
           "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id LEFT JOIN usuarios u ON u.id=t.usuario_id "
           "WHERE t.cliente_id=? ORDER BY t.id DESC LIMIT ?", g.cliente["id"], limite)
    return [t for t in ts if not t["segmento"] or puede_ver_segmento(g.usuario, g.cliente["id"], t["segmento"])]


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
    return {"serie": serie, "maxh": maxh, "servicios": servicios[:8], "maxs": max([s["h"] for s in servicios] + [0.1]),
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
    return render_template("inicio.html", seg=seg_, estados_pol=ESTADOS_POL, sevs=SEVS, costos_seg=costos_seg, equipos=eqs, hallazgos=hs, tickets=ts[:8], pendientes=pendientes, g_=graficos, mapa=mapa,
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
    from .compatibilidad import compatible
    return [e for e in equipos_visibles() if e["estado"] != "retirado" and compatible(a, e)[0]]


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
    if request.method == "POST":
        params, secretos = {}, {}
        validos = {e["nombre"] for e in elegibles}
        for c in campos:
            v = request.form.get("v_" + c["var"], "").strip()
            if c["tipo"] in ("multiselect", "equipos"):
                v = [x for x in request.form.getlist("v_" + c["var"]) if x]
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
    historial = q("SELECT t.*, u.nombre solicitante FROM tickets t LEFT JOIN usuarios u ON u.id=t.usuario_id "
                  "WHERE t.automatizacion_id=? ORDER BY t.id DESC LIMIT 10", aid)
    exitos = [t for t in historial if t["estado"] == "exitoso"]
    from .operacion import conf_servicenow
    sn = conf_servicenow(a["nombre_aap"]) if g.cliente.get("tickets_tipo") == "servicenow" else None
    return render_template("automatizacion.html", compat_eqs=_compatibles(a), met=__met(a), a=a, campos=campos, puede=puede, por_que=por_que, elegibles=elegibles,
                           segs_negocio=segmentos_visibles(g.usuario, g.cliente["id"]),
                           infras_cli=q("SELECT nombre, tipo, region FROM infraestructuras WHERE cliente_id=? ORDER BY id", g.cliente["id"]) if any(c["tipo"] == "infraestructura" for c in campos) else [],
                           guia=j(a.get("guia"), {}), tec=j(a.get("tecnico"), {}), sn=sn,
                           segmentos=segs, historial=historial, tasa=(len(exitos) * 100 // len(historial)) if historial else None)


# ------------------------------------------------------------------ tickets
@bp.route("/tickets")
@requiere_login
def tickets():
    ts = tickets_visibles(500)
    est = request.args.get("estado")
    if est:
        ts = [t for t in ts if t["estado"] == est]
    return render_template("tickets.html", tickets=ts, estado=est)


@bp.route("/tickets/<int:tid>", methods=["GET", "POST"])
@requiere_login
def ticket(tid):
    t = q1("SELECT t.*, u.nombre solicitante, u.usuario solicitante_usuario, ap.nombre aprobador FROM tickets t "
           "LEFT JOIN usuarios u ON u.id=t.usuario_id LEFT JOIN usuarios ap ON ap.id=t.aprobado_por "
           "WHERE t.id=? AND t.cliente_id=?", tid, g.cliente["id"])
    if not t or (t["segmento"] and not puede_ver_segmento(g.usuario, g.cliente["id"], t["segmento"])):
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
        elif not puede_aprobar(g.usuario, g.cliente["id"]):
            abort(403)
        try:
            resolver_aprobacion(g.usuario, tid, aprobar_, request.form.get("comentario", ""))
            flash("Respuesta registrada en AAP", "ok")
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
    traza = q("SELECT * FROM auditoria WHERE cliente_id=? AND objeto=? ORDER BY id", g.cliente["id"], t["numero"])
    sol = None
    aprobar_ok = puede_aprobar(g.usuario, g.cliente["id"]) and t["estado"] == "pendiente_aprobacion" and bool(t["aap_aprobacion_id"])
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
    return render_template("ticket.html", t=t, a=a, url_aap=url_aap, url_aprob=url_aprob, url_sn=url_sn, traza=traza,
                           bitacora=bitacora, web_aap=web_aap, sol=sol, aprobar=aprobar_ok, validacion=validacion_de(t["id"]),
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
    tipos_todos = Counter(grupo_periferico(e) for e in equipos_visibles() if (not fi_ or e["ubicacion"] == fi_)
                          and (not f_amb or ambito_eq(e, amb_ubi) == f_amb) and (not f_clase or clase_eq(e) == f_clase))
    eqs = [e for e in eqs if (not f_amb or ambito_eq(e, amb_ubi) == f_amb) and (not f_clase or clase_eq(e) == f_clase)]
    if filtros["tipo"]:
        eqs = [e for e in eqs if grupo_periferico(e) == filtros["tipo"]]
    if filtros["infra"]:
        eqs = [e for e in eqs if e["ubicacion"] == filtros["infra"]]
    if filtros["segmento"]:
        eqs = [e for e in eqs if filtros["segmento"] in e["segmentos"]]
    if filtros["skill"]:
        eqs = [e for e in eqs if filtros["skill"] in e["skills"]]
    if filtros["ambiente"]:
        eqs = [e for e in eqs if e["ambiente"] == filtros["ambiente"]]
    if filtros["estado"] == "con-hallazgos":
        eqs = [e for e in eqs if por_eq.get(e["id"])]
    todos = equipos_visibles()
    from .consola import disponible, tipo_consola
    from .inventario import sistema_eq
    for e in eqs:
        e["consola"] = tipo_consola(e, sistema_eq(e)) if disponible() and g.perfil["modo"] == "escritura" else None
    from .infraestructuras import lista as lista_infra
    infras = lista_infra(g.cliente["id"], todos)
    for e in eqs:
        e["ambito"], e["clase"] = ambito_eq(e, amb_ubi), clase_eq(e)
    cuenta_amb = Counter(ambito_eq(e, amb_ubi) for e in todos)
    cuenta_clase = Counter(clase_eq(e) for e in todos if not f_amb or ambito_eq(e, amb_ubi) == f_amb)
    return render_template("inventario.html", infras=infras, f_amb=f_amb, f_clase=f_clase, cuenta_amb=cuenta_amb, cuenta_clase=cuenta_clase,
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


@bp.route("/inventario/<int:eid>/web/", defaults={"sub": ""}, methods=["GET", "POST"])
@bp.route("/inventario/<int:eid>/web/<path:sub>", methods=["GET", "POST"])
@requiere_login
def web_equipo(eid, sub):
    """Consola web del equipo a través de FaceOps: proxy simple a su IP interna (reescribe enlaces y redirecciones)."""
    import re
    import requests
    from .consola import web_de
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    w = web_de(e) if e else None
    if not w:
        abort(404)
    if w["producto"] == "Redis":
        return redirect(url_for("consola.redis_equipo", eid=eid))
    base = w["url"].split("/", 3)
    origen = "/".join(base[:3])                      # esquema://ip:puerto
    prefijo = url_for("consola.web_equipo", eid=eid, sub="").rstrip("/")
    destino = origen + "/" + (sub or (base[3] if len(base) > 3 else ""))
    if not sub:
        auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola web", e["nombre"], {"url": w["url"]})
    cab = {k: v for k, v in request.headers if k.lower() in ("accept", "accept-language", "content-type", "user-agent")}
    galletas = {k: v for k, v in request.cookies.items() if k != current_app.config.get("SESSION_COOKIE_NAME", "session")}
    try:
        r = requests.request(request.method, destino, params=request.args, data=request.get_data(), headers=cab, cookies=galletas,
                             timeout=10, verify=False, allow_redirects=False)
    except requests.RequestException as x:
        return render_template("app_caida.html", segmento=e["nombre"], url=destino, error=str(x).split(":")[0][:160]), 502
    tipo = r.headers.get("Content-Type", "application/octet-stream")
    cuerpo = r.content
    if "text/html" in tipo or "text/css" in tipo or "javascript" in tipo:
        txt = cuerpo.decode(r.encoding or "utf-8", "replace").replace(origen, prefijo)
        txt = re.sub(r'((?:href|src|action)=["\'])/(?!/)', lambda m: m.group(1) + prefijo + "/", txt)
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
        resp.headers["Location"] = prefijo + loc if loc.startswith("/") and not loc.startswith(prefijo) else loc
    for c in r.raw.headers.getlist("Set-Cookie") if hasattr(r.raw.headers, "getlist") else []:
        resp.headers.add("Set-Cookie", re.sub(r"(?i)path=/[^;]*", f"Path={prefijo}/", c))
    return resp


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
        out.append({"sha": c["sha"], "corto": c["sha"][:7], "fecha": c["commit"]["author"]["date"], "autor": c["commit"]["author"]["name"],
                    "titulo": lineas[0] if lineas else "", "detalle": "\n".join(lineas[1:]).strip(), "url": c["html_url"], "actual": i == 0})
    return {"versiones": out, "rama": e["nombre"], "historial": out[0]["url"].split("/commit/")[0] + f"/commits/branch/{e['nombre']}" if out else "",
            "puede": g.perfil["modo"] == "escritura", "volver": url_for("consola.volver_equipo", eid=eid),
            "recuperar": url_for("consola.recuperar_equipo", eid=eid)}


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
    c = web(e["nombre"]) if e else None
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
    return redirect(url_consola(g.usuario, e, tipo))


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
    redis_eq = (url_for("consola.redis_equipo", eid=e["id"]) if ("redis" in e["skills"] or (_web_b(e["nombre"]) or {}).get("sitio") == "Redis")
                and not (web_eq and web_eq["producto"] == "Redis") else None)
    return render_template("equipo.html", redis_eq=redis_eq, web_eq=web_eq, cves_eq=_cves_equipo(e), puede_eliminar=eliminable(e) and g.perfil["modo"] == "escritura", autos_compat=_autos_compatibles(e), pols_eq=_politicas_equipo(e), e=e, datos=j(e["datos"], {}), consola_tipo=consola_tipo, acciones=acciones, discos=j(e["discos"], []),
                           servicios=j(e["servicios"], []), grupos=j(e["grupos"], []), hallazgos=hs,
                           cumplimiento=cumplimiento(e, g.cliente["id"]),
                           manual=j(e["skills_manual"], []))


@bp.route("/segmentos")
@requiere_login
def segmentos():
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
                           grupos=GRUPOS, nombre_grupo=NOMBRE)


@bp.route("/segmentos/grupo", methods=["POST"])
@requiere_login
def segmento_grupo():
    """Cambia el grupo (entrada del menú principal) de un segmento de negocio."""
    from .grupos_segmento import NOMBRE, normalizar
    if g.perfil["modo"] != "escritura" or request.form.get("_csrf") != session.get("csrf"):
        abort(403)
    seg, gr = request.form.get("segmento", ""), normalizar(request.form.get("grupo"))
    if not gr or not puede_ver_segmento(g.usuario, g.cliente["id"], seg):
        abort(400)
    ej("UPDATE segmentos SET grupo=? WHERE cliente_id=? AND nombre=?", gr, g.cliente["id"], seg)
    auditar(g.usuario["usuario"], g.cliente["id"], "cambiar grupo de segmento", seg, {"grupo": gr})
    flash(f"«{seg}» ahora está en {NOMBRE[gr]}", "ok")
    return redirect(url_for("consola.segmentos") + "#segmentos")


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
        return redirect(request.referrer or url_for("consola.segmentos"))
    return redirect(url_for("consola.segmentos") + "#archivados")


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
    return redirect(url_for("consola.segmentos"))


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
    return render_template("mapa.html", vista=vista, segmento=segmento, tarjetas=tarjetas, texto=texto, ent=entendido, seg_info=seg_info,
                           grupo=grupo if grupo in NOMBRE else "", grupos=GRUPOS, nombre_grupo=NOMBRE)


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
    if g.perfil["modo"] != "escritura" or request.form.get("_csrf") != session.get("csrf"):
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
    from .politicas_seg import CATEGORIAS, ETIQUETA, tablero
    pols, _ = tablero(g.cliente["id"], equipos_visibles())
    cat = request.args.get("categoria", "")
    conteo = {k: len([p for p in pols if p["categoria"] == k]) for k, _ in CATEGORIAS}
    total = {k: sum(p["cuenta"][k] for p in pols) for k in ETIQUETA}
    orden = [k for k, _ in CATEGORIAS]
    pols.sort(key=lambda p: (orden.index(p["categoria"]) if p["categoria"] in orden else 99, p["codigo"]))
    return render_template("politicas.html", pols=[p for p in pols if not cat or p["categoria"] == cat], categorias=CATEGORIAS, cat=cat,
                           conteo=conteo, total=total, etiqueta=ETIQUETA)


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
    return render_template("cves.html", vista=vista, kpi=kpi, pend=pend, cub=resumen(g.cliente["id"], evaluables, "cubierto") if vista == "cubiertos" else [],
                           equipos=[(e, pe[e["id"]]) for e in evaluables])


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
    meses = sorted({(t["creado"] or "")[:7] for t in ts if t["creado"]} | {mes}, reverse=True)[:12]
    tabla = []
    for a in autos_visibles() + q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=0", g.cliente["id"]):
        rg = regla(a)
        tabla.append(dict(rg, nombre=a["nombre"], nombre_aap=a["nombre_aap"], area=area_de(a), minutos=a["minutos_manual"], id=a["id"], met=metricas(a, est_t)))
    nombres = {k: n for k, n, _ in AREAS}
    tabla.sort(key=lambda x: ([k for k, _, _ in AREAS].index(x["area"]) if x["area"] in nombres else 99, -x["creditos"], x["nombre"]))
    return render_template("facturacion.html", r=r, mes=mes, meses=meses, tabla=tabla, areas=nombres,
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
    return render_template("alertas.html", r=r, lista=lista, dias=dias, origen=origen, por_origen=por_origen)


@bp.route("/certificados")
@requiere_login
def certificados():
    from .certificados_panel import leer
    r = leer(g.cliente, equipos_visibles())
    vista = request.args.get("vista", "servicios")
    filas = [f for f in r["filas"] if vista == "todos" or (vista == "servicios" and f["tipo"] == "servidor" and f["estado"] not in ("reemplazado", "revocado"))
             or (vista == "historial" and f["estado"] in ("reemplazado", "revocado"))]
    renovar = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "Certificados · Renovar por vencer")
    emitir = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "Certificados · Emitir para servicio")
    return render_template("certificados.html", r=r, filas=filas, vista=vista, renovar=renovar, emitir=emitir,
                           cuenta={"servicios": sum(1 for f in r["filas"] if f["tipo"] == "servidor" and f["estado"] not in ("reemplazado", "revocado")),
                                   "historial": sum(1 for f in r["filas"] if f["estado"] in ("reemplazado", "revocado")), "todos": len(r["filas"])})


@bp.route("/ciclo-vida")
@requiere_login
def ciclo_vida():
    from .ciclo_vida import FASES, panel
    eqs = [dict(e, _datos=j(e["datos"], {})) for e in equipos_visibles()]
    r = panel(eqs)
    fase = request.args.get("fase", "")
    grupos = [x for x in r["grupos"] if not fase or x["fase"] == fase or (fase == "riesgo" and x["riesgo"])]
    return render_template("ciclo_vida.html", r=r, grupos=grupos, fase=fase, FASES=FASES)


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
            n = incorporar(g.usuario, g.cliente, request.form)
            flash(f"Infraestructura «{n}» incorporada: credencial e inventario creados en AAP. Usa «Probar acceso».", "ok")
        except (ValueError, ErrorAAP) as e:
            flash(str(e), "error")
        return redirect(url_for("consola.infraestructuras"))
    from .infraestructuras import AMBITOS
    from .ejecucion import plano
    eqs_ = equipos_visibles()
    infras = lista(g.cliente["id"], eqs_)
    try:
        from .aap import AAP
        instancias = AAP.de_cliente(g.cliente).todos("instances/")
    except ErrorAAP:
        instancias = []
    pl = plano(g.cliente, eqs_, infras, instancias)
    con = request.args.get("conectar", type=int)
    inc_ = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Incorporar máquinas'", g.cliente["id"])
    from .infraestructuras import NATURALEZAS, NUBE_PUBLICA
    orden = sorted({x["centro"] for x in infras}, key=lambda c: (c == NUBE_PUBLICA, c))
    return render_template("infraestructuras.html", inc_id=inc_["id"] if inc_ else None, pl=pl, infras=infras, proveedores=PROVEEDORES, ambitos=AMBITOS,
                           centros=orden, naturalezas=NATURALEZAS, nube_publica=NUBE_PUBLICA,
                           nuevo=request.args.get("nuevo", ""), conectar=next((x for x in infras if x["id"] == con), None))


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
    from .politicas_seg import CATEGORIAS as CATS_POL
    activo = {"consola.arquitectura": "consola.mapa", "consola.arquitectura_nueva": "consola.mapa", "consola.equipo": "consola.inventario",
              "consola.ticket": "consola.tickets", "consola.automatizacion": "consola.automatizaciones",
              "consola.cves": "consola.politicas", "consola.politica": "consola.politicas", "consola.certificados": "consola.politicas", "consola.ciclo_vida": "consola.politicas"}.get(request.endpoint, request.endpoint)
    from .areas import AREAS, area_de
    con = {area_de(a) for a in autos_visibles()}
    g_autos = [("Todas", {})] + [(n, {"area": k}) for k, n, _ in AREAS if k in con]
    eqs = equipos_visibles()
    hay = Counter(grupo_periferico(e) for e in eqs)
    g_inv = [("Todos los equipos", {})] + [(n, {"tipo": k}) for k, n, _ in PERIFERICOS if hay.get(k)]
    g_inv += [("Con hallazgos", {"estado": "con-hallazgos"}), ("Apagados", {"q": "apagados"})]
    from .grupos_segmento import GRUPOS, grupo_de
    segs_v = segmentos_visibles(g.usuario, g.cliente["id"])
    sel = request.args.get("segmento")
    grupo_act = request.args.get("grupo") or (grupo_de(next((x for x in segs_v if x["nombre"] == sel), {})) if sel else "") \
        or ("redes" if request.args.get("vista") == "infra" else "aplicaciones")
    g_grupos = []
    for gk, gn, gic, _ in GRUPOS:
        hijos = [("Todos", {"grupo": gk})] + [(x["nombre"], {"grupo": gk, "segmento": x["nombre"]}) for x in segs_v if grupo_de(x) == gk]
        if gk == "redes":
            hijos.append(("Centros de datos y redes", {"grupo": gk, "vista": "infra"}))
        if g.perfil["modo"] == "escritura":
            hijos.append(("+ Solicitar nuevo", {"_ep": "consola.arquitectura_nueva", "grupo": gk}))
        g_grupos.append(("consola.mapa", gn, gic, hijos, gk))
    secciones = [("consola.inicio", "Panel", "inicio", None),
                 ("consola.automatizaciones", "Automatizaciones", "autos", g_autos),
                 ("consola.tickets", "Tickets", "tickets", [("Todos", {}), ("Pendientes de aprobación", {"estado": "pendiente_aprobacion"}),
                                                            ("En ejecución", {"estado": "en_ejecucion"}), ("Exitosos", {"estado": "exitoso"}),
                                                            ("Fallidos", {"estado": "fallido"})]),
                 ("consola.infraestructuras", "Infraestructura", "infra", [("Todas", {}), ("Incorporar infraestructura", {"_ancla": "incorporar"})]),
                 ("consola.inventario", "Inventario", "inventario", g_inv),
                 *g_grupos,
                 ("consola.segmentos", "Gobierno", "gobierno", [("Segmentos de negocio", {"_ancla": "segmentos"}), ("Skills", {"_ancla": "skills"}),
                                                                ("Políticas de seguridad", {"_ancla": "politicas"})]),
                 ("consola.politicas", "Seguridad", "gobierno", [("Políticas", {}), ("CVEs", {"_ep": "consola.cves"}),
                                                                ("Certificados", {"_ep": "consola.certificados"}),
                                                                ("Ciclo de vida", {"_ep": "consola.ciclo_vida"})]),
                 ("consola.alertas", "Alertas", "alertas", [("Todas", {}), ("Event-Driven Ansible", {"origen": "eda"}), ("Trabajos fallidos", {"origen": "trabajo"}),
                                                             ("Aprobaciones pendientes", {"origen": "aprobacion"})]),
                 ("consola.facturacion", "Facturación", "tarifas", [("Consumo del mes", {}), ("Tabla de créditos", {"vista": "tabla"})])]
    out = []
    for ep, t, ic, hijos, *grupo in secciones:
        es = activo == ep and (not grupo or grupo[0] == grupo_act)   # los grupos de segmentos comparten la página del mapa
        it = {"t": t, "ic": ic, "url": url_for(ep, **({"grupo": grupo[0]} if grupo else {})), "abierto": es, "on": es, "hijos": []}
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
            it["hijos"].append({"t": ht, "url": url, "on": on})
        out.append(it)
    return out
