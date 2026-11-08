"""FaceAdmin · ciclo comercial (propuesta → contrato → orden de compra) y tenants (clientes que administran a sus propios clientes).
Se registra sobre la app de app.py: mismas tablas, mismo ingreso y mismo alcance por tenant."""
import json
import os
import re
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


def _k(num):
    """Contrato por su número actual o por el que tuvo antes de pasar al formato correlativo (los datos de prueba lo buscan así)."""
    try:
        return q1("SELECT * FROM contratos WHERE numero=? OR ref_semilla=?", num, num)
    except Exception:
        return q1("SELECT * FROM contratos WHERE numero=?", num)


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
    import cobro
    f = request.form
    try:
        r = cobro.calcular(f)
        impl = cobro._n(f.get("implementacion"), 0)
        if impl < 0 or f.get("alcance") not in ALCANCES:
            raise ValueError("Indica el alcance de la implementación y un monto de implementación válido")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("cliente", cid=cid) + "#comercial")
    mensual = r["mensual"]
    ver = (q1("SELECT MAX(version) v FROM propuestas WHERE cliente_id=?", cid) or {}).get("v") or 0
    ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, emisor, "
       "modalidad, plan, creditos_anio, valor_credito, items, nodos, valor_nodo, servicio_mensual, rh_mensual) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
       cid, f"PRO-{c['codigo'].upper()}-{date.today().year}", ver + 1, f["alcance"], max(1, int(f.get("anios") or 1)), mensual, impl, f.get("moneda") or "USD",
       r["creditos_anio"], f.get("condiciones", "")[:3000], "borrador", ahora(), g.usuario["usuario"], _emisor(c)["codigo"], r["modalidad"], r["plan"],
       r["creditos_anio"], r["valor_credito"], json.dumps(r["items"], ensure_ascii=False), r["nodos"], r["valor_nodo"], r["servicio_mensual"], r["rh_mensual"])
    bitacora("propuesta creada", c["codigo"], {"version": ver + 1, "mensual": mensual})
    flash(f"Propuesta v{ver + 1} creada en borrador", "ok")
    return redirect(url_for("cliente", cid=cid) + "#comercial")


@app.route("/propuestas/<int:pid>")
@requiere_login
def propuesta_doc(pid):
    p = q1("SELECT * FROM propuestas WHERE id=?", pid) or abort(404)
    c = cliente_ok(p["cliente_id"])
    import cobro
    return render_template("doc_propuesta.html", p=cobro.detalle(p), c=c, a=ALCANCES.get(p["alcance"], ALCANCES["estandar"]), em=_emisor(c),
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
       cid, p["id"], __import__("contratos").numero_contrato(c, "T" if c.get("tipo") == "tenant" else "C"), "borrador", ahora(), g.usuario["usuario"])
    bitacora("contrato generado", c["codigo"], {"propuesta": p["version"]})
    flash("Contrato generado desde la propuesta aceptada: revísalo y envíalo a firma", "ok")
    return redirect(url_for("cliente", cid=cid) + "#comercial")


