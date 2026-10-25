#!/bin/bash
# Devuelve el laboratorio al estado limpio (configuración base de este repositorio) en segundos.
# Si el FortiGate ya está corriendo no se vuelve a desplegar (arrancarlo toma minutos y perdería su licencia):
#   · el router y el ISP recargan su configuración base de FRR
#   · el FortiGate queda solo con su regla base (se borran las reglas y objetos creados en la demo)
# Con "--completo" se redespliega todo el laboratorio.
set -e
D=$(cd "$(dirname "$0")/.." && pwd); L=/opt/summit-red
if [ "${1:-}" = --completo ] || ! sudo podman ps --format '{{.Names}}' | grep -qx clab-summit-red-fgt; then
  exec bash "$D/instalar-fgt.sh" desplegar
fi
# FRR: configuración base del repositorio
for n in rtr isp; do
  sudo cp "$D/lab/config/$n.conf" $L/config/$n.conf
  sudo podman exec clab-summit-red-$n sh -c 'python3 /usr/lib/frr/frr-reload.py --reload /etc/frr/frr.conf' >/dev/null 2>&1 \
    || echo "   aviso: no se pudo recargar FRR en $n"
done
# FortiGate: borra lo creado por "Red · Solicitar acceso" (políticas 100-199, objetos acceso-* y servicios tcp-*)
F="ssh -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -i $HOME/demo4/llave-lab admin@172.20.20.12"
{ echo "config firewall policy"; for i in $(seq 100 199); do echo "delete $i"; done; echo "end"; } | $F >/dev/null 2>&1 || true
OBJ=$($F "show firewall address" 2>/dev/null | sed -n 's/^ *edit "\(acceso-[^"]*\)".*/\1/p')
SRV=$($F "show firewall service custom" 2>/dev/null | sed -n 's/^ *edit "\(tcp-[0-9]*\)".*/\1/p')
{ echo "config firewall address"; for o in $OBJ; do echo "delete \"$o\""; done; echo "end"
  echo "config firewall service custom"; for s in $SRV; do echo "delete \"$s\""; done; echo "end"; } | $F >/dev/null 2>&1 || true
for i in $(seq 1 30); do
  sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null | grep -q Established && break; sleep 2
done
bash "$D/herramientas/probar-red.sh"
