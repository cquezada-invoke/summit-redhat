#!/bin/bash
# Prueba el camino de datos desde pc-usuario (detrás del FortiGate): Internet (ISP simulado) y la app en srv-app.
# srv-app:8080 solo responde si el FortiGate tiene la regla pedida con "Red · Solicitar acceso" (caso 1).
for i in $(seq 1 15); do sudo podman exec clab-summit-red-pc-usuario ip -4 a show eth1 2>/dev/null | grep -q 10.40.40 && break; sleep 1; done
p(){ for i in 1 2 3; do sudo podman exec clab-summit-red-pc-usuario "$@" >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }
r(){ for i in 1 2 3; do sudo podman exec clab-summit-red-rtr "$@" >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }
printf "router → Internet (203.0.113.1)    : "; r ping -c2 -W1 203.0.113.1 && echo OK || echo FALLA
printf "pc-usuario → Internet (vía FortiGate): "; p ping -c2 -W1 203.0.113.1 && echo OK || echo "FALLA (¿FortiGate sin licencia?)"
printf "pc-usuario → srv-app:8080        : "; p wget -qO- -T3 http://10.10.10.10:8080 && echo "OK (hay regla en el FortiGate)" || echo "BLOQUEADO por el FortiGate (sin regla)"
printf "BGP router ↔ ISP                 : "
sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null \
 | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("ipv4Unicast",{}).get("peers",{}).get("10.0.0.1",{}).get("state","SIN VECINO"))'
