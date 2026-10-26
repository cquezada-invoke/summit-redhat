#!/bin/bash
# Demo 4 · caso "IP sospechosa": vacía la lista de IPs bloqueadas del firewall para repetir la demo.
# (Los incidentes de ServiceNow se mantienen como historia.)
D=$(cd "$(dirname "$0")" && pwd)
for ip in $(bash "$D/consola-fw.sh" "/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands" 2>/dev/null \
            | sed -n "s/^set firewall group network-group bloqueadas network '\(.*\)'$/\1/p"); do
  bash "$D/consola-fw.sh" "vbash -c 'source /opt/vyatta/etc/functions/script-template; configure; delete firewall group network-group bloqueadas network $ip; commit; save; exit'" >/dev/null 2>&1
  echo "desbloqueada $ip"
done
sudo podman exec clab-summit-red-isp timeout 4 nc -z -w3 -s 203.0.113.50 10.20.20.2 22 >/dev/null 2>&1 \
  && echo "203.0.113.50 → firewall: LLEGA (listo para la demo)" || echo "203.0.113.50 → firewall: NO LLEGA (revisar)"
