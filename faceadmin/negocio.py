"""FaceAdmin · ciclo comercial (propuesta → contrato → orden de compra) y tenants (clientes que administran a sus propios clientes).
Se registra sobre la app de app.py: mismas tablas, mismo ingreso y mismo alcance por tenant."""
import os
import secrets
from datetime import date, datetime

from flask import abort, flash, g, redirect, render_template, request, send_file, url_for
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from app import (ALCANCES, BD, ahora, app, bitacora, cliente_ok, ej, en_alcance, es_invoke, facturacion, q, q1, requiere_login,
                 solo_invoke)

ARCH = os.path.join(os.path.dirname(BD), "archivos")
EXT = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".doc"}


def _num(prefijo):
    return f"{prefijo}-{date.today().year}-{secrets.randbelow(9000) + 1000}"


def _emisor(c):
    """Quién emite propuestas, contratos y facturas al cliente: Invoke o su tenant."""
    if c.get("tenant_id") and not c.get("propio"):   # el FaceOps propio del tenant lo contrata y factura Invoke
        t = q1("SELECT * FROM clientes WHERE id=?", c["tenant_id"]) or {}
        return {"codigo": t.get("codigo", ""), "nombre": t.get("razon_social") or t.get("nombre", ""), "rut": t.get("rut", "")}
    return {"codigo": "invoke", "nombre": "Invoke SpA", "rut": ""}


def _solo_invoke_si_propio(c):
    if c.get("propio") and not es_invoke():   # el ciclo del FaceOps propio del tenant lo lleva Invoke
        abort(403)


def _guardar(arch, cid, tipo):
    if not arch or not arch.filename:
        return ""
    ext = os.path.splitext(arch.filename)[1].lower()
    if ext not in EXT:
        raise ValueError("Formato no permitido (PDF, imagen o Word)")
    os.makedirs(ARCH, exist_ok=True)
    nombre = f"{cid}-{tipo}-{secrets.token_hex(6)}{ext}"
    arch.save(os.path.join(ARCH, nombre))
    return nombre


def comercial(cid):
    """Propuestas, contratos y órdenes del cliente, y la etapa."""
    return {"propuestas": q("SELECT * FROM propuestas WHERE cliente_id=? ORDER BY id DESC", cid),
            "contratos": q("SELECT * FROM contratos WHERE cliente_id=? ORDER BY id DESC", cid),
            "ordenes": q("SELECT * FROM ordenes WHERE cliente_id=? ORDER BY id DESC", cid)}


# ---------------------------------------------------------------- propuestas
@app.route("/clientes/<int:cid>/propuesta", methods=["POST"])
@requiere_login
def propuesta_nueva(cid):
    c = cliente_ok(cid)
    _solo_invoke_si_propio(c)
    f = request.form
    try:
        mensual = float((f.get("mensual") or "0").replace(".", "").replace(",", "."))
        impl = float((f.get("implementacion") or "0").replace(".", "").replace(",", "."))
    except ValueError:
        mensual = impl = -1
    if mensual <= 0 or impl < 0 or f.get("alcance") not in ALCANCES:
        flash("Indica el alcance y un valor mensual mayor que cero", "error")
        return redirect(url_for("cliente", cid=cid) + "#comercial")
    ver = (q1("SELECT MAX(version) v FROM propuestas WHERE cliente_id=?", cid) or {}).get("v") or 0
    ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, emisor) "
       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cid, f"PRO-{c['codigo'].upper()}-{date.today().year}", ver + 1, f["alcance"], max(1, int(f.get("anios") or 1)),
       mensual, impl, f.get("moneda") or "CLP", int(f.get("creditos") or 0), f.get("condiciones", "")[:3000], "borrador", ahora(), g.usuario["usuario"],
       _emisor(c)["codigo"])
    bitacora("propuesta creada", c["codigo"], {"version": ver + 1, "mensual": mensual})
    flash(f"Propuesta v{ver + 1} creada en borrador", "ok")
    return redirect(url_for("cliente", cid=cid) + "#comercial")


