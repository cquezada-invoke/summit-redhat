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
    if not sugerencia or sugerencia == "recolectar":
        return None
    s = normal(sugerencia)
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
    return render_template("inicio.html", equipos=eqs, hallazgos=hs, tickets=ts[:8], pendientes=pendientes,
                           conteo=dict(Counter(h["severidad"] for h in hs)), ejecuciones_mes=len(del_mes),
                           horas_mes=round(sum(t["valor_min"] or 0 for t in ok_mes) / 60, 1),
                           valor_mes=sum((t["valor_min"] or 0) / 60 * (g.cliente["costo_hora"] or 0) for t in ok_mes),
                           autos=autos, sugerencias=sugerencias[:6], segmentos=segs)


@bp.route("/buscar")
@requiere_login
def buscar_vista():
    texto = request.args.get("q", "").strip()
    if not texto:
        return redirect(url_for("consola.inicio"))
    eqs = equipos_visibles()
    filtros, res = buscar(texto, g.cliente["id"], autos_visibles(), eqs, tickets_visibles(), hallazgos_visibles(eqs))
    if len(res["automatizaciones"]) == 1 and not (res["equipos"] or res["tickets"] or res["hallazgos"]):
        flash(f"Entendí «{texto}»: te llevo directo a la automatización", "ok")
    return render_template("buscar.html", texto=texto, f=filtros, r=res)


# ------------------------------------------------------------------ automatizaciones
@bp.route("/automatizaciones")
@requiere_login
def automatizaciones():
    autos = autos_visibles()
    tipo = request.args.get("tipo")
    if tipo in ("lectura", "escritura"):
        autos = [a for a in autos if a["tipo"] == tipo]
    usos = Counter(t["automatizacion_id"] for t in q("SELECT automatizacion_id FROM tickets WHERE cliente_id=?", g.cliente["id"]))
    demos, otras = defaultdict(list), defaultdict(list)
    for a in sorted(autos, key=lambda x: (x["orden"] if x.get("orden") is not None else 999, x["nombre"])):
        a["usos"] = usos.get(a["id"], 0)
        a["puede"], a["por_que"] = puede_ejecutar(g.usuario, g.cliente["id"], a)
        a["proposito"] = j(a.get("guia"), {}).get("proposito", "")
        (demos[a["demo"]] if a.get("demo") else otras[a["categoria"]]).append(a)
    from .operacion import resumen_demos
    res = resumen_demos()
    demos = dict(sorted(demos.items(), key=lambda kv: min(x["orden"] for x in kv[1])))
    return render_template("automatizaciones.html", demos=demos, resumenes=res, otras=dict(sorted(otras.items())),
                           tipo=tipo, total=len(autos))


@bp.route("/automatizaciones/<int:aid>", methods=["GET", "POST"])
@requiere_login
def automatizacion(aid):
    a = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", aid, g.cliente["id"])
    if not a:
        abort(404)
    puede, por_que = puede_ejecutar(g.usuario, g.cliente["id"], a)
    segs = segmentos_aplicables(g.usuario, g.cliente["id"], a) if a["usa_limite"] else []
    campos = j(a["campos"], [])
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
                          {"ambientes": request.form.getlist("f_ambiente"), "infraestructura": request.form.getlist("f_infra")})
            flash(f"{t['numero']} creado y enviado a AAP" + (" (esperará la aprobación en AAP)" if a["requiere_aprobacion"] else ""), "ok")
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
    if request.method == "POST":
        if not puede_aprobar(g.usuario, g.cliente["id"]):
            abort(403)
        try:
            resolver_aprobacion(g.usuario, tid, request.form.get("accion") == "aprobar", request.form.get("comentario", ""))
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
    return render_template("ticket.html", t=t, a=a, url_aap=url_aap, url_aprob=url_aprob, url_sn=url_sn, traza=traza,
                           bitacora=bitacora, web_aap=web_aap,
                           aprobar=puede_aprobar(g.usuario, g.cliente["id"]) and t["estado"] == "pendiente_aprobacion" and bool(t["aap_aprobacion_id"]))


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
    return render_template("inventario.html", equipos=eqs, por_eq=por_eq, filtros=filtros,
                           opciones={"infra": sorted({e["ubicacion"] for e in todos if e["ubicacion"]}),
                                     "segmento": sorted({s for e in todos for s in e["segmentos"]}),
                                     "skill": sorted({s for e in todos for s in e["skills"]}),
                                     "ambiente": sorted({e["ambiente"] for e in todos if e["ambiente"]})},
                           ultima=max([e["ultima_recoleccion"] or "" for e in todos] or [""]))


