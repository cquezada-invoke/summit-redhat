"""FaceOps Admin · Validador (servicio en la nube).

Toda automatización de toda instalación de FaceOps pasa por aquí antes de llegar a Ansible Automation Platform:
  1. Relación de confianza: cada instalación se registra una vez con un código de enrolamiento de un solo uso y deja
     su llave pública (Ed25519). Cada pedido viene firmado con su llave privada, con hora y nonce (no se puede repetir).
  2. Autorización: se emite un ID único de ejecución, una huella HMAC-SHA256 calculada con una clave que cambia cada
     hora (derivada del secreto maestro, que nunca sale de este servicio) y un token firmado con una llave Ed25519 que
     también rota cada hora. Las llaves públicas vigentes se publican en /v1/llaves.
  3. Control central: cada ejecución queda registrada por instancia y cliente, con su resultado; cualquiera puede
     comparar un ID con su huella en /v1/verificar.
Persistencia: SQLite en /data. Secreto maestro: /data/maestro (en la nube real: un KMS)."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import Flask, abort, jsonify, request

DATOS = os.environ.get("VALIDADOR_DATOS", "/data")
VENTANA = int(os.environ.get("VALIDADOR_VENTANA_S", "3600"))      # rotación de claves: cada hora
VIGENCIA_TOKEN = int(os.environ.get("VALIDADOR_TOKEN_S", "600"))  # el token sirve 10 minutos para lanzar
DESFASE = 120                                                     # tolerancia de reloj del pedido
app = Flask(__name__)


# ------------------------------------------------------------------ claves
def maestro():
    p = os.path.join(DATOS, "maestro")
    if not os.path.exists(p):
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, secrets.token_bytes(64))
        os.close(fd)
    return open(p, "rb").read()


def derivar(etiqueta, n=32):
    return HKDF(algorithm=SHA256(), length=n, salt=b"faceops-admin", info=etiqueta.encode()).derive(maestro())


def ventana(t=None):
    return int((t or time.time()) // VENTANA)


def kid(v):
    return datetime.fromtimestamp(v * VENTANA, timezone.utc).strftime("%Y%m%d%H")


def llave_firma(v):
    return Ed25519PrivateKey.from_private_bytes(derivar(f"firma:{v}"))


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def ub64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# ------------------------------------------------------------------ datos
def bd():
    c = sqlite3.connect(os.path.join(DATOS, "validador.db"))
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS codigos (codigo_hash TEXT PRIMARY KEY, organizacion TEXT, creado TEXT, usado TEXT);
    CREATE TABLE IF NOT EXISTS instancias (id TEXT PRIMARY KEY, nombre TEXT, organizacion TEXT, llave TEXT, estado TEXT,
      creada TEXT, ultimo_contacto TEXT);
    CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, ts INTEGER);
    CREATE TABLE IF NOT EXISTS ejecuciones (id TEXT PRIMARY KEY, instancia TEXT, cliente TEXT, ticket TEXT, automatizacion TEXT,
      plantilla TEXT, usuario TEXT, origen TEXT, huella_parametros TEXT, huella TEXT, kid TEXT, emitida TEXT, expira TEXT,
      estado TEXT DEFAULT 'autorizada', resultado_en TEXT, duracion_s INTEGER);
    CREATE TABLE IF NOT EXISTS rechazos (ts TEXT, instancia TEXT, motivo TEXT, ip TEXT);
    """)
    return c