@app.route("/propuestas/<int:pid>")
@requiere_login
def propuesta_doc(pid):
    p = q1("SELECT * FROM propuestas WHERE id=?", pid) or abort(404)
    c = cliente_ok(p["cliente_id"])
    return render_template("doc_propuesta.html", p=p, c=c, a=ALCANCES.get(p["alcance"], ALCANCES["estandar"]), em=_emisor(c),
                           hoy=date.today().isoformat())


@app.route("/propuestas/<int:pid>/estado", methods=["POST"])
@requiere_login
def propuesta_estado(pid):
    p = q1("SELECT * FROM propuestas WHERE id=?", pid) or abort(404)
    c = cliente_ok(p["cliente_id"])
    nuevo = request.form.get("estado")
    _solo_invoke_si_propio(c)
    paso = {"borrador": ("enviada",), "enviada": ("aceptada", "rechazada")}.get(p["estado"], ())
    if nuevo not in paso:
        abort(400)
    campo = "enviada" if nuevo == "enviada" else "respuesta"
    ej(f"UPDATE propuestas SET estado=?, {campo}=? WHERE id=?", nuevo, ahora(), pid)
    if nuevo == "aceptada":   # las demás versiones quedan descartadas
        ej("UPDATE propuestas SET estado='rechazada', respuesta=? WHERE cliente_id=? AND id<>? AND estado IN ('borrador','enviada')", ahora(), c["id"], pid)
    bitacora(f"propuesta {nuevo}", c["codigo"], {"version": p["version"]})
    flash(f"Propuesta v{p['version']} {nuevo}" + (": ya se puede generar el contrato" if nuevo == "aceptada" else ""), "ok")
    return redirect(url_for("cliente", cid=c["id"]) + "#comercial")


# ---------------------------------------------------------------- contratos
@app.route("/clientes/<int:cid>/contrato", methods=["POST"])
@requiere_login
def contrato_generar(cid):
    c = cliente_ok(cid)
    _solo_invoke_si_propio(c)
    p = q1("SELECT * FROM propuestas WHERE cliente_id=? AND estado='aceptada' ORDER BY id DESC LIMIT 1", cid)
    if not p:
        flash("Primero la propuesta debe estar aceptada", "error")
        return redirect(url_for("cliente", cid=cid) + "#comercial")
    if q1("SELECT id FROM contratos WHERE propuesta_id=? AND estado<>'anulado'", p["id"]):
        flash("Esa propuesta ya tiene su contrato", "error")
        return redirect(url_for("cliente", cid=cid) + "#comercial")
    ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por) VALUES (?,?,?,?,?,?)",
       cid, p["id"], _num("CTR"), "borrador", ahora(), g.usuario["usuario"])
    bitacora("contrato generado", c["codigo"], {"propuesta": p["version"]})
    flash("Contrato generado desde la propuesta aceptada: revísalo y envíalo a firma", "ok")
    return redirect(url_for("cliente", cid=cid) + "#comercial")


@app.route("/contratos/<int:kid>")
@requiere_login
def contrato_doc(kid):
    k = q1("SELECT * FROM contratos WHERE id=?", kid) or abort(404)
    c = cliente_ok(k["cliente_id"])
    p = q1("SELECT * FROM propuestas WHERE id=?", k["propuesta_id"]) or {}
    return render_template("doc_contrato.html", k=k, p=p, c=c, a=ALCANCES.get(p.get("alcance"), ALCANCES["estandar"]), em=_emisor(c),
                           hoy=date.today().isoformat())


