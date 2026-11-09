"""FaceAdmin · Contactos: personas, independientes de las cuentas. Un contacto tiene una cuenta actual (o ninguna) y su historial:
cuando cambia de empresa se mueve a la otra cuenta y queda registrado dónde estuvo."""
from flask import abort, flash, g, redirect, render_template, request, url_for

from app import ahora, app, bitacora, ej, en_alcance, q, q1, requiere_login, solo_invoke
from cuentas import ROLES


def iniciar():
    with app.app_context():
        ej("CREATE TABLE IF NOT EXISTS contacto_cuentas (id INTEGER PRIMARY KEY, contacto_id INTEGER NOT NULL, cliente_id INTEGER, cargo TEXT DEFAULT '', "
           "desde TEXT, hasta TEXT DEFAULT '', por TEXT)")
        for c in q("SELECT * FROM contactos WHERE id NOT IN (SELECT contacto_id FROM contacto_cuentas) AND COALESCE(cliente_id,0)>0"):
            ej("INSERT INTO contacto_cuentas (contacto_id, cliente_id, cargo, desde, por) VALUES (?,?,?,?,?)", c["id"], c["cliente_id"], c.get("cargo") or "",
               (c.get("creado") or ahora())[:10], c.get("por") or "sistema")


iniciar()


def _cuentas():
    return [c for c in q("SELECT id, nombre, codigo, pais FROM clientes WHERE activo=1 AND COALESCE(propio,0)=0 ORDER BY nombre") if en_alcance(c)]


def _guardar(f, x=None):
    nom = (f.get("nombre") or "").strip()[:120]
    if not nom:
        raise ValueError("El contacto necesita un nombre")
    d = {"nombre": nom, "cargo": (f.get("cargo") or "").strip()[:120], "correo": (f.get("correo") or "").strip()[:160],
         "telefono": (f.get("telefono") or "").strip()[:40], "rol": f.get("rol") if f.get("rol") in ROLES else "", "notas": (f.get("notas") or "")[:1000]}
    if x:
        ej("UPDATE contactos SET " + ", ".join(f"{k}=?" for k in d) + " WHERE id=?", *d.values(), x["id"])
        return x["id"]
    cid = int(f["cliente_id"]) if (f.get("cliente_id") or "").isdigit() else 0
    kid = ej("INSERT INTO contactos (cliente_id, nombre, cargo, correo, telefono, rol, principal, notas, creado, por) VALUES (?,?,?,?,?,?,?,?,?,?)",
             cid, d["nombre"], d["cargo"], d["correo"], d["telefono"], d["rol"], 0, d["notas"], ahora(), g.usuario["usuario"])
    if cid:
        ej("INSERT INTO contacto_cuentas (contacto_id, cliente_id, cargo, desde, por) VALUES (?,?,?,?,?)", kid, cid, d["cargo"], ahora()[:10], g.usuario["usuario"])
    return kid


@app.route("/contactos", methods=["GET", "POST"])
@requiere_login
def contactos_lista():
    solo_invoke()
    if request.method == "POST":
        try:
            kid = _guardar(request.form)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("contactos_lista"))
        bitacora("contacto creado", "invoke", {"nombre": request.form.get("nombre")})
        flash("Contacto creado", "ok")
        return redirect(url_for("contacto", kid=kid))
    vis = {c["id"]: c for c in _cuentas()}
    filas = []
    for x in q("SELECT * FROM contactos ORDER BY nombre"):
        if x["cliente_id"] and x["cliente_id"] not in vis:
            continue
        x["cuenta"] = vis.get(x["cliente_id"])
        x["n_cuentas"] = (q1("SELECT COUNT(*) n FROM contacto_cuentas WHERE contacto_id=?", x["id"]) or {}).get("n", 0)
        filas.append(x)
    return render_template("contactos.html", filas=filas, cuentas=list(vis.values()), roles=ROLES)


@app.route("/contactos/<int:kid>", methods=["GET", "POST"])
@requiere_login
def contacto(kid):
    solo_invoke()
    x = q1("SELECT * FROM contactos WHERE id=?", kid) or abort(404)
    vis = {c["id"]: c for c in _cuentas()}
    if x["cliente_id"] and x["cliente_id"] not in vis:
        abort(404)
    if request.method == "POST":
        f = request.form
        if f.get("accion") == "mover":
            nueva = int(f["cliente_id"]) if (f.get("cliente_id") or "").isdigit() and int(f["cliente_id"]) in vis else 0
            if nueva == (x["cliente_id"] or 0):
                flash("El contacto ya está en esa cuenta", "error")
                return redirect(url_for("contacto", kid=kid))
            hoy = ahora()[:10]
            ej("UPDATE contacto_cuentas SET hasta=? WHERE contacto_id=? AND COALESCE(hasta,'')=''", hoy, kid)
            cargo = (f.get("cargo") or "").strip()[:120] or x["cargo"]
            if nueva:
                ej("INSERT INTO contacto_cuentas (contacto_id, cliente_id, cargo, desde, por) VALUES (?,?,?,?,?)", kid, nueva, cargo, hoy, g.usuario["usuario"])
            ej("UPDATE contactos SET cliente_id=?, cargo=?, principal=0 WHERE id=?", nueva, cargo, kid)
            bitacora("contacto movido de cuenta", "invoke", {"contacto": x["nombre"], "de": (vis.get(x["cliente_id"]) or {}).get("nombre", "—"),
                                                               "a": (vis.get(nueva) or {}).get("nombre", "sin cuenta")})
            flash(f"{x['nombre']} ahora está en {(vis.get(nueva) or {}).get('nombre', 'ninguna cuenta')}", "ok")
        else:
            try:
                _guardar(f, x)
                flash("Contacto actualizado", "ok")
            except ValueError as e:
                flash(str(e), "error")
        return redirect(url_for("contacto", kid=kid))
    hist = q("SELECT h.*, c.nombre cuenta FROM contacto_cuentas h LEFT JOIN clientes c ON c.id=h.cliente_id WHERE h.contacto_id=? ORDER BY h.id DESC", kid)
    return render_template("contacto.html", x=x, cuenta=vis.get(x["cliente_id"]), cuentas=list(vis.values()), hist=hist, roles=ROLES)
