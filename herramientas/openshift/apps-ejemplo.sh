#!/bin/bash
# Sube las apps de ejemplo del Catálogo en línea a Gitea (invoke/catalogo-web y invoke/catalogo-ws). Idempotente.
set -euo pipefail
R=$(cd "$(dirname "$0")/../.." && pwd)
U=$(grep '^gitea_usuario=' ~/demo-drift/cicd-secretos.txt | cut -d= -f2); C=$(grep '^gitea_clave=' ~/demo-drift/cicd-secretos.txt | cut -d= -f2)
for app in catalogo-web catalogo-ws; do
  curl -sf -o /dev/null -u "$U:$C" http://127.0.0.1:3000/api/v1/repos/invoke/$app || \
    curl -sf -o /dev/null -u "$U:$C" -H 'Content-Type: application/json' -X POST http://127.0.0.1:3000/api/v1/admin/users/invoke/repos \
      -d "{\"name\":\"$app\",\"private\":false,\"default_branch\":\"main\",\"description\":\"Catálogo en línea · $app (código de ejemplo)\"}"
  T=$(mktemp -d); cp -r $R/faceops/demo-apps/$app/. $T/; cd $T
  git init -q -b main && git add -A && git -c user.name='Cristian Quezada' -c user.email=cquezada@invoke.cl commit -qm "Código inicial de $app"
  git -c credential.helper= push -q -f "http://$U:$C@127.0.0.1:3000/invoke/$app.git" main 2>&1 | grep -v "^remote" || true
  cd - >/dev/null; rm -rf $T
  echo "   · invoke/$app"
done
