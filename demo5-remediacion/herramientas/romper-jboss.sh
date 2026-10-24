#!/bin/bash
# Caso 1 (riesgo bajo): JBoss se detiene, como si se hubiera caído.
source "$(dirname "$0")/comun.sh"
$SSHJ sudo systemctl stop eap8-standalone
echo "JBoss detenido en nodo-jboss."
estado_app
echo "En unos 15 s el vigía avisa a EDA → AAP lanza 'Remediación · Reiniciar JBoss' (sin aprobación)."
echo "Ver en AAP: $AAP_URL/execution/jobs  ·  la app vuelve en 1 a 2 minutos."
