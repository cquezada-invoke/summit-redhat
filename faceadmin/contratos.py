"""FaceAdmin · contratos: creación directa con toda la información del cliente o tenant, orden de compra que debe coincidir con el
monto del contrato, y activación.

Firmar el contrato no lo activa. Desde la firma corre un período de puesta en marcha de 30 días en que Invoke habilita el FaceOps
y va subiendo infraestructura y automatizaciones. El cliente aprueba la activación (desde su FaceOps, o Invoke la registra en su
nombre); si no lo hace, se activa sola al día 30. La vigencia y el cobro del servicio corren desde la activación."""
import hashlib
import json
import time
from datetime import date, timedelta

from flask import abort, flash, g, redirect, render_template, request, url_for

from app import ALCANCES, ahora, app, bitacora, cliente_ok, ej, en_alcance, es_invoke, q, q1, requiere_login

DIAS_PUESTA_EN_MARCHA = 30


def iniciar():
    with app.app_context():
        cols = {r["name"] for r in q("PRAGMA table_info(contratos)")}
        nuevas = [c for c in ("activacion_limite", "activado", "activado_por") if c not in cols]
        for col in nuevas:
            ej(f"ALTER TABLE contratos ADD COLUMN {col} TEXT DEFAULT ''")
        if nuevas:   # contratos firmados antes de esta regla: quedan en puesta en marcha desde su firma, sin vigencia ni créditos aún
            for k in q("SELECT * FROM contratos WHERE estado='firmado'"):
                lim = str(date.fromisoformat(k["firmado"][:10]) + timedelta(days=DIAS_PUESTA_EN_MARCHA)) if k.get("firmado") else ""
                ej("UPDATE contratos SET activacion_limite=?, inicio='', fin='' WHERE id=?", lim, k["id"])
                ej("DELETE FROM creditos WHERE cliente_id=? AND ref=?", k["cliente_id"], f"plan:{k['numero']}:1")


iniciar()


def monto(p):
    """Monto total del contrato: servicio mensual (incluida la suscripción Red Hat) por los meses del plazo, más la implementación.
    La orden de compra del cliente debe ser por este monto."""
    p = p or {}
    return round(float(p.get("mensual") or 0) * 12 * int(p.get("anios") or 1) + float(p.get("implementacion") or 0))


def estado_activacion(k):
    if k.get("estado") != "firmado":
        return ""
    return "activo" if k.get("activado") else "puesta_en_marcha"


def activar(k, por):
    """Activa el contrato: desde hoy corren la vigencia y el cobro; con plan de créditos, se cargan los del primer año."""
    import cobro
    if k.get("estado") != "firmado" or k.get("activado"):
        return False
    p = q1("SELECT anios FROM propuestas WHERE id=?", k["propuesta_id"]) or {"anios": 1}
    hoy = date.today()
    ej("UPDATE contratos SET activado=?, activado_por=?, inicio=?, fin=? WHERE id=?", str(hoy), por[:120], str(hoy),
       str(hoy.replace(year=hoy.year + int(p["anios"] or 1))), k["id"])
    c = q1("SELECT * FROM clientes WHERE id=?", k["cliente_id"])
    cobro.al_habilitar(c)
    ej("INSERT INTO bitacora (fecha, usuario, accion, objeto, detalle) VALUES (?,?,?,?,?)", ahora(), (g.get("usuario") or {}).get("usuario", "sistema"),
       "contrato activado", c["codigo"], json.dumps({"contrato": k["numero"], "por": por}, ensure_ascii=False))
    return True


_rev = {"t": 0.0}


def revisar_vencidos():
    """Día 30 sin aprobación: el contrato se activa solo (se revisa a lo más una vez por minuto)."""
    if time.time() - _rev["t"] < 60:
        return
    _rev["t"] = time.time()
    for k in q("SELECT * FROM contratos WHERE estado='firmado' AND COALESCE(activado,'')='' AND COALESCE(activacion_limite,'')<>'' "
               "AND activacion_limite<=?", str(date.today())):
        activar(k, f"automática (día {DIAS_PUESTA_EN_MARCHA} sin aprobación)")


@app.before_request
def _revisar():
    if request.endpoint and request.endpoint != "static":
        revisar_vencidos()


def contrato_vigente(cid):
    """Último contrato firmado del cliente, con su estado de activación (para FaceOps y las fichas)."""
    k = q1("SELECT * FROM contratos WHERE cliente_id=? AND estado='firmado' ORDER BY id DESC LIMIT 1", cid)
    if not k:
        return {}
    return {"numero": k["numero"], "estado": estado_activacion(k), "limite": k.get("activacion_limite", ""), "activado": k.get("activado", ""),
            "inicio": k.get("inicio", ""), "fin": k.get("fin", "")}


