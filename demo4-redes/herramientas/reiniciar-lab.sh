#!/bin/bash
# Devuelve el laboratorio al estado limpio (configuración base de este repositorio) en segundos.
# Si el firewall ya está corriendo no se redespliega:
#   · el router y el ISP recargan su configuración base de FRR
#   · el firewall VyOS queda solo con su política base (se borran las reglas 100-199 y los grupos creados en la demo)
# Con "--completo" se redespliega todo el laboratorio.
set -e
D=$(cd "$(dirname "$0")/.." && pwd); L=/opt/summit-red
if [ "${1:-}" = --completo ] || ! sudo podman ps --format '{{.Names}}' | grep -qx clab-summit-red-fw; then
  exec bash "$D/instalar-fw.sh" desplegar
fi
# FRR: configuración base del repositorio
for n in rtr isp; do
  sudo cp "$D/lab/config/$n.conf" $L/config/$n.conf
  sudo podman exec clab-summit-red-$n sh -c 'python3 /usr/lib/frr/frr-reload.py --reload /etc/frr/frr.conf' >/dev/null 2>&1 \
    || echo "   aviso: no se pudo recargar FRR en $n"
done
# srv-app: asegura el segundo servicio de prueba (:8443) aunque el laboratorio venga de una topología anterior
sudo podman exec clab-summit-red-srv-app sh -c 'wget -qO- -T2 http://127.0.0.1:8443 >/dev/null 2>&1 || (cd /www && setsid python3 -m http.server 8443 >/dev/null 2>&1 &)' || true
# Firewall: borra lo creado por "Red · Solicitar acceso"
sudo cp "$D/lab/config/fw-limpiar.vbash" $L/config/fw-limpiar.vbash
sudo podman exec clab-summit-red-fw sg vyattacfg -c "/bin/vbash /usr/local/bin/fw-limpiar.vbash" >/dev/null 2>&1 || true
for i in $(seq 1 30); do
  sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null | grep -q Established && break; sleep 2
done
bash "$D/herramientas/probar-red.sh"