@app.route("/contratos/<int:kid>/estado", methods=["POST"])
@requiere_login
def contrato_estado(kid):
    k = q1("SELECT * FROM contratos WHERE id=?", kid) or abort(404)
    c = cliente_ok(k["cliente_id"])
    f = request.form
    _solo_invoke_si_propio(c)
    ac = f.get("accion")
    try:
        if ac == "enviar" and k["estado"] == "borrador":
            ej("UPDATE contratos SET estado='enviado', enviado=? WHERE id=?", ahora(), kid)
        elif ac == "firmar" and k["estado"] in ("borrador", "enviado"):
            if not f.get("firmante") or not f.get("fecha"):
                raise ValueError("Indica quién firmó por el cliente y la fecha")
            arch = _guardar(request.files.get("archivo"), c["id"], "contrato")
            if not arch:
                raise ValueError("Adjunta el contrato firmado")
            p = q1("SELECT anios FROM propuestas WHERE id=?", k["propuesta_id"]) or {"anios": 1}
            ini = date.fromisoformat(f["fecha"])
            fin = ini.replace(year=ini.year + int(p["anios"] or 1))
            ej("UPDATE contratos SET estado='firmado', firmado=?, firmante=?, archivo=?, inicio=?, fin=? WHERE id=?",
               f["fecha"], f["firmante"][:120], arch, str(ini), str(fin), kid)
        elif ac == "anular" and k["estado"] != "firmado":
            ej("UPDATE contratos SET estado='anulado' WHERE id=?", kid)
        else:
            raise ValueError("Acción no válida en este estado")
        bitacora(f"contrato · {ac}", c["codigo"], {"numero": k["numero"]})
        flash({"enviar": "Contrato enviado a firma", "firmar": "Contrato firmado y archivado: falta la orden de compra",
               "anular": "Contrato anulado"}[ac], "ok")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("cliente", cid=c["id"]) + "#comercial")


@app.route("/clientes/<int:cid>/oc", methods=["POST"])
@requiere_login
def orden_compra(cid):
    c = cliente_ok(cid)
    _solo_invoke_si_propio(c)
    f = request.form
    k = q1("SELECT * FROM contratos WHERE cliente_id=? AND estado='firmado' ORDER BY id DESC LIMIT 1", cid)
    try:
        if not k:
            raise ValueError("La orden de compra va después del contrato firmado")
        monto = float((f.get("monto") or "0").replace(".", "").replace(",", "."))
        if not f.get("numero") or monto <= 0:
            raise ValueError("Número y monto de la orden de compra son obligatorios")
        arch = _guardar(request.files.get("archivo"), cid, "oc")
        ej("INSERT INTO ordenes (cliente_id, contrato_id, numero, fecha, monto, moneda, archivo, creado, por) VALUES (?,?,?,?,?,?,?,?,?)",
           cid, k["id"], f["numero"][:40], f.get("fecha") or date.today().isoformat(), monto, f.get("moneda") or "CLP", arch, ahora(), g.usuario["usuario"])
        bitacora("orden de compra", c["codigo"], {"numero": f["numero"], "monto": monto})
        flash("Orden de compra registrada: ya se puede habilitar FaceOps", "ok")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("cliente", cid=cid) + "#comercial")


@app.route("/archivos/<nombre>")
@requiere_login
def archivo(nombre):
    nombre = secure_filename(nombre)
    cid = int(nombre.split("-")[0]) if nombre.split("-")[0].isdigit() else 0
    cliente_ok(cid)
    ruta = os.path.join(ARCH, nombre)
    if not os.path.exists(ruta):
        abort(404)
    return send_file(ruta, as_attachment=True)


# ---------------------------------------------------------------- tenants
@app.route("/tenants/nuevo", methods=["POST"])
@requiere_login
def tenant_nuevo():
    """Tenant: un cliente de Invoke que administra a sus propios clientes desde FaceAdmin (un nivel)."""
    solo_invoke()
    f = request.form
    cod = "".join(ch for ch in f.get("codigo", "").lower() if ch.isalnum() or ch == "-")[:20]
    if not cod or not f.get("nombre") or not f.get("admin_correo"):
        flash("Código, nombre y correo del administrador del tenant son obligatorios", "error")
        return redirect(url_for("panel"))
    if q1("SELECT id FROM clientes WHERE codigo=?", cod):
        flash("Ya existe ese código", "error")
        return redirect(url_for("panel"))
    tid = ej("INSERT INTO clientes (codigo, nombre, url, rol, creado, instancia_id, razon_social, rut, pais, contacto_nombre, contacto_correo, contacto_telefono, "
             "tipo, habilitado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cod, f["nombre"].strip()[:80], "", "tenant", ahora(), "",
             f.get("razon_social", "")[:120], f.get("rut", "")[:30], f.get("pais", "")[:40], f.get("contacto_nombre", "")[:80],
             f.get("contacto_correo", "")[:120], f.get("contacto_telefono", "")[:40], "tenant", 0)
    enlace = _usuario_tenant(tid, f["admin_correo"], f.get("admin_nombre") or f["admin_correo"])
    bitacora("tenant nuevo", cod, {"admin": f["admin_correo"]})
    flash(f"Tenant {f['nombre']} creado. Envía este enlace (un uso, 72 h) a su administrador para definir su clave: {enlace}", "ok")
    return redirect(url_for("tenant", tid=tid))