# ---------------------------------------------------------------- activación
@app.route("/contratos/<int:kid>/activar", methods=["POST"])
@requiere_login
def contrato_activar(kid):
    """Invoke registra la aprobación del cliente (o el tenant aprueba su propio contrato con Invoke)."""
    k = q1("SELECT * FROM contratos WHERE id=?", kid) or abort(404)
    c = cliente_ok(k["cliente_id"])
    quien = (request.form.get("aprobado_por") or "").strip()
    if not quien:
        flash("Indica quién aprobó la activación por el cliente", "error")
    elif activar(k, f"aprobada por {quien} (registrada por {g.usuario['usuario']})"):
        flash(f"Contrato {k['numero']} activado: vigencia y cobro desde hoy", "ok")
    else:
        flash("El contrato no está en puesta en marcha", "error")
    return redirect(url_for("contrato_ficha", kid=kid))


@app.route("/api/instancia/activar", methods=["POST"])
def api_activar():
    """El cluster-admin del cliente aprueba la activación desde su FaceOps (con la llave de la instancia)."""
    tok = request.headers.get("X-FaceAdmin-Token", "")
    b = q1("SELECT * FROM bloqueos WHERE token=?", hashlib.sha256(tok.encode()).hexdigest()) if tok else None
    if not b:
        return {"error": "no autorizado"}, 401
    k = q1("SELECT * FROM contratos WHERE cliente_id=? AND estado='firmado' ORDER BY id DESC LIMIT 1", b["cliente_id"])
    if not k or k.get("activado"):
        return {"error": "No hay un contrato en puesta en marcha"}, 409
    usr = str((request.get_json(silent=True) or {}).get("usuario", "cliente"))[:60]
    activar(k, f"aprobada por {usr} desde su FaceOps")
    k = q1("SELECT * FROM contratos WHERE id=?", k["id"])
    return {"ok": True, "numero": k["numero"], "inicio": k["inicio"], "fin": k["fin"]}


# ---------------------------------------------------------------- crear contrato
@app.route("/contratos/nuevo", methods=["GET", "POST"])
@requiere_login
def contrato_nuevo():
    """Contrato directo con toda la información del cliente o tenant. Queda en borrador: luego firma, orden de compra y activación."""
    import cobro
    from negocio import _emisor
    yo_t = None if es_invoke() else g.usuario["tenant_id"]
    tenants = q("SELECT * FROM clientes WHERE tipo='tenant' AND activo=1 ORDER BY nombre") if es_invoke() else []
    existentes = [c for c in q("SELECT * FROM clientes WHERE activo=1 AND COALESCE(propio,0)=0 ORDER BY nombre") if en_alcance(c) and c["id"] != yo_t]
    if request.method == "GET":
        return render_template("contrato_nuevo.html", tenants=tenants, existentes=existentes, alcances=ALCANCES, hoy=str(date.today()), f=request.args)
    f = request.form
    tipo = f.get("tipo") if es_invoke() else "de-tenant"
    try:
        if tipo not in ("tenant", "directo", "de-tenant"):
            raise ValueError("Elige el tipo de contrato")
        tid = yo_t if yo_t else (int(f["tenant_id"]) if tipo == "de-tenant" and (f.get("tenant_id") or "").isdigit() else None)
        if tipo == "de-tenant" and not tid:
            raise ValueError("Elige el tenant al que pertenece el cliente")
        c = _empresa(f, tipo, tid)
        if f.get("alcance") not in ALCANCES:
            raise ValueError("Elige el alcance de la implementación")
        anios = max(1, int(f.get("anios") or 1))
        impl = cobro._n(f.get("implementacion"), 0)
        if impl < 0:
            raise ValueError("Monto de implementación no válido")
        if tipo == "tenant":
            lic = cobro._n(f.get("licencia"), 0)
            if lic <= 0:
                raise ValueError("Indica el valor mensual de la licencia de tenant")
            r = {"modalidad": "", "plan": "", "creditos_anio": 0, "valor_credito": 0, "items": [], "nodos": 0, "valor_nodo": 0,
                 "servicio_mensual": lic, "rh_mensual": 0, "mensual": lic}
        else:
            r = cobro.calcular(f)
    except ValueError as e:
        flash(str(e), "error")
        return render_template("contrato_nuevo.html", tenants=tenants, existentes=existentes, alcances=ALCANCES, hoy=str(date.today()), f=f), 400
    hoy = date.today()
    pid = ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, "
             "enviada, respuesta, emisor, modalidad, plan, creditos_anio, valor_credito, items, nodos, valor_nodo, servicio_mensual, rh_mensual) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             c["id"], f"PRO-{c['codigo'].upper()}-{hoy.year}", ((q1("SELECT MAX(version) v FROM propuestas WHERE cliente_id=?", c["id"]) or {}).get("v") or 0) + 1,
             f["alcance"], anios, r["mensual"], impl, f.get("moneda") or "CLP", r["creditos_anio"], f.get("condiciones", "")[:3000], "aceptada", ahora(),
             g.usuario["usuario"], ahora(), ahora(), _emisor(c)["codigo"], r["modalidad"], r["plan"], r["creditos_anio"], r.get("valor_credito", 0),
             json.dumps(r["items"], ensure_ascii=False), r["nodos"], r["valor_nodo"], r["servicio_mensual"], r["rh_mensual"])
    ej("UPDATE propuestas SET estado='rechazada', respuesta=? WHERE cliente_id=? AND id<>? AND estado IN ('borrador','enviada')", ahora(), c["id"], pid)
    letra = {"tenant": "T", "directo": "C", "de-tenant": "C"}[tipo]
    num = f"CTR-{c['codigo'].upper()}-{letra}-{hoy.year}"
    if q1("SELECT id FROM contratos WHERE numero=?", num):
        num = f"{num}-{q1('SELECT COUNT(*) n FROM contratos WHERE cliente_id=?', c['id'])['n'] + 1}"
    kid = ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por) VALUES (?,?,?,?,?,?)", c["id"], pid, num, "borrador", ahora(),
             g.usuario["usuario"])
    bitacora("contrato creado", c["codigo"], {"contrato": num, "tipo": tipo, "monto": monto(q1("SELECT * FROM propuestas WHERE id=?", pid))})
    flash(f"Contrato {num} creado en borrador: revisa el documento, envíalo a firma y registra la orden de compra", "ok")
    return redirect(url_for("contrato_ficha", kid=kid))


