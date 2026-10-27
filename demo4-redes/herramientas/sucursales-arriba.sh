#!/bin/bash
# Demo 4 · firmware: deja los routers de sucursal (un laboratorio summit-sucN por router) arriba y en FRR 9.1, la versión "antigua" de la demo.
# Lo llaman reiniciar-lab.sh y arrancar-lab.sh (al encender la VM los contenedores quedan detenidos y se redespliegan).
L=/opt/summit-red
if [ -x /usr/local/sbin/lab-sucursal ]; then
  for n in 1 2 3; do
    if [ "$(sudo podman inspect -f '{{.State.Running}}' suc$n 2>/dev/null)" != true ]; then
      (cd $L && sudo containerlab deploy -t $L/suc$n.clab.yml --reconfigure >/tmp/summit-suc$n-deploy.log 2>&1) \
        || echo "   aviso: no se pudo desplegar suc$n (/tmp/summit-suc$n-deploy.log)"
    fi
  done
  for n in 1 2 3; do
    sudo /usr/local/sbin/lab-sucursal estado suc$n 2>/dev/null | grep -q '"imagen":"quay.io/frrouting/frr:9.1.0"' \
      || sudo /usr/local/sbin/lab-sucursal actualizar suc$n quay.io/frrouting/frr:9.1.0 >/dev/null 2>&1 \
      || echo "   aviso: suc$n no volvió a FRR 9.1"
  done
fi
