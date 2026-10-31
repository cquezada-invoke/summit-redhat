"""Relación de confianza con FaceOps Admin (validador en la nube).

Ninguna automatización llega a AAP sin una autorización vigente del validador:
  · esta instalación firma cada pedido con su llave privada Ed25519 (registrada una sola vez con un código de enrolamiento)
  · el validador responde un ID único, una huella HMAC (clave que rota cada hora) y un token firmado con su llave del momento
  · FaceOps verifica la firma del token con las llaves públicas del validador y lo pasa a AAP como «faceops_autorizacion»
Si el validador no responde (sin internet, dominio caído, firma inválida), la automatización NO se lanza."""
import base64
import contextvars
import hashlib
import json
import os
import secrets
import time

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .aap import ErrorAAP
from .bd import ahora, ej, j, q1

URL = os.environ.get("FACEOPS_VALIDADOR_URL", "https://validador.34.176.42.56.sslip.io").rstrip("/")
DIR = os.environ.get("FACEOPS_CONFIANZA_DIR", "/var/lib/faceops/confianza")
TIEMPO = 8
_llaves = {"t": 0, "d": {}}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS validaciones (
  id TEXT PRIMARY KEY, ticket_id INTEGER, huella TEXT, kid TEXT, huella_parametros TEXT, automatizacion TEXT, plantilla TEXT,
  origen TEXT, emitida TEXT, expira INTEGER, resultado TEXT DEFAULT '', reportado TEXT);