@app.route("/contratos/<int:kid>")
@requiere_login
def contrato_doc(kid):
    k = q1("SELECT * FROM contratos WHERE id=?", kid) or abort(404)
    c = cliente_ok(k["cliente_id"])
    p = q1("SELECT * FROM propuestas WHERE id=?", k["propuesta_id"]) or {}
    import cobro
    return render_template("doc_contrato.html", k=k, p=cobro.detalle(p), c=c, a=ALCANCES.get(p.get("alcance"), ALCANCES["estandar"]), em=_emisor(c),
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
            from datetime import timedelta
            from contratos import DIAS_PUESTA_EN_MARCHA
            lim = date.fromisoformat(f["fecha"]) + timedelta(days=DIAS_PUESTA_EN_MARCHA)   # la vigencia corre desde la activación
            ej("UPDATE contratos SET estado='firmado', firmado=?, firmante=?, archivo=?, inicio='', fin='', activacion_limite=? WHERE id=?",
               f["fecha"], f["firmante"][:120], arch, str(lim), kid)
        elif ac == "anular" and k["estado"] != "firmado":
            ej("UPDATE contratos SET estado='anulado' WHERE id=?", kid)
        else:
            raise ValueError("Acción no válida en este estado")
        bitacora(f"contrato · {ac}", c["codigo"], {"numero": k["numero"]})
        flash({"enviar": "Contrato enviado a firma", "firmar": "Contrato firmado y archivado: falta la orden de compra. Corre la puesta en marcha "
               "(30 días) hasta la activación", "anular": "Contrato anulado"}[ac], "ok")
    except ValueError as e:
        flash(str(e), "error")
    return _volver(c["id"], kid)


def _volver(cid, kid=None):
    if request.form.get("volver") == "ficha" and kid:
        return redirect(url_for("contrato_ficha", kid=kid))
    return redirect(url_for("cliente", cid=cid) + "#comercial")


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
        from contratos import monto as monto_contrato
        p = q1("SELECT * FROM propuestas WHERE id=?", k["propuesta_id"]) or {}
        esperado, mon = monto_contrato(p), p.get("moneda") or "USD"
        if abs(monto - esperado) >= 1 or (f.get("moneda") or "USD") != mon:
            raise ValueError(f"La orden de compra debe coincidir con el monto del contrato {k['numero']}: {mon} {esperado:,.0f}".replace(",", ".")
                             + f" (llegó {f.get('moneda') or 'USD'} {monto:,.0f})".replace(",", "."))
        arch = _guardar(request.files.get("archivo"), cid, "oc")
        ej("INSERT INTO ordenes (cliente_id, contrato_id, numero, fecha, monto, moneda, archivo, creado, por) VALUES (?,?,?,?,?,?,?,?,?)",
           cid, k["id"], f["numero"][:40], f.get("fecha") or date.today().isoformat(), monto, f.get("moneda") or "USD", arch, ahora(), g.usuario["usuario"])
        bitacora("orden de compra", c["codigo"], {"numero": f["numero"], "monto": monto})
        flash("Orden de compra registrada y conciliada con el contrato: ya se puede habilitar FaceOps", "ok")
    except ValueError as e:
        flash(str(e), "error")
    return _volver(cid, k["id"] if k else None)


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
        import cobro
        d = cobro.despliegue_tenant(c["id"]) if c.get("id") else {"k": ""}
        return {"k": "tenant", "t": "Tenant · " + d["t"] if d["k"] else "Tenant", "despliegue": d,
                "habilita": "Administrar sus propios clientes en FaceAdmin" + (f" · {d['titulo'].lower()}" if d["k"] else "")}
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
    import cobro
    from app import ETAPAS, etapa
    filas, graf = [], []
    for k in q("SELECT * FROM contratos ORDER BY id DESC"):
        c = q1("SELECT * FROM clientes WHERE id=?", k["cliente_id"])
        if not c or not en_alcance(c):
            continue
        k["c"], k["tipo"], k["em"] = c, tipo_contrato(c), _emisor(c)
        if k["tipo"]["k"] == "de-tenant":   # en la lista van consolidados en el del tenant; en la gráfica cuentan a precio Invoke
            if k["estado"] == "firmado":
                p_ = q1("SELECT mensual, mensual_invoke FROM propuestas WHERE id=?", k["propuesta_id"]) or {}
                graf.append({"firmado": k.get("firmado") or "", "fin": k.get("fin") or "", "monto": round(float(p_.get("mensual_invoke") or p_.get("mensual") or 0) * 12)})
            continue
        k["oc"] = q1("SELECT * FROM ordenes WHERE contrato_id=? ORDER BY id DESC LIMIT 1", k["id"])
        k["etapa"] = ETAPAS.get(etapa(c), "")
        from contratos import estado_activacion
        k["activacion"] = estado_activacion(k)
        p = q1("SELECT * FROM propuestas WHERE id=?", k["propuesta_id"]) or {}
        k["cobro"] = cobro.resumen(p)
        from contratos import monto as _monto
        k["anual"], k["total"], k["moneda"] = round(float(p.get("mensual") or 0) * 12), _monto(p), p.get("moneda") or "USD"
        k["neg"] = None
        if k["tipo"]["k"] == "tenant":   # el negocio con un tenant incluye a todos sus clientes
            k["neg"] = cobro.negocio_tenant(c["id"])
            k["anual"] = k["neg"]["total_anual"]
        elif k["tipo"]["k"] == "de-tenant":   # lo de sus clientes lo factura Invoke al tenant: no se suma dos veces
            k["anual_cliente"], k["anual"] = k["anual"], round(float(p.get("mensual_invoke") or 0) * 12) or k["anual"]
        filas.append(k)
    grupos = {}
    for k in filas:
        e = _empresa(k["c"])
        grupos.setdefault(e, {"empresa": q1("SELECT * FROM clientes WHERE id=?", e), "contratos": []})["contratos"].append(k)
    orden = sorted(grupos.values(), key=lambda gr: (-len(gr["contratos"]), gr["empresa"]["nombre"]))
    # contrataciones por mes: el mes actual en la posición 6 (5 meses atrás, 6 adelante); adelante se proyectan las renovaciones
    hoy = date.today()
    meses = []
    for i in range(-5, 7):
        a, m = divmod(hoy.month - 1 + i, 12)
        meses.append({"k": f"{hoy.year + a}-{m + 1:02d}", "t": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"][m] + f" {str(hoy.year + a)[2:]}",
                      "actual": i == 0, "futuro": i > 0, "n": 0, "monto": 0, "ren": 0, "ren_monto": 0})
    pm = {x["k"]: x for x in meses}
    # cada contrato firmado por su propio monto: el tenant por su licencia y cada cliente suyo por su precio Invoke (sin contar dos veces)
    graf += [{"firmado": k.get("firmado") or "", "fin": k.get("fin") or "", "monto": k["neg"]["licencia_anual"] if k.get("neg") else k["anual"]}
             for k in filas if k["estado"] == "firmado"]
    for k in graf:
        f_ = pm.get(k["firmado"][:7])
        if f_:
            f_["n"] += 1
            f_["monto"] += k["monto"]
        r_ = pm.get(k["fin"][:7])
        if r_ and r_["futuro"]:
            r_["ren"] += 1
            r_["ren_monto"] += k["monto"]
    tope = max([x["monto"] + x["ren_monto"] for x in meses] + [1])
    return render_template("contratos.html", meses=meses, tope=tope, grupos=orden, contratos=sorted(filas, key=lambda k: k["numero"] or "", reverse=True), n=len(filas), anual_total=sum(k["anual"] for k in filas if k["estado"] == "firmado" and k["tipo"]["k"] != "de-tenant"), firmados=sum(1 for k in filas if k["estado"] == "firmado"),
                           habilitan=sum(1 for k in filas if k["estado"] == "firmado" and (k["tipo"]["k"] == "tenant" or k["c"].get("habilitado"))))


@app.route("/contratos/<int:kid>/ficha")
@requiere_login
def contrato_ficha(kid):
    import cobro
    from app import ETAPAS, central, etapa, saldos
    k = q1("SELECT * FROM contratos WHERE id=?", kid) or abort(404)
    c = cliente_ok(k["cliente_id"])
    p = cobro.detalle(q1("SELECT * FROM propuestas WHERE id=?", k["propuesta_id"]) or {})
    emp = _empresa(c)
    hermanos = [h for h in q("SELECT k.*, c.nombre cn, c.tipo ct, c.propio, c.tenant_id FROM contratos k JOIN clientes c ON c.id=k.cliente_id "
                             "WHERE k.id<>? ORDER BY k.id DESC", kid) if _empresa({"id": h["cliente_id"], "propio": h["propio"], "tenant_id": h["tenant_id"]}) == emp
                and en_alcance(q1("SELECT * FROM clientes WHERE id=?", h["cliente_id"]))]
    for h in hermanos:
        h["tipo"] = tipo_contrato({"tipo": h["ct"], "propio": h["propio"], "tenant_id": h["tenant_id"]})
    tipo = tipo_contrato(c)
    from contratos import estado_activacion, monto, DIAS_PUESTA_EN_MARCHA
    from instalacion import puede_instalar, archivos
    inst_ok, inst_m = puede_instalar(c) if tipo["k"] != "tenant" else (False, "")
    from instalacion import eventos, FASES, progreso, intentos, contrato_aprobado
    cmd_inst = None
    neg_t = cobro.negocio_tenant(c["id"]) if tipo["k"] == "tenant" else None
    return render_template("contrato_ficha.html", neg_t=neg_t, contrato_aprobado=contrato_aprobado(k), cmd_inst=cmd_inst, inst_int=intentos(c["id"]) if tipo["k"] != "tenant" else [], inst_pr=progreso(c["id"]) if tipo["k"] != "tenant" else None, inst_ev=eventos(c["id"]) if tipo["k"] != "tenant" else [], fases=FASES, inst_ok=inst_ok, inst_motivo=inst_m if not inst_ok else "", inst_ar=archivos() if inst_ok else {}, monto=monto(p), activacion=estado_activacion(k), dias_pm=DIAS_PUESTA_EN_MARCHA, hoy=str(date.today()), k=k, c=c, p=p, a=ALCANCES.get(p.get("alcance"), ALCANCES["estandar"]), em=_emisor(c), tipo=tipo,
                           oc=q1("SELECT * FROM ordenes WHERE contrato_id=? ORDER BY id DESC LIMIT 1", kid), hermanos=hermanos,
                           etapa_=etapa(c), etapas=ETAPAS, nodos=cobro.nodos(c["id"]), saldo=saldos().get(c["id"]),
                           consumo=q("SELECT * FROM creditos WHERE cliente_id=? ORDER BY id DESC LIMIT 15", c["id"]), central=central() if tipo["k"] != "tenant" and c.get("habilitado") else {},
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
        planes = ((t, "T", "estandar", 2, 2700, 0, 0, "Licencia de tenant: administra sus propios clientes en FaceAdmin (propuestas, contratos, facturación y "
                   "bloqueo). Invoke factura al tenant. Contrato ficticio de prueba."),
                  (pr, "C", "estandar", 2, 20000, 4500, 3000, "FaceOps propio de Inversiones Apolo como cliente directo de Invoke, con el consultor de "
                   "Invoke como cluster-admin durante el contrato. Contrato ficticio de prueba."))
        for c, letra, alc, anios, mensual, impl, cred, cond in planes:
            num = f"CTR-APOLO-{letra}-{hoy.year}"
            if _k(num):
                continue
            ej("UPDATE propuestas SET estado='rechazada', respuesta=? WHERE cliente_id=? AND estado IN ('borrador','enviada')", ahora(), c["id"])
            ver = (q1("SELECT MAX(version) v FROM propuestas WHERE cliente_id=?", c["id"]) or {}).get("v") or 0
            pid = ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, "
                     "enviada, respuesta, emisor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], f"PRO-APOLO-{letra}-{hoy.year}", ver + 1, alc, anios, mensual,
                     impl, "USD", cred, cond, "aceptada", ahora(), "cquezada", ahora(), ahora(), "invoke")
            kid = ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por, enviado, firmado, firmante, archivo, inicio, fin, "
                     "activacion_limite) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], pid, num, "firmado", ahora(), "cquezada", ahora(), str(hoy),
                     "Marcela Ríos, Gerente General (dato ficticio)", "", "", "", str(hoy + timedelta(days=30)))
            ej("INSERT INTO ordenes (cliente_id, contrato_id, numero, fecha, monto, moneda, archivo, creado, por) VALUES (?,?,?,?,?,?,?,?,?)",
               c["id"], kid, f"OC-APOLO-{letra}-{hoy.year}", str(hoy), impl or mensual, "USD", "", ahora(), "cquezada")
            ej("INSERT INTO bitacora (fecha, usuario, accion, objeto, detalle) VALUES (?,?,?,?,?)", ahora(), "sistema", "contrato ficticio firmado con OC",
               c["codigo"], f'{{"contrato": "{num}"}}')
        import cobro
        kc = _k(f"CTR-APOLO-C-{hoy.year}")
        if kc and not (q1("SELECT modalidad FROM propuestas WHERE id=?", kc["propuesta_id"]) or {}).get("modalidad"):
            pl = cobro.PLANES["c12k"]
            serv, rh = round(pl["creditos"] * pl["valor"] / 12), 25 * cobro.VALOR_NODO
            ej("UPDATE propuestas SET modalidad='creditos', plan='c12k', creditos_anio=?, creditos=?, valor_credito=?, nodos=25, valor_nodo=?, "
               "servicio_mensual=?, rh_mensual=?, mensual=?, moneda='USD', implementacion=4500 WHERE id=?", pl["creditos"], pl["creditos"], pl["valor"], cobro.VALOR_NODO, serv, rh, serv + rh,
               kc["propuesta_id"])
        kt = _k(f"CTR-APOLO-T-{hoy.year}")
        if kt:
            ej("UPDATE propuestas SET creditos=0 WHERE id=? AND COALESCE(modalidad,'')=''", kt["propuesta_id"])
        from contratos import monto as monto_contrato
        for letra in ("T", "C"):   # la OC ficticia coincide con el monto del contrato
            k_ = _k(f"CTR-APOLO-{letra}-{hoy.year}")
            if k_:
                ej("UPDATE ordenes SET monto=? WHERE contrato_id=? AND numero LIKE 'OC-APOLO-%'",
                   monto_contrato(q1("SELECT * FROM propuestas WHERE id=?", k_["propuesta_id"])), k_["id"])
        pr = q1("SELECT * FROM clientes WHERE id=?", pr["id"])
        if not pr.get("habilitado") and (_k(f"CTR-APOLO-C-{hoy.year}") or {}).get("estado") == "firmado":
            ej("UPDATE clientes SET habilitado=1, instancia_id=?, consultor_nombre=?, consultor_correo=?, consultor_usuario=?, contrato_inicio=?, contrato_anios=?, "
               "alcance=?, activacion_limite=? WHERE id=?", pr.get("instancia_id") or nuevo_id(pr["codigo"]), "Cristian Quezada", "cquezada@invoke.cl", "cquezada",
               str(hoy), 2, "estandar", str(hoy + timedelta(days=42)), pr["id"])
            ej("INSERT INTO bitacora (fecha, usuario, accion, objeto, detalle) VALUES (?,?,?,?,?)", ahora(), "sistema", "FaceOps habilitado por contrato",
               pr["codigo"], f'{{"contrato": "CTR-APOLO-C-{hoy.year}"}}')
            cobro.al_habilitar(q1("SELECT * FROM clientes WHERE id=?", pr["id"]))

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
import cobro as _cobro  # noqa: E402
_cobro.a_dolares()   # lo sembrado en pesos pasa a dólares


