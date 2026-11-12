#!/bin/bash
c=$(curl -sk -o /dev/null -w '%{http_code}' https://localhost/api/controller/v2/ping/); echo "AAP ping $c · $(cat ~/faceops-autodeploy/estado)"
bash ~/summit-redhat/herramientas/pruebas/p-nube-consumo.sh 2>&1 | grep -v -i -E "private_key|client_secret" | cut -c1-1800