def _usuario_tenant(tid, correo, nombre):
    from app import nueva_activacion
    usr = correo.split("@")[0].lower()[:40]
    if q1("SELECT id FROM usuarios WHERE usuario=?", usr):
        usr = f"{usr}.{secrets.randbelow(90) + 10}"
    ej("INSERT INTO usuarios (usuario, nombre, clave, creado, tenant_id, correo) VALUES (?,?,?,?,?,?)", usr, nombre[:80], "!", ahora(), tid, correo[:120])
    tok = nueva_activacion(usr, horas=72)
    return request.host_url.rstrip("/") + url_for("activar", tok=tok)


@app.route("/tenants/<int:tid>", methods=["GET", "POST"])
@requiere_login
def tenant(tid):
    t = cliente_ok(tid)
    if t.get("tipo") != "tenant":
        abort(404)
    if request.method == "POST":   # agregar usuario del tenant (Invoke o el propio tenant)
        f = request.form
        if not f.get("correo"):
            flash("Indica el correo", "error")
        else:
            enlace = _usuario_tenant(tid, f["correo"], f.get("nombre") or f["correo"])
            bitacora("usuario de tenant", t["codigo"], {"correo": f["correo"]})
            flash(f"Usuario creado. Envíale este enlace (un uso, 72 h) para definir su clave: {enlace}", "ok")
        return redirect(url_for("tenant", tid=tid))
    from app import etapa, ETAPAS
    cl = q("SELECT * FROM clientes WHERE tenant_id=? ORDER BY nombre", tid)
    for c in cl:
        c["etapa"] = etapa(c)
        c["fa"] = facturacion(c["id"])
    propio = next((c for c in cl if c.get("propio")), None)
    cl = [c for c in cl if not c.get("propio")]
    return render_template("tenant.html", t=t, fa=facturacion(tid), clientes=cl, propio=propio, etapas=ETAPAS, hoy=date.today().isoformat(),
                           usuarios=q("SELECT id, usuario, nombre, correo, creado, clave FROM usuarios WHERE tenant_id=? ORDER BY nombre", tid))


@app.route("/tenants/<int:tid>/propio", methods=["POST"])
@requiere_login
def tenant_propio(tid):
    """El tenant también es cliente de Invoke: su FaceOps propio parte como prospecto con su ciclo comercial con Invoke."""
    solo_invoke()
    t = cliente_ok(tid)
    if t.get("tipo") != "tenant":
        abort(404)
    ya = q1("SELECT id FROM clientes WHERE tenant_id=? AND propio=1", tid)
    if ya:
        return redirect(url_for("cliente", cid=ya["id"]))
    cid = ej("INSERT INTO clientes (codigo, nombre, url, rol, creado, instancia_id, razon_social, rut, pais, contacto_nombre, contacto_correo, contacto_telefono, "
             "tipo, tenant_id, habilitado, propio, alcance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", f"{t['codigo']}-propio", f"{t['nombre']} (FaceOps propio)", "",
             "cliente", ahora(), "", t.get("razon_social", ""), t.get("rut", ""), t.get("pais", ""), t.get("contacto_nombre", ""), t.get("contacto_correo", ""),
             t.get("contacto_telefono", ""), "cliente", tid, 0, 1, "estandar")
    bitacora("FaceOps propio de tenant", t["codigo"])
    flash(f"{t['nombre']} también como cliente: arma la propuesta de su FaceOps propio", "ok")
    return redirect(url_for("cliente", cid=cid) + "#comercial")



