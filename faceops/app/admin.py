"""FaceOps-admin: clientes, usuarios y perfiles, catálogo y tarifas, gobierno (segmentos, skills, políticas) e ingresos."""
import os
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
    for t in ok:   # consumo en créditos (factor de beneficio × máquinas)
        t["tarifa"] = t["creditos"] or 0
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
    from .confianza import estado as estado_confianza
    return render_template("admin/panel.html", confianza=estado_confianza(), clientes=clientes, usuarios=q1("SELECT COUNT(*) n FROM usuarios")["n"],
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
    from .creditos import estado as estado_creditos
    for c in cs:
        c["cr"] = estado_creditos(c)
        m = q1("SELECT COALESCE(SUM(CASE WHEN creditos>0 THEN creditos END),0) total, COALESCE(-SUM(CASE WHEN creditos<0 THEN creditos END),0) consumido "
               "FROM creditos_mov WHERE cliente_id=?", c["id"])   # misma cuenta que el panel del cliente
        c["cr"].update(total=m["total"], consumido=m["consumido"])
    return render_template("admin/clientes.html", clientes=cs)


ROLES_CONTACTO = ["Contacto principal", "Sponsor / dirección", "Técnico", "Aprobador", "Comercial", "Facturación", "Seguridad"]
TIPOS_CONTRATO = [("demo", "Demo (laboratorio de Invoke)"), ("poc", "Prueba de concepto"), ("anual", "Contrato anual"),
                  ("plurianual", "Contrato plurianual")]


@bp.route("/clientes/alta", methods=["GET", "POST"])
@requiere_admin
def cliente_alta():
    """Alta de un cliente: datos, contacto, contrato (créditos y Managed Service), prueba de concepto y conexión con AAP."""
    from .aprobacion_areas import AREAS
    if request.method == "POST":
        f = request.form
        import re as _re
        codigo = (f.get("codigo") or "").strip().lower()
        nombre = (f.get("nombre") or "").strip()
        tipo = f.get("tipo_contrato", "anual")
        ms = bool(f.get("managed_service"))
        inicio = (f.get("inicio") or ahora()[:10])[:10]
        meses = int(f.get("meses") or 12)
        creditos = float(f.get("creditos") or 0)
        contactos = []
        for i, n_ in enumerate(f.getlist("ct_nombre")):
            fila = {k: (f.getlist(f"ct_{k}")[i] if i < len(f.getlist(f"ct_{k}")) else "").strip() for k in ("nombre", "cargo", "rol", "correo", "telefono")}
            if fila["nombre"] or fila["correo"]:
                contactos.append(fila)
        errores = []
        if not contactos:
            errores.append("Agrega al menos un contacto del cliente")
        if not nombre or not _re.fullmatch(r"[a-z0-9]{2,12}", codigo):
            errores.append("Nombre y código (2 a 12 letras o números, en minúsculas) son obligatorios")
        if q1("SELECT 1 FROM clientes WHERE codigo=?", codigo):
            errores.append(f"Ya existe un cliente con el código «{codigo}»")
        if tipo in ("anual", "plurianual") and not ms:
            errores.append("El primer año el cliente compra créditos y Managed Service: marca el Managed Service")
        if tipo in ("anual", "plurianual") and creditos <= 0:
            errores.append("Indica los créditos que compra")
        if errores:
            for e_ in errores:
                flash(e_, "error")
            return render_template("admin/alta.html", f=f, contactos=contactos, tipos=TIPOS_CONTRATO, areas=AREAS, roles=ROLES_CONTACTO)
        from datetime import date
        d0 = date.fromisoformat(inicio)
        ms_hasta = ""
        if ms:
            y_, m_ = divmod(d0.month - 1 + max(meses, 12), 12)
            ms_hasta = d0.replace(year=d0.year + y_, month=m_ + 1, day=min(d0.day, 28)).isoformat()
        poc = {"si": bool(f.get("poc")), "entorno": f.get("poc_entorno", "cliente"), "inicio": f.get("poc_inicio", ""), "fin": f.get("poc_fin", ""),
               "objetivos": f.get("poc_objetivos", "").strip(), "areas": f.getlist("poc_areas")}
        contrato = {"tipo": tipo, "meses": meses, "inicio": inicio, "managed_service": ms, "precio_credito": float(f.get("precio_credito") or 0),
                    "pais": f.get("pais", ""), "industria": f.get("industria", ""), "vendedor": f.get("vendedor", ""),
                    "contactos": contactos, "contacto": contactos[0] if contactos else {},
                    "poc": poc, "alta_por": g.usuario["usuario"], "alta": ahora()}
        datos = {"codigo": codigo, "nombre": nombre, "descripcion": f.get("descripcion", ""), "aap_url": f.get("aap_url", "").strip(),
                 "aap_token": f.get("aap_token", "").strip(), "aap_verificar": int(bool(f.get("aap_verificar"))), "tickets_tipo": "faceops",
                 "tickets_conf": "{}", "clasificacion": "{}", "recoleccion": "{}", "moneda": f.get("moneda", "USD"),
                 "costo_hora": float(f.get("costo_hora") or 40), "activo": 1, "creado": ahora(), "cr_base": float(f.get("cr_base") or 0),
                 "precio_credito": float(f.get("precio_credito") or 0), "contrato_inicio": inicio, "ms_hasta": ms_hasta,
                 "contrato": json.dumps(contrato, ensure_ascii=False)}
        cid = ej("INSERT INTO clientes(" + ", ".join(datos) + ") VALUES (" + ",".join("?" * len(datos)) + ")", *datos.values())
        from .semillas import gobierno_base
        gobierno_base(cid)
        if creditos:
            from .creditos import cargar
            cargar(cid, creditos, f"Bolsa inicial · {dict(TIPOS_CONTRATO).get(tipo, tipo)}", g.usuario["usuario"])
        auditar(g.usuario["usuario"], cid, "alta de cliente", nombre, {k: v for k, v in contrato.items() if k != "contacto"})
        flash(f"Cliente «{nombre}» dado de alta" + (f" con {creditos:g} créditos" if creditos else "") + ". Sigue con la conexión a AAP, los perfiles y las áreas.", "ok")
        return redirect(url_for("admin.cliente", cid=cid))
    return render_template("admin/alta.html", f={}, contactos=[], tipos=TIPOS_CONTRATO, areas=AREAS, roles=ROLES_CONTACTO)


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
        if c and accion == "soporte":
            from . import kb
            try:
                kb.fijar_productos(cid, request.form.getlist("productos"))
                auditar(g.usuario["usuario"], cid, "productos con soporte de incidentes", c["nombre"], {"productos": request.form.getlist("productos")})
                flash("Productos con soporte de incidentes guardados", "ok")
            except kb.SinKB as e:
                flash(str(e), "error")
            return redirect(url_for("admin.cliente", cid=cid) + "#soporte")
        if c and accion == "managed_service":
            from .creditos import validar_contrato
            try:
                validar_contrato(f.get("contrato_inicio", ""), f.get("ms_hasta", ""))
                ej("UPDATE clientes SET contrato_inicio=?, ms_hasta=? WHERE id=?", f["contrato_inicio"][:10], f["ms_hasta"][:10], cid)
                auditar(g.usuario["usuario"], cid, "contrato managed service", c["nombre"], {"inicio": f["contrato_inicio"], "ms_hasta": f["ms_hasta"]})
                flash("Contrato de Managed Service guardado", "ok")
            except ValueError as e:
                flash(str(e), "error")
            return redirect(url_for("admin.cliente", cid=cid) + "#managed-service")
        if c and accion in ("creditos", "cargos"):
            from .creditos import cargar
            try:
                if accion == "creditos":
                    n = float(f.get("cr_monto") or 0)
                    if not n:
                        raise ValueError("Indica cuántos créditos")
                    cargar(cid, n, f.get("cr_detalle") or ("Compra de créditos" if n > 0 else "Ajuste"), g.usuario["usuario"],
                           "compra" if n > 0 and f.get("cr_tipo") != "ajuste" else "ajuste")
                    auditar(g.usuario["usuario"], cid, "cargar créditos", c["nombre"], {"creditos": n, "detalle": f.get("cr_detalle", "")})
                    flash(f"{n:+g} créditos para {c['nombre']}", "ok")
                else:
                    ej("UPDATE clientes SET cr_base=?, precio_credito=? WHERE id=?", float(f.get("cr_base") or 0), float(f.get("precio_credito") or 0), cid)
                    auditar(g.usuario["usuario"], cid, "cargos mensuales", c["nombre"], {"base": f.get("cr_base")})
                    flash("Cargo fijo mensual guardado (rige desde el próximo mes)", "ok")
            except ValueError as e:
                flash(str(e), "error")
            return redirect(url_for("admin.cliente", cid=cid) + "#creditos")
        if c and accion == "aprobadores":   # área de cada persona y cadena de aprobación (por áreas) de cada automatización
            from .aprobacion_areas import NOMBRES
            for k in f.keys():
                if k.startswith("area_p__") and k[8:].isdigit():
                    v = f.get(k) if f.get(k) in NOMBRES else ""
                    ej("UPDATE perfiles SET area=? WHERE cliente_id=? AND usuario_id=?", v, cid, int(k[8:]))
                if k.startswith("area_a__") and k[8:].isdigit():
                    aid = int(k[8:])
                    if f.get(k) in NOMBRES:
                        ej("UPDATE automatizaciones SET area=? WHERE id=? AND cliente_id=?", f.get(k), aid, cid)
                    sel = f.getlist(f"etapas__{aid}")
                    par = set(f.getlist(f"paralelo__{aid}"))   # índices de etapas que van en paralelo con la anterior
                    grupos = []
                    for i, x in enumerate(sel):
                        if x not in NOMBRES or any(x in g_ for g_ in grupos):
                            continue
                        if grupos and str(i) in par:
                            grupos[-1].append(x)
                        else:
                            grupos.append([x])
                    cad = [g_[0] if len(g_) == 1 else g_ for g_ in grupos]
                    ej("UPDATE automatizaciones SET aprobacion_etapas=? WHERE id=? AND cliente_id=?",
                       json.dumps(cad if len(cad) > 1 or (cad and cad[0] != f.get(k)) else []), aid, cid)
            auditar(g.usuario["usuario"], cid, "áreas y cadenas de aprobación", c["nombre"], {})
            flash("Áreas y cadenas de aprobación guardadas: rigen para las aprobaciones nuevas y las etapas siguientes", "ok")
            return redirect(url_for("admin.cliente", cid=cid) + "#aprobadores")
        if c and accion == "readme":   # README de cada automatización en Git (y su descripción en FaceOps)
            from .readme import publicar
            r = publicar(cid)
            ok = sum(1 for v in r.values() if v in ("ok", "sin cambios"))
            flash(f"README publicados en Git: {ok} de {len(r)}" + ("" if ok == len(r) else " · con errores: " + ", ".join(k for k, v in r.items() if v not in ("ok", "sin cambios"))[:300]), "ok" if ok == len(r) else "error")
            return redirect(url_for("admin.cliente", cid=cid))
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
    from .creditos import TIPOS, estado as estado_creditos, movimientos
    cred = {"e": estado_creditos(c), "movs": movimientos(c["id"], 30), "tipos": TIPOS} if c else None
    soporte = None
    if c:
        from . import kb
        try:
            soporte = {"productos": kb.productos(), "activos": kb.productos_cliente(c["id"])}
        except kb.SinKB as e:
            soporte = {"error": str(e)}
    ap_ctx = {}
    if c:   # áreas: de cada persona y de cada automatización con aprobación (y su cadena de etapas)
        from .aprobacion_areas import AREAS, area_de, etapas_de, grupos_de
        ap_ctx = {"ap_areas": [(k, n) for k, n, _ in AREAS],
                  "ap_personas": q("SELECT u.id, u.usuario, u.nombre, p.aprobador, p.modo, COALESCE(p.area, '') area FROM perfiles p JOIN usuarios u ON u.id=p.usuario_id "
                                   "WHERE p.cliente_id=? AND u.activo=1 ORDER BY u.nombre", c["id"]),
                  "ap_autos": [dict(a_, area_k=area_de(a_), etapas_l=etapas_de(a_),
                                    paralelo_l=[i for i, x in enumerate(etapas_de(a_)) if i and any(x in g_ and g_.index(x) > 0 for g_ in grupos_de(a_))]) for a_ in q(
                      "SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1 AND (requiere_aprobacion=1 OR aprobacion_ambientes NOT IN ('', '[]') "
                      "OR aprobacion_aap=1) ORDER BY nombre", c["id"])]}
    return render_template("admin/cliente.html", **ap_ctx, c=c, cred=cred, soporte=soporte, conf=j(c["tickets_conf"], {}) if c else {},
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
            roles = [r for r in f.getlist("roles") if r in ("arquitectura", "infraestructura")]
            ej("INSERT INTO perfiles(usuario_id, cliente_id, segmentos, ambientes, modo, aprobador, roles) VALUES (?,?,?,?,?,?,?) "
               "ON CONFLICT(usuario_id, cliente_id) DO UPDATE SET segmentos=excluded.segmentos, ambientes=excluded.ambientes, "
               "modo=excluded.modo, aprobador=excluded.aprobador, roles=excluded.roles",
               uid, int(f["cliente_id"]), json.dumps(segs, ensure_ascii=False), json.dumps(ambs), f.get("modo", "lectura"), int(bool(f.get("aprobador"))),
               json.dumps(roles))
            auditar(g.usuario["usuario"], int(f["cliente_id"]), "perfil", u["usuario"], {"segmentos": segs, "ambientes": ambs, "modo": f.get("modo"), "roles": roles})
            flash("Perfil guardado", "ok")
            return redirect(url_for("admin.usuario", uid=uid))
        if f.get("accion") == "invitacion" and u:   # enlace de un solo uso: la persona escanea su QR al entrar por primera vez
            import hashlib, secrets
            from datetime import datetime, timedelta, timezone
            tok = secrets.token_urlsafe(24)
            ej("UPDATE usuarios SET invita_hash=?, invita_expira=? WHERE id=?", hashlib.sha256(tok.encode()).hexdigest(),
               (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(timespec="seconds"), uid)
            auditar(g.usuario["usuario"], None, "enlace de activación del doble factor", u["usuario"], {})
            return render_template("admin/usuario.html", **_ctx_usuario(uid), invitacion=os.environ.get("FACEOPS_URL_PUBLICA", "https://faceops.34.176.42.56.sslip.io:8843/faceops").rstrip("/")
                                   + url_for("consola.activar", tok=tok)[len(request.script_root):])
        if f.get("accion") == "doble_factor" and u:   # para quien entrará solo desde internet: el admin le entrega el código
            from .doble_factor import nuevo_secreto, uri
            sec = nuevo_secreto()
            ej("UPDATE usuarios SET totp_secreto=?, totp_activo=1 WHERE id=?", sec, uid)
            auditar(g.usuario["usuario"], None, "doble factor preparado por administrador", u["usuario"], {})
            return render_template("admin/usuario.html", **_ctx_usuario(uid), enrolar={"uri": uri(sec, u["usuario"]), "secreto": sec})
        if f.get("accion") == "quitar_perfil" and u:
            ej("DELETE FROM perfiles WHERE id=? AND usuario_id=?", int(f["perfil_id"]), uid)
            return redirect(url_for("admin.usuario", uid=uid))
        datos = {"usuario": f["usuario"].strip().lower(), "nombre": f["nombre"].strip(), "correo": f.get("correo", ""),
                 "es_admin": int(bool(f.get("es_admin"))), "activo": int(bool(f.get("activo"))),
                 "expira": (f.get("expira") + "T23:59:59+00:00") if f.get("expira") else ""}
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
    return render_template("admin/usuario.html", **_ctx_usuario(uid))


def _ctx_usuario(uid):
    u = q1("SELECT * FROM usuarios WHERE id=?", uid) if uid else None
    clientes = q("SELECT * FROM clientes ORDER BY nombre")
    perfiles = q("SELECT p.*, c.nombre cliente FROM perfiles p JOIN clientes c ON c.id=p.cliente_id WHERE usuario_id=?", uid) if u else []
    segs = {c["id"]: [s["nombre"] for s in q("SELECT nombre FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", c["id"])]
            for c in clientes}
    ambs = {c["id"]: [r["ambiente"] for r in q("SELECT DISTINCT ambiente FROM equipos WHERE cliente_id=? AND ambiente!='' ORDER BY ambiente", c["id"])]
            for c in clientes}
    return {"u": u, "perfiles": perfiles, "clientes": clientes, "segs": segs, "ambs": ambs}


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


# ------------------------------------------------------------------ cobro por beneficio (fórmula global)
@bp.route("/cobro", methods=["GET", "POST"])
@requiere_admin
def cobro():
    """Cobro por beneficio: tareas automatizadas (la unidad del factor; una tarea sirve a varias automatizaciones) y
    automatizaciones como suma de sus tareas. Lo único que se ajusta es el esfuerzo manual de cada tarea."""
    from .economia import beneficio, catalogo_tareas, cobro as conf_cobro, conf, factor_tarea, sincronizar_minutos, tiempos_medidos
    if request.method == "POST":
        cod, m = request.form.get("codigo", ""), request.form.get("manual", "")
        tareas, _ = catalogo_tareas()
        if cod in tareas and m.replace(".", "", 1).isdigit():
            ej("INSERT INTO tareas_ajuste(codigo, manual, usuario, fecha) VALUES (?,?,?,?) ON CONFLICT(codigo) DO UPDATE SET "
               "manual=excluded.manual, usuario=excluded.usuario, fecha=excluded.fecha", cod, float(m), g.usuario["usuario"], ahora())
            sincronizar_minutos()
            auditar(g.usuario["usuario"], None, "ajustar esfuerzo manual de tarea", cod, {"manual": float(m)})
            flash(f"«{tareas[cod]['nombre']}»: {m} min a mano · se recalcularon las automatizaciones que la usan", "ok")
        return redirect(url_for("admin.cobro", vista="tareas") + "#t-" + cod)
    cb, med, cr = conf_cobro(), tiempos_medidos(), conf().get("automatizaciones") or {}
    cat = catalogo_tareas()
    tareas, comp = cat
    autos, vistos = [], set()
    for a in q("SELECT * FROM automatizaciones ORDER BY categoria, nombre"):
        if a["nombre_aap"] in vistos:
            continue
        vistos.add(a["nombre_aap"])
        b = beneficio(a, med, cb, cat)
        unidad = (cr.get(a["nombre_aap"]) or {}).get("unidad") or ("equipo" if a.get("usa_limite") else "ejecución")
        autos.append(dict(b, nombre=a["nombre"], nombre_aap=a["nombre_aap"], categoria=a["categoria"] or "General", unidad=unidad, visible=a["visible"]))
    usos = {}
    for x in autos:
        for t in x["tareas"]:
            if t.get("codigo"):
                usos.setdefault(t["codigo"], []).append(x["nombre"])
    lista_t = []
    for k, t in tareas.items():
        o, f = factor_tarea(t["manual"], t["auto"])
        lista_t.append(dict(t, optimizacion=o, factor=round(f, 2), usos=usos.get(k, [])))
    lista_t.sort(key=lambda t: (-len(t["usos"]), t["nombre"]))
    autos.sort(key=lambda x: (x["categoria"], -x["factor"], x["nombre"]))
    return render_template("admin/cobro.html", vista=request.args.get("vista", "tareas"), tareas=lista_t, autos=autos, cb=cb,
                           sin_desglose=[x for x in autos if not x["desglose"]])


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
