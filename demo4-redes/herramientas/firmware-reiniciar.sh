#!/bin/bash
# Demo 4 · firmware: deja los routers de sucursal en la versión base (FRR 9.1.0) para repetir la demo.
# Los servidores RHEL no se "desparchan" (una vez aplicados, los parches de seguridad se mantienen).
for n in 1 2 3; do
  E=$(sudo /usr/local/sbin/lab-sucursal estado suc$n)
  if ! echo "$E" | grep -q '"imagen":"quay.io/frrouting/frr:9.1.0"'; then
    echo "suc$n → 9.1.0"; sudo /usr/local/sbin/lab-sucursal actualizar suc$n quay.io/frrouting/frr:9.1.0 >/dev/null
  fi
done
sleep 20
bash "$(dirname "$0")/firmware-estado.sh"