# ---------------------------------------------------------------- contratos (menú)
def tipo_contrato(c):
    """Qué habilita el contrato: un tenant (administra clientes) o un FaceOps (cliente directo o cliente de un tenant)."""
    if c.get("tipo") == "tenant":
        return {"k": "tenant", "t": "Tenant", "habilita": "Administrar sus propios clientes en FaceAdmin"}
    if c.get("propio"):
        return {"k": "directo", "t": "Cliente directo", "habilita": "Su FaceOps propio (lo contrata y factura Invoke)"}
    if c.get("tenant_id"):
        return {"k": "de-tenant", "t": "Cliente de tenant", "habilita": "Su FaceOps (lo contrata y factura el tenant)"}
    return {"k": "directo", "t": "Cliente directo", "habilita": "Su FaceOps"}


def _empresa(c):
    """El FaceOps propio de un tenant es la misma empresa que el tenant: sus contratos se muestran juntos."""
    return c["tenant_id"] if c.get("propio") and c.get("tenant_id") else c["id"]


@app.route("/contratos")
@requiere_login
def contratos_lista():
    from app import ETAPAS, etapa
    filas = []
    for k in q("SELECT * FROM contratos ORDER BY id DESC"):
        c = q1("SELECT * FROM clientes WHERE id=?", k["cliente_id"])
        if not c or not en_alcance(c):
            continue
        k["c"], k["tipo"], k["em"] = c, tipo_contrato(c), _emisor(c)
        k["oc"] = q1("SELECT * FROM ordenes WHERE contrato_id=? ORDER BY id DESC LIMIT 1", k["id"])
        k["etapa"] = ETAPAS.get(etapa(c), "")
        filas.append(k)
    grupos = {}
    for k in filas:
        e = _empresa(k["c"])
        grupos.setdefault(e, {"empresa": q1("SELECT * FROM clientes WHERE id=?", e), "contratos": []})["contratos"].append(k)
    orden = sorted(grupos.values(), key=lambda gr: (-len(gr["contratos"]), gr["empresa"]["nombre"]))
    return render_template("contratos.html", grupos=orden, n=len(filas), firmados=sum(1 for k in filas if k["estado"] == "firmado"),
                           habilitan=sum(1 for k in filas if k["estado"] == "firmado" and (k["tipo"]["k"] == "tenant" or k["c"].get("habilitado"))))


@app.route("/contratos/<int:kid>/ficha")
@requiere_login
def contrato_ficha(kid):
    from app import ETAPAS, central, etapa
    k = q1("SELECT * FROM contratos WHERE id=?", kid) or abort(404)
    c = cliente_ok(k["cliente_id"])
    p = q1("SELECT * FROM propuestas WHERE id=?", k["propuesta_id"]) or {}
    emp = _empresa(c)
    hermanos = [h for h in q("SELECT k.*, c.nombre cn, c.tipo ct, c.propio, c.tenant_id FROM contratos k JOIN clientes c ON c.id=k.cliente_id "
                             "WHERE k.id<>? ORDER BY k.id DESC", kid) if _empresa({"id": h["cliente_id"], "propio": h["propio"], "tenant_id": h["tenant_id"]}) == emp
                and en_alcance(q1("SELECT * FROM clientes WHERE id=?", h["cliente_id"]))]
    for h in hermanos:
        h["tipo"] = tipo_contrato({"tipo": h["ct"], "propio": h["propio"], "tenant_id": h["tenant_id"]})
    tipo = tipo_contrato(c)
    return render_template("contrato_ficha.html", k=k, c=c, p=p, a=ALCANCES.get(p.get("alcance"), ALCANCES["estandar"]), em=_emisor(c), tipo=tipo,
                           oc=q1("SELECT * FROM ordenes WHERE contrato_id=? ORDER BY id DESC LIMIT 1", kid), hermanos=hermanos,
                           etapa_=etapa(c), etapas=ETAPAS, central=central() if tipo["k"] != "tenant" and c.get("habilitado") else {},
                           paquetes=q("SELECT * FROM paquetes WHERE cliente_id=? ORDER BY id DESC LIMIT 5", c["id"]),
                           clientes_t=q("SELECT * FROM clientes WHERE tenant_id=? AND COALESCE(propio,0)=0 ORDER BY nombre", c["id"]) if tipo["k"] == "tenant" else [])