def ulid():
    t = int(time.time() * 1000).to_bytes(6, "big")
    a = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    n = int.from_bytes(t + secrets.token_bytes(10), "big")
    return "".join(a[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def rechazar(c, instancia, motivo, codigo=403):
    c.execute("INSERT INTO rechazos VALUES (?,?,?,?)", (datetime.now(timezone.utc).isoformat(), instancia, motivo,
                                                         request.headers.get("X-Real-IP", request.remote_addr)))
    c.commit()
    abort(codigo, motivo)


def pedido_firmado(c):
    """Cuerpo JSON firmado por una instancia registrada (cabeceras X-FaceOps-Instancia y X-FaceOps-Firma)."""
    inst_id = request.headers.get("X-FaceOps-Instancia", "")
    firma = request.headers.get("X-FaceOps-Firma", "")
    cuerpo = request.get_data()
    inst = c.execute("SELECT * FROM instancias WHERE id=?", (inst_id,)).fetchone()
    if not inst or inst["estado"] != "activa":
        rechazar(c, inst_id, "instancia no registrada o suspendida")
    try:
        Ed25519PublicKey.from_public_bytes(ub64(inst["llave"])).verify(ub64(firma), cuerpo)
    except (InvalidSignature, ValueError):
        rechazar(c, inst_id, "firma inválida")
    d = json.loads(cuerpo)
    if abs(time.time() - float(d.get("ts", 0))) > DESFASE:
        rechazar(c, inst_id, "pedido fuera de hora (reloj desfasado o pedido repetido)")
    n = str(d.get("nonce", ""))
    if len(n) < 16 or c.execute("SELECT 1 FROM nonces WHERE nonce=?", (n,)).fetchone():
        rechazar(c, inst_id, "nonce repetido")
    c.execute("INSERT INTO nonces VALUES (?,?)", (n, int(time.time())))
    c.execute("DELETE FROM nonces WHERE ts < ?", (int(time.time()) - 3600,))
    c.execute("UPDATE instancias SET ultimo_contacto=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), inst_id))
    return inst, d


# ------------------------------------------------------------------ API
@app.get("/v1/salud")
def salud():
    return {"ok": True, "kid": kid(ventana())}


@app.get("/v1/llaves")
def llaves():
    """Llaves públicas para verificar los tokens: la vigente, las 24 anteriores y la siguiente."""
    v = ventana()
    out = []
    for x in range(v - 24, v + 2):
        pub = llave_firma(x).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        out.append({"kid": kid(x), "alg": "EdDSA", "x": b64u(pub), "desde": x * VENTANA, "hasta": (x + 1) * VENTANA})
    return {"llaves": out, "vigente": kid(v)}


@app.post("/v1/instancias/registrar")
def registrar():
    c = bd()
    d = request.get_json(force=True)
    h = hashlib.sha256(str(d.get("codigo", "")).encode()).hexdigest()
    cod = c.execute("SELECT * FROM codigos WHERE codigo_hash=? AND usado IS NULL", (h,)).fetchone()
    if not cod:
        rechazar(c, "", "código de enrolamiento inválido o ya usado")
    try:
        Ed25519PublicKey.from_public_bytes(ub64(d["llave_publica"]))
    except (KeyError, ValueError):
        abort(400, "llave pública inválida")
    iid = "fo-" + secrets.token_hex(8)
    now = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO instancias VALUES (?,?,?,?,?,?,?)", (iid, d.get("nombre", ""), cod["organizacion"], d["llave_publica"], "activa", now, now))
    c.execute("UPDATE codigos SET usado=? WHERE codigo_hash=?", (now, h))
    c.commit()
    return {"instancia": iid, "organizacion": cod["organizacion"]}


@app.post("/v1/autorizar")
def autorizar():
    c = bd()
    inst, d = pedido_firmado(c)
    for k in ("cliente", "automatizacion", "huella_parametros"):
        if not d.get(k):
            rechazar(c, inst["id"], f"falta {k}", 400)
    v = ventana()
    eid = ulid()
    ahora_ = int(time.time())
    reclamo = {"id": eid, "instancia": inst["id"], "organizacion": inst["organizacion"], "cliente": d["cliente"],
               "ticket": d.get("ticket", ""), "automatizacion": d["automatizacion"], "plantilla": str(d.get("plantilla", "")),
               "usuario": d.get("usuario", ""), "origen": d.get("origen", "faceops"), "huella_parametros": d["huella_parametros"],
               "iat": ahora_, "exp": ahora_ + VIGENCIA_TOKEN, "kid": kid(v)}
    huella = hmac.new(derivar(f"huella:{v}"), canon(reclamo), hashlib.sha256).hexdigest()
    reclamo["huella"] = huella
    cab = b64u(canon({"alg": "EdDSA", "kid": kid(v), "typ": "faceops+autorizacion"}))
    cuerpo = b64u(canon(reclamo))
    token = f"{cab}.{cuerpo}." + b64u(llave_firma(v).sign(f"{cab}.{cuerpo}".encode()))
    c.execute("INSERT INTO ejecuciones(id, instancia, cliente, ticket, automatizacion, plantilla, usuario, origen, huella_parametros, huella, kid, emitida, expira) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (eid, inst["id"], d["cliente"], reclamo["ticket"], d["automatizacion"], reclamo["plantilla"], reclamo["usuario"], reclamo["origen"],
               d["huella_parametros"], huella, kid(v), datetime.fromtimestamp(ahora_, timezone.utc).isoformat(),
               datetime.fromtimestamp(reclamo["exp"], timezone.utc).isoformat()))
    c.commit()
    return {"id": eid, "huella": huella, "kid": kid(v), "token": token, "expira": reclamo["exp"]}


@app.post("/v1/resultado")
def resultado():
    c = bd()
    inst, d = pedido_firmado(c)
    c.execute("UPDATE ejecuciones SET estado=?, resultado_en=?, duracion_s=? WHERE id=? AND instancia=?",
              (str(d.get("estado", ""))[:30], datetime.now(timezone.utc).isoformat(), d.get("duracion_s"), d.get("id"), inst["id"]))
    c.commit()
    return {"ok": True}


@app.get("/v1/verificar/<eid>")
def verificar(eid):
    """Compara un ID con su huella (la que guardó la instalación o la que trae el token)."""
    c = bd()
    e = c.execute("SELECT * FROM ejecuciones WHERE id=?", (eid,)).fetchone()
    if not e:
        return {"valida": False, "motivo": "ID desconocido"}, 404
    ok = hmac.compare_digest(e["huella"], request.args.get("huella", ""))
    return {"valida": ok, "id": eid, "cliente": e["cliente"], "automatizacion": e["automatizacion"], "ticket": e["ticket"],
            "emitida": e["emitida"], "estado": e["estado"]}


@app.get("/admin")
def panel():
    """Control central: instancias, clientes y ejecuciones (requiere el token de administración)."""
    tok = open(os.path.join(DATOS, "admin-token")).read().strip()
    if not hmac.compare_digest(request.headers.get("Authorization", "").removeprefix("Bearer ").strip() or request.args.get("t", ""), tok):
        abort(401)
    c = bd()
    fila = lambda xs: "".join(f"<tr>{''.join(f'<td>{v if v is not None else chr(8212)}</td>' for v in x)}</tr>" for x in xs)
    inst = c.execute("SELECT id, nombre, organizacion, estado, creada, ultimo_contacto FROM instancias ORDER BY creada").fetchall()
    porc = c.execute("SELECT cliente, COUNT(*), SUM(estado='exitoso'), SUM(estado='fallido'), SUM(estado='autorizada'), MAX(emitida) "
                     "FROM ejecuciones GROUP BY cliente ORDER BY 2 DESC").fetchall()
    ult = c.execute("SELECT id, cliente, ticket, automatizacion, usuario, origen, kid, estado, emitida FROM ejecuciones ORDER BY emitida DESC LIMIT 50").fetchall()
    rch = c.execute("SELECT ts, instancia, motivo, ip FROM rechazos ORDER BY ts DESC LIMIT 20").fetchall()
    css = ("body{font:14px system-ui,Arial;margin:0;background:#f5f5f5;color:#151515}header{background:#151515;color:#fff;padding:14px 24px;"
           "border-bottom:4px solid #ee0000}main{padding:16px 24px}table{border-collapse:collapse;width:100%;background:#fff;margin:6px 0 18px;font-size:13px}"
           "td,th{border-bottom:1px solid #e5e5e5;padding:6px 8px;text-align:left}th{background:#fafafa}h2{font-size:17px;margin:14px 0 4px}")
    return (f"<!doctype html><meta charset=utf-8><title>FaceOps Admin · Validador</title><style>{css}</style>"
            f"<header><b>FaceOps Admin</b> · validador de confianza · llave vigente {kid(ventana())}</header><main>"
            f"<h2>Instancias de FaceOps</h2><table><tr><th>ID</th><th>Nombre</th><th>Organización</th><th>Estado</th><th>Registrada</th><th>Último contacto</th></tr>{fila(inst)}</table>"
            f"<h2>Ejecuciones por cliente</h2><table><tr><th>Cliente</th><th>Total</th><th>Exitosas</th><th>Fallidas</th><th>En curso</th><th>Última</th></tr>{fila(porc)}</table>"
            f"<h2>Últimas ejecuciones</h2><table><tr><th>ID</th><th>Cliente</th><th>Ticket</th><th>Automatización</th><th>Usuario</th><th>Origen</th><th>Llave</th><th>Estado</th><th>Emitida</th></tr>{fila(ult)}</table>"
            f"<h2>Pedidos rechazados</h2><table><tr><th>Cuándo</th><th>Instancia</th><th>Motivo</th><th>IP</th></tr>{fila(rch)}</table></main>")


# ------------------------------------------------------------------ administración (dentro del contenedor)
def cli():
    c = bd()
    if sys.argv[1] == "codigo":   # código de enrolamiento de un solo uso; se escribe en un archivo, no en pantalla
        cod = secrets.token_urlsafe(24)
        c.execute("INSERT INTO codigos VALUES (?,?,?,NULL)", (hashlib.sha256(cod.encode()).hexdigest(), sys.argv[2],
                                                            datetime.now(timezone.utc).isoformat()))
        c.commit()
        fd = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, cod.encode())
        os.close(fd)
        print("código creado")
    elif sys.argv[1] == "admin-token":
        p = os.path.join(DATOS, "admin-token")
        if not os.path.exists(p):
            fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(fd, secrets.token_urlsafe(32).encode())
            os.close(fd)
        print("token de administración listo")


if __name__ == "__main__":
    maestro()
    cli()
