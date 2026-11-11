"""Compatibilidad de cada automatización con los equipos (sistema operativo, tipo de periférico, componente de red,
producto, proveedor): etiquetas para el catálogo y la regla que decide en qué equipos se puede ejecutar."""
import os
import re

import yaml

from .bd import j

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "compatibilidad.yml")
NOMBRES = {"rhel": "RHEL", "ubuntu": "Ubuntu", "linux": "Linux", "windows": "Windows Server", "servidor": "Servidor", "router": "Router",
           "switch": "Switch", "firewall": "Firewall", "balanceador": "Balanceador", "almacenamiento": "Storage", "estacion": "Estación",
           "frr": "FRRouting", "vyos": "VyOS", "fortios": "FortiGate", "gcp": "Cloud GCP", "kvm": "On-premise (KVM)", "clúster openshift": "Clúster OpenShift"}
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
    if "linux" in s or any(d in s for d in ("rocky", "alma", "centos", "oracle", "fedora", "suse", "sles", "amazon")):
        return {"linux"}   # «Rocky 9.8», «CentOS Stream 9»…: el inventario no siempre dice «Linux»
    return set()


def red_de(e):
    s = (e.get("so") or "").lower()
    return {"frr" if "frr" in s else "vyos" if "vyos" in s else "fortios" if "forti" in s else ""} - {""}


def tipo_de(e):
    t = (e.get("categoria") or "servidor").lower()
    return {"estación de trabajo": "estacion", "estacion de trabajo": "estacion"}.get(t, t)


def falta_recurso(a, e):
    """Lo que le falta a la máquina en recursos (memoria) para la plataforma, o "" si alcanza. Se puede ajustar: no oculta."""
    rq = (conf().get("requisitos") or {}).get(a.get("nombre_aap") or "") or {}
    if rq.get("ram_mb") and (e.get("ram_mb") or 0) and int(e["ram_mb"]) < int(rq["ram_mb"]) * 0.85:
        return f"requiere {round(rq['ram_mb'] / 1024, 1):g} GB de memoria (tiene {round(int(e['ram_mb']) / 1024, 1):g} GB)"
    return ""


def requisito_encender(e, cod, en_ejecucion=()):
    """Memoria que necesita la máquina para encender la plataforma `cod` junto a las que ya corren (`en_ejecucion`: familias).
    None si la plataforma no tiene requisito definido (datos/compatibilidad.yml › encender). Se compara contra la memoria
    ASIGNADA (la que se ajusta en el hipervisor y muestra la ficha), no contra la que ve el sistema."""
    from .vistas import familia
    rq = conf().get("encender") or {}
    pl = rq.get("plataformas") or {}
    f = familia(cod)
    if f not in pl or not e.get("ram_mb"):
        return None
    otras = sorted({familia(x) for x in en_ejecucion} - {f})
    req = int(rq.get("sistema_mb") or 512) + int(pl[f]) + sum(int(pl.get(x) or 0) for x in otras)
    tiene = int(e["ram_mb"])
    gb = lambda mb: f"{round(mb / 1024, 1):g}".replace(".", ",")
    gb2 = lambda mb: f"{round(mb / 1024, 2):g}".replace(".", ",")
    return {"req_mb": req, "tiene_mb": tiene, "ok": tiene >= req, "otras": [x for x in otras if pl.get(x)],
            "texto": (f"Para encenderla se requieren ~{gb(req)} GB de RAM en la máquina (hoy {gb(tiene)} GB): ajusta la memoria"
                      if tiene < req else ""),
            "detalle": f"sistema {gb2(int(rq.get('sistema_mb') or 512))} GB + " + " + ".join(
                f"{x} {gb2(int(pl[x]))} GB" for x in [f] + [x for x in otras if pl.get(x)])}


def compatible(a, e, recursos=True):
    """(sí/no, por qué no). recursos=False no cuenta la memoria (se puede ajustar; la plataforma se muestra con alerta)."""
    c = de(a)
    if "kvm" in (e.get("skills") or []):
        return False, "es el anfitrión de la plataforma (no se opera con automatizaciones de equipos)"
    if c.get("crea"):
        return False, "crea equipos nuevos: no se ejecuta sobre uno existente"
    if c.get("sistemas") and not set(c["sistemas"]) & sistema_fino(e):
        return False, "sistema " + (e.get("so") or "desconocido")
    rq = (conf().get("requisitos") or {}).get(a.get("nombre_aap") or "")
    if rq:   # requisitos del fabricante de la plataforma: sistema, versión mayor y memoria
        if rq.get("sistemas") and not set(rq["sistemas"]) & sistema_fino(e):
            return False, f"requiere {' o '.join(NOMBRES.get(x, x) for x in rq['sistemas'])} (tiene {e.get('so') or 'sistema desconocido'})"
        m_ = re.search(r"(\d+)(?:\.\d+)?", e.get("so") or "")
        if rq.get("versiones") and (not m_ or m_.group(1) not in [str(v) for v in rq["versiones"]]):
            return False, f"requiere versión {' o '.join(str(v) for v in rq['versiones'])} del sistema (tiene {e.get('so') or '—'})"
        if recursos and rq.get("ram_mb") and (e.get("ram_mb") or 0) and int(e["ram_mb"]) < int(rq["ram_mb"]) * 0.85:
            return False, f"requiere {round(rq['ram_mb'] / 1024, 1):g} GB de memoria (tiene {round(int(e['ram_mb']) / 1024, 1):g} GB)"
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