def sembrar_contratos_apolo():
    """Inversiones Apolo con dos contratos ficticios con Invoke: uno como tenant y otro como cliente directo (su FaceOps propio).
    El de cliente directo, firmado y con orden de compra, habilita su FaceOps y la descarga del instalador."""
    from datetime import timedelta
    from app import nuevo_id
    with app.app_context():
        t = q1("SELECT * FROM clientes WHERE codigo='apolo' AND tipo='tenant'")
        if not t:
            return
        pr = q1("SELECT * FROM clientes WHERE tenant_id=? AND propio=1", t["id"])
        if not pr:
            ej("INSERT INTO clientes (codigo, nombre, url, rol, creado, instancia_id, razon_social, rut, pais, contacto_nombre, contacto_correo, contacto_telefono, "
               "tipo, tenant_id, habilitado, propio, alcance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", f"{t['codigo']}-propio", f"{t['nombre']} (FaceOps propio)", "",
               "cliente", ahora(), "", t.get("razon_social", ""), t.get("rut", ""), t.get("pais", ""), t.get("contacto_nombre", ""), t.get("contacto_correo", ""),
               t.get("contacto_telefono", ""), "cliente", t["id"], 0, 1, "estandar")
            pr = q1("SELECT * FROM clientes WHERE tenant_id=? AND propio=1", t["id"])
        hoy = date.today()
        fin = hoy.replace(year=hoy.year + 2)
        planes = ((t, "T", "estandar", 2, 2500000, 0, 0, "Licencia de tenant: administra sus propios clientes en FaceAdmin (propuestas, contratos, facturación y "
                   "bloqueo). Invoke factura al tenant. Contrato ficticio de prueba."),
                  (pr, "C", "estandar", 2, 1900000, 4200000, 3000, "FaceOps propio de Inversiones Apolo como cliente directo de Invoke, con el consultor de "
                   "Invoke como cluster-admin durante el contrato. Contrato ficticio de prueba."))
        for c, letra, alc, anios, mensual, impl, cred, cond in planes:
            num = f"CTR-APOLO-{letra}-{hoy.year}"
            if q1("SELECT id FROM contratos WHERE numero=?", num):
                continue
            ej("UPDATE propuestas SET estado='rechazada', respuesta=? WHERE cliente_id=? AND estado IN ('borrador','enviada')", ahora(), c["id"])
            ver = (q1("SELECT MAX(version) v FROM propuestas WHERE cliente_id=?", c["id"]) or {}).get("v") or 0
            pid = ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, "
                     "enviada, respuesta, emisor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], f"PRO-APOLO-{letra}-{hoy.year}", ver + 1, alc, anios, mensual,
                     impl, "CLP", cred, cond, "aceptada", ahora(), "cquezada", ahora(), ahora(), "invoke")
            kid = ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por, enviado, firmado, firmante, archivo, inicio, fin) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], pid, num, "firmado", ahora(), "cquezada", ahora(), str(hoy),
                     "Marcela Ríos, Gerente General (dato ficticio)", "", str(hoy), str(fin))
            ej("INSERT INTO ordenes (cliente_id, contrato_id, numero, fecha, monto, moneda, archivo, creado, por) VALUES (?,?,?,?,?,?,?,?,?)",
               c["id"], kid, f"OC-APOLO-{letra}-{hoy.year}", str(hoy), impl or mensual, "CLP", "", ahora(), "cquezada")
            ej("INSERT INTO bitacora (fecha, usuario, accion, objeto, detalle) VALUES (?,?,?,?,?)", ahora(), "sistema", "contrato ficticio firmado con OC",
               c["codigo"], f'{{"contrato": "{num}"}}')
        pr = q1("SELECT * FROM clientes WHERE id=?", pr["id"])
        if not pr.get("habilitado") and q1("SELECT id FROM contratos WHERE numero=? AND estado='firmado'", f"CTR-APOLO-C-{hoy.year}"):
            ej("UPDATE clientes SET habilitado=1, instancia_id=?, consultor_nombre=?, consultor_correo=?, consultor_usuario=?, contrato_inicio=?, contrato_anios=?, "
               "alcance=?, activacion_limite=? WHERE id=?", pr.get("instancia_id") or nuevo_id(pr["codigo"]), "Cristian Quezada", "cquezada@invoke.cl", "cquezada",
               str(hoy), 2, "estandar", str(hoy + timedelta(days=42)), pr["id"])
            ej("INSERT INTO bitacora (fecha, usuario, accion, objeto, detalle) VALUES (?,?,?,?,?)", ahora(), "sistema", "FaceOps habilitado por contrato",
               pr["codigo"], f'{{"contrato": "CTR-APOLO-C-{hoy.year}"}}')

