"""FaceOps-admin: clientes, usuarios y perfiles, catálogo y tarifas, gobierno (segmentos, skills, políticas) e ingresos."""
import json
from collections import defaultdict
from datetime import datetime, timezone

import yaml
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import requiere_admin
from .aap import AAP, ErrorAAP
from .bd import ahora, auditar, ej, j, q, q1

bp = Blueprint("admin", __name__)


def meses(n=6):
    hoy = datetime.now(timezone.utc)
    out = []
    y, m = hoy.year, hoy.month
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


@bp.route("/")
@requiere_admin
def panel():
    clientes = q("SELECT * FROM clientes ORDER BY nombre")
    ts = q("SELECT t.*, a.categoria, a.nombre auto_nombre, c.nombre cliente, c.moneda, c.costo_hora FROM tickets t "
           "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id JOIN clientes c ON c.id=t.cliente_id")
    ok = [t for t in ts if t["estado"] == "exitoso"]
    ms = meses()
    mes = ms[-1]
    por_servicio = defaultdict(lambda: defaultdict(float))
    por_cliente = defaultdict(lambda: {"ejecuciones": 0, "exitosas": 0, "ingresos": 0.0, "horas": 0.0, "valor": 0.0, "moneda": "USD"})
    for t in ts:
        pc = por_cliente[t["cliente"]]
        pc["moneda"] = t["moneda"]
        if (t["creado"] or "").startswith(mes):
            pc["ejecuciones"] += 1
    for t in ok:
        m = (t["creado"] or "")[:7]
        if m in ms:
            por_servicio[t["categoria"] or "General"][m] += t["tarifa"] or 0
        if m == mes:
            pc = por_cliente[t["cliente"]]
            pc["exitosas"] += 1
            pc["ingresos"] += t["tarifa"] or 0
            pc["horas"] += (t["valor_min"] or 0) / 60
            pc["valor"] += (t["valor_min"] or 0) / 60 * (t["costo_hora"] or 0)
    top = defaultdict(int)
    for t in ok:
        top[(t["cliente"], t["auto_nombre"])] += 1
    return render_template("admin/panel.html", clientes=clientes, usuarios=q1("SELECT COUNT(*) n FROM usuarios")["n"],
                           meses=ms, por_servicio={k: dict(v) for k, v in sorted(por_servicio.items())},
                           por_cliente=dict(por_cliente), mes=mes,
                           top=sorted(top.items(), key=lambda x: -x[1])[:8],
                           totales={"ingresos": sum(v["ingresos"] for v in por_cliente.values()),
                                    "horas": sum(v["horas"] for v in por_cliente.values()),
                                    "valor": sum(v["valor"] for v in por_cliente.values()),
                                    "ejecuciones": sum(v["ejecuciones"] for v in por_cliente.values())})


# ------------------------------------------------------------------ clientes
@bp.route("/clientes")
@requiere_admin
def clientes():
    cs = q("SELECT c.*, (SELECT COUNT(*) FROM equipos e WHERE e.cliente_id=c.id AND e.estado!='retirado') equipos, "
           "(SELECT COUNT(*) FROM automatizaciones a WHERE a.cliente_id=c.id) autos, "
           "(SELECT COUNT(*) FROM perfiles p WHERE p.cliente_id=c.id) perfiles FROM clientes c ORDER BY nombre")
    return render_template("admin/clientes.html", clientes=cs)


