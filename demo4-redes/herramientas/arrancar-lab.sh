#!/bin/bash
# Demo 4 · se ejecuta al encender la VM (summit-lab.service): levanta el laboratorio de red de containerlab
# (ISP, router, firewall VyOS, pc-usuario, srv-app) con su configuración base y el DNS del laboratorio.
D=$(cd "$(dirname "$0")/.." && pwd)
for i in $(seq 1 60); do sudo podman info >/dev/null 2>&1 && break; sleep 5; done
bash "$D/herramientas/reiniciar-lab.sh"
sudo podman start summit-dns >/dev/null 2>&1 && echo "DNS del laboratorio: arriba" || echo "DNS del laboratorio: no existe (instalar-dns.sh)"
