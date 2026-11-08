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
    if _c["v"] is not None and time.time() - _c["t"] < 15:
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


_u = {"t": 0.0, "corriendo": False}


def reportar_uso(cada=600):
    """Cada 10 minutos, en segundo plano: informa a FaceAdmin los nodos que gestiona el AAP de esta instancia (suscripción Red Hat)
    y las automatizaciones ejecutadas con sus créditos. Cada ejecución lleva su id: FaceAdmin la registra una sola vez."""
    url, tok = os.environ.get("FACEOPS_ADMIN_URL", "").rstrip("/"), os.environ.get("FACEOPS_ADMIN_TOKEN", "")
    if not url or not tok or _u["corriendo"] or time.time() - _u["t"] < cada:
        return
    _u.update(t=time.time(), corriendo=True)
    import threading
    threading.Thread(target=_reportar, args=(url, tok), daemon=True).start()


def _nodos():
    """Nodos gestionados según la licencia de AAP: instancias automatizadas (o, si no informa, hosts actuales)."""
    from .aap import AAP, ErrorAAP
    from .bd import q1
    c = q1("SELECT * FROM clientes WHERE COALESCE(aap_url,'')<>'' AND COALESCE(aap_token,'')<>'' ORDER BY id LIMIT 1")
    if not c:
        return None
    try:
        lic = (AAP.de_cliente(c).get("config/") or {}).get("license_info") or {}
    except ErrorAAP:
        return None
    usados = lic.get("automated_instances")
    if usados is None:
        usados = lic.get("current_instances")
    return {"nodos": int(usados or 0), "automatizados": int(lic.get("automated_instances") or 0),
            "fuente": f"AAP · licencia {lic.get('instance_count', '?')} nodos"}


def _reportar(url, tok):
    from .bd import ajuste, q
    try:
        desde = int(ajuste("faceadmin_uso_ultimo") or 0)
        movs = q("SELECT id, fecha, creditos, detalle FROM creditos_mov WHERE tipo='ejecucion' AND id>? ORDER BY id LIMIT 500", desde)
        datos = {"consumo": [{"id": f"mov-{m['id']}", "fecha": m["fecha"], "creditos": -float(m["creditos"] or 0), "detalle": m["detalle"]}
                             for m in movs if float(m["creditos"] or 0) < 0]}
        n = _nodos()
        if n:
            datos.update(n)
        r = requests.post(url + "/api/instancia/uso", json=datos, headers={"X-FaceAdmin-Token": tok}, timeout=10, verify=False)
        if r.ok and movs:
            ajuste("faceadmin_uso_ultimo", str(movs[-1]["id"]))
    except (requests.RequestException, ValueError):
        pass
    finally:
        _u["corriendo"] = False


def aprobar_activacion(usuario):
    """El cliente (cluster-admin de su FaceOps) aprueba la activación de su contrato en FaceAdmin."""
    url, tok = os.environ.get("FACEOPS_ADMIN_URL", "").rstrip("/"), os.environ.get("FACEOPS_ADMIN_TOKEN", "")
    if not url or not tok:
        raise ValueError("Esta instancia no está enlazada a FaceAdmin")
    try:
        r = requests.post(url + "/api/instancia/activar", json={"usuario": usuario}, headers={"X-FaceAdmin-Token": tok}, timeout=10, verify=False)
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        raise ValueError("FaceAdmin no respondió; intenta de nuevo en unos minutos") from e
    if not r.ok:
        raise ValueError(d.get("error") or "No se pudo activar")
    _c.update(t=0)   # se vuelve a preguntar el estado en la próxima página
    return d
