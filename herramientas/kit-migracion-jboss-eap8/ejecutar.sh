#!/bin/bash
# Atajo para la automatización «Migración · JBoss a EAP 8».
#   ./ejecutar.sh evaluar <origen>             evalúa el origen (no cambia nada); informe en informes/<origen>/evaluacion.md
#   ./ejecutar.sh migrar  <origen> [destino]   migra las aplicaciones del origen al destino (por defecto nodo-jboss)
# Variables opcionales: APPS="pedidos.war,otra.war" (solo esas aplicaciones) · DS_CLAVE (clave de los datasources en EAP 8)
set -euo pipefail
cd "$(dirname "$0")"
modo=${1:-}; origen=${2:-}; destino=${3:-nodo-jboss}
case "$modo" in
  evaluar) ansible-playbook migrar-jboss-eap8.yml -l "$origen" -e mj_modo=evaluar ${APPS:+-e mj_apps=$APPS} ;;
  migrar)  [ -n "${DS_CLAVE:-}" ] || { read -r -s -p "Clave para los datasources en EAP 8: " DS_CLAVE; echo; }
           ansible-playbook migrar-jboss-eap8.yml -l "$origen,$destino" -e mj_modo=migrar -e mj_destino="$destino" \
             ${APPS:+-e mj_apps=$APPS} -e mj_ds_clave="$DS_CLAVE" ;;
  *) sed -n '2,5p' "$0"; exit 1 ;;
esac
