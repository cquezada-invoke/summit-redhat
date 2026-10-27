#!/bin/bash
# Demo 4 · caso "IP sospechosa": vacía la lista de IPs bloqueadas del firewall para repetir la demo.
# (Los incidentes de ServiceNow se mantienen como historia.)
D=$(cd "$(dirname "$0")" && pwd)
bash "$D/limpiar-sesiones-fw.sh"
IPS=$(bash "$D/consola-fw.sh" "/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands" 2>/dev/null \
      | sed -n "s/^set firewall group network-group bloqueadas network '\(.*\)'$/\1/p")
if [ -n "$IPS" ]; then
  { echo "source /opt/vyatta/etc/functions/script-template"; echo configure
    for ip in $IPS; do echo "delete firewall group network-group bloqueadas network $ip"; done
    echo commit; echo save; echo exit; } | sudo podman exec -i clab-summit-red-fw sg vyattacfg -c "/bin/vbash -s" >/dev/null 2>&1
  for ip in $IPS; do echo "desbloqueada $ip"; done
fi
sudo podman exec clab-summit-red-isp timeout 4 nc -z -w3 -s 203.0.113.50 10.20.20.2 22 >/dev/null 2>&1 \
  && echo "203.0.113.50 → firewall: LLEGA (listo para la demo)" || echo "203.0.113.50 → firewall: NO LLEGA (revisar)"
