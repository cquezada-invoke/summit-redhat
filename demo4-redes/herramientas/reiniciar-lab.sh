#!/bin/bash
# Devuelve el laboratorio al estado limpio (configuración base de este repositorio) en segundos.
set -e
D=$(cd "$(dirname "$0")/.." && pwd); L=/opt/summit-red
sudo cp "$D"/lab/config/* $L/config/ && sudo chcon -R -t container_file_t $L/config 2>/dev/null || true
cd $L && sudo containerlab deploy --runtime podman -t summit-red.clab.yml --reconfigure >/dev/null 2>&1
for i in $(seq 1 30); do
  sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null | grep -q Established && break; sleep 2
done
bash "$D/herramientas/probar-red.sh"