CREATE TABLE IF NOT EXISTS validaciones_rechazos (ts TEXT, automatizacion TEXT, ticket_id INTEGER, motivo TEXT);
"""


CONTEXTO = contextvars.ContextVar("faceops_contexto_validacion", default=None)   # ticket y solicitante en curso


class SinValidacion(ErrorAAP):
    """El validador no autorizó (o no se pudo contactar): no se lanza nada."""


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _ub64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def identidad():
    p = os.path.join(DIR, "instancia.json")
    return json.load(open(p)) if os.path.exists(p) else None


def registrar(codigo, nombre):
    """Enrolamiento único: genera la llave de esta instalación (la privada no sale de aquí) y la registra en la nube."""
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    r = requests.post(f"{URL}/v1/instancias/registrar", json={"codigo": codigo, "nombre": nombre, "llave_publica": _b64u(pub)}, timeout=TIEMPO)
    r.raise_for_status()
    d = r.json()
    fd = os.open(os.path.join(DIR, "instancia.key"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
    os.close(fd)
    json.dump({"instancia": d["instancia"], "organizacion": d["organizacion"], "nombre": nombre, "validador": URL, "registrada": ahora()},
              open(os.path.join(DIR, "instancia.json"), "w"))
    return d["instancia"]


def _firmado(ruta, cuerpo):
    ide = identidad()
    if not ide:
        raise SinValidacion("Esta instalación de FaceOps no está registrada en FaceOps Admin")
    priv = Ed25519PrivateKey.from_private_bytes(open(os.path.join(DIR, "instancia.key"), "rb").read())
    b = _canon(dict(cuerpo, ts=time.time(), nonce=secrets.token_hex(16)))
    try:
        r = requests.post(URL + ruta, data=b, timeout=TIEMPO,
                          headers={"Content-Type": "application/json", "X-FaceOps-Instancia": ide["instancia"], "X-FaceOps-Firma": _b64u(priv.sign(b))})
    except requests.RequestException as e:
        raise SinValidacion(f"No hay conexión con FaceOps Admin ({URL}): {str(e).split(':')[0][:120]}") from e
    if r.status_code != 200:
        raise SinValidacion(f"FaceOps Admin rechazó el pedido ({r.status_code}): {r.text[:200]}")
    return r.json()


def _llave_publica(kid):
    if time.time() - _llaves["t"] > 600 or kid not in _llaves["d"]:
        try:
            r = requests.get(f"{URL}/v1/llaves", timeout=TIEMPO)
            r.raise_for_status()
        except requests.RequestException as e:
            raise SinValidacion(f"No se pudieron leer las llaves de FaceOps Admin: {str(e).split(':')[0][:120]}") from e
        _llaves.update(t=time.time(), d={x["kid"]: x["x"] for x in r.json()["llaves"]})
    if kid not in _llaves["d"]:
        raise SinValidacion(f"Llave {kid} desconocida")
    return Ed25519PublicKey.from_public_bytes(_ub64(_llaves["d"][kid]))


def verificar_token(token, huella_parametros):
    """Firma de la nube, vigencia y que el token corresponda exactamente a estos parámetros."""
    try:
        cab, cuerpo, firma = token.split(".")
        h = json.loads(_ub64(cab))
        _llave_publica(h["kid"]).verify(_ub64(firma), f"{cab}.{cuerpo}".encode())
        d = json.loads(_ub64(cuerpo))
    except (ValueError, KeyError, InvalidSignature) as e:
        raise SinValidacion(f"Token de autorización inválido ({type(e).__name__})") from e
    if d["exp"] < time.time():
        raise SinValidacion("Token de autorización vencido")
    if d["huella_parametros"] != huella_parametros:
        raise SinValidacion("El token no corresponde a estos parámetros")
    return d


def huella_parametros(plantilla, extra_vars):
    """Hash de lo que se va a ejecutar (sin secretos ni la propia autorización)."""
    limpio = {k: v for k, v in (extra_vars or {}).items() if k != "faceops_autorizacion"}
    return hashlib.sha256(_canon({"plantilla": str(plantilla), "extra_vars": limpio})).hexdigest()


def autorizar(cliente, automatizacion, plantilla, extra_vars, ticket=None, usuario="", origen="faceops"):
    """Pide la autorización a la nube; devuelve el token para AAP. Si falla, lanza SinValidacion (nada se ejecuta)."""
    hp = huella_parametros(plantilla, extra_vars)
    try:
        r = _firmado("/v1/autorizar", {"cliente": cliente["nombre"], "automatizacion": automatizacion, "plantilla": str(plantilla),
                                       "ticket": (ticket or {}).get("numero", ""), "usuario": usuario, "origen": origen, "huella_parametros": hp})
        d = verificar_token(r["token"], hp)
        if d["huella"] != r["huella"] or d["id"] != r["id"]:
            raise SinValidacion("La respuesta del validador no coincide con su token")
    except SinValidacion as e:
        ej("INSERT INTO validaciones_rechazos VALUES (?,?,?,?)", ahora(), automatizacion, (ticket or {}).get("id"), str(e)[:300])
        raise
    ej("INSERT INTO validaciones(id, ticket_id, huella, kid, huella_parametros, automatizacion, plantilla, origen, emitida, expira) "
       "VALUES (?,?,?,?,?,?,?,?,?,?)", r["id"], (ticket or {}).get("id"), r["huella"], r["kid"], hp, automatizacion, str(plantilla),
       origen, ahora(), r["expira"])
    return r["token"], r


def reportar(ticket_id, estado, duracion_s=None):
    """Resultado de la ejecución a la nube (control central por cliente). No bloquea: si falla, queda pendiente."""
    v = q1("SELECT * FROM validaciones WHERE ticket_id=? ORDER BY emitida DESC LIMIT 1", ticket_id)
    if not v:
        return
    try:
        _firmado("/v1/resultado", {"id": v["id"], "estado": estado, "duracion_s": duracion_s})
        ej("UPDATE validaciones SET resultado=?, reportado=? WHERE id=?", estado, ahora(), v["id"])
    except SinValidacion:
        ej("UPDATE validaciones SET resultado=? WHERE id=?", estado, v["id"])


def de_ticket(ticket_id):
    return q1("SELECT * FROM validaciones WHERE ticket_id=? ORDER BY emitida DESC LIMIT 1", ticket_id)


def estado():
    ide = identidad()
    try:
        r = requests.get(f"{URL}/v1/salud", timeout=TIEMPO)
        nube = r.json() if r.status_code == 200 else None
    except requests.RequestException:
        nube = None
    ult = q1("SELECT * FROM validaciones ORDER BY emitida DESC LIMIT 1")
    rch = q1("SELECT * FROM validaciones_rechazos ORDER BY ts DESC LIMIT 1")
    return {"url": URL, "registrada": bool(ide), "instancia": (ide or {}).get("instancia"), "organizacion": (ide or {}).get("organizacion"),
            "conectada": bool(nube), "kid": (nube or {}).get("kid"), "ultima": ult, "ultimo_rechazo": rch}
