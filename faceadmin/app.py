"""FaceAdmin · consola global de Invoke: los clientes de FaceOps, sus instancias (versión y estado) y sus créditos.
Es un sistema aparte de FaceOps: vive en su propia máquina, con su propia base de datos (SQLite en FACEADMIN_BD) y su
propio ingreso. No opera máquinas: solo consulta el /version público de cada FaceOps y el repositorio central (GitHub)."""
import json
import os
import secrets
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import wraps

import requests
import urllib3
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

urllib3.disable_warnings()
BD = os.environ.get("FACEADMIN_BD", "/var/lib/faceadmin/faceadmin.db")
REPO = os.environ.get("FACEADMIN_REPO", "cquezada-invoke/summit-redhat")
app = Flask(__name__)
_clave = os.path.join(os.path.dirname(BD), "secreto")
os.makedirs(os.path.dirname(BD), exist_ok=True)
if not os.path.exists(_clave):
    with open(_clave, "w") as f:
        f.write(secrets.token_hex(32))
app.secret_key = open(_clave).read().strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=True, PERMANENT_SESSION_LIFETIME=8 * 3600)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY, usuario TEXT UNIQUE NOT NULL, nombre TEXT, clave TEXT NOT NULL, creado TEXT);
CREATE TABLE IF NOT EXISTS clientes (id INTEGER PRIMARY KEY, codigo TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL, url TEXT NOT NULL,
  maquina TEXT DEFAULT '', tamano TEXT DEFAULT '', rol TEXT DEFAULT 'cliente', notas TEXT DEFAULT '', activo INTEGER DEFAULT 1, creado TEXT);
CREATE TABLE IF NOT EXISTS creditos (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, fecha TEXT NOT NULL, tipo TEXT NOT NULL,
  monto REAL NOT NULL, detalle TEXT DEFAULT '', usuario TEXT);
