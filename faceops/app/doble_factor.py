"""Doble factor (TOTP, RFC 6238: Google Authenticator, Microsoft Authenticator, 1Password…) y freno de fuerza bruta.
El acceso público (por el proxy TLS) exige doble factor; desde la red interna es opcional pero se recomienda."""
import base64
import hashlib
import hmac
import os
import struct
import time
import urllib.parse

from .bd import ahora, ej, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ingresos_fallidos (id INTEGER PRIMARY KEY, ip TEXT, usuario TEXT, cuando REAL);
CREATE INDEX IF NOT EXISTS ix_ingresos_fallidos ON ingresos_fallidos(cuando);
"""
MAX_IP, MAX_USUARIO, VENTANA = 10, 5, 15 * 60


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)


def nuevo_secreto():
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _codigo(secreto, paso):
    k = base64.b32decode(secreto + "=" * (-len(secreto) % 8))
    h = hmac.new(k, struct.pack(">Q", paso), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000)


def verificar(secreto, codigo):
    codigo = "".join(c for c in (codigo or "") if c.isdigit())
    if not secreto or len(codigo) != 6:
        return False
    paso = int(time.time()) // 30
    return any(hmac.compare_digest(_codigo(secreto, paso + d), codigo) for d in (-1, 0, 1))


def uri(secreto, usuario):
    return "otpauth://totp/" + urllib.parse.quote(f"FaceOps:{usuario}") + "?" + urllib.parse.urlencode(
        {"secret": secreto, "issuer": "FaceOps · Invoke", "digits": 6, "period": 30})


def es_publico(req):
    """El proxy público marca sus peticiones (y pisa lo que mande el cliente con ese encabezado)."""
    return req.headers.get("X-FaceOps-Acceso") == "publico"


def ip_cliente(req):
    return (req.headers.get("X-Real-IP") or req.remote_addr or "")[:64]


def bloqueado(ip, usuario):
    desde = time.time() - VENTANA
    n_ip = q1("SELECT COUNT(*) n FROM ingresos_fallidos WHERE ip=? AND cuando>?", ip, desde)["n"]
    n_us = q1("SELECT COUNT(*) n FROM ingresos_fallidos WHERE usuario=? AND cuando>?", usuario, desde)["n"] if usuario else 0
    return n_ip >= MAX_IP or n_us >= MAX_USUARIO


def fallo(ip, usuario):
    ej("INSERT INTO ingresos_fallidos(ip, usuario, cuando) VALUES (?,?,?)", ip, (usuario or "")[:60], time.time())
    ej("DELETE FROM ingresos_fallidos WHERE cuando<?", time.time() - 86400)


def limpiar(usuario):
    ej("DELETE FROM ingresos_fallidos WHERE usuario=?", usuario)
