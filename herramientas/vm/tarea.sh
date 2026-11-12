#!/bin/bash
# Repetir el plan «Ciclo de vida · Evaluar destino» (C1 vino vacío con la máquina recién encendida). Sin secretos.
sed -n '1,16p' ~/summit-redhat/herramientas/pruebas/p-varios.sh > /tmp/p-cv.sh
bash /tmp/p-cv.sh 2>&1 | grep -E "^(CASO|CERT|C[0-9]|energía)" | grep -v -i -E "token|authorization|password" | cut -c1-300; rm -f /tmp/p-cv.sh