@bp.route("/inventario/<int:eid>", methods=["GET", "POST"])
@requiere_login
def equipo(eid):
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    if request.method == "POST":
        if g.perfil["modo"] != "escritura":
            abort(403)
        manual = sorted({s.strip().lower() for s in request.form.get("skills", "").split(",") if s.strip()})
        ej("UPDATE equipos SET skills_manual=? WHERE id=?", json.dumps(manual), eid)
        auditar(g.usuario["usuario"], g.cliente["id"], "editar skills", e["nombre"], {"skills_manual": manual})
        from .inventario import calcular
        calcular(g.cliente)
        flash("Skills actualizadas; políticas y hallazgos recalculados", "ok")
        return redirect(url_for("consola.equipo", eid=eid))
    autos = autos_visibles()
    hs = [h for h in q("SELECT * FROM hallazgos WHERE equipo_id=?", eid)]
    for h in hs:
        h["auto"] = auto_sugerida(h["sugerencia"], autos)
    relacionadas = [a for a in autos if a["inventario"] in j(e["inventarios"], []) and a["usa_limite"]][:12]
    return render_template("equipo.html", e=e, datos=j(e["datos"], {}), discos=j(e["discos"], []),
                           servicios=j(e["servicios"], []), grupos=j(e["grupos"], []), hallazgos=hs,
                           cumplimiento=cumplimiento(e, g.cliente["id"]), relacionadas=relacionadas,
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
    for s in sk:
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
    eqs = equipos_visibles()
    hs = hallazgos_visibles(eqs)
    peor = {}
    for h in hs:
        peor[h["equipo_id"]] = max(peor.get(h["equipo_id"], 0), SEV.get(h["severidad"], 0))
    seg = request.args.get("segmento")
    inf = request.args.get("infra")
    skill = request.args.get("skill")
    negocio = segmentos_visibles(g.usuario, g.cliente["id"])

    def caja(es):
        return {"n": len(es), "alta": sum(1 for e in es if peor.get(e["id"]) == 3),
                "media": sum(1 for e in es if peor.get(e["id"]) == 2), "ok": sum(1 for e in es if not peor.get(e["id"]))}
    vista = request.args.get("vista", "infra")
    if not seg and not inf:
        if vista == "negocio":
            grupos = [(s["nombre"], s["descripcion"], [e for e in eqs if s["nombre"] in e["segmentos"]]) for s in negocio]
        else:
            grupos = [(u, "", [e for e in eqs if e["ubicacion"] == u]) for u in sorted({e["ubicacion"] for e in eqs if e["ubicacion"]})]
        nivel = [{"nombre": n, "desc": d, **caja(es), "skills": Counter(k for e in es for k in e["skills"]).most_common(6),
                  "ambientes": sorted({e["ambiente"] for e in es if e["ambiente"]}),
                  "otros": sorted({x for e in es for x in (e["segmentos"] if vista == "infra" else [e["ubicacion"]]) if x})}
                 for n, d, es in grupos if es]
        return render_template("mapa.html", nivel="cliente", vista=vista, cajas=nivel, total=caja(eqs))
    es = [e for e in eqs if (seg and seg in e["segmentos"]) or (inf and e["ubicacion"] == inf)]
    seg = seg or inf
    if not skill:
        grupos = defaultdict(list)
        for e in es:
            for k in (e["skills"] or ["sin skill"]):
                grupos[k].append(e)
        cajas = [{"nombre": k, **caja(v), "equipos": [{"id": e["id"], "nombre": e["nombre"], "peor": peor.get(e["id"], 0),
                                                       "so": e["so"]} for e in v]} for k, v in sorted(grupos.items())]
        return render_template("mapa.html", nivel="segmento", seg=seg, clave="infra" if inf else "segmento", cajas=cajas, total=caja(es))
    es = [e for e in es if skill in (e["skills"] or ["sin skill"])]
    return render_template("mapa.html", nivel="skill", seg=seg, clave="infra" if inf else "segmento", skill=skill, total=caja(es),
                           cajas=[{"id": e["id"], "nombre": e["nombre"], "so": e["so"], "ip": e["ip"], "peor": peor.get(e["id"], 0),
                                   "hallazgos": [h for h in hs if h["equipo_id"] == e["id"]]} for e in es])


@bp.route("/auditoria")
@requiere_login
def auditoria():
    if not puede_aprobar(g.usuario, g.cliente["id"]):
        abort(403)
    return render_template("auditoria.html", filas=q("SELECT * FROM auditoria WHERE cliente_id=? OR cliente_id IS NULL "
                                                      "ORDER BY id DESC LIMIT 300", g.cliente["id"]))