def _margenes():
    """Contratos anteriores al margen del tenant: el tenant queda con el margen por defecto y sus clientes con su precio Invoke."""
    with app.app_context():
        for t in q("SELECT id FROM clientes WHERE tipo='tenant'"):
            ej("UPDATE propuestas SET margen=? WHERE cliente_id=? AND COALESCE(margen,0)=0", _cobro.MARGEN_TENANT, t["id"])
            m = _cobro.margen_tenant(t["id"])
            for c in q("SELECT id FROM clientes WHERE tenant_id=? AND COALESCE(propio,0)=0", t["id"]):
                ej("UPDATE propuestas SET margen=?, mensual_invoke=ROUND(mensual/?, 2) WHERE cliente_id=? AND COALESCE(mensual_invoke,0)=0", m, 1 + m / 100, c["id"])
        ej("UPDATE propuestas SET mensual_invoke=mensual WHERE COALESCE(mensual_invoke,0)=0")


def _contratos_clientes_tenant():
    """Contratos ficticios para los clientes del tenant que ya operan su FaceOps sin contrato (Banco Austral y Retail Pacífico):
    firmados, con OC y activos, a precio Invoke más el margen del tenant."""
    from datetime import timedelta
    with app.app_context():
        hoy = date.today()
        for cod, plan, nodos in (("bau", "c12k", 40), ("rpa", "c6k", 20)):
            c = q1("SELECT * FROM clientes WHERE codigo=? AND tenant_id IS NOT NULL", cod)
            if not c or q1("SELECT id FROM contratos WHERE cliente_id=?", c["id"]):
                continue
            m = _cobro.margen_tenant(c["tenant_id"])
            pl = _cobro.PLANES[plan]
            inv = round(pl["creditos"] * pl["valor"] / 12) + nodos * _cobro.VALOR_NODO
            cli = round(inv * (1 + m / 100), 2)
            pid = ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, "
                     "enviada, respuesta, emisor, modalidad, plan, creditos_anio, valor_credito, items, nodos, valor_nodo, servicio_mensual, rh_mensual, margen, "
                     "mensual_invoke) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], f"PRO-{cod.upper()}-{hoy.year}", 1, "estandar", 1, cli,
                     0, "USD", pl["creditos"], "Contrato ficticio de prueba (cliente del tenant que ya operaba)", "aceptada", ahora(), "sistema", ahora(), ahora(),
                     (q1("SELECT codigo FROM clientes WHERE id=?", c["tenant_id"]) or {}).get("codigo", "invoke"), "creditos", plan, pl["creditos"], pl["valor"], "[]",
                     nodos, _cobro.VALOR_NODO, round(pl["creditos"] * pl["valor"] / 12), nodos * _cobro.VALOR_NODO, m, inv)
            ini = hoy - timedelta(days=60)
            kid = ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por, enviado, firmado, firmante, archivo, inicio, fin, activacion_limite, "
                     "activado, activado_por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], pid, f"CTR-{cod.upper()}-C-{hoy.year}", "firmado", ahora(), "sistema",
                     ahora(), str(ini), "dato ficticio", "", str(ini), str(ini.replace(year=ini.year + 1)), str(ini + timedelta(days=30)), str(ini),
                     "contrato previo a FaceAdmin (dato ficticio)")
            ej("INSERT INTO ordenes (cliente_id, contrato_id, numero, fecha, monto, moneda, archivo, creado, por) VALUES (?,?,?,?,?,?,?,?,?)", c["id"], kid,
               f"OC-{cod.upper()}-{hoy.year}", str(ini), round(cli * 12, 2), "USD", "", ahora(), "sistema")


