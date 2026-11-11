"""FaceAdmin · Cuentas y contactos: toda empresa con la que Invoke conversa, tenga o no contrato. Cuando cierra un contrato pasa
también a Clientes (o a Tenants); mientras tanto vive aquí, con sus contactos, oportunidades y contratos en borrador."""
from flask import abort, flash, g, redirect, render_template, request, url_for

from app import ahora, app, bitacora, cliente_ok, ej, en_alcance, q, q1, requiere_login, solo_invoke

ROLES = ["Decisor", "Técnico", "Compras", "Finanzas", "Usuario", "Otro"]


def iniciar():
    with app.app_context():
        ej("CREATE TABLE IF NOT EXISTS contactos (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, cargo TEXT DEFAULT '', "
           "correo TEXT DEFAULT '', telefono TEXT DEFAULT '', rol TEXT DEFAULT '', principal INTEGER DEFAULT 0, notas TEXT DEFAULT '', creado TEXT, por TEXT)")
        # el contacto que ya tenía cada empresa pasa a ser su contacto principal
        for c in q("SELECT * FROM clientes WHERE COALESCE(contacto_nombre,'')<>'' AND id NOT IN (SELECT cliente_id FROM contactos)"):
            ej("INSERT INTO contactos (cliente_id, nombre, cargo, correo, telefono, rol, principal, creado, por) VALUES (?,?,?,?,?,?,?,?,?)", c["id"],
               c["contacto_nombre"], c.get("contacto_cargo") or "", c.get("contacto_correo") or "", c.get("contacto_telefono") or "", "Decisor", 1, ahora(), "sistema")


iniciar()


def estado_cuenta(c):
    k = q("SELECT estado FROM contratos WHERE cliente_id=? AND estado<>'anulado'", c["id"])
    if any(x["estado"] == "firmado" for x in k):
        return ("cliente", "Cliente")
    if any(x["estado"] == "demo" for x in k):
        return ("demo", "En demo")
    if k or q1("SELECT id FROM oportunidades WHERE cliente_id=? AND etapa NOT IN ('perdida')", c["id"]):
        return ("prospecto", "Prospecto")
    return ("cuenta", "Cuenta")


@app.route("/cuentas")
@requiere_login
def cuentas():
    solo_invoke()
    filas = []
    for c in q("SELECT * FROM clientes WHERE activo=1 AND COALESCE(propio,0)=0 ORDER BY nombre"):
        if not en_alcance(c):
            continue
        c["est"] = estado_cuenta(c)
        c["n_contactos"] = (q1("SELECT COUNT(*) n FROM contactos WHERE cliente_id=?", c["id"]) or {}).get("n", 0)
        c["n_ops"] = (q1("SELECT COUNT(*) n FROM oportunidades WHERE cliente_id=? AND etapa NOT IN ('ganada','perdida')", c["id"]) or {}).get("n", 0)
        c["principal"] = q1("SELECT * FROM contactos WHERE cliente_id=? ORDER BY principal DESC, id LIMIT 1", c["id"])
        c["tenant_nombre"] = (q1("SELECT nombre FROM clientes WHERE id=?", c["tenant_id"]) or {}).get("nombre", "") if c.get("tenant_id") else ""
        filas.append(c)
    kpi = {k: sum(1 for c in filas if c["est"][0] == k) for k in ("cliente", "demo", "prospecto", "cuenta")}
    return render_template("cuentas.html", filas=filas, kpi=kpi)


