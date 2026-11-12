#!/bin/bash
# Espera a que AAP responda (ping) y corre el plan «Nube · Recuperar consumo y estado»
for i in $(seq 1 120); do
  c=$(curl -sk -o /dev/null -w '%{http_code}' https://localhost/api/controller/v2/ping/); [ "$c" = 200 ] && break; sleep 20
done
echo "AAP ping $c tras $i intentos · $(cat ~/faceops-autodeploy/estado)"
grep -E "^ERROR" ~/faceops-autodeploy/configurar.log | cut -c1-200 | head -3
sleep 60
bash ~/summit-redhat/herramientas/pruebas/p-nube-consumo.sh 2>&1 | grep -v -i -E "private_key|client_secret" | cut -c1-2500
