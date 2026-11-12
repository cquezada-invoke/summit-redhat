#!/bin/bash
# Espera el despliegue del commit 00f155b7 y corre el plan «Nube · Recuperar consumo y estado»
D=~/faceops-autodeploy
for i in $(seq 1 150); do
  DEP=$(cat $D/desplegado 2>/dev/null)
  git -C ~/summit-redhat merge-base --is-ancestor 00f155b7 "$DEP" 2>/dev/null && break
  sleep 12
done
echo "desplegado: ${DEP:0:8} · $(cat $D/estado)"
grep -E "Recuperar consumo|Traceback|^ERROR" $D/configurar.log | grep -v -i -E "clave|password|token|secret" | head -5
sleep 30
bash ~/summit-redhat/herramientas/pruebas/p-nube-consumo.sh 2>&1 | grep -v -i -E "private_key|client_secret" | cut -c1-2500
