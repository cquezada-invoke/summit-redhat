"""Consola web de los equipos (SSH para Linux, escritorio remoto para Windows) con Apache Guacamole.
FaceOps arma una conexión de un solo uso (vence en 5 minutos), la firma (HMAC-SHA256) y la cifra (AES-128-CBC) con la
clave compartida de la extensión «auth-json» de Guacamole. El navegador nunca ve la llave SSH."""
import base64
import hashlib
import secrets
import hmac
import json
import os
import time
import urllib.parse

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

LLAVE_SSH = os.environ.get("FACEOPS_LLAVE_SSH", "/var/lib/faceops/llaves/id_demo")


def disponible():
    return bool(os.environ.get("FACEOPS_GUAC_CLAVE"))


def tipo_consola(e, sistema):
    """'ssh' | 'rdp' | None según el equipo (solo servidores con IP)."""
    if not e.get("ip") or (e.get("categoria") or "servidor") != "servidor":
        return None
    if sistema == "windows":
        return "rdp"
    if sistema == "linux" and os.path.exists(LLAVE_SSH) and not e.get("ip", "").startswith("34."):
        return "ssh"
    return None


def _cifrar(datos):
    clave = bytes.fromhex(os.environ["FACEOPS_GUAC_CLAVE"])
    js = json.dumps(datos).encode()
    firmado = hmac.new(clave, js, hashlib.sha256).digest() + js
    rel = padding.PKCS7(128).padder()
    plano = rel.update(firmado) + rel.finalize()
    c = Cipher(algorithms.AES(clave), modes.CBC(b"\0" * 16)).encryptor()
    return base64.b64encode(c.update(plano) + c.finalize()).decode()


def url_consola(usuario, e, tipo):
    if tipo == "ssh":
        param = {"hostname": e["ip"], "port": "22", "username": "ansible", "private-key": open(LLAVE_SSH).read(),
                 "color-scheme": "gray-black", "font-size": "12"}
    else:
        param = {"hostname": e["ip"], "port": "3389", "security": "any", "ignore-cert": "true",
                 "resize-method": "display-update", "enable-wallpaper": "false"}
    nombre = f"{e['nombre']} ({'consola' if tipo == 'ssh' else 'escritorio remoto'})"
    # Usuario único por apertura: Guacamole no reutiliza la sesión anterior (que traería otra conexión)
    datos = {"username": f"faceops:{usuario['usuario']}:{e['nombre']}:{secrets.token_hex(3)}",
             "expires": int((time.time() + 300) * 1000),
             "connections": {nombre: {"protocol": tipo, "parameters": param}}}
    cliente = base64.b64encode(f"{nombre}\0c\0json".encode()).decode()
    return "/consola/?data=" + urllib.parse.quote(_cifrar(datos), safe="") + "#/client/" + urllib.parse.quote(cliente, safe="")


# consola web de cada equipo según el producto que corre (la abre FaceOps, porque las IP viven en la red interna)
WEB = [  # (skill o pista, esquema, puerto, ruta, etiqueta)
    ("fortigate", "https", 443, "/", "FortiGate"), ("fortios", "https", 443, "/", "FortiGate"),
    ("keycloak", "http", 8080, "/auth/admin/", "Keycloak"), ("midpoint", "http", 8080, "/midpoint/", "midPoint"),
    ("jboss", "http", 8080, "/", "JBoss EAP"), ("nginx", "http", 80, "/", "Nginx"), ("iis", "http", 80, "/", "IIS"),
    ("red-hat-idm", "https", 443, "/ipa/ui/", "Red Hat IdM"), ("389-directory-server", "https", 9090, "/", "Cockpit"),
]


def web_de(e):
    """{'url', 'producto'} de la consola web del equipo, o None si no tiene una."""
    ip = e.get("ip")
    if not ip:
        return None
    pistas = [s.lower() for s in e.get("skills", [])] + [(e.get("so") or "").lower().split(" ")[0]]
    for clave, esq, puerto, ruta, prod in WEB:
        if clave in pistas:
            defecto = (esq == "http" and puerto == 80) or (esq == "https" and puerto == 443)
            return {"url": f"{esq}://{ip}{'' if defecto else ':' + str(puerto)}{ruta}", "producto": prod}
    return None
