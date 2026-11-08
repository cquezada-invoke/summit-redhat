"""FaceAdmin · instalación de FaceOps en la máquina del cliente con un solo comando.

    curl -fsSL https://<faceadmin>/instalar/<id> | sudo bash

El <id> es un enlace de un solo cliente (vence con el plazo de activación). Antes de entregar el instalador se valida el contrato:
firmado, con su orden de compra conciliada, FaceOps habilitado y el cliente sin bloqueo. El instalador vuelve a validar al
arrancar y descarga desde aquí, autenticado con la llave del cliente, todo lo que necesita: el bundle de Ansible Automation
Platform (con sus bases de datos), el catálogo de acciones y el manifiesto de suscripción CCSP de Invoke."""
import glob
import hashlib
import json
import os
import secrets
from datetime import date

from flask import Response, abort, flash, g, redirect, render_template, request, send_file, url_for

from app import BD, ahora, app, bitacora, central, cliente_ok, ej, facturacion, llave, q1, REPO, requiere_login, solo_invoke

DIR = os.path.join(os.path.dirname(BD), "instalacion")   # bundle de AAP, catálogo de acciones y manifiesto CCSP
MIN = {"cpu": 4, "ram_gb": 15, "disco_gb": 60}


def archivos():
    """Lo que FaceAdmin tiene para entregar al instalador."""
    bun = sorted(glob.glob(os.path.join(DIR, "ansible-automation-platform-containerized-setup-bundle-*.tar")))
    man = os.path.join(DIR, "manifiesto-ccsp.zip")
    cat = os.path.join(DIR, "catalogo-aap.json")

    def info(p):
        if not p or not os.path.exists(p):
            return None
        st = os.stat(p)
        return {"nombre": os.path.basename(p), "gb": round(st.st_size / 1024 ** 3, 2), "bytes": st.st_size,
                "fecha": date.fromtimestamp(st.st_mtime).isoformat()}
    cat_n = 0
    if os.path.exists(cat):
        try:
            cat_n = len(json.load(open(cat, encoding="utf-8")).get("plantillas") or [])
        except (OSError, ValueError):
            cat_n = 0
    return {"bundle": info(bun[-1] if bun else None), "manifiesto": info(man), "catalogo": dict(info(cat) or {}, n=cat_n) if os.path.exists(cat) else None}


def puede_instalar(c):
    """(True, contrato) si el cliente puede instalar su FaceOps; si no, (False, motivo)."""
    if not c or c.get("tipo") == "tenant":
        return False, "Este enlace no corresponde a un FaceOps de cliente."
    k = q1("SELECT * FROM contratos WHERE cliente_id=? AND estado<>'anulado' ORDER BY id DESC LIMIT 1", c["id"])
    if not k or k["estado"] != "firmado":
        return False, "El contrato del cliente aún no está firmado."
    if not q1("SELECT id FROM ordenes WHERE contrato_id=?", k["id"]):
        return False, f"El contrato {k['numero']} no tiene su orden de compra conciliada."
    if not c.get("habilitado") or not c.get("instancia_id"):
        return False, f"El FaceOps del contrato {k['numero']} aún no está habilitado en FaceAdmin."
    fa = facturacion(c["id"])
    if fa["bloqueado"]:
        return False, f"Servicio suspendido: {fa['motivo']}"
    return True, k


def _por_llave():
    tok = request.headers.get("X-FaceAdmin-Token", "")
    b = q1("SELECT * FROM bloqueos WHERE token=?", hashlib.sha256(tok.encode()).hexdigest()) if tok else None
    if not b:
        abort(401)
    c = q1("SELECT * FROM clientes WHERE id=?", b["cliente_id"])
    ok, k = puede_instalar(c)
    if not ok:
        abort(Response(json.dumps({"ok": False, "motivo": k}, ensure_ascii=False), 403, mimetype="application/json"))
    return c, k


def _bash_error(motivo):
    m = motivo.replace("'", " ")
    return Response(f"#!/bin/bash\necho\necho '✗ FaceOps no se puede instalar: {m}'\necho '  Revisa el contrato en FaceAdmin o pide un enlace nuevo.'\nexit 1\n",
                    mimetype="text/plain")


