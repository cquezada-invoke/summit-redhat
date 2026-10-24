#!/bin/bash
# Demo 4 · Redes — fase 1: containerlab (runtime podman) + FRR + equipos de prueba + AAP. Idempotente.
# Uso en la VM:  bash ~/summit-redhat/demo4-redes/instalar-lab.sh
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd); L=/opt/summit-red
export PATH=$PATH:/usr/local/bin
log(){ echo "== $*"; }

log "1. containerlab"
command -v containerlab >/dev/null || curl -sL https://get.containerlab.dev | sudo bash >/dev/null
containerlab version | grep -i version | head -1
sudo systemctl enable --now podman.socket >/dev/null 2>&1

log "2. Llave SSH del laboratorio (la privada va a Vault)"
mkdir -p ~/demo4 && chmod 700 ~/demo4
[ -f ~/demo4/llave-lab ] || ssh-keygen -q -t ed25519 -N '' -C red-laboratorio -f ~/demo4/llave-lab

log "3. Imagen FRR con SSH"
sudo mkdir -p $L/config $L/frr-ssh
sudo cp "$D"/lab/summit-red.clab.yml $L/; sudo cp "$D"/lab/config/* $L/config/
sudo cp "$D"/lab/frr-ssh/Containerfile $L/frr-ssh/; sudo cp ~/demo4/llave-lab.pub $L/frr-ssh/llave.pub
if ! sudo podman image exists localhost/frr-summit:1; then
  ok=0
  for v in 10.2.1 10.1.1 10.0.1 9.1.2; do
    if sudo podman build -q --build-arg FRR=quay.io/frrouting/frr:$v -t localhost/frr-summit:1 $L/frr-ssh >/dev/null 2>&1; then
      echo "   FRR $v"; ok=1; break; fi
  done
  [ $ok = 1 ] || { echo "No se pudo construir la imagen FRR"; exit 1; }
fi
sudo podman pull -q docker.io/library/alpine:3.20 >/dev/null; sudo podman pull -q docker.io/library/python:3.12-alpine >/dev/null
sudo chcon -R -t container_file_t $L/config 2>/dev/null || true

log "4. Desplegar el laboratorio"
cd $L && sudo containerlab deploy --runtime podman -t summit-red.clab.yml --reconfigure 2>&1 | tail -12
for i in $(seq 1 30); do
  sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null | grep -q Established && break; sleep 2
done
bash "$D/herramientas/probar-red.sh"
printf "SSH al router desde el host: "
ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -i ~/demo4/llave-lab root@172.20.20.11 "vtysh -c 'show version' | head -1" || echo FALLA

log "5. Gitea, Vault y AAP"
python3 "$D/configurar_red_aap.py"
python3 "$D/configurar_eda_red.py"

log "6. Event-Driven Ansible"
if podman ps --format '{{.Names}}' | grep -qi eda; then echo "   EDA instalado"; else echo "   EDA no está instalado: la detección queda como plantilla programada en AAP"; fi
log "LISTO"
