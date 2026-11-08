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

from app import BD, ahora, app, bitacora, central, cliente_ok, ej, facturacion, llave, q, q1, REPO, requiere_login, solo_invoke

DIR = os.path.join(os.path.dirname(BD), "instalacion")   # bundle de AAP, catálogo de acciones y manifiesto CCSP
MIN = {"cpu": 4, "ram_gb": 15, "disco_gb": 60}
FASES = {"validacion": "Validación", "instalacion": "Instalación", "commit": "Commit", "rollback": "Rollback"}
VALIDEZ_H = 24   # horas que vale una validación exitosa para pasar a la instalación


def iniciar():
    with app.app_context():
        if "instalacion_lanzada" not in {r["name"] for r in q("PRAGMA table_info(clientes)")}:   # FaceOps se instala una sola vez por cliente
            ej("ALTER TABLE clientes ADD COLUMN instalacion_lanzada TEXT DEFAULT ''")
        ej("CREATE TABLE IF NOT EXISTS instalaciones (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, host_id TEXT, host TEXT, fase TEXT, estado TEXT, "
           "detalle TEXT DEFAULT '{}', fecha TEXT)")


iniciar()


def eventos(cid, n=30):
    out = q("SELECT * FROM instalaciones WHERE cliente_id=? ORDER BY id DESC LIMIT ?", cid, n)
    for e in out:
        try:
            e["d"] = json.loads(e.get("detalle") or "{}")
        except ValueError:
            e["d"] = {}
    return out


PASOS = [("validacion", "Validación", "contrato y requisitos", 2), ("contrato", "Contrato", "revalidación", 1), ("sistema", "Sistema", "disco, paquetes y firewall", 4),
         ("bundle", "Bundle de AAP", "descarga de ≈4 GB", 8), ("aap", "AAP y bases de datos", "gateway, controller, hub, EDA, PostgreSQL", 40),
         ("suscripcion", "Suscripción CCSP", "manifiesto de Invoke", 1), ("catalogo", "Catálogo de acciones", "código y plantillas en AAP", 4),
         ("faceops", "FaceOps", "construcción e inicio", 6), ("verificacion", "Verificación", "AAP, FaceOps y enlace", 2), ("cierre", "Commit / rollback", "", 1)]


def progreso(cid):
    """La última instalación (por máquina) en cajas: estado, inicio y duración de cada paso, contra su tiempo típico."""
    from datetime import datetime
    evs = list(reversed(eventos(cid, 300)))
    if not evs:
        return None
    host_id = evs[-1]["host_id"]
    evs = [e for e in evs if e["host_id"] == host_id]
    ult_val = max((i for i, e in enumerate(evs) if e["fase"] == "validacion"), default=0)
    evs = evs[ult_val:]
    f = lambda x: datetime.strptime(x, "%Y-%m-%d %H:%M:%S")
    st = {k: {"estado": "pendiente", "inicio": "", "fin": ""} for k, *_ in PASOS}
    for e in evs:
        k = "validacion" if e["fase"] == "validacion" else ("cierre" if e["fase"] in ("commit", "rollback") else e["d"].get("paso"))
        if k not in st:
            continue
        x = st[k]
        x["inicio"] = x["inicio"] or e["fecha"]
        if e["d"].get("log"):
            x["log"] = e["d"]["log"]
        if e["fase"] == "validacion" and e["d"].get("fallas"):
            x["motivo"] = " · ".join(e["d"]["fallas"])[:300]
        if e["fase"] in ("commit", "rollback"):
            x.update(estado="commit" if e["fase"] == "commit" else "rollback", fin=e["fecha"], motivo=e["d"].get("motivo", ""))
        elif e["estado"] == "en_curso":
            x["estado"] = "en_curso"
        else:
            x.update(estado="ok" if e["estado"] == "ok" else "error", fin=e["fecha"], motivo=e["d"].get("motivo", "") if e["estado"] == "error" else x.get("motivo", ""))
    out, ahora_ = [], datetime.now()
    for k, t, d, tip in PASOS:
        x = st[k]
        dur = None
        if x["inicio"]:
            dur = round(((f(x["fin"]) if x["fin"] else ahora_) - f(x["inicio"])).total_seconds() / 60, 1)
        out.append(dict(x, k=k, t=t, d=d, tipico=tip, dur=dur))
    fin = st["cierre"]["estado"]
    en_curso = next((p for p in out if p["estado"] == "en_curso"), None)
    resta = sum(p["tipico"] for p in out if p["estado"] in ("pendiente", "en_curso") and p["k"] != "cierre") if en_curso else 0
    return {"host": evs[-1].get("host") or host_id[:12], "pasos": out, "resultado": fin, "en_curso": en_curso, "resta_min": resta,
            "inicio": evs[0]["fecha"], "ultimo": evs[-1]["fecha"]}


