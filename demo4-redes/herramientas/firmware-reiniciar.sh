#!/bin/bash
# Demo 4 · firmware: deja los routers de sucursal en la versión base (FRR 9.1.0) para repetir la demo y muestra el parque.
# Los servidores RHEL no se "desparchan" (una vez aplicados, los parches de seguridad se mantienen).
D=$(cd "$(dirname "$0")" && pwd)
bash "$D/sucursales-arriba.sh"
for i in $(seq 1 30); do
  sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null | grep -o '"state":"Established"' | wc -l | grep -qE '^[4-9]' && break; sleep 3
done
bash "$D/firmware-estado.sh"
