#!/bin/bash
# Simula un cambio manual en el router (lo que haría alguien por consola, fuera de Git).
#   romper-red.sh          → borra el vecino BGP del ISP: se cae la salida a Internet
#   romper-red.sh ruta     → agrega una ruta estática no declarada en Git
M=${1:-bgp}
case "$M" in
  ruta) sudo podman exec clab-summit-red-rtr vtysh -c 'conf t' -c 'ip route 192.0.2.0/24 Null0' 2>/dev/null ;;
  *)    sudo podman exec clab-summit-red-rtr vtysh -c 'conf t' -c 'router bgp 65010' -c 'no neighbor 10.0.0.1' 2>/dev/null ;;
esac
echo "Cambio manual aplicado en rtr ($M)."
sleep 3
printf "router → Internet (203.0.113.1): "
sudo podman exec clab-summit-red-rtr ping -c2 -W1 203.0.113.1 >/dev/null 2>&1 && echo OK || echo "FALLA (esperado si se borró el vecino BGP)"
echo "EDA recibe el aviso del vigía y AAP lo restaura desde Git en menos de un minuto."
