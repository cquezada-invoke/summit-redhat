"""Bóveda de contraseñas (HashiCorp Vault en nodo-vault): FaceOps entra con su AppRole de solo lectura y toma de ahí la
credencial de acceso de cada máquina (SSH, escritorio remoto) y la de su consola web. Sin la bóveda, sigue con lo local."""
import os
import time

import requests

_tok = {"t": None, "vence": 0}


def disponible():
    return bool(os.environ.get("FACEOPS_VAULT_ADDR") and os.environ.get("FACEOPS_VAULT_SECRET_ID"))


def _token():
    if _tok["t"] and _tok["vence"] > time.time() + 60:
        return _tok["t"]
    r = requests.post(os.environ["FACEOPS_VAULT_ADDR"] + "/v1/auth/approle/login", timeout=5,
                      json={"role_id": os.environ.get("FACEOPS_VAULT_ROLE_ID"), "secret_id": os.environ.get("FACEOPS_VAULT_SECRET_ID")})
    r.raise_for_status()
    a = r.json()["auth"]
    _tok.update(t=a["client_token"], vence=time.time() + a["lease_duration"])
    return _tok["t"]


def leer(ruta):
    """faceops/<ruta> → dict, o None si no está o la bóveda no responde."""
    if not disponible():
        return None
    try:
        r = requests.get(f"{os.environ['FACEOPS_VAULT_ADDR']}/v1/faceops/data/{ruta}", headers={"X-Vault-Token": _token()}, timeout=5)
        return r.json()["data"]["data"] if r.ok else None
    except (requests.RequestException, KeyError, ValueError):
        return None


def acceso(nombre):
    return leer(f"maquinas/{nombre}")


def web(nombre):
    return leer(f"web/{nombre}")


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
    try:
        r = requests.request("LIST", f"{os.environ['FACEOPS_VAULT_ADDR']}/v1/faceops/metadata/maquinas", headers={"X-Vault-Token": _token()}, timeout=5)
        if r.ok:
            v = set(r.json()["data"]["keys"])
    except (requests.RequestException, KeyError, ValueError):
        v = None
    if v is None:  # el AppRole puede no tener permiso de listar: se lee una por una
        v = {n for n in nombres if acceso(n)}
    _lista.update(t=time.time(), v=v)
    return v & set(nombres)
