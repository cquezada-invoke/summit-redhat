"""Paquetes del sistema de cada máquina (los lista AAP con «Sistema · Paquetes · inventario y respaldo») y qué está ya
respaldado en el registro de paquetes propio (Gitea · repositorio/rpm). FaceOps solo lee el registro (caché de 5 min)."""
import time

import requests

_cache = {}
REPO_LOCAL = ("@commandline", "commandline", "<unknown>", "")


def limpiar_cache():
    _cache.clear()


def en_registro():
    """{'nombre|versión-release'} de los RPM del registro propio."""
    if "rpm" in _cache and time.time() - _cache["rpm"][0] < 300:
        return _cache["rpm"][1]
    out = set()
    try:
        import os
        from .readme import _gitea
        base, h = _gitea()
        tok = os.environ.get("FACEOPS_RESPALDO_TOKEN", "")   # el usuario de servicio del registro ve sus paquetes privados
        if tok:
            h = {"Authorization": f"token {tok}"}
        for page in range(1, 200):
            r = requests.get(f"{base}/api/v1/packages/repositorio", params={"type": "rpm", "limit": 50, "page": page}, headers=h, timeout=10)
            lote = r.json() if r.ok else []
            if not lote:
                break
            out.update(f"{p['name']}|{p['version']}" for p in lote)
    except Exception:  # noqa: BLE001 - sin registro se muestra la lista igual
        pass
    _cache["rpm"] = (time.time(), out)
    return out


def de_equipo(datos):
    """{'fecha', 'ticket', 'lista': [{n, v, a, r, s, respaldo}], 'total', 'respaldados', 'fuentes': {repo: n}} o None."""
    p = (datos or {}).get("paquetes")
    if not p:
        return None
    reg = en_registro()
    lista, fuentes = [], {}
    for x in p.get("lista") or []:
        rpm = x.get("r") != "apt"
        resp = (f"{x['n']}|{x['v']}" in reg) if rpm else None
        lista.append(dict(x, respaldo=resp, local=x.get("r") in REPO_LOCAL))
        fuentes[x.get("r") or "—"] = fuentes.get(x.get("r") or "—", 0) + 1
    lista.sort(key=lambda x: x["n"].lower())
    return {**{k: p.get(k) for k in ("fecha", "ticket", "gestor", "subidos", "errores", "pendientes")}, "lista": lista, "total": len(lista),
            "respaldados": sum(1 for x in lista if x["respaldo"]), "rpm": any(x["respaldo"] is not None for x in lista),
            "fuentes": dict(sorted(fuentes.items(), key=lambda kv: -kv[1]))}
