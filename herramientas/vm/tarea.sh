#!/bin/bash
echo "== estado"; sudo systemctl is-active faceops; sudo systemctl show faceops -p ActiveState,SubState,NRestarts,ExecMainStatus
echo "== podman ps"; sudo podman ps -a --filter name=faceops --format '{{.Names}} {{.Status}}'
echo "== logs (filtrados)"; sudo podman logs --tail 60 faceops 2>&1 | grep -viE 'token|password|clave|secret|authorization|bearer' | tail -50
echo "== journal"; sudo journalctl -u faceops -n 30 --no-pager 2>&1 | grep -viE 'token|password|clave|secret' | tail -30
echo "== git"; cd ~/summit-redhat && git log -3 --format='%h %s' 
echo "== curl local"; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/ingresar || true
sudo ss -ltnp | grep -E ':80[0-9][0-9] ' | head
