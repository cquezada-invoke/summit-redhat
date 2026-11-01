"""Versión instalada de cada plataforma de una máquina (hoy JBoss EAP, leída de su API de administración con la
credencial de la bóveda), con caché en equipos.datos["versiones"] y su ciclo de vida (datos/ciclo-vida.yml)."""
import json
import re
import time

import requests

from .bd import ej, j

VIGENCIA_S = 3600      # se vuelve a preguntar cada hora
VIGENCIA_FALLA_S = 600


def _leer_jboss(e):
    from requests.auth import HTTPDigestAuth
    from .boveda import web
    cr = web(e["nombre"], "jboss") or {}
    if not (cr.get("usuario") and cr.get("clave") and e.get("ip")):
        return None
    try:
        r = requests.post(f"http://{e['ip']}:9990/management", json={"operation": "read-resource", "address": [], "attributes-only": True},
                          auth=HTTPDigestAuth(cr["usuario"], cr["clave"]), timeout=3)
        res = r.json().get("result") or {}
        return {"producto": res.get("product-name") or "JBoss EAP", "version": res.get("product-version") or ""}
    except (requests.RequestException, ValueError):
        return None


LECTORES = {"jboss": _leer_jboss}


def de(e, cod):
    """{'version': '8.0 Update 12', 'mayor': '8.0', 'ciclo': {...}} o None."""
    from .vistas import familia
    fam = familia(cod)
    lector = LECTORES.get(fam)
    if not lector:
        return None
    datos = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    cache = (datos.get("versiones") or {}).get(fam)
    if not cache or time.time() - cache.get("ts", 0) > (VIGENCIA_S if cache.get("version") else VIGENCIA_FALLA_S):
        leido = lector(e) or {}
        cache = {"version": leido.get("version", "") or (cache or {}).get("version", ""), "ts": time.time()}
        datos.setdefault("versiones", {})[fam] = cache
        ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(datos, ensure_ascii=False), e["id"])
    v = cache.get("version") or ""
    if not v:
        return None
    m = re.match(r"(\d+\.\d+)", v)
    mayor = m.group(1) if m else v
    ciclo = None
    try:
        from .ciclo_vida import evaluar_producto
        ciclo = evaluar_producto({"jboss": "jboss-eap"}.get(fam, fam), mayor)
    except Exception:  # noqa: BLE001
        pass
    return {"version": v, "mayor": mayor, "ciclo": ciclo}
