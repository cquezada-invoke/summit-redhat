#!/bin/bash
# Estado del lab tras el arranque: FaceOps, AAP (ping, instancias, contenedores). Solo lectura. Sin secretos.
uptime
for i in $(seq 1 30); do c=$(curl -sk -m 8 -o /dev/null -w %{http_code} https://localhost:8843/faceops/ingresar); [ "$c" = 200 ] && break; sleep 10; done; echo "faceops $c"
for i in $(seq 1 30); do c=$(curl -sk -m 8 -o /tmp/ping.json -w %{http_code} https://aap.34.176.42.56.sslip.io/api/controller/v2/ping/); [ "$c" = 200 ] && break; sleep 10; done; echo "aap ping $c"; python3 -c "import json;d=json.load(open('/tmp/ping.json'));print([ (i['node'],i['capacity'],i.get('heartbeat')) for i in d.get('instances',[])])" 2>&1 | tail -1
sudo podman ps --format '{{.Names}} {{.Status}}' | head -40
podman ps --format '{{.Names}} {{.Status}}' 2>/dev/null | head -40
sudo virsh list --all | head -40
cd ~/summit-redhat && git log -1 --format='%h %s' | cut -c1-100