_margenes()
_contratos_clientes_tenant()


# ---------------------------------------------------------------- país y número correlativo de contrato
def _renumerar():
    """Cada cliente tiene su país; los contratos pasan al formato CTR-<PAÍS>-<T|C>-<AÑO>-<NNNN> con un correlativo único de Invoke."""
    from contratos import numero_contrato
    with app.app_context():
        for col in ("ref_semilla TEXT DEFAULT ''",):
            try:
                ej(f"ALTER TABLE contratos ADD COLUMN {col}")
            except Exception:
                pass
        ej("CREATE TABLE IF NOT EXISTS secuencias (clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
        if not q1("SELECT valor FROM secuencias WHERE clave='migracion_pais'"):
            ap = q1("SELECT id FROM clientes WHERE codigo='apolo'")
            if ap:   # Inversiones Apolo es de Honduras, igual que su FaceOps propio y sus clientes
                ej("UPDATE clientes SET pais='Honduras' WHERE id=? OR tenant_id=?", ap["id"], ap["id"])
            ej("UPDATE clientes SET pais='Chile' WHERE COALESCE(pais,'')=''")
            ej("INSERT INTO secuencias (clave, valor) VALUES ('migracion_pais', 1)")
        for k in q("SELECT * FROM contratos WHERE cliente_id NOT IN (SELECT id FROM clientes WHERE codigo LIKE 'demo-%') ORDER BY id"):
            if re.match(r"^CTR-[A-Z]{2}-[TC]-\d{4}-\d{4}$", k["numero"] or ""):
                continue
            c = q1("SELECT * FROM clientes WHERE id=?", k["cliente_id"]) or {}
            letra = "T" if c.get("tipo") == "tenant" else "C"
            nuevo = numero_contrato(c, letra, (k.get("creado") or "")[:4])
            ej("UPDATE contratos SET numero=?, ref_semilla=? WHERE id=?", nuevo, k["numero"], k["id"])
            ej("UPDATE creditos SET ref=REPLACE(ref, ?, ?) WHERE cliente_id=? AND ref LIKE ?", f"plan:{k['numero']}:", f"plan:{nuevo}:", k["cliente_id"], f"plan:{k['numero']}:%")
            ej("INSERT INTO bitacora (fecha, usuario, accion, objeto, detalle) VALUES (?,?,?,?,?)", ahora(), "sistema", "contrato renumerado", c.get("codigo", ""),
               json.dumps({"antes": k["numero"], "ahora": nuevo}))


_renumerar()


def _contrato_lab():
    """Invoke Lab (Chile) también opera con contrato: uso interno de Invoke, firmado y activo, sin cobro."""
    from datetime import timedelta
    from contratos import numero_contrato
    with app.app_context():
        c = q1("SELECT * FROM clientes WHERE rol='laboratorio' AND activo=1 ORDER BY id LIMIT 1")
        if not c or q1("SELECT id FROM contratos WHERE cliente_id=?", c["id"]):
            return
        if not c.get("pais"):
            ej("UPDATE clientes SET pais='Chile' WHERE id=?", c["id"])
            c["pais"] = "Chile"
        hoy = date.today()
        pid = ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, "
                 "enviada, respuesta, emisor, mensual_invoke) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], f"PRO-LAB-{hoy.year}", 1, "estandar", 1, 0, 0,
                 "USD", 0, "Laboratorio interno de Invoke (Chile): uso interno, sin cobro.", "aceptada", ahora(), "sistema", ahora(), ahora(), "invoke", 0)
        num = numero_contrato(c, "C")
        kid = ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por, enviado, firmado, firmante, archivo, inicio, fin, activacion_limite, "
                 "activado, activado_por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], pid, num, "firmado", ahora(), "sistema", ahora(), str(hoy),
                 "Invoke SpA (uso interno)", "", str(hoy), str(hoy.replace(year=hoy.year + 1)), str(hoy + timedelta(days=30)), str(hoy), "uso interno de Invoke")
        ej("INSERT INTO ordenes (cliente_id, contrato_id, numero, fecha, monto, moneda, archivo, creado, por) VALUES (?,?,?,?,?,?,?,?,?)", c["id"], kid,
           "OC-INTERNA-LAB", str(hoy), 0, "USD", "", ahora(), "sistema")
        bitacora_sis = "INSERT INTO bitacora (fecha, usuario, accion, objeto, detalle) VALUES (?,?,?,?,?)"
        ej(bitacora_sis, ahora(), "sistema", "contrato interno creado", c["codigo"], json.dumps({"contrato": num}))


