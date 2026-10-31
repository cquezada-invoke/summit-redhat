"""Ciclo de vida: en qué fase de soporte del fabricante está cada equipo (sistema operativo o plataforma).
Los datos del equipo vienen de AAP (recolector y «OpenShift · Recolectar clúster»); las fechas, de datos/ciclo-vida.yml."""
import os
import re
from datetime import date

import yaml

_DATOS = None
FASES = {"completo": ("Soporte completo", "s-ok"), "mantencion": ("Mantención", "s-media"),
         "extendido": ("Solo soporte extendido", "s-alta"), "fuera": ("Fuera de soporte", "s-alta"), "desconocido": ("Sin datos de ciclo de vida", "")}
UMBRAL_DIAS = 180


def datos():
    global _DATOS
    if _DATOS is None:
        _DATOS = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "datos", "ciclo-vida.yml"), encoding="utf-8"))["productos"]
    return _DATOS


def producto_de(e):
    """(clave, versión del ciclo) a partir del sistema o plataforma del equipo."""
    so = (e.get("so") or "").strip()
    s = so.lower()
    if e.get("categoria") == "clúster openshift" or s.startswith("openshift"):
        m = re.search(r"(\d+\.\d+)", so)
        return ("openshift", m.group(1)) if m else ("openshift", "")
    if "red hat" in s or "redhat" in s or s.startswith("rhel"):
        m = re.search(r"(\d+)", so)
        return "rhel", m.group(1) if m else ""
    if "centos" in s:
        m = re.search(r"(\d+)", so)
        return "centos", m.group(1) if m else ""
    if "ubuntu" in s:
        m = re.search(r"(\d+\.\d+)", so)
        return "ubuntu", m.group(1) if m else ""
    if "windows" in s:
        m = re.search(r"(20\d\d)", so)
        return "windows", m.group(1) if m else ""
    return None, ""


def evaluar(e, hoy=None):
    hoy = hoy or date.today()
    p, v = producto_de(e)
    if not p:
        return None
    prod = datos().get(p, {})
    ver = (prod.get("versiones") or {}).get(v)
    fila = {"producto": p, "nombre": prod.get("nombre", p), "version": v, "fuente": prod.get("fuente", ""), "fase": "desconocido",
            "fin_fase": None, "fin_soporte": None, "dias": None, "siguiente": "", "registrado": (e.get("_datos") or {}).get("registrado")}
    if not ver:
        return fila
    fechas = {k: (x if isinstance(x, date) else date.fromisoformat(str(x))) for k, x in ver.items()}
    fin_total = fechas.get("extendido") or fechas.get("mantencion")
    fila["fin_soporte"] = fin_total
    for fase in ("completo", "mantencion", "extendido"):
        if fase in fechas and hoy <= fechas[fase]:
            fila.update(fase=fase, fin_fase=fechas[fase], dias=(fechas[fase] - hoy).days)
            break
    else:
        fila.update(fase="fuera", dias=(fin_total - hoy).days if fin_total else None)
    # versión vigente más nueva del mismo producto: a la que conviene ir
    nuevas = [k for k, x in prod["versiones"].items() if _orden(k) > _orden(v)]
    fila["siguiente"] = max(nuevas, key=_orden) if nuevas else ""
    fila["riesgo"] = fila["fase"] in ("fuera", "extendido") or (fila["dias"] is not None and fila["dias"] <= UMBRAL_DIAS and fila["fase"] != "completo")
    return fila


def _orden(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


def panel(equipos, hoy=None):
    filas = []
    for e in equipos:
        ev = evaluar(e, hoy)
        if ev:
            filas.append(dict(ev, equipo=e))
    grupos = {}
    for f in filas:
        grupos.setdefault((f["producto"], f["version"]), []).append(f)
    lista = []
    for (p, v), fs in grupos.items():
        f0 = fs[0]
        lista.append({k: f0[k] for k in ("producto", "nombre", "version", "fuente", "fase", "fin_fase", "fin_soporte", "dias", "siguiente")}
                     | {"riesgo": f0.get("riesgo", False), "equipos": [x["equipo"] for x in fs],
                        "sin_registro": [x["equipo"] for x in fs if x["registrado"] is False]})
    orden = {"fuera": 0, "extendido": 1, "mantencion": 2, "completo": 3, "desconocido": 4}
    lista.sort(key=lambda g: (orden[g["fase"]], g["dias"] if g["dias"] is not None else 99999))
    cuenta = {k: sum(len(g["equipos"]) for g in lista if g["fase"] == k) for k in orden}
    return {"grupos": lista, "cuenta": cuenta, "total": len(filas), "umbral": UMBRAL_DIAS,
            "sin_registro": sum(len(g["sin_registro"]) for g in lista),
            "proximos": sum(len(g["equipos"]) for g in lista if g["dias"] is not None and 0 <= g["dias"] <= UMBRAL_DIAS)}
