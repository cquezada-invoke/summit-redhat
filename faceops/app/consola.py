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
    """'ssh' | 'rdp' | None según el equipo (solo servidores con IP). Con bóveda, manda lo que diga su credencial."""
    if not e.get("ip"):
        return None
    if e.get("energia") == "apagado":   # no se ofrece entrar a un equipo que no responde
        return None
    from .boveda import acceso, disponible as bov_disponible
    cred = acceso(e["nombre"])
    metodo = (cred or {}).get("metodo") or (cred or {}).get("protocolo")
    if cred and metodo in ("ssh", "rdp"):   # cualquier equipo con su acceso en la bóveda (router, firewall, estación…)
        return metodo
    if bov_disponible():   # con bóveda conectada, solo se entra a las máquinas vinculadas (credencial propia en la bóveda)
        return None
    if (e.get("categoria") or "servidor") != "servidor":
        return None
    if sistema == "windows":
        return "rdp"
    if "kvm" in (e.get("skills") or []):  # el anfitrión de la plataforma no se abre con la llave de los nodos
        return None
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


def url_consola(usuario, e, tipo, comando=None):
    """La credencial sale de la bóveda (Vault) si la tiene; si no, de la llave local de FaceOps (o la escribe la persona en RDP)."""
    from .boveda import acceso
    cred = acceso(e["nombre"]) or {}
    if tipo == "ssh":
        param = {"hostname": e["ip"], "port": "22", "username": cred.get("usuario") or "ansible", "color-scheme": "gray-black", "font-size": "12"}
        if cred.get("clave") and not cred.get("llave_privada"):   # equipos de red: usuario y clave
            param["password"] = cred["clave"]
        else:
            param["private-key"] = cred.get("llave_privada") or open(LLAVE_SSH).read()
        if comando:   # p. ej. abrir un archivo de log; al salir queda la consola
            param["command"] = comando
    else:
        param = {"hostname": e["ip"], "port": "3389", "security": "any", "ignore-cert": "true",
                 "resize-method": "display-update", "enable-wallpaper": "false"}
        if cred.get("usuario"):
            param.update({"username": cred["usuario"], "password": cred.get("clave", "")})
    nombre = f"{e['nombre']} ({'consola' if tipo == 'ssh' else 'escritorio remoto'}{' · log' if comando else ''})"
    # Usuario único por apertura: Guacamole no reutiliza la sesión anterior (que traería otra conexión)
    datos = {"username": f"faceops:{usuario['usuario']}:{e['nombre']}:{secrets.token_hex(3)}",
             "expires": int((time.time() + 300) * 1000),
             "connections": {nombre: {"protocol": tipo, "parameters": param}}}
    cliente = base64.b64encode(f"{nombre}\0c\0json".encode()).decode()
    return "/consola/?data=" + urllib.parse.quote(_cifrar(datos), safe="") + "#/client/" + urllib.parse.quote(cliente, safe="")


# consola web de cada equipo según el producto que corre (la abre FaceOps, porque las IP viven en la red interna)
WEB = [  # (skill o pista, esquema, puerto, ruta, etiqueta)
    ("fortigate", "https", 443, "/", "FortiGate"), ("fortios", "https", 443, "/", "FortiGate"),
    ("jboss", "http", 8080, "/", "JBoss EAP"),   # primero el rol principal: una máquina JBoss con Keycloak declarado abre JBoss
    ("keycloak", "http", 8080, "/admin/", "Keycloak"), ("midpoint", "http", 8080, "/midpoint/", "midPoint"), ("nginx", "http", 80, "/", "Nginx"), ("iis", "http", 80, "/", "IIS"),
    ("vault", "http", 8200, "/ui/", "Vault"), ("redis", "redis", 6379, "/", "Redis"), ("gitea", "http", 3000, "/", "Gitea"),
    ("red-hat-idm", "https", 443, "/ipa/ui/", "Red Hat IdM"), ("389-directory-server", "https", 9090, "/", "Cockpit"),
]


def web_de(e):
    """{'url', 'producto'} de la consola web del equipo, o None si no tiene una."""
    d = e.get("datos") or {}
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = {}
    ocp = d.get("openshift")
    if ocp and ocp.get("consola"):
        return {"url": ocp["consola"], "producto": f"Consola de OpenShift {ocp.get('version', '')}".strip()}
    ip = e.get("ip")
    if not ip:
        return None
    pistas = [s.lower() for s in e.get("skills", [])] + [(e.get("so") or "").lower().split(" ")[0]]
    sv_ = e.get("servicios") or []
    sv_ = " ".join(json.loads(sv_) if isinstance(sv_, str) and sv_.startswith("[") else ([sv_] if isinstance(sv_, str) else sv_)).lower()
    for clave, esq, puerto, ruta, prod in WEB:
        if clave == "keycloak" and "keycloak" not in sv_ and "rhbk" not in sv_:
            continue   # Keycloak declarado pero sin correr en la máquina: no tiene esa consola
        if clave in pistas:
            defecto = (esq == "http" and puerto == 80) or (esq == "https" and puerto == 443)
            return {"url": f"{esq}://{ip}{'' if defecto else ':' + str(puerto)}{ruta}", "producto": prod}
    return None
