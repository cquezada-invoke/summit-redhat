#!/bin/bash
# Demo 4 · firmware: versión actual de cada equipo del parque (routers de sucursal y servidores RHEL).
echo "Routers de sucursal (FRR):"
for n in 1 2 3; do
  sudo /usr/local/sbin/lab-sucursal estado suc$n | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f"  {d[\"nodo\"]}: FRR {d[\"version\"]:<8} · BGP con el borde: {d[\"bgp\"]:<11} · imagen {d[\"imagen\"]}")'
done
echo "Servidores RHEL (kernel · parches de seguridad pendientes):"
for h in nodo-so nodo-nginx; do
  printf "  %-11s " "$h:"; ssh $h 'echo "$(uname -r) · $(sudo dnf -q updateinfo list --security 2>/dev/null | wc -l) pendientes"'
done
