"""Bóveda de contraseñas (HashiCorp Vault en nodo-vault): FaceOps entra con su AppRole de solo lectura y toma de ahí la
credencial de acceso de cada máquina (SSH, escritorio remoto) y la de su consola web. Sin la bóveda, sigue con lo local."""
import os
import time

import requests

_tok = {"t": None, "vence": 0}
_caida = {"hasta": 0}     # cortacircuito: si la bóveda no responde, no se reintenta en 30 s (las páginas no esperan)
_cache = {}               # ruta → (t, dict|None): 60 s, para no ir a la bóveda en cada carga de página
TTL = 60
TIMEOUT = (1, 2)


def _no_responde():
    _caida["hasta"] = time.time() + 30


def responde():
    return time.time() >= _caida["hasta"]


def disponible():
    return bool(os.environ.get("FACEOPS_VAULT_ADDR") and os.environ.get("FACEOPS_VAULT_SECRET_ID"))


def _token():
    if _tok["t"] and _tok["vence"] > time.time() + 60:
        return _tok["t"]
    if not responde():
        raise requests.ConnectionError("bóveda sin respuesta (reintento en 30 s)")
    r = requests.post(os.environ["FACEOPS_VAULT_ADDR"] + "/v1/auth/approle/login", timeout=TIMEOUT,
                      json={"role_id": os.environ.get("FACEOPS_VAULT_ROLE_ID"), "secret_id": os.environ.get("FACEOPS_VAULT_SECRET_ID")})
    r.raise_for_status()
    a = r.json()["auth"]
    _tok.update(t=a["client_token"], vence=time.time() + a["lease_duration"])
    return _tok["t"]


def leer(ruta):
    """faceops/<ruta> → dict, o None si no está o la bóveda no responde."""
    if not disponible():
        return None
    c = _cache.get(ruta)
    if c and time.time() - c[0] < TTL:
        return c[1]
    if not responde():
        return c[1] if c else None
    try:
        r = requests.get(f"{os.environ['FACEOPS_VAULT_ADDR']}/v1/faceops/data/{ruta}", headers={"X-Vault-Token": _token()}, timeout=TIMEOUT)
        v = r.json()["data"]["data"] if r.ok else None
    except (requests.ConnectionError, requests.Timeout):
        _no_responde()
        return c[1] if c else None
    except (requests.RequestException, KeyError, ValueError):
        v = None
    _cache[ruta] = (time.time(), v)
    return v


def acceso(nombre):
    return leer(f"maquinas/{nombre}")


def web(nombre, plataforma=None):
    """Credencial web de la máquina. Con plataforma: la propia de esa plataforma (web/<máquina>/<plataforma>) o, si no hay,
    la general de la máquina solo cuando corresponde a esa plataforma (una máquina puede tener JBoss y Redis a la vez)."""
    if plataforma:
        d = leer(f"web/{nombre}/{plataforma}")
        if d:
            return d
    d = leer(f"web/{nombre}")
    if plataforma and d and ((d.get("sitio") or "").lower() == "redis") != (plataforma == "redis"):
        return None
    return d


def ubicacion():
    a = os.environ.get("FACEOPS_VAULT_ADDR", "")
    return a.split("//")[-1].split(":")[0] if a else ""


_lista = {"t": 0, "v": None}


def maquinas_con_credencial(nombres):
    """Nombres (de la lista) con credencial de acceso en la bóveda. Cache de 5 minutos."""
    if not disponible():
        return set()
    if _lista["v"] is not None and time.time() - _lista["t"] < 300:
        return _lista["v"] & set(nombres)
    v = None
    if not responde():
        return (_lista["v"] or set()) & set(nombres)
    try:
        r = requests.request("LIST", f"{os.environ['FACEOPS_VAULT_ADDR']}/v1/faceops/metadata/maquinas", headers={"X-Vault-Token": _token()}, timeout=TIMEOUT)
        if r.ok:
            v = set(r.json()["data"]["keys"])
    except (requests.ConnectionError, requests.Timeout):
        _no_responde()
        return (_lista["v"] or set()) & set(nombres)
    except (requests.RequestException, KeyError, ValueError):
        v = None
    if v is None:  # el AppRole puede no tener permiso de listar: se lee una por una
        v = {n for n in nombres if acceso(n)}
    _lista.update(t=time.time(), v=v)
    return v & set(nombres)


def escribir(ruta, datos):
    """Guarda datos en faceops/<ruta> (KV v2). Devuelve True si la bóveda lo aceptó (el rol de FaceOps puede no tener escritura)."""
    _cache.pop(ruta, None)
    if not disponible():
        return False
    try:
        r = requests.post(f"{os.environ['FACEOPS_VAULT_ADDR']}/v1/faceops/data/{ruta}", headers={"X-Vault-Token": _token()},
                          json={"data": datos}, timeout=5)
        return r.ok
    except requests.RequestException:
        return False
