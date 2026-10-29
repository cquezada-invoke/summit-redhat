"""Consola FaceOps: panel, buscador, automatizaciones, tickets, inventario, segmentos y mapa."""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
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
@bp.route("/ingresar", methods=["GET", "POST"])
def ingresar():
    if request.method == "POST":
        u = q1("SELECT * FROM usuarios WHERE usuario=? AND activo=1", request.form.get("usuario", "").strip().lower())
        if u and check_password_hash(u["clave_hash"], request.form.get("clave", "")):
            session.clear()
            session["uid"] = u["id"]
            session.permanent = True
            ej("UPDATE usuarios SET ultimo_ingreso=? WHERE id=?", ahora(), u["id"])
            auditar(u["usuario"], None, "ingresar", "sesión")
            sig = request.args.get("siguiente", "")
            if sig.startswith("/") and not sig.startswith("//") and sig.rstrip("?") not in ("/", ""):
                return redirect(request.script_root + sig)
            return redirect(url_for("consola.inicio"))
        auditar(request.form.get("usuario", "")[:60], None, "ingreso fallido", "sesión", {"ip": request.remote_addr})
        flash("Usuario o clave incorrectos", "error")
    return render_template("ingresar.html")


@bp.route("/salir")
def salir():
    session.clear()
    return redirect(url_for("consola.ingresar"))


@bp.route("/cliente/<int:cid>")
@requiere_login
def cambiar_cliente(cid):
    if any(c["id"] == cid for c in g.clientes):
        session["cliente_id"] = cid
    return redirect(request.referrer or url_for("consola.inicio"))


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
    return render_template("inicio.html", costos_seg=costos_seg, equipos=eqs, hallazgos=hs, tickets=ts[:8], pendientes=pendientes, g_=graficos, mapa=mapa,
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
    demos, otras, skills_cat, sistemas_cat = defaultdict(list), defaultdict(list), [], []
    for a in sorted(autos, key=lambda x: (x["orden"] if x.get("orden") is not None else 999, x["nombre"])):
        a["usos"] = usos.get(a["id"], 0)
        a["puede"], a["por_que"] = puede_ejecutar(g.usuario, g.cliente["id"], a)
        a["proposito"] = j(a.get("guia"), {}).get("proposito", "")
        if a.get("habilita_skill"):
            skills_cat.append(a)
        elif a["categoria"] == "Sistemas operativos":
            sistemas_cat.append(a)
        else:
            (demos[a["demo"]] if a.get("demo") else otras[a["categoria"]]).append(a)
    from .operacion import resumen_demos
    res = resumen_demos()
    demos = dict(sorted(demos.items(), key=lambda kv: min(x["orden"] for x in kv[1])))
    por_id = {a["id"]: a for v in list(demos.values()) + list(otras.values()) + [skills_cat, sistemas_cat] for a in v}
    encontradas = [por_id[a["id"]] for a in encontradas if a["id"] in por_id] if encontradas is not None else None
    return render_template("automatizaciones.html", demos=demos, resumenes=res, otras=dict(sorted(otras.items())),
                           tipo=tipo, total=len(autos), texto=texto, encontradas=encontradas, fq=filtros_q,
                           skills_cat=sorted(skills_cat, key=lambda a: a["habilita_skill"]), sistemas_cat=sistemas_cat)


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
    return render_template("automatizacion.html", a=a, campos=campos, puede=puede, por_que=por_que, elegibles=elegibles,
                           segs_negocio=segmentos_visibles(g.usuario, g.cliente["id"]),
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
    return render_template("ticket.html", t=t, a=a, url_aap=url_aap, url_aprob=url_aprob, url_sn=url_sn, traza=traza,
                           bitacora=bitacora, web_aap=web_aap, sol=sol, aprobar=aprobar_ok)


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
    filtros = {k: request.args.get(k, "") for k in ("infra", "ambiente", "segmento", "skill", "estado")}
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
    return render_template("inventario.html", equipos=eqs, por_eq=por_eq, filtros=filtros, texto=texto, ent=entendido, total=len(todos),
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
    return render_template("equipo.html", e=e, datos=j(e["datos"], {}), consola_tipo=consola_tipo, acciones=acciones, discos=j(e["discos"], []),
                           servicios=j(e["servicios"], []), grupos=j(e["grupos"], []), hallazgos=hs,
                           cumplimiento=cumplimiento(e, g.cliente["id"]),
                           manual=j(e["skills_manual"], []))


@bp.route("/segmentos")
@requiere_login
def segmentos():
    eqs = equipos_visibles()
    segs = segmentos_visibles(g.usuario, g.cliente["id"])
    for s in segs:
        s["equipos"] = [e for e in eqs if s["nombre"] in e["segmentos"]]
        s["regla_d"] = j(s["regla"], {})
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
    return render_template("segmentos.html", segmentos=segs, skills=sk, politicas=pol)


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
        tarjetas = resumen_segmentos(g.cliente, eqs, hs, segmentos_visibles(g.usuario, g.cliente["id"]))
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
    return render_template("mapa.html", vista=vista, segmento=segmento, tarjetas=tarjetas, texto=texto, ent=entendido, seg_info=seg_info)


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


@bp.route("/mapa/datos")
@requiere_login
def mapa_datos():
    from .topologia import construir
    eqs = equipos_visibles()
    els, miga = construir(g.cliente, eqs, hallazgos_visibles(eqs), request.args.get("nivel", "global"),
                          request.args.get("valor", ""), request.args.get("vista", "infra"))
    acceso = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Acceso de red' AND visible=1", g.cliente["id"])
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
    return {"elementos": els, "miga": miga}


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