CREATE TABLE IF NOT EXISTS estados (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, fecha TEXT NOT NULL, responde INTEGER, version TEXT, ms INTEGER);
CREATE TABLE IF NOT EXISTS activaciones (id INTEGER PRIMARY KEY, usuario TEXT NOT NULL, token TEXT NOT NULL, vence REAL NOT NULL, usado INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS facturas (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, numero TEXT NOT NULL, emision TEXT NOT NULL,
  vence TEXT NOT NULL, monto REAL NOT NULL, moneda TEXT DEFAULT 'CLP', detalle TEXT DEFAULT '', pagada TEXT DEFAULT '', creado TEXT);
CREATE TABLE IF NOT EXISTS bloqueos (cliente_id INTEGER PRIMARY KEY, bloqueado INTEGER DEFAULT 0, motivo TEXT DEFAULT '', desde TEXT DEFAULT '',
  por TEXT DEFAULT '', gracia INTEGER DEFAULT 15, automatico INTEGER DEFAULT 0, token TEXT DEFAULT '', consulta TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS paquetes (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, so TEXT NOT NULL, commit_ TEXT, generado TEXT, por TEXT,
  huella TEXT, instalado TEXT DEFAULT '', host TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS bitacora (id INTEGER PRIMARY KEY, fecha TEXT, usuario TEXT, accion TEXT, objeto TEXT, detalle TEXT);
"""
TIPOS = {"carga": "Carga", "consumo": "Consumo", "ajuste": "Ajuste"}


def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def bd():
    if "bd" not in g:
        g.bd = sqlite3.connect(BD)
        g.bd.row_factory = sqlite3.Row
    return g.bd


@app.teardown_appcontext
def _cerrar(_e):
    c = g.pop("bd", None)
    if c:
        c.close()


def q(sql, *a):
    return [dict(r) for r in bd().execute(sql, a).fetchall()]


def q1(sql, *a):
    r = q(sql, *a)
    return r[0] if r else None


def ej(sql, *a):
    c = bd().execute(sql, a)
    bd().commit()
    return c.lastrowid


def bitacora(accion, objeto, detalle=None):
    ej("INSERT INTO bitacora (fecha, usuario, accion, objeto, detalle) VALUES (?,?,?,?,?)", ahora(),
       (g.get("usuario") or {}).get("usuario", "sistema"), accion, objeto, json.dumps(detalle or {}, ensure_ascii=False))


def nuevo_id(codigo):
    """FOP-<código>-<12 hex>: huella única de la instancia de FaceOps del cliente."""
    import hashlib
    return f"FOP-{codigo.upper()}-" + hashlib.sha256(f"{codigo}|{secrets.token_hex(16)}|{time.time()}".encode()).hexdigest()[:12]


def iniciar():
    with app.app_context():
        bd().executescript(ESQUEMA)
        u0 = os.environ.get("FACEADMIN_ADMIN_USUARIO")
        if u0 and not q1("SELECT id FROM usuarios WHERE usuario=?", u0):   # sin clave: se define con un enlace de activación
            ej("INSERT INTO usuarios (usuario, nombre, clave, creado) VALUES (?,?,?,?)", u0, os.environ.get("FACEADMIN_ADMIN_NOMBRE", u0),
               generate_password_hash(os.environ["FACEADMIN_ADMIN_CLAVE"]) if os.environ.get("FACEADMIN_ADMIN_CLAVE") else "!", ahora())
        cols = {r["name"] for r in q("PRAGMA table_info(clientes)")}
        if "instancia_id" not in cols:   # identidad única de la instancia de FaceOps de cada cliente (no cambia con la versión)
            ej("ALTER TABLE clientes ADD COLUMN instancia_id TEXT DEFAULT ''")
        for linea in filter(None, (os.environ.get("FACEADMIN_CLIENTES") or "").split(";")):   # codigo|nombre|url|maquina|tamaño|rol
            p = (linea.split("|") + [""] * 6)[:6]
            if p[0] and not q1("SELECT id FROM clientes WHERE codigo=?", p[0]):
                ej("INSERT INTO clientes (codigo, nombre, url, maquina, tamano, rol, creado) VALUES (?,?,?,?,?,?,?)",
                   p[0], p[1], p[2].rstrip("/"), p[3], p[4], p[5] or "cliente", ahora())
        for c in q("SELECT id, codigo FROM clientes WHERE COALESCE(instancia_id,'')=''"):
            ej("UPDATE clientes SET instancia_id=? WHERE id=?", nuevo_id(c["codigo"]), c["id"])


iniciar()


def _sembrar_facturas():
    """Datos de prueba de facturación (solo si no hay facturas): una al día, una pendiente por vencer y una vencida."""
    from datetime import date, timedelta
    with app.app_context():
        if q1("SELECT id FROM facturas"):
            return
        hoy = date.today()
        demo = {"lab": [("F-1021", -95, -65, 1850000, "2026-08-02"), ("F-1088", -64, -34, 1850000, "2026-09-03"), ("F-1140", -10, 20, 1850000, "")],
                "bau": [("F-1022", -95, -65, 4200000, "2026-08-04"), ("F-1089", -64, -34, 4200000, "2026-09-05"), ("F-1141", -33, -3, 4200000, "2026-10-29")],
                "rpa": [("F-1023", -95, -65, 3100000, "2026-08-10"), ("F-1090", -64, -34, 3100000, "2026-09-12"), ("F-1142", -33, -3, 3100000, "")]}
        for cod, fs in demo.items():
            c = q1("SELECT id FROM clientes WHERE codigo=?", cod)
            for num, em, ve, monto, pag in fs if c else []:
                ej("INSERT INTO facturas (cliente_id, numero, emision, vence, monto, moneda, detalle, pagada, creado) VALUES (?,?,?,?,?,?,?,?,?)",
                   c["id"], num, str(hoy + timedelta(days=em)), str(hoy + timedelta(days=ve)), monto, "CLP", "Managed Service FaceOps · mensualidad (dato de prueba)", pag, ahora())


_sembrar_facturas()


def facturacion(cid):
    """Facturas del cliente, su deuda y la fecha de posible bloqueo (vencimiento de la factura impaga más antigua + días de gracia)."""
    from datetime import date, timedelta
    b = q1("SELECT * FROM bloqueos WHERE cliente_id=?", cid) or {"bloqueado": 0, "motivo": "", "desde": "", "por": "", "gracia": 15, "automatico": 0, "token": "", "consulta": ""}
    fs = q("SELECT * FROM facturas WHERE cliente_id=? ORDER BY emision DESC", cid)
    hoy = str(date.today())
    pend = [f for f in fs if not f["pagada"]]
    for f in fs:
        f["estado"] = "pagada" if f["pagada"] else ("vencida" if f["vence"] < hoy else "pendiente")
    mas_antigua = min(pend, key=lambda f: f["vence"]) if pend else None
    posible = str(date.fromisoformat(mas_antigua["vence"]) + timedelta(days=int(b["gracia"] or 0))) if mas_antigua else ""
    bloqueado, motivo = bool(b["bloqueado"]), b["motivo"]
    if not bloqueado and b["automatico"] and posible and posible <= hoy:
        bloqueado = True
        motivo = (f"Servicio suspendido por factura impaga {mas_antigua['numero']} (vencida el {mas_antigua['vence']}). "
                  "Regulariza el pago para recuperar el acceso.")
    return {"facturas": fs, "pendientes": pend, "deuda": sum(f["monto"] for f in pend), "vencidas": [f for f in pend if f["vence"] < hoy],
            "posible": posible, "bloqueo": b, "bloqueado": bloqueado, "motivo": motivo,
            "automatico_hoy": bool(not b["bloqueado"] and bloqueado)}


def requiere_login(f):
    @wraps(f)
    def w(*a, **k):
        if not g.get("usuario"):
            return redirect(url_for("ingresar", siguiente=request.path))
        return f(*a, **k)
    return w


@app.before_request
def _antes():
    g.usuario = q1("SELECT id, usuario, nombre FROM usuarios WHERE id=?", session["uid"]) if session.get("uid") else None
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST" and request.endpoint not in ("ingresar", "api_registro") and request.form.get("_csrf") != session["csrf"]:
        abort(403)


@app.context_processor
def _ctx():
    return {"csrf": session.get("csrf"), "usuario": g.get("usuario"), "version": _version_propia()}


def _version_propia():
    try:
        return json.load(open(os.path.join(os.path.dirname(__file__), "version.json")))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- estado de cada FaceOps y del repositorio central
_cache = {}


def _cacheado(clave, segundos, fn):
    v = _cache.get(clave)
    if v and time.time() - v[0] < segundos:
        return v[1]
    r = fn()
    _cache[clave] = (time.time(), r)
    return r


def central():
    """Último commit de main en el repositorio central (GitHub)."""
    def f():
        try:
            r = requests.get(f"https://api.github.com/repos/{REPO}/commits/main", timeout=6, headers={"Accept": "application/vnd.github+json"})
            d = r.json() if r.ok else {}
            return {"commit": d.get("sha"), "fecha": ((d.get("commit") or {}).get("committer") or {}).get("date", "")[:16].replace("T", " "),
                    "mensaje": ((d.get("commit") or {}).get("message") or "").split("\n")[0][:120]}
        except (requests.RequestException, ValueError):
            return {}
    return _cacheado("central", 300, f)


def atraso(commit):
    """Commits que le faltan a una instancia respecto de main (comparación en GitHub)."""
    c = central().get("commit")
    if not commit or not c:
        return None
    if commit == c:
        return 0

    def f():
        try:
            r = requests.get(f"https://api.github.com/repos/{REPO}/compare/{commit}...{c}", timeout=6)
            return r.json().get("ahead_by") if r.ok else None
        except (requests.RequestException, ValueError):
            return None
    return _cacheado("cmp-" + commit, 300, f)


def sondear(cli):
    """GET <url>/version de un FaceOps (público, sin datos del cliente)."""
    t0 = time.time()
    try:
        r = requests.get(cli["url"] + "/version", timeout=5, verify=False)
        v = r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        v = None
    return {"responde": bool(v), "version": v, "ms": round((time.time() - t0) * 1000)}


def estados(clientes):
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(lambda c: _cacheado("est-" + c["codigo"], 30, lambda: sondear(c)), clientes))
    for c, r in zip(clientes, res):
        c["estado"] = r
        v = r["version"] or {}
        c["atraso"] = atraso(v.get("commit")) if r["responde"] else None
        ult = q1("SELECT fecha, responde FROM estados WHERE cliente_id=? ORDER BY id DESC LIMIT 1", c["id"])
        if not ult or ult["responde"] != int(r["responde"]):   # se registra solo el cambio (encendido ↔ sin respuesta)
            ej("INSERT INTO estados (cliente_id, fecha, responde, version, ms) VALUES (?,?,?,?,?)", c["id"], ahora(), int(r["responde"]),
               json.dumps(v), r["ms"])
            ult = {"fecha": ahora()}
        c["desde"] = ult["fecha"]
    return clientes


def saldos():
    return {r["cliente_id"]: r for r in q("SELECT cliente_id, SUM(CASE WHEN tipo='consumo' THEN -monto ELSE monto END) saldo, "
                                          "SUM(CASE WHEN tipo='carga' THEN monto ELSE 0 END) cargado, "
                                          "SUM(CASE WHEN tipo='consumo' THEN monto ELSE 0 END) consumido FROM creditos GROUP BY cliente_id")}


# ---------------------------------------------------------------- vistas
@app.route("/ingresar", methods=["GET", "POST"])
def ingresar():
    if request.method == "POST":
        u = q1("SELECT * FROM usuarios WHERE usuario=?", request.form.get("usuario", ""))
        if u and u["clave"] != "!" and check_password_hash(u["clave"], request.form.get("clave", "")):
            session.clear()
            session.update(uid=u["id"], csrf=secrets.token_hex(16))
            session.permanent = True
            g.usuario = u
            bitacora("ingreso", u["usuario"])
            sig = request.args.get("siguiente", "")
            return redirect(sig if sig.startswith("/") and not sig.startswith("//") else url_for("panel"))
        time.sleep(1)
        flash("Usuario o clave incorrectos", "error")
    return render_template("ingresar.html")


def nueva_activacion(usuario, horas=24):
    """Enlace de un solo uso para que el usuario defina su clave (se guarda solo la huella del token)."""
    import hashlib
    with app.app_context():
        if not q1("SELECT id FROM usuarios WHERE usuario=?", usuario):
            raise ValueError("usuario desconocido")
        t = secrets.token_urlsafe(24)
        ej("INSERT INTO activaciones (usuario, token, vence) VALUES (?,?,?)", usuario, hashlib.sha256(t.encode()).hexdigest(), time.time() + horas * 3600)
        return t


@app.route("/activar/<tok>", methods=["GET", "POST"])
def activar(tok):
    import hashlib
    a = q1("SELECT * FROM activaciones WHERE token=? AND usado=0 AND vence>?", hashlib.sha256(tok.encode()).hexdigest(), time.time())
    if not a:
        return render_template("ingresar.html", aviso="El enlace de activación no es válido o ya se usó."), 410
    if request.method == "POST":
        c1, c2 = request.form.get("clave", ""), request.form.get("clave2", "")
        if len(c1) < 12 or c1 != c2:
            flash("La clave debe tener al menos 12 caracteres y coincidir en ambos campos", "error")
        else:
            ej("UPDATE usuarios SET clave=? WHERE usuario=?", generate_password_hash(c1), a["usuario"])
            ej("UPDATE activaciones SET usado=1 WHERE usuario=?", a["usuario"])
            g.usuario = {"usuario": a["usuario"]}
            bitacora("clave definida", a["usuario"])
            flash("Clave definida. Ya puedes ingresar.", "ok")
            return redirect(url_for("ingresar"))
    return render_template("activar.html", usuario=a["usuario"])


@app.route("/salir")
def salir():
    session.clear()
    return redirect(url_for("ingresar"))


@app.route("/version")
def version():
    return _version_propia() | {"sistema": "faceadmin"}


@app.route("/")
@requiere_login
def panel():
    cl = estados(q("SELECT * FROM clientes WHERE activo=1 ORDER BY rol='laboratorio' DESC, nombre"))
    s = saldos()
    for c in cl:
        c["creditos"] = s.get(c["id"]) or {"saldo": 0, "cargado": 0, "consumido": 0}
        c["fa"] = facturacion(c["id"])
    return render_template("panel.html", clientes=cl, central=central(), repo=REPO,
                           resumen={"n": len(cl), "on": sum(1 for c in cl if c["estado"]["responde"]),
                                    "al_dia": sum(1 for c in cl if c["atraso"] == 0), "saldo": sum(c["creditos"]["saldo"] or 0 for c in cl),
                                    "bloqueados": sum(1 for c in cl if c["fa"]["bloqueado"]), "con_deuda": sum(1 for c in cl if c["fa"]["vencidas"])})


@app.route("/clientes/<int:cid>", methods=["GET", "POST"])
@requiere_login
def cliente(cid):
    c = q1("SELECT * FROM clientes WHERE id=?", cid) or abort(404)
    if request.method == "POST":
        tipo = request.form.get("tipo", "")
        try:
            monto = round(float(request.form.get("monto", "0").replace(",", ".")), 2)
        except ValueError:
            monto = 0
        if tipo not in TIPOS or monto <= 0 or not request.form.get("detalle", "").strip():
            flash("Indica tipo, un monto mayor que cero y el detalle", "error")
        else:
            ej("INSERT INTO creditos (cliente_id, fecha, tipo, monto, detalle, usuario) VALUES (?,?,?,?,?,?)", cid, ahora(), tipo, monto,
               request.form["detalle"].strip()[:200], g.usuario["usuario"])
            bitacora(f"créditos · {tipo}", c["codigo"], {"monto": monto, "detalle": request.form["detalle"][:200]})
            flash(f"{TIPOS[tipo]} de {monto:g} créditos registrada para {c['nombre']}", "ok")
        return redirect(url_for("cliente", cid=cid))
    estados([c])
    return render_template("cliente.html", c=c, fa=facturacion(cid), paquetes=q("SELECT * FROM paquetes WHERE cliente_id=? ORDER BY id DESC LIMIT 10", cid), hoy=datetime.now().strftime("%Y-%m-%d"), mov=q("SELECT * FROM creditos WHERE cliente_id=? ORDER BY id DESC", cid),
                           saldo=saldos().get(cid) or {"saldo": 0, "cargado": 0, "consumido": 0}, tipos=TIPOS,
                           historial=q("SELECT * FROM estados WHERE cliente_id=? ORDER BY id DESC LIMIT 20", cid), central=central())


@app.route("/clientes/<int:cid>/facturacion", methods=["POST"])
@requiere_login
def cliente_facturacion(cid):
    c = q1("SELECT * FROM clientes WHERE id=?", cid) or abort(404)
    f, ac = request.form, request.form.get("accion", "")
    ej("INSERT OR IGNORE INTO bloqueos (cliente_id) VALUES (?)", cid)
    if ac == "bloquear":
        motivo = (f.get("motivo") or "").strip()[:300]
        if not motivo:
            flash("Indica el motivo: es lo que verán los usuarios al intentar entrar", "error")
        else:
            ej("UPDATE bloqueos SET bloqueado=1, motivo=?, desde=?, por=? WHERE cliente_id=?", motivo, ahora(), g.usuario["usuario"], cid)
            bitacora("bloqueo manual", c["codigo"], {"motivo": motivo})
            flash(f"{c['nombre']} bloqueado: sus usuarios verán el motivo al ingresar", "ok")
    elif ac == "desbloquear":
        ej("UPDATE bloqueos SET bloqueado=0, desde=?, por=? WHERE cliente_id=?", ahora(), g.usuario["usuario"], cid)
        bitacora("desbloqueo", c["codigo"])
        flash(f"{c['nombre']} desbloqueado", "ok")
    elif ac == "regla":
        g_ = max(0, min(120, int(f.get("gracia") or 15)))
        ej("UPDATE bloqueos SET gracia=?, automatico=? WHERE cliente_id=?", g_, int(bool(f.get("automatico"))), cid)
        bitacora("regla de bloqueo", c["codigo"], {"gracia": g_, "automatico": bool(f.get("automatico"))})
        flash("Regla de bloqueo guardada", "ok")
    elif ac == "pagada":
        ej("UPDATE facturas SET pagada=? WHERE id=? AND cliente_id=?", datetime.now().strftime("%Y-%m-%d"), int(f.get("factura_id") or 0), cid)
        bitacora("factura pagada", c["codigo"], {"factura_id": f.get("factura_id")})
        flash("Factura marcada como pagada", "ok")
    elif ac == "factura":
        try:
            monto = float((f.get("monto") or "0").replace(".", "").replace(",", "."))
        except ValueError:
            monto = 0
        if not f.get("numero") or monto <= 0 or not f.get("vence"):
            flash("Número, monto y vencimiento son obligatorios", "error")
        else:
            ej("INSERT INTO facturas (cliente_id, numero, emision, vence, monto, moneda, detalle, creado) VALUES (?,?,?,?,?,?,?,?)", cid, f["numero"][:30],
               f.get("emision") or datetime.now().strftime("%Y-%m-%d"), f["vence"], monto, f.get("moneda") or "CLP", f.get("detalle", "")[:200], ahora())
            bitacora("factura nueva", c["codigo"], {"numero": f["numero"], "monto": monto})
            flash("Factura registrada", "ok")
    return redirect(url_for("cliente", cid=cid) + "#facturacion")


@app.route("/api/instancia/estado")
def api_estado():
    """Cada FaceOps pregunta aquí (con su token propio) si su acceso está habilitado. Responde solo lo que esa instancia necesita."""
    import hashlib
    tok = request.headers.get("X-FaceAdmin-Token", "")
    b = q1("SELECT * FROM bloqueos WHERE token=?", hashlib.sha256(tok.encode()).hexdigest()) if tok else None
    if not b:
        return {"error": "no autorizado"}, 401
    fa = facturacion(b["cliente_id"])
    aviso = ""
    if not fa["bloqueado"] and fa["vencidas"] and fa["posible"]:
        aviso = f"Hay facturas vencidas. El servicio podría suspenderse a partir del {fa['posible']}."
    ej("UPDATE bloqueos SET consulta=? WHERE cliente_id=?", ahora(), b["cliente_id"])
    return {"bloqueado": fa["bloqueado"], "motivo": fa["motivo"] if fa["bloqueado"] else "", "aviso": aviso, "posible_bloqueo": fa["posible"]}


@app.route("/clientes/nuevo", methods=["POST"])
@requiere_login
def cliente_nuevo():
    f = request.form
    cod = "".join(ch for ch in f.get("codigo", "").lower() if ch.isalnum() or ch == "-")[:20]
    if not cod or not f.get("nombre") or (f.get("url") and not f["url"].startswith("https://")):
        flash("Código y nombre son obligatorios (la URL, si la indicas, con https://)", "error")
    elif q1("SELECT id FROM clientes WHERE codigo=?", cod):
        flash("Ya existe un cliente con ese código", "error")
    else:
        cid = ej("INSERT INTO clientes (codigo, nombre, url, maquina, tamano, rol, creado, instancia_id) VALUES (?,?,?,?,?,?,?,?)", cod, f["nombre"].strip()[:80],
                 (f.get("url") or "").strip().rstrip("/"), f.get("maquina", "")[:80], f.get("tamano", "")[:40], "cliente", ahora(), nuevo_id(cod))
        bitacora("cliente nuevo", cod, {"url": f.get("url")})
        flash(f"Cliente {f['nombre']} agregado: descarga su paquete de instalación", "ok")
        return redirect(url_for("cliente", cid=cid) + "#instalacion")
    return redirect(url_for("panel"))


@app.route("/clientes/<int:cid>/instalador/<so>", methods=["POST"])
@requiere_login
def instalador(cid, so):
    """Paquete de instalación de FaceOps para el cliente, armado con la última versión del repositorio central. Lleva su
    identidad única (instancia_id) y una llave nueva: al descargarlo, la llave anterior deja de valer."""
    import hashlib
    from flask import Response
    c = q1("SELECT * FROM clientes WHERE id=?", cid) or abort(404)
    if so not in ("linux", "windows"):
        abort(404)
    cen = central()
    if not cen.get("commit"):
        flash("GitHub no respondió: no se puede fijar la última versión. Prueba de nuevo.", "error")
        return redirect(url_for("cliente", cid=cid) + "#instalacion")
    tok = secrets.token_hex(24)
    ej("INSERT OR IGNORE INTO bloqueos (cliente_id) VALUES (?)", cid)
    ej("UPDATE bloqueos SET token=? WHERE cliente_id=?", hashlib.sha256(tok.encode()).hexdigest(), cid)
    plantilla = "instalar-linux.sh" if so == "linux" else "instalar-windows.ps1"
    txt = render_template(f"paquetes/{plantilla}", c=c, tok=tok, commit=cen["commit"], repo=f"https://github.com/{REPO}.git",
                          admin=request.host_url.rstrip("/"), generado=ahora())
    huella = hashlib.sha256(txt.encode()).hexdigest()
    ej("INSERT INTO paquetes (cliente_id, so, commit_, generado, por, huella) VALUES (?,?,?,?,?,?)", cid, so, cen["commit"], ahora(), g.usuario["usuario"], huella)
    bitacora("paquete de instalación", c["codigo"], {"so": so, "commit": cen["commit"][:12], "huella": huella[:16]})
    nombre = f"faceops-{c['codigo']}-{cen['commit'][:7]}.{'sh' if so == 'linux' else 'ps1'}"
    return Response(txt, mimetype="text/plain", headers={"Content-Disposition": f'attachment; filename="{nombre}"', "Cache-Control": "no-store"})


@app.route("/api/instancia/registro", methods=["POST"])
def api_registro():
    """El instalador avisa que terminó: URL, máquina y versión instalada. Solo con la llave del paquete."""
    import hashlib
    tok = request.headers.get("X-FaceAdmin-Token", "")
    b = q1("SELECT * FROM bloqueos WHERE token=?", hashlib.sha256(tok.encode()).hexdigest()) if tok else None
    if not b:
        return {"error": "no autorizado"}, 401
    d = request.get_json(silent=True) or {}
    c = q1("SELECT * FROM clientes WHERE id=?", b["cliente_id"])
    if d.get("instancia_id") != c["instancia_id"]:
        return {"error": "el paquete no corresponde a este cliente"}, 409
    if str(d.get("url", "")).startswith("https://"):
        ej("UPDATE clientes SET url=? WHERE id=?", d["url"].rstrip("/")[:200], c["id"])
    ej("UPDATE paquetes SET instalado=?, host=? WHERE id=(SELECT MAX(id) FROM paquetes WHERE cliente_id=?)", ahora(), str(d.get("host", ""))[:80], c["id"])
    bitacora("instalación registrada (instalador)", c["codigo"], {"host": d.get("host"), "url": d.get("url"), "commit": str(d.get("commit", ""))[:12]})
    return {"ok": True, "cliente": c["codigo"]}


@app.route("/arquitectura")
@requiere_login
def arquitectura():
    cl = estados(q("SELECT * FROM clientes WHERE activo=1 ORDER BY rol='laboratorio' DESC, nombre"))
    return render_template("arquitectura.html", clientes=cl, central=central(), repo=REPO)


@app.route("/bitacora")
@requiere_login
def ver_bitacora():
    return render_template("bitacora.html", filas=q("SELECT * FROM bitacora ORDER BY id DESC LIMIT 300"))


@app.template_filter("num")
def _num(v):
    return f"{(v or 0):,.0f}".replace(",", ".")