_contrato_lab()


@app.route("/modo-demo")
@requiere_login
def modo_demo():
    """Activa o desactiva la vista con datos ficticios (para presentar FaceAdmin a un tenant)."""
    from flask import session
    from app import solo_invoke
    solo_invoke()
    session["ver_demo"] = not session.get("ver_demo")
    ir = request.args.get("ir", "")
    return redirect(ir if ir.startswith("/") and not ir.startswith("//") else url_for("panel"))


# ---------------------------------------------------------------- demo para presentar a tenants (todo ficticio y marcado «demo»)
DEMO_TENANT = ("demo-tenant", "Nexo Integraciones (demo)", "Chile", "Ana Ruiz (demo)", "ana.ruiz@nexo.example")
DEMO_CLIENTES = [   # código, nombre, país, plan, nodos, meses desde la firma, estado del contrato
    ("demo-minera", "Minera Los Andes (demo)", "Chile", "c12k", 40, 9, "activo"),
    ("demo-banco", "Banco del Pacífico Sur (demo)", "Perú", "c24k", 80, 4, "activo"),
    ("demo-clinica", "Clínica Santa Lucía (demo)", "Honduras", "c6k", 15, 0, "puesta_en_marcha"),
    ("demo-retail", "Retail Austral (demo)", "Chile", "c6k", 20, None, "borrador"),
]


