#!/bin/bash
# Demo 4 · deshace "Preparar red para clúster OpenShift" para repetir la demo:
# Git (ipam/clusters.yml y config/rtr.conf vuelven a la base), router, firewall (reglas 200-299, ocp-*, dummy) y DNS.
# Los demás casos (accesos 100-199, IPs bloqueadas) no se tocan.
set -e
D=$(cd "$(dirname "$0")/.." && pwd)
export GT=$(sed -n 's/^gitea_token=//p' ~/demo-drift/cicd-secretos.txt) RL="$D/red-laboratorio"
python3 - <<'PY'
import base64, json, os, urllib.request, urllib.error
G = "http://127.0.0.1:3000/api/v1/repos/invoke/red-laboratorio/contents/"
H = {"Authorization": "token " + os.environ["GT"], "Content-Type": "application/json"}
def api(m, p, d=None):
    r = urllib.request.Request(G + p, method=m, headers=H, data=json.dumps(d).encode() if d else None)
    try:
        with urllib.request.urlopen(r) as x: return json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise
for ruta in ("config/rtr.conf", "ipam/clusters.yml"):
    base = open(os.path.join(os.environ["RL"], ruta), "rb").read()
    act = api("GET", ruta + "?ref=main")
    if act and base64.b64decode(act["content"]) == base:
        print(f"Git: {ruta} ya está en su base"); continue
    d = {"branch": "main", "message": f"Reinicio de la demo OpenShift: {ruta} vuelve a la base",
         "author": {"name": "Cristian Quezada", "email": "cquezada@invoke.cl"}, "content": base64.b64encode(base).decode()}
    if act: d["sha"] = act["sha"]
    api("PUT" if act else "POST", ruta, d); print(f"Git: {ruta} restaurado")
PY
unset GT
PYTHONPATH=~/bin python3 - <<'PY'
import time, urllib.parse
from aapapi import aap
p = aap("GET", "projects/?name=" + urllib.parse.quote("Red · laboratorio"))["results"][0]
u = aap("POST", f"projects/{p['id']}/update/", {})
while (st := aap("GET", f"project_updates/{u['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"): time.sleep(3)
print("AAP: proyecto", st)
PY
# Router: configuración base
sudo podman cp "$RL/config/rtr.conf" clab-summit-red-rtr:/tmp/base.conf
sudo podman exec clab-summit-red-rtr sh -c 'python3 /usr/lib/frr/frr-reload.py --reload /tmp/base.conf >/dev/null 2>&1' && echo "Router: configuración base"
# Firewall: solo lo de OpenShift
sudo podman cp "$D/lab/config/fw-limpiar-ocp.vbash" clab-summit-red-fw:/tmp/fw-limpiar-ocp.vbash
sudo podman exec clab-summit-red-fw sg vyattacfg -c "/bin/vbash /tmp/fw-limpiar-ocp.vbash" >/dev/null 2>&1 || true
echo "Firewall: reglas 200-299, grupos ocp-* e interfaces dummy eliminados"
# DNS: solo los registros base
sudo podman exec summit-dns sh -c 'find /etc/dnsmasq.d -name "*.conf" ! -name "00-base.conf" -delete; pkill dnsmasq' 2>/dev/null && echo "DNS: registros de clústeres eliminados" || echo "DNS: no está instalado (instalar-dns.sh)"