# ---------------------------------------------------------------- comando de instalación
@app.route("/contratos/<int:kid>/comando", methods=["POST"])
@requiere_login
def contrato_comando(kid):
    """Genera el comando de instalación del FaceOps del contrato (enlace propio del cliente, hasta el plazo de activación)."""
    k = q1("SELECT * FROM contratos WHERE id=?", kid) or abort(404)
    c = cliente_ok(k["cliente_id"])
    ok, motivo = puede_instalar(c)
    if not ok:
        flash(motivo, "error")
        return redirect(url_for("contrato_ficha", kid=kid))
    tok = secrets.token_hex(20)
    vence = k.get("activacion_limite") or c.get("activacion_limite") or ""
    ej("INSERT INTO enlaces_paquete (cliente_id, token, vence, creado) VALUES (?,?,?,?)", c["id"], hashlib.sha256(tok.encode()).hexdigest(), vence, ahora())
    bitacora("comando de instalación", c["codigo"], {"contrato": k["numero"], "vence": vence})
    cmd = f"curl -fsSL {request.host_url.rstrip('/')}{url_for('instalar_publico', tok=tok)} | sudo bash"
    flash(cmd, "comando")
    return redirect(url_for("contrato_ficha", kid=kid) + "#instalacion")


@app.route("/instalar/<tok>")
def instalar_publico(tok):
    e = q1("SELECT * FROM enlaces_paquete WHERE token=?", hashlib.sha256(tok.encode()).hexdigest())
    if not e or (e["vence"] and e["vence"] < date.today().isoformat()):
        return _bash_error("el enlace no es válido o venció.")
    c = q1("SELECT * FROM clientes WHERE id=?", e["cliente_id"])
    ok, k = puede_instalar(c)
    if not ok:
        return _bash_error(k)
    cen = central()
    if not cen.get("commit"):
        return _bash_error("GitHub no respondió para fijar la versión; prueba de nuevo en unos minutos.")
    txt = render_template("paquetes/instalar-completo.sh", c=c, k=k, tok=llave(c), commit=cen["commit"], repo=f"https://github.com/{REPO}.git",
                          admin=request.host_url.rstrip("/"), generado=ahora(), minimo=MIN)
    huella = hashlib.sha256(txt.encode()).hexdigest()
    ej("INSERT INTO paquetes (cliente_id, so, commit_, generado, por, huella) VALUES (?,?,?,?,?,?)", c["id"], "linux", cen["commit"], ahora(),
       "comando de instalación", huella)
    bitacora("instalador entregado", c["codigo"], {"contrato": k["numero"], "commit": cen["commit"][:12], "ip": request.headers.get("X-Forwarded-For", request.remote_addr)})
    return Response(txt, mimetype="text/plain", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- lo que descarga el instalador (con la llave del cliente)
@app.route("/api/instalacion/validar")
def api_inst_validar():
    c, k = _por_llave()
    return {"ok": True, "cliente": c["nombre"], "instancia_id": c["instancia_id"], "contrato": k["numero"],
            "puesta_en_marcha_hasta": k.get("activacion_limite", ""), "archivos": {x: bool(v) for x, v in archivos().items()}}


@app.route("/api/instalacion/bundle")
def api_inst_bundle():
    c, _ = _por_llave()
    b = archivos()["bundle"]
    if not b:
        return {"ok": False, "motivo": "FaceAdmin aún no tiene el bundle de AAP"}, 404
    if not request.headers.get("Range"):
        bitacora("bundle de AAP descargado", c["codigo"], {"bundle": b["nombre"]})
    return send_file(os.path.join(DIR, b["nombre"]), as_attachment=True, download_name=b["nombre"], conditional=True)


@app.route("/api/instalacion/manifiesto")
def api_inst_manifiesto():
    _por_llave()
    p = os.path.join(DIR, "manifiesto-ccsp.zip")
    if not os.path.exists(p):
        return {"ok": False, "motivo": "Sin manifiesto CCSP cargado en FaceAdmin"}, 404
    return send_file(p, as_attachment=True, download_name="manifiesto.zip")


@app.route("/api/instalacion/catalogo")
def api_inst_catalogo():
    _por_llave()
    p = os.path.join(DIR, "catalogo-aap.json")
    if not os.path.exists(p):
        return {"plantillas": []}
    return send_file(p, mimetype="application/json")


# ---------------------------------------------------------------- administración (Invoke)
@app.route("/fabrica/instalacion", methods=["GET", "POST"])
@requiere_login
def fabrica_instalacion():
    solo_invoke()
    if request.method == "POST":
        f = request.files.get("manifiesto")
        if not f or not f.filename.lower().endswith(".zip"):
            flash("Sube el manifiesto de suscripción (.zip) exportado del portal de Red Hat", "error")
        else:
            os.makedirs(DIR, exist_ok=True)
            f.save(os.path.join(DIR, "manifiesto-ccsp.zip"))
            bitacora("manifiesto CCSP cargado", "invoke", {"archivo": f.filename})
            flash("Manifiesto CCSP cargado: los próximos FaceOps instalan AAP con esta suscripción", "ok")
        return redirect(url_for("fabrica_instalacion"))
    return render_template("fabrica_instalacion.html", ar=archivos(), minimo=MIN)
