"""Compatibilidad de cada automatización con los equipos (sistema operativo, tipo de periférico, componente de red,
producto, proveedor): etiquetas para el catálogo y la regla que decide en qué equipos se puede ejecutar."""
import os
import re

import yaml

from .bd import j

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "compatibilidad.yml")
NOMBRES = {"rhel": "RHEL", "ubuntu": "Ubuntu", "linux": "Linux", "windows": "Windows Server", "servidor": "Servidor", "router": "Router",
           "switch": "Switch", "firewall": "Firewall", "balanceador": "Balanceador", "almacenamiento": "Storage", "estacion": "Estación",
           "frr": "FRRouting", "vyos": "VyOS", "fortios": "FortiGate", "gcp": "Cloud GCP", "kvm": "On-premise (KVM)"}
_cache = {}


def conf():
    m = os.path.getmtime(RUTA) if os.path.exists(RUTA) else 0
    if _cache.get("m") != m:
        _cache.update(m=m, c=yaml.safe_load(open(RUTA, encoding="utf-8")) if m else {})
    return _cache["c"] or {}


def de(a):
    """Compatibilidad de la automatización: explícita, por regla, o deducida de la skill que habilita."""
    c = conf()
    n = a.get("nombre_aap") or ""
    if n in (c.get("automatizaciones") or {}):
        return c["automatizaciones"][n]
    for patron, comp in c.get("reglas", []):
        if re.search(patron, n):
            return comp
    sis = j(a.get("compatibles"), [])
    out = {}
    if sis:
        out["sistemas"] = sis
    if j(a.get("skills"), []):
        out["skills"] = j(a.get("skills"), [])
    if a.get("usa_limite") or a.get("por_inventario"):
        out.setdefault("tipos", ["servidor"])
    return out


def etiquetas(a):
    c = de(a)
    out = []
    if c.get("crea"):
        return ["crea: " + " · ".join(NOMBRES.get(x, x) for x in c["crea"])]
    for k in ("sistemas", "tipos", "red", "proveedor"):
        if c.get(k):
            out.append(" · ".join(NOMBRES.get(x, x) for x in c[k]))
    if c.get("skills"):
        out.append("con " + " o ".join(c["skills"]))
    return out or ["cualquier equipo"]


def sistema_fino(e):
    s = (e.get("so") or "").lower()
    if "windows" in s:
        return {"windows"}
    if "ubuntu" in s or "debian" in s:
        return {"ubuntu", "linux"}
    if "red hat" in s or "redhat" in s or "rhel" in s:
        return {"rhel", "linux"}
    if "linux" in s:
        return {"linux"}
    return set()


def red_de(e):
    s = (e.get("so") or "").lower()
    return {"frr" if "frr" in s else "vyos" if "vyos" in s else "fortios" if "forti" in s else ""} - {""}


def tipo_de(e):
    t = (e.get("categoria") or "servidor").lower()
    return {"estación de trabajo": "estacion", "estacion de trabajo": "estacion"}.get(t, t)


def compatible(a, e):
    """(sí/no, por qué no)."""
    c = de(a)
    if "kvm" in (e.get("skills") or []):
        return False, "es el anfitrión de la plataforma (no se opera con automatizaciones de equipos)"
    if c.get("crea"):
        return False, "crea equipos nuevos: no se ejecuta sobre uno existente"
    if c.get("sistemas") and not set(c["sistemas"]) & sistema_fino(e):
        return False, "sistema " + (e.get("so") or "desconocido")
    if c.get("tipos") and tipo_de(e) not in c["tipos"]:
        return False, "es " + tipo_de(e)
    if c.get("red") and not set(c["red"]) & red_de(e):
        return False, "componente de red " + (e.get("so") or "—")
    if c.get("skills") and not set(c["skills"]) & set(e.get("skills") or []):
        return False, "no tiene " + " ni ".join(c["skills"])
    if c.get("proveedor"):
        nube = "gcp" in (e.get("proveedor") or "").lower() or "cloud" in (e.get("ubicacion") or "").lower()
        if ("gcp" in c["proveedor"]) != nube:
            return False, "está " + ("en la nube" if nube else "on-premise")
    return True, ""
