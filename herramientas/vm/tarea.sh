#!/bin/bash
# Instala faceops-autodeploy.timer en el anfitrión y corre un primer despliegue. No imprime secretos.
echo "usuario: $(id -un) home: $HOME"
cd ~/summit-redhat && for i in 1 2 3; do git pull -q --ff-only origin main && break; sleep 10; done; git log -1 --format='%h %s' | cut -c1-70
U=$(id -un)
sed "s#User=cquezada#User=$U#; s#/home/cquezada#$HOME#" herramientas/faceops-autodeploy.service | sudo tee /etc/systemd/system/faceops-autodeploy.service >/dev/null
sudo install -m 644 herramientas/faceops-autodeploy.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable -q --now faceops-autodeploy.timer && echo "timer habilitado"
echo "== faceops publica: $(sudo podman port faceops 2>/dev/null | tr '\n' ' ') · red: $(sudo podman inspect -f '{{.HostConfig.NetworkMode}}' faceops 2>/dev/null)"
for u in http://127.0.0.1:8110/ingresar http://127.0.0.1:8080/ingresar https://127.0.0.1/faceops/ingresar; do echo "$u -> $(curl -sk -o /dev/null -m 10 -w '%{http_code}' $u)"; done
echo "== primer despliegue (servicio)"
sudo systemctl start faceops-autodeploy.service; echo "código: $?"
echo "estado: $(cat ~/faceops-autodeploy/estado 2>/dev/null)"
tail -8 ~/faceops-autodeploy/autodeploy.log | grep -v -i -E "clave|password|token|secret"
systemctl list-timers faceops-autodeploy.timer --no-pager | head -3