def _empresa(f, tipo, tid):
    """Empresa del contrato: una existente (actualiza sus datos) o una nueva. El contrato directo de un tenant va a su FaceOps propio."""
    campos = ("razon_social", "rut", "pais", "industria", "contacto_nombre", "contacto_cargo", "contacto_correo", "contacto_telefono")
    datos = {k: (f.get(k) or "").strip()[:120] for k in campos}
    if (f.get("empresa_id") or "").isdigit():
        c = cliente_ok(int(f["empresa_id"]))
        for k, v in datos.items():
            if v:
                ej(f"UPDATE clientes SET {k}=? WHERE id=?", v, c["id"])
        c = q1("SELECT * FROM clientes WHERE id=?", c["id"])
        if tipo == "tenant" and c.get("tipo") != "tenant":
            raise ValueError("Esa empresa es un cliente, no un tenant: créala como tenant")
        if tipo == "directo" and c.get("tipo") == "tenant":   # el tenant también es cliente de Invoke: su FaceOps propio
            pr = q1("SELECT * FROM clientes WHERE tenant_id=? AND propio=1", c["id"])
            if not pr:
                pid = ej("INSERT INTO clientes (codigo, nombre, url, rol, creado, instancia_id, razon_social, rut, pais, contacto_nombre, contacto_correo, "
                         "contacto_telefono, tipo, tenant_id, habilitado, propio, alcance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         f"{c['codigo']}-propio", f"{c['nombre']} (FaceOps propio)", "", "cliente", ahora(), "", c.get("razon_social", ""), c.get("rut", ""),
                         c.get("pais", ""), c.get("contacto_nombre", ""), c.get("contacto_correo", ""), c.get("contacto_telefono", ""), "cliente", c["id"], 0, 1,
                         f.get("alcance") or "estandar")
                pr = q1("SELECT * FROM clientes WHERE id=?", pid)
            return pr
        if tipo == "de-tenant" and c.get("tenant_id") != tid:
            raise ValueError("Ese cliente no pertenece al tenant elegido")
        return c
    cod = "".join(ch for ch in (f.get("codigo") or "").lower() if ch.isalnum() or ch == "-")[:20]
    if not cod or not (f.get("nombre") or "").strip() or not datos["contacto_nombre"] or not datos["contacto_correo"]:
        raise ValueError("Para una empresa nueva: código, nombre, contacto y correo son obligatorios")
    if q1("SELECT id FROM clientes WHERE codigo=?", cod):
        raise ValueError("Ya existe una empresa con ese código: elígela de la lista")
    cid = ej("INSERT INTO clientes (codigo, nombre, url, rol, creado, instancia_id, razon_social, rut, pais, industria, contacto_nombre, contacto_cargo, "
             "contacto_correo, contacto_telefono, tipo, tenant_id, habilitado, alcance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             cod, f["nombre"].strip()[:80], "", "tenant" if tipo == "tenant" else "cliente", ahora(), "", datos["razon_social"], datos["rut"], datos["pais"],
             datos["industria"], datos["contacto_nombre"], datos["contacto_cargo"], datos["contacto_correo"], datos["contacto_telefono"],
             "tenant" if tipo == "tenant" else "cliente", tid if tipo == "de-tenant" else None, 0, f.get("alcance") or "estandar")
    if tipo == "tenant" and (f.get("admin_correo") or "").strip():
        from negocio import _usuario_tenant
        enlace = _usuario_tenant(cid, f["admin_correo"].strip(), (f.get("admin_nombre") or f["admin_correo"]).strip())
        flash(f"Administrador del tenant creado. Envíale este enlace (un uso, 72 h) para definir su clave: {enlace}", "ok")
    return q1("SELECT * FROM clientes WHERE id=?", cid)
