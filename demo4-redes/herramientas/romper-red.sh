#!/bin/bash
# Simula un cambio manual en el router (lo que haría alguien por consola, fuera de Git).
#   romper-red.sh          → borra el vecino BGP del ISP: se cae la salida a Internet
#   romper-red.sh ruta     → agrega una ruta estática no declarada en Git
case "${1:-bgp}" in
  ruta) sudo podman exec clab-summit-red-rtr vtysh -c 'conf t' -c 'ip route 192.0.2.0/24 Null0' ;;
  *)    sudo podman exec clab-summit-red-rtr vtysh -c 'conf t' -c 'router bgp 65010' -c 'no neighbor 10.0.0.1' ;;
esac
echo "Cambio manual aplicado en rtr ($1). Pruébalo con: bash ~/summit-redhat/demo4-redes/herramientas/probar-red.sh"
