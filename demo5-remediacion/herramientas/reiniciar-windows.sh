#!/bin/bash
# Demo 5 · Windows: deja nodo-windows listo para la demo (IIS arriba, página de la app) con "Windows · Preparar IIS".
PYTHONPATH=~/bin python3 - <<'PY'
import time, urllib.parse
from aapapi import aap
jt = aap("GET", "job_templates/?name=" + urllib.parse.quote("Windows · Preparar IIS"))["results"][0]
j = aap("POST", f"job_templates/{jt['id']}/launch/", {})
while (st := aap("GET", f"jobs/{j['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"): time.sleep(3)
print("Windows · Preparar IIS:", st, f"(job {j['id']})")
PY
curl -s -m 4 -o /dev/null -w "Sitio de nodo-windows: %{http_code}\n" http://192.168.122.17/
