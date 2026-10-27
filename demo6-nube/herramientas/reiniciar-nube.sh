#!/bin/bash
# Demo 6 · deja la nube lista para la demo: borra los recursos de prueba (disco, IP y regla summit-cloud-prueba-*)
# lanzando las plantillas de prueba con "borrar". No toca nada más del proyecto.
set -e
python3 - <<'PY'
import sys, time, urllib.parse
sys.path.insert(0, "/home/cquezada/bin")
from aapapi import aap
for n in ("Nube · Prueba: crear huérfanos", "Nube · Prueba: regla peligrosa"):
    jt = aap("GET", "job_templates/?name=" + urllib.parse.quote(n))["results"][0]["id"]
    j = aap("POST", f"job_templates/{jt}/launch/", {"extra_vars": {"prueba_accion": "borrar"}})["job"]
    while (st := aap("GET", f"jobs/{j}/")["status"]) not in ("successful", "failed", "error", "canceled"):
        time.sleep(3)
    print(f"{n} (borrar): {st}")
PY