def intentos(cid):
    """Bitácora de intentos de instalación: cada intento parte con una validación y termina en commit, rollback o validación fallida."""
    from datetime import datetime
    evs = list(reversed(eventos(cid, 1000)))
    out, cur = [], None
    for e in evs:
        if e["fase"] == "validacion":
            cur = {"inicio": e["fecha"], "host": e.get("host") or e["host_id"][:12], "fin": e["fecha"], "resultado": "validado" if e["estado"] == "ok" else "validacion",
                   "paso": "validación", "motivo": " · ".join(e["d"].get("fallas") or []) if e["estado"] != "ok" else "", "avisos": len(e["d"].get("avisos") or [])}
            out.append(cur)
            continue
        if not cur:
            continue
        cur["fin"] = e["fecha"]
        if e["fase"] == "instalacion":
            cur["resultado"] = "en_curso" if cur["resultado"] in ("validado", "en_curso") else cur["resultado"]
            cur["paso"] = dict((k, t) for k, t, *_ in PASOS).get(e["d"].get("paso"), e["d"].get("paso"))
            if e["estado"] == "error":
                cur["motivo"] = e["d"].get("motivo", "")
        elif e["fase"] == "commit":
            cur.update(resultado="commit", paso="commit", motivo=e["d"].get("motivo", ""))
        elif e["fase"] == "rollback":
            cur.update(resultado="rollback", motivo=e["d"].get("motivo", "") or cur.get("motivo", ""))
    f = lambda x: datetime.strptime(x, "%Y-%m-%d %H:%M:%S")
    for x in out:
        x["min"] = round((f(x["fin"]) - f(x["inicio"])).total_seconds() / 60, 1)
        if x["resultado"] == "en_curso" and (datetime.now() - f(x["fin"])).total_seconds() > 900:
            x["resultado"] = "sin_avisos"   # dejó de avisar: se cortó sin rollback o perdió la conexión con FaceAdmin
    return list(reversed(out))


def validacion_vigente(cid, host_id):
    from datetime import datetime, timedelta
    lim = (datetime.now() - timedelta(hours=VALIDEZ_H)).strftime("%Y-%m-%d %H:%M:%S")
    return q1("SELECT * FROM instalaciones WHERE cliente_id=? AND host_id=? AND fase='validacion' AND estado='ok' AND fecha>=? ORDER BY id DESC LIMIT 1",
              cid, host_id, lim)


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


def puede_instalar(c, en_curso=False):
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
    if c.get("instalacion_lanzada") and not en_curso:   # el bloqueo impide lanzar otra; la que ya corre sigue con su llave
        return False, (f"La instalación de {c['instancia_id']} ya se lanzó ({c['instalacion_lanzada']}): FaceOps se instala una sola vez por cliente "
                       "y su ID quedó bloqueado. Solo Invoke puede liberarlo.")
    return True, k


def _por_llave():
    tok = request.headers.get("X-FaceAdmin-Token", "")
    b = q1("SELECT * FROM bloqueos WHERE token=?", hashlib.sha256(tok.encode()).hexdigest()) if tok else None
    if not b:
        abort(401)
    c = q1("SELECT * FROM clientes WHERE id=?", b["cliente_id"])
    ok, k = puede_instalar(c, en_curso=True)
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
    cmd = f"curl -fsSLk {request.host_url.rstrip('/')}{url_for('instalar_publico', tok=tok)} | sudo bash"
    from flask import session
    session[f"cmd_{kid}"] = cmd   # se muestra una sola vez, junto al botón
    return redirect(url_for("contrato_ficha", kid=kid) + "#instalacion")


def _enlace(tok):
    e = q1("SELECT * FROM enlaces_paquete WHERE token=?", hashlib.sha256(tok.encode()).hexdigest())
    if not e or (e["vence"] and e["vence"] < date.today().isoformat()):
        return None, None, "el enlace no es válido o venció."
    c = q1("SELECT * FROM clientes WHERE id=?", e["cliente_id"])
    ok, k = puede_instalar(c)
    return (c, k, None) if ok else (None, None, k)


@app.route("/instalar/<tok>")
def instalar_publico(tok):
    """Paso 1: solo el validador. Revisa contrato y requisitos de la máquina; si todo se cumple, entrega el comando del paso 2."""
    c, k, err = _enlace(tok)
    if err:
        return _bash_error(err)
    txt = render_template("paquetes/validar.sh", c=c, k=k, tok=llave(c), admin=request.host_url.rstrip("/"), enlace=tok, generado=ahora(), minimo=MIN)
    bitacora("validador entregado", c["codigo"], {"contrato": k["numero"], "ip": request.headers.get("X-Forwarded-For", request.remote_addr)})
    return Response(txt, mimetype="text/plain", headers={"Cache-Control": "no-store"})


