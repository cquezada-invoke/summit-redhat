#!/bin/bash
# Sede Providencia (router WAN, firewall VyOS, switch core, switch de acceso y balanceador): la redespliega si no está
# arriba (al encender la VM los contenedores quedan detenidos). Idempotente. La primera vez: instalar-red-sede.sh.
L=/opt/summit-red/sede; export PATH=$PATH:/usr/local/bin
if sudo podman ps --format '{{.Names}}' | grep -qx clab-red-sede-sw-core-01; then echo "sede: arriba"; exit 0; fi
[ -f $L/red-sede.clab.yml ] || { echo "sede: no instalada (instalar-red-sede.sh)"; exit 0; }
cd $L && sudo containerlab deploy --runtime podman -t red-sede.clab.yml --reconfigure 2>&1 | tail -3
