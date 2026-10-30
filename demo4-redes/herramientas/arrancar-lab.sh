#!/bin/bash
# Demo 4 · se ejecuta al encender la VM (summit-lab.service): levanta el laboratorio de red de containerlab
# (ISP, router, firewall VyOS, pc-usuario, srv-app, routers de sucursal) con su configuración base y el DNS del laboratorio.
D=$(cd "$(dirname "$0")/.." && pwd)
for i in $(seq 1 60); do sudo podman info >/dev/null 2>&1 && break; sleep 5; done
bash "$D/herramientas/reiniciar-lab.sh"
# Routers de sucursal (si reiniciar-lab.sh redesplegó todo el laboratorio no pasó por ellos)
bash "$D/herramientas/sucursales-arriba.sh"
# Switches (Nokia SR Linux, spine-leaf)
# (apagados para ahorrar recursos; levantar a mano cuando se usen) bash "$D/herramientas/switches-arriba.sh"
sudo podman start summit-dns >/dev/null 2>&1 && echo "DNS del laboratorio: arriba" || echo "DNS del laboratorio: no existe (instalar-dns.sh)"
# Activaciones de EDA que hayan quedado en error al encender la VM
python3 "$D/../herramientas/reactivar-eda.py"
