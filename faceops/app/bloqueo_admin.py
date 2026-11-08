"""Bloqueo de acceso decidido en FaceAdmin (consola global de Invoke), p. ej. por facturas impagas. Cada instancia pregunta a
FaceAdmin con su token propio (FACEOPS_ADMIN_URL, FACEOPS_ADMIN_TOKEN). Si FaceAdmin no responde, rige lo último que se supo."""
import json
import os
import time

import requests

from .bd import ajuste

_c = {"t": 0, "v": None}


def estado():
    """{'bloqueado': bool, 'motivo': str, 'aviso': str, 'posible_bloqueo': str} o {} si la instancia no está conectada a FaceAdmin."""
    url, tok = os.environ.get("FACEOPS_ADMIN_URL", "").rstrip("/"), os.environ.get("FACEOPS_ADMIN_TOKEN", "")
    if not url or not tok:
        return {}
    if _c["v"] is not None and time.time() - _c["t"] < 60:
        return _c["v"]
    v = None
    try:
        r = requests.get(url + "/api/instancia/estado", headers={"X-FaceAdmin-Token": tok}, timeout=4, verify=False)
        if r.ok:
            v = r.json()
            ajuste("faceadmin_estado", json.dumps(v))
    except (requests.RequestException, ValueError):
        v = None
    if v is None:
        try:
            v = json.loads(ajuste("faceadmin_estado") or "{}")
        except ValueError:
            v = {}
    _c.update(t=time.time(), v=v)
    return v
