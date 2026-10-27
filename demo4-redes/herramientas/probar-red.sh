#!/bin/bash
# Prueba el camino de datos desde pc-usuario (detrás del firewall VyOS): Internet (ISP simulado) y la app en srv-app.
# srv-app:8080 y :8443 solo responden si el firewall tiene la regla pedida con "Red · Solicitar acceso" (caso 1).
for i in $(seq 1 15); do sudo podman exec clab-summit-red-pc-usuario ip -4 a show eth1 2>/dev/null | grep -q 10.40.40 && break; sleep 1; done
p(){ for i in 1 2 3; do sudo podman exec clab-summit-red-pc-usuario "$@" >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }
r(){ for i in 1 2 3; do sudo podman exec clab-summit-red-rtr "$@" >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }
printf "router → Internet (203.0.113.1)    : "; r ping -c2 -W1 203.0.113.1 && echo OK || echo FALLA
printf "pc-usuario → Internet (vía firewall)  : "; p ping -c2 -W1 203.0.113.1 && echo OK || echo "FALLA (revisar el firewall)"
printf "pc-usuario → srv-app:8080        : "; p wget -qO- -T3 http://10.10.10.10:8080 && echo "OK (hay regla en el firewall)" || echo "BLOQUEADO por el firewall (sin regla)"
printf "pc-usuario → srv-app:8443        : "; p wget -qO- -T3 http://10.10.10.10:8443 && echo "OK (hay regla en el firewall)" || echo "BLOQUEADO por el firewall (sin regla)"
printf "BGP router ↔ ISP                 : "
sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null \
 | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("ipv4Unicast",{}).get("peers",{}).get("10.0.0.1",{}).get("state","SIN VECINO"))'
printf "BGP router ↔ sucursales         : "
sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null \
 | python3 -c 'import sys,json;p=json.load(sys.stdin).get("ipv4Unicast",{}).get("peers",{});print(" · ".join("suc%d %s" % (n, p.get("172.20.20.4%d" % n,{}).get("state","SIN VECINO")) for n in (1,2,3)))'