# ---------------------------------------------------------------- datos de prueba: Inversiones Apolo
def sembrar_tenant_demo():
    from datetime import timedelta
    with app.app_context():
        if q1("SELECT id FROM clientes WHERE codigo='apolo'"):
            return
        hoy = date.today()
        tid = ej("INSERT INTO clientes (codigo, nombre, url, rol, creado, instancia_id, razon_social, rut, pais, contacto_nombre, contacto_correo, tipo, habilitado) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", "apolo", "Inversiones Apolo", "", "tenant", ahora(), "", "Inversiones Apolo S.A. (dato de prueba)",
                 "76.000.000-0", "Chile", "Marcela Ríos", "mrios@apolo.example", "tenant", 0)
        ej("INSERT INTO usuarios (usuario, nombre, clave, creado, tenant_id, correo) VALUES (?,?,?,?,?,?)", "apolo.admin", "Administración Apolo", "!", ahora(),
           tid, "admin@apolo.example")
        for num, em, ve, monto, pag in (("F-2001", -40, -10, 2500000, str(hoy - timedelta(days=12))), ("F-2044", -10, 20, 2500000, "")):
            ej("INSERT INTO facturas (cliente_id, numero, emision, vence, monto, moneda, detalle, pagada, creado, emisor) VALUES (?,?,?,?,?,?,?,?,?,?)",
               tid, num, str(hoy + timedelta(days=em)), str(hoy + timedelta(days=ve)), monto, "CLP", "Licencia tenant FaceOps · mensualidad (dato de prueba)",
               pag, ahora(), "invoke")
        sub = [("apolo-seguros", "Apolo Seguros", "Ana Torres", "atorres@apoloseguros.example", "listo"),
               ("apolo-logistica", "Apolo Logística", "Diego Paz", "dpaz@apolologistica.example", "enviada")]
        for cod, nom, cn, cc, hasta in sub:
            cid = ej("INSERT INTO clientes (codigo, nombre, url, rol, creado, instancia_id, razon_social, pais, contacto_nombre, contacto_correo, tipo, tenant_id, "
                     "habilitado, alcance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cod, nom, "", "cliente", ahora(), "", f"{nom} (dato de prueba)", "Chile",
                     cn, cc, "cliente", tid, 0, "estandar")
            pid = ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, "
                     "enviada, respuesta, emisor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cid, f"PRO-{cod.upper()}-{hoy.year}", 1, "estandar", 2, 1800000,
                     3500000, "CLP", 3000, "Dato de prueba", "aceptada" if hasta == "listo" else "enviada", ahora(), "apolo.admin", ahora(),
                     ahora() if hasta == "listo" else "", "apolo")
            if hasta == "listo":
                kid = ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por, enviado, firmado, firmante, archivo, inicio, fin) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", cid, pid, f"CTR-{hoy.year}-7001", "firmado", ahora(), "apolo.admin", ahora(), str(hoy),
                         "Ana Torres (dato de prueba)", "", str(hoy), str(hoy.replace(year=hoy.year + 2)))
                ej("INSERT INTO ordenes (cliente_id, contrato_id, numero, fecha, monto, moneda, archivo, creado, por) VALUES (?,?,?,?,?,?,?,?,?)",
                   cid, kid, "OC-45871", str(hoy), 3500000, "CLP", "", ahora(), "apolo.admin")


sembrar_tenant_demo()
sembrar_contratos_apolo()
