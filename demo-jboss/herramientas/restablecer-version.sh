#!/bin/bash
# Demo · deja ambos JBoss como estaban en un commit del manifiesto (repo ambientes): versión del .war + configuración.
# Uso: restablecer-version.sh            → muestra los últimos commits del manifiesto para elegir
#      restablecer-version.sh <commit>   → restablece a ese commit
set -e; source "$(dirname "$0")/comun.sh"
if [ -z "${1:-}" ]; then
  echo "Últimos estados de producción (repo ambientes):"
  curl -s -H "Authorization: token $GT" "http://127.0.0.1:3000/api/v1/repos/invoke/ambientes/commits?limit=10" \
    | python3 -c 'import sys,json;[print("  ",c["sha"][:10],c["created"][:16].replace("T"," "),"·",c["commit"]["message"].strip().splitlines()[0]) for c in json.load(sys.stdin)]'
  echo "Uso: $0 <commit>"; exit 0
fi
echo "Antes:"; estado
lanzar "JBoss · Restablecer a versión" "{\"ambiente\":\"produccion\",\"manifiesto_ref\":\"$1\"}"
echo "Después:"; estado