@bp.route("/clientes/nuevo", methods=["GET", "POST"])
@bp.route("/clientes/<int:cid>", methods=["GET", "POST"])
@requiere_admin
def cliente(cid=None):
    c = q1("SELECT * FROM clientes WHERE id=?", cid) if cid else None
    if cid and not c:
        abort(404)
    if request.method == "POST":
        f = request.form
        accion = f.get("accion", "guardar")
        if c and accion in ("probar", "sincronizar", "recolectar"):
            try:
                if accion == "probar":
                    aap = AAP.de_cliente(c)
                    yo = aap.yo()
                    flash(f"Conexión OK con AAP {aap.ping().get('version', '')} como «{yo['username']}»", "ok")
                elif accion == "sincronizar":
                    from .inventario import sincronizar
                    from .operacion import sincronizar_catalogo
                    n, m = sincronizar_catalogo(c, g.usuario["usuario"]), sincronizar(c, g.usuario["usuario"])
                    flash(f"Catálogo: {n} automatizaciones · inventario: {m} equipos", "ok")
                else:
                    from .inventario import recolectar
                    flash(f"Recolector lanzado en AAP: {len(recolectar(c, g.usuario['usuario']))} trabajos", "ok")
            except ErrorAAP as e:
                flash(str(e), "error")
            return redirect(url_for("admin.cliente", cid=cid))
        try:
            clasif = json.loads(f.get("clasificacion") or "{}")
            recol = json.loads(f.get("recoleccion") or "{}")
        except ValueError as e:
            flash(f"JSON inválido: {e}", "error")
            return redirect(request.url)
        conf = j(c["tickets_conf"], {}) if c else {}
        conf.update({"url": f.get("sn_url", ""), "usuario": f.get("sn_usuario", "")})
        if f.get("sn_clave"):
            conf["clave"] = f["sn_clave"]
        datos = {"codigo": f["codigo"].strip().lower(), "nombre": f["nombre"].strip(), "descripcion": f.get("descripcion", ""),
                 "aap_url": f.get("aap_url", "").strip(), "aap_verificar": int(bool(f.get("aap_verificar"))),
                 "tickets_tipo": f.get("tickets_tipo", "faceops"), "tickets_conf": json.dumps(conf),
                 "clasificacion": json.dumps(clasif, ensure_ascii=False), "recoleccion": json.dumps(recol, ensure_ascii=False),
                 "moneda": f.get("moneda", "USD"), "costo_hora": float(f.get("costo_hora") or 0), "activo": int(bool(f.get("activo")))}
        if f.get("aap_token"):
            datos["aap_token"] = f["aap_token"].strip()
        if c:
            ej("UPDATE clientes SET " + ", ".join(f"{k}=?" for k in datos) + " WHERE id=?", *datos.values(), cid)
        else:
            datos["creado"] = ahora()
            cid = ej("INSERT INTO clientes(" + ", ".join(datos) + ") VALUES (" + ",".join("?" * len(datos)) + ")", *datos.values())
            from .semillas import gobierno_base
            gobierno_base(cid)
        auditar(g.usuario["usuario"], cid, "guardar cliente", datos["nombre"], {k: v for k, v in datos.items() if k != "aap_token"})
        flash("Cliente guardado", "ok")
        return redirect(url_for("admin.cliente", cid=cid))
    return render_template("admin/cliente.html", c=c, conf=j(c["tickets_conf"], {}) if c else {},
                           clasif=json.dumps(j(c["clasificacion"], {}) if c else {}, indent=2, ensure_ascii=False),
                           recol=json.dumps(j(c["recoleccion"], {}) if c else {}, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------ usuarios y perfiles
@bp.route("/usuarios")
@requiere_admin
def usuarios():
    us = q("SELECT * FROM usuarios ORDER BY nombre")
    for u in us:
        u["perfiles"] = q("SELECT p.*, c.nombre cliente FROM perfiles p JOIN clientes c ON c.id=p.cliente_id WHERE usuario_id=?", u["id"])
    return render_template("admin/usuarios.html", usuarios=us)


@bp.route("/usuarios/nuevo", methods=["GET", "POST"])
@bp.route("/usuarios/<int:uid>", methods=["GET", "POST"])
@requiere_admin
def usuario(uid=None):
    u = q1("SELECT * FROM usuarios WHERE id=?", uid) if uid else None
    clientes = q("SELECT * FROM clientes ORDER BY nombre")
    if request.method == "POST":
        f = request.form
        if f.get("accion") == "perfil" and u:
            segs = f.getlist("segmentos") or ["*"]
            ambs = f.getlist("ambientes") or ["*"]
            ej("INSERT INTO perfiles(usuario_id, cliente_id, segmentos, ambientes, modo, aprobador) VALUES (?,?,?,?,?,?) "
               "ON CONFLICT(usuario_id, cliente_id) DO UPDATE SET segmentos=excluded.segmentos, ambientes=excluded.ambientes, "
               "modo=excluded.modo, aprobador=excluded.aprobador",
               uid, int(f["cliente_id"]), json.dumps(segs, ensure_ascii=False), json.dumps(ambs), f.get("modo", "lectura"), int(bool(f.get("aprobador"))))
            auditar(g.usuario["usuario"], int(f["cliente_id"]), "perfil", u["usuario"], {"segmentos": segs, "ambientes": ambs, "modo": f.get("modo")})
            flash("Perfil guardado", "ok")
            return redirect(url_for("admin.usuario", uid=uid))
        if f.get("accion") == "quitar_perfil" and u:
            ej("DELETE FROM perfiles WHERE id=? AND usuario_id=?", int(f["perfil_id"]), uid)
            return redirect(url_for("admin.usuario", uid=uid))
        datos = {"usuario": f["usuario"].strip().lower(), "nombre": f["nombre"].strip(), "correo": f.get("correo", ""),
                 "es_admin": int(bool(f.get("es_admin"))), "activo": int(bool(f.get("activo")))}
        if f.get("clave"):
            if len(f["clave"]) < 10:
                flash("La clave debe tener al menos 10 caracteres", "error")
                return redirect(request.url)
            datos["clave_hash"] = generate_password_hash(f["clave"])
        if u:
            ej("UPDATE usuarios SET " + ", ".join(f"{k}=?" for k in datos) + " WHERE id=?", *datos.values(), uid)
        else:
            if "clave_hash" not in datos:
                flash("Define una clave inicial", "error")
                return redirect(request.url)
            datos["creado"] = ahora()
            uid = ej("INSERT INTO usuarios(" + ", ".join(datos) + ") VALUES (" + ",".join("?" * len(datos)) + ")", *datos.values())
        auditar(g.usuario["usuario"], None, "guardar usuario", datos["usuario"], {k: v for k, v in datos.items() if k != "clave_hash"})
        flash("Usuario guardado", "ok")
        return redirect(url_for("admin.usuario", uid=uid))
    perfiles = q("SELECT p.*, c.nombre cliente FROM perfiles p JOIN clientes c ON c.id=p.cliente_id WHERE usuario_id=?", uid) if u else []
    segs = {c["id"]: [s["nombre"] for s in q("SELECT nombre FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", c["id"])]
            for c in clientes}
    ambs = {c["id"]: [r["ambiente"] for r in q("SELECT DISTINCT ambiente FROM equipos WHERE cliente_id=? AND ambiente!='' ORDER BY ambiente", c["id"])]
            for c in clientes}
    return render_template("admin/usuario.html", u=u, perfiles=perfiles, clientes=clientes, segs=segs, ambs=ambs)


# ------------------------------------------------------------------ catálogo y tarifas
CAMPOS_GUIA = {"proposito": "Para qué sirve", "flujo": "Cómo funciona en la demo / quién la lanza normalmente",
               "faceops": "Qué pasa si la lanzas desde FaceOps", "cuando": "Cuándo usarla", "cambios": "Qué cambia", "riesgo": "Riesgo (bajo · medio · alto)",
               "reversa": "Cómo se deshace", "resultado": "Resultado esperado"}


@bp.route("/catalogo", methods=["GET", "POST"])
@requiere_admin
def catalogo():
    cid = int(request.args.get("cliente") or (g.cliente["id"] if g.cliente else 0) or 0)
    c = q1("SELECT * FROM clientes WHERE id=?", cid)
    if request.method == "POST" and c:
        f = request.form
        aid = int(f["id"])
        datos = {"nombre": f["nombre"], "descripcion": f.get("descripcion", ""), "categoria": f.get("categoria", "General"),
                 "tipo": f.get("tipo", "escritura"), "ambito": f.get("ambito", ""), "usa_limite": int(bool(f.get("usa_limite"))),
                 "requiere_aprobacion": int(bool(f.get("requiere_aprobacion"))), "minutos_manual": int(f.get("minutos_manual") or 0),
                 "tarifa": float(f.get("tarifa") or 0), "visible": int(bool(f.get("visible"))), "info": f.get("info", ""),
                 "etiquetas": json.dumps([x.strip() for x in f.get("etiquetas", "").split(",") if x.strip()])}
        previa = j((q1("SELECT guia FROM automatizaciones WHERE id=?", aid) or {}).get("guia"), {})
        guia = dict(previa, **{k: f.get("g_" + k, "").strip() for k in CAMPOS_GUIA})
        guia.pop("_origen", None)  # editada a mano: ya no se pisa con datos/guias*.yml
        datos["guia"] = json.dumps({k: v for k, v in guia.items() if v}, ensure_ascii=False)
        ej("UPDATE automatizaciones SET " + ", ".join(f"{k}=?" for k in datos) + " WHERE id=? AND cliente_id=?", *datos.values(), aid, cid)
        auditar(g.usuario["usuario"], cid, "editar ficha", datos["nombre"], datos)
        flash(f"Ficha de «{datos['nombre']}» guardada", "ok")
        return redirect(url_for("admin.catalogo", cliente=cid, _anchor=f"a{aid}"))
    autos = q("SELECT * FROM automatizaciones WHERE cliente_id=? ORDER BY orden, categoria, nombre", cid) if c else []
    return render_template("admin/catalogo.html", c=c, autos=autos, campos_guia=CAMPOS_GUIA, clientes=q("SELECT * FROM clientes ORDER BY nombre"))


# ------------------------------------------------------------------ gobierno: segmentos, skills y políticas (YAML)
@bp.route("/gobierno", methods=["GET", "POST"])
@requiere_admin
def gobierno():
    cid = int(request.args.get("cliente") or (g.cliente["id"] if g.cliente else 0) or 0)
    c = q1("SELECT * FROM clientes WHERE id=?", cid)
    if request.method == "POST" and c:
        try:
            d = yaml.safe_load(request.form["yaml"]) or {}
        except yaml.YAMLError as e:
            flash(f"YAML inválido: {e}", "error")
            return redirect(request.url)
        from .semillas import cargar_gobierno
        cargar_gobierno(cid, d, reemplazar=True)
        from .inventario import calcular
        calcular(c)
        auditar(g.usuario["usuario"], cid, "editar gobierno", "segmentos, skills y políticas", {})
        flash("Gobierno guardado; skills, políticas y hallazgos recalculados", "ok")
        return redirect(request.url)
    from .semillas import exportar_gobierno
    return render_template("admin/gobierno.html", c=c, clientes=q("SELECT * FROM clientes ORDER BY nombre"),
                           texto=yaml.safe_dump(exportar_gobierno(cid), allow_unicode=True, sort_keys=False, width=140) if c else "")
