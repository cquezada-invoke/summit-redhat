# FaceOps · lectura de un equipo de red por SSH (solo lectura; sin Python en el equipo). Cada sección empieza con
# «@@@NOMBRE»; lo que el equipo no tenga sale vacío. La normaliza files/red_normalizar.py en el nodo de ejecución.
S=""; [ "$(id -u)" = 0 ] || S="sudo -n"
m(){ echo "@@@$1"; }
m HOST; hostname
m UPTIME; cat /proc/uptime
m EQUIPO; cat /etc/faceops-equipo 2>/dev/null
m VYOS; cat /usr/share/vyos/version.json 2>/dev/null
m FRR; $S vtysh -c 'show version' 2>/dev/null | head -2
m LINK; ip -j -s -d link show 2>/dev/null
m ADDR; ip -j addr show 2>/dev/null
m VLAN; bridge -j vlan show 2>/dev/null
m LLDP; $S lldpcli -f json0 show neighbors details 2>/dev/null
m RUTAS; $S vtysh -c 'show ip route json' 2>/dev/null
m RUTASK; ip -j route show 2>/dev/null
m BGP; $S vtysh -c 'show bgp summary json' 2>/dev/null
m HAPROXY; haproxy -v 2>/dev/null | head -1
m HASTAT; wget -qO- -T 3 'http://127.0.0.1:8404/stats;csv' 2>/dev/null || curl -s -m 3 'http://127.0.0.1:8404/stats;csv' 2>/dev/null
m FIN