def sembrar_demo_tenant():
    from datetime import timedelta
    import cobro
    from contratos import pais_iso
    with app.app_context():
        if q1("SELECT id FROM clientes WHERE codigo=?", DEMO_TENANT[0]):
            return
        hoy = date.today()
        cod, nom, pais, cn, cc = DEMO_TENANT
        tid = ej("INSERT INTO clientes (codigo, nombre, url, rol, creado, instancia_id, razon_social, rut, pais, contacto_nombre, contacto_correo, tipo, habilitado) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", cod, nom, "", "tenant", ahora(), "", "Nexo Integraciones SpA (demo)", "77.777.777-7", pais, cn, cc, "tenant", 0)
        seq = 0

        def contrato(cid, pid, letra, pais_c, firmado, estado):
            nonlocal seq
            seq += 1
            num = f"DEMO-{pais_iso(pais_c)}-{letra}-{hoy.year}-{seq:03d}"
            if estado == "borrador":
                return ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por) VALUES (?,?,?,?,?,?)", cid, pid, num, "borrador", ahora(), "demo")
            ini = firmado
            act = str(ini) if estado == "activo" else ""
            return ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por, enviado, firmado, firmante, archivo, inicio, fin, activacion_limite, "
                      "activado, activado_por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cid, pid, num, "firmado", ahora(), "demo", ahora(), str(firmado), "firmante (demo)", "",
                      act, str(ini.replace(year=ini.year + 1)) if act else "", str(firmado + timedelta(days=30)), act, "demo" if act else "")

        # licencia del tenant
        D = cobro.DESPLIEGUES["centralizado"]
        pid = ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, "
                 "enviada, respuesta, emisor, margen, mensual_invoke, despliegue, habilitacion_instancia, operacion_instancia) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 tid, f"PRO-DEMO-T-{hoy.year}", 1, "estandar", 2, 2700 + D["operacion"], D["habilitacion"], "USD", 0,
                 "Licencia de tenant (demo) con un FaceOps centralizado para todos sus clientes.", "aceptada", ahora(), "demo", ahora(), ahora(), "invoke",
                 cobro.MARGEN_TENANT, 2700 + D["operacion"], "centralizado", D["habilitacion"], D["operacion"])
        kid = contrato(tid, pid, "T", pais, hoy - timedelta(days=300), "activo")
        ej("INSERT INTO ordenes (cliente_id, contrato_id, numero, fecha, monto, moneda, archivo, creado, por) VALUES (?,?,?,?,?,?,?,?,?)", tid, kid, "OC-DEMO-T",
           str(hoy - timedelta(days=300)), (2700 + D["operacion"]) * 24 + D["habilitacion"], "USD", "", ahora(), "demo")
        m = cobro.MARGEN_TENANT
        for cod, nom, pais_c, plan, nodos, meses, estado in DEMO_CLIENTES:
            cid = ej("INSERT INTO clientes (codigo, nombre, url, rol, creado, instancia_id, razon_social, pais, contacto_nombre, contacto_correo, tipo, tenant_id, habilitado, "
                     "alcance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cod, nom, "", "cliente", ahora(), "", nom, pais_c, "contacto (demo)", f"contacto@{cod}.example",
                     "cliente", tid, 1 if estado == "activo" else 0, "estandar")
            pl = cobro.PLANES[plan]
            inv = round(pl["creditos"] * pl["valor"] / 12) + nodos * cobro.VALOR_NODO
            cli = round(inv * (1 + m / 100), 2)
            pid = ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, "
                     "enviada, respuesta, emisor, modalidad, plan, creditos_anio, valor_credito, items, nodos, valor_nodo, servicio_mensual, rh_mensual, margen, "
                     "mensual_invoke) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cid, f"PRO-{cod.upper()}", 1, "estandar", 1, cli, 0, "USD",
                     pl["creditos"], "Contrato de demostración", "aceptada" if estado != "borrador" else "enviada", ahora(), "demo", ahora(), ahora(), DEMO_TENANT[0],
                     "creditos", plan, pl["creditos"], pl["valor"], "[]", nodos, cobro.VALOR_NODO, round(pl["creditos"] * pl["valor"] / 12), nodos * cobro.VALOR_NODO, m, inv)
            firmado = hoy - timedelta(days=30 * (meses or 0))
            kid = contrato(cid, pid, "C", pais_c, firmado, estado)
            if estado != "borrador":
                ej("INSERT INTO ordenes (cliente_id, contrato_id, numero, fecha, monto, moneda, archivo, creado, por) VALUES (?,?,?,?,?,?,?,?,?)", cid, kid,
                   f"OC-{cod.upper()}", str(firmado), round(cli * 12, 2), "USD", "", ahora(), "demo")


sembrar_demo_tenant()


def _despliegue_apolo():
    """Inversiones Apolo opera un FaceOps en la infraestructura de cada cliente (Banco Austral y Retail Pacífico tienen el suyo)."""
    import cobro
    with app.app_context():
        t = q1("SELECT id FROM clientes WHERE codigo='apolo'")
        if not t:
            return
        D = cobro.DESPLIEGUES["por_cliente"]
        for k in q("SELECT propuesta_id FROM contratos WHERE cliente_id=?", t["id"]):
            ej("UPDATE propuestas SET despliegue='por_cliente', habilitacion_instancia=?, operacion_instancia=? WHERE id=? AND COALESCE(despliegue,'')=''",
               D["habilitacion"], D["operacion"], k["propuesta_id"])


_despliegue_apolo()