@app.route("/cuentas/<int:cid>", methods=["GET", "POST"])
@requiere_login
def cuenta(cid):
    solo_invoke()
    c = cliente_ok(cid)
    if request.method == "POST":
        f = request.form
        acc = f.get("accion")
        if acc == "contacto":
            from contactos import completo
            pila, ape = (f.get("nombre") or "").strip()[:60], (f.get("apellido") or "").strip()[:60]
            nom = completo(pila, ape)
            if not pila or not ape:
                flash("El contacto necesita nombre y apellido", "error")
            else:
                if f.get("principal"):
                    ej("UPDATE contactos SET principal=0 WHERE cliente_id=?", cid)
                kid_ = ej("INSERT INTO contactos (cliente_id, nombre, nombre_pila, apellido, cargo, correo, telefono, rol, principal, notas, creado, por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", cid, nom[:120], pila, ape,
                   (f.get("cargo") or "")[:120], (f.get("correo") or "").strip()[:160], (f.get("telefono") or "")[:40], f.get("rol") if f.get("rol") in ROLES else "",
                   1 if f.get("principal") else 0, (f.get("notas") or "")[:1000], ahora(), g.usuario["usuario"])
                if f.get("principal"):
                    ej("UPDATE clientes SET contacto_nombre=?, contacto_cargo=?, contacto_correo=?, contacto_telefono=? WHERE id=?", nom[:120], (f.get("cargo") or "")[:120],
                       (f.get("correo") or "").strip()[:160], (f.get("telefono") or "")[:40], cid)
                ej("CREATE TABLE IF NOT EXISTS contacto_cuentas (id INTEGER PRIMARY KEY, contacto_id INTEGER NOT NULL, cliente_id INTEGER, cargo TEXT DEFAULT '', "
                   "desde TEXT, hasta TEXT DEFAULT '', por TEXT)")
                ej("INSERT INTO contacto_cuentas (contacto_id, cliente_id, cargo, desde, por) VALUES (?,?,?,?,?)", kid_, cid, (f.get("cargo") or "")[:120], ahora()[:10],
                   g.usuario["usuario"])
                bitacora("contacto agregado", c["codigo"], {"nombre": nom})
                flash(f"Contacto {nom} agregado", "ok")
                return redirect(url_for("cuenta", cid=cid, ct=1))
        elif acc == "principal" and (f.get("id") or "").isdigit():
            x = q1("SELECT * FROM contactos WHERE id=? AND cliente_id=?", int(f["id"]), cid) or abort(404)
            ej("UPDATE contactos SET principal=(id=?) WHERE cliente_id=?", x["id"], cid)
            ej("UPDATE clientes SET contacto_nombre=?, contacto_cargo=?, contacto_correo=?, contacto_telefono=? WHERE id=?", x["nombre"], x["cargo"], x["correo"], x["telefono"], cid)
            flash(f"{x['nombre']} es ahora el contacto principal", "ok")
        elif acc == "quitar" and (f.get("id") or "").isdigit():
            x = q1("SELECT * FROM contactos WHERE id=? AND cliente_id=?", int(f["id"]), cid) or abort(404)
            ej("UPDATE contactos SET cliente_id=0, principal=0 WHERE id=?", x["id"])   # el contacto sigue existiendo, sin cuenta
            ej("UPDATE contacto_cuentas SET hasta=? WHERE contacto_id=? AND COALESCE(hasta,'')=''", ahora()[:10], x["id"])
            bitacora("contacto quitado de la cuenta", c["codigo"], {"nombre": x["nombre"]})
            flash(f"{x['nombre']} ya no está en esta cuenta (se conserva en Contactos)", "ok")
        elif acc == "datos":
            for k in ("razon_social", "rut", "pais", "industria"):
                if k in f:
                    ej(f"UPDATE clientes SET {k}=? WHERE id=?", (f.get(k) or "").strip()[:120], cid)
            flash("Datos de la cuenta actualizados", "ok")
        return redirect(url_for("cuenta", cid=cid))
    contactos = q("SELECT * FROM contactos WHERE cliente_id=? ORDER BY principal DESC, nombre", cid)
    ops = q("SELECT * FROM oportunidades WHERE cliente_id=? ORDER BY id DESC", cid)
    ks = q("SELECT * FROM contratos WHERE cliente_id=? AND estado<>'anulado' ORDER BY id DESC", cid)
    from oportunidades import ETAPA_T
    return render_template("cuenta.html", c=c, est=estado_cuenta(c), contactos=contactos, ops=ops, ks=ks, roles=ROLES, etapa_t=ETAPA_T)
