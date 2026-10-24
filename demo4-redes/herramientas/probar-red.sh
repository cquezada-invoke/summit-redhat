#!/bin/bash
# Prueba el camino de datos desde pc-usuario: Internet (ISP simulado) y la app en srv-app.
p(){ sudo podman exec clab-summit-red-pc-usuario "$@" >/dev/null 2>&1; }
printf "pc-usuario → Internet (203.0.113.1): "; p ping -c2 -W1 203.0.113.1 && echo OK || echo FALLA
printf "pc-usuario → srv-app:8080        : "; p wget -qO- -T3 http://10.10.10.10:8080 && echo OK || echo FALLA
printf "BGP router ↔ ISP                 : "
sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null \
 | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("ipv4Unicast",{}).get("peers",{}).get("10.0.0.1",{}).get("state","SIN VECINO"))'
