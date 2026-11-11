"""Monitoreo › ITIL: apego de FaceOps a las 34 prácticas de ITIL 4 (datos/itil.yml).
Regla del % de apego: cubierta = 1, parcial = 0,5, no cubierta = 0; apego = suma / n.º de prácticas × 100.
La misma regla se aplica a lo que declaraba la versión anterior de la página («antes») para comparar."""
import os

import yaml
from flask import url_for
from werkzeug.routing import BuildError

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "itil.yml")
PESO = {"cubierta": 1.0, "parcial": 0.5, "no": 0.0}
NOMBRE = {"cubierta": "Cubierta", "parcial": "Parcial", "no": "No cubierta"}
ORDEN = ["cubierta", "parcial", "no"]


def _pct(ps, campo):
    return round(100 * sum(PESO[str(p.get(campo) or "no")] for p in ps) / len(ps)) if ps else None


def calcular(v):
    d = yaml.safe_load(open(RUTA, encoding="utf-8")) or {}
    ps = d.get("practicas") or []
    for p in ps:
        p["estado"], p["antes"] = str(p.get("estado") or "no"), str(p.get("antes") or "no")
        p["sube"] = PESO[p["estado"]] > PESO[p["antes"]]
        p["baja"] = PESO[p["estado"]] < PESO[p["antes"]]
        for e in p.get("evidencia") or []:
            try:
                e["url"] = url_for(e["ep"], **(e.get("args") or {}))
            except BuildError:
                e["url"] = None
        for c in p.get("criterios") or []:
            x = v.get(c["valor"])
            c["actual"] = x
            c["ok"] = None if x is None else (x <= c["maximo"] if "maximo" in c else x >= c.get("minimo", 0))
    grupos = {}
    for p in ps:
        grupos.setdefault(p["grupo"], []).append(p)
    resumen = {k: sum(1 for p in ps if p["estado"] == k) for k in ORDEN}
    resumen_antes = {k: sum(1 for p in ps if p["antes"] == k) for k in ORDEN}
    medidos = [c for p in ps for c in p.get("criterios") or [] if c["ok"] is not None]
    return {"grupos": {g: {"ps": l, "pct": _pct(l, "estado"), "antes": _pct(l, "antes")} for g, l in grupos.items()},
            "total": _pct(ps, "estado"), "total_antes": _pct(ps, "antes"), "n": len(ps),
            "resumen": resumen, "resumen_antes": resumen_antes, "nombre": NOMBRE,
            "subieron": [p for p in ps if p["sube"]], "bajaron": [p for p in ps if p["baja"]],
            "medidos_ok": sum(1 for c in medidos if c["ok"]), "medidos_n": len(medidos),
            "corregido": d.get("corregido") or [], "revision": d.get("revision"), "revision_anterior": d.get("revision_anterior")}
