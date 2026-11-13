#!/bin/bash
# Demo 5 · Windows: detiene IIS en nodo-windows (vía AAP). Prometheus ve el sitio caído en ~20 s, EDA lanza
# "Remediación · IIS en Windows", que lo levanta, verifica y deja el ticket cerrado en Odoo.
# Uso: provocar-windows.sh   ·   Volver al estado inicial: reiniciar-windows.sh
D=$(cd "$(dirname "$0")" && pwd)
estado(){ curl -s -m 4 -o /dev/null -w "%{http_code}" http://192.168.122.17/ 2>/dev/null | sed 's/^200$/RESPONDE/;s/^000$/NO RESPONDE/'; }
echo "1) Sitio de nodo-windows: $(estado)"
echo "2) Se detiene IIS en nodo-windows…"
PYTHONPATH=~/bin python3 - <<'PY'
import time, urllib.parse
from aapapi import aap
jt = aap("GET", "job_templates/?name=" + urllib.parse.quote("Windows · Provocar caída de IIS (demo)"))["results"][0]
j = aap("POST", f"job_templates/{jt['id']}/launch/", {})
while (st := aap("GET", f"jobs/{j['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"): time.sleep(3)
print("   job", j["id"], st)
PY
echo "3) Sitio de nodo-windows: $(estado)"
printf "4) Esperando que Ansible lo repare"
for i in $(seq 1 60); do [ "$(estado)" = RESPONDE ] && break; printf "."; sleep 3; done
echo; echo "5) Sitio de nodo-windows: $(estado)"
echo "   Tickets en Odoo: https://invoke.odoo.com/odoo/action-helpdesk.helpdesk_ticket_action_main_tree"