@app.route("/instalar/<tok>/instalar")
def instalar_paso2(tok):
    """Paso 2 y 3: la instalación transaccional (commit o rollback). Solo para una máquina con validación exitosa reciente."""
    c, k, err = _enlace(tok)
    if err:
        return _bash_error(err)
    h = (request.args.get("h") or "")[:64]
    if not h or not validacion_vigente(c["id"], h):
        return _bash_error(f"esta máquina no tiene una validación exitosa de las últimas {VALIDEZ_H} horas. Corre primero el paso 1 (validación).")
    cen = central()
    if not cen.get("commit"):
        return _bash_error("GitHub no respondió para fijar la versión; prueba de nuevo en unos minutos.")
    txt = render_template("paquetes/instalar-completo.sh", c=c, k=k, tok=llave(c), commit=cen["commit"], repo=f"https://github.com/{REPO}.git",
                          admin=request.host_url.rstrip("/"), generado=ahora(), minimo=MIN, host_id=h)
    huella = hashlib.sha256(txt.encode()).hexdigest()
    ej("INSERT INTO paquetes (cliente_id, so, commit_, generado, por, huella) VALUES (?,?,?,?,?,?)", c["id"], "linux", cen["commit"], ahora(),
       "comando de instalación", huella)
    bitacora("instalador entregado", c["codigo"], {"contrato": k["numero"], "commit": cen["commit"][:12], "host_id": h})
    host = (validacion_vigente(c["id"], h) or {}).get("host") or h[:12]
    ej("UPDATE clientes SET instalacion_lanzada=? WHERE id=?", f"{ahora()[:16]} en {host}", c["id"])   # el ID queda bloqueado: una sola instalación
    return Response(txt, mimetype="text/plain", headers={"Cache-Control": "no-store"})


@app.route("/api/instalacion/evento", methods=["POST"])
def api_inst_evento():
    """El validador y el instalador informan cada fase: validación, instalación, commit o rollback (con su detalle)."""
    tok = request.headers.get("X-FaceAdmin-Token", "")
    b = q1("SELECT * FROM bloqueos WHERE token=?", hashlib.sha256(tok.encode()).hexdigest()) if tok else None
    if not b:
        return {"error": "no autorizado"}, 401
    d = request.get_json(silent=True) or {}
    fase, estado = d.get("fase"), d.get("estado")
    if fase not in FASES or estado not in ("ok", "error", "en_curso"):
        return {"error": "fase o estado no válido"}, 400
    det = d.get("detalle") if isinstance(d.get("detalle"), dict) else {}
    ej("INSERT INTO instalaciones (cliente_id, host_id, host, fase, estado, detalle, fecha) VALUES (?,?,?,?,?,?,?)", b["cliente_id"],
       str(d.get("host_id", ""))[:64], str(d.get("host", ""))[:80], fase, estado, json.dumps(det, ensure_ascii=False)[:20000], ahora())
    c = q1("SELECT codigo FROM clientes WHERE id=?", b["cliente_id"]) or {}
    bitacora(f"instalación · {FASES[fase].lower()} {estado}", c.get("codigo", ""), {"host": d.get("host"), "motivo": det.get("motivo", "")})
    if fase == "rollback":   # la instalación se revirtió: el flujo queda habilitado de nuevo para reintentar
        ej("UPDATE clientes SET instalacion_lanzada='' WHERE id=?", b["cliente_id"])
        bitacora("ID de instalación liberado automáticamente", c.get("codigo", ""), {"motivo": "rollback: " + str(det.get("motivo", ""))[:200]})
    return {"ok": True}


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


@app.route("/clientes/<int:cid>/instalacion/liberar", methods=["POST"])
@requiere_login
def instalacion_liberar(cid):
    """Solo Invoke: libera el ID para una nueva instalación (p. ej. tras un rollback). Queda en la bitácora con el motivo."""
    solo_invoke()
    c = cliente_ok(cid)
    m = (request.form.get("motivo") or "").strip()
    if not m:
        flash("Indica el motivo para liberar el ID", "error")
    else:
        ej("UPDATE clientes SET instalacion_lanzada='' WHERE id=?", cid)
        bitacora("ID de instalación liberado", c["codigo"], {"motivo": m[:300], "antes": c.get("instalacion_lanzada")})
        flash(f"ID {c['instancia_id']} liberado para una nueva instalación", "ok")
    return redirect(request.referrer or url_for("cliente", cid=cid))


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
