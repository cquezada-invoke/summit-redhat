#!/bin/bash
# Despliega JavIA en el SNO. La clave inicial del administrador se genera una vez y queda en ~/demo-drift/cicd-secretos.txt (javia_clave).
set -euo pipefail
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc; D=$(cd "$(dirname "$0")" && pwd)
SEC=$HOME/demo-drift/cicd-secretos.txt
grep -q '^javia_clave=' $SEC 2>/dev/null || echo "javia_clave=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)" >> $SEC
grep -q '^javia_secreto=' $SEC || echo "javia_secreto=$(openssl rand -hex 32)" >> $SEC
CL=$(grep '^javia_clave=' $SEC | cut -d= -f2-); SK=$(grep '^javia_secreto=' $SEC | cut -d= -f2-)
$OC apply -f $D/openshift/javia.yaml >/dev/null
$OC -n demo-javia create secret generic javia-login --from-literal=JAVIA_ADMIN_USUARIO=cquezada@invoke.cl \
  --from-literal=JAVIA_ADMIN_NOMBRE="Cristian Quezada" --from-literal=JAVIA_ADMIN_CLAVE="$CL" --from-literal=JAVIA_SECRETO="$SK" \
  --dry-run=client -o yaml | $OC apply -f - >/dev/null
$OC -n demo-javia start-build javia --from-dir=$D --follow --wait 2>&1 | tail -2
$OC -n demo-javia rollout restart deploy/javia >/dev/null; $OC -n demo-javia rollout status deploy/javia --timeout=300s | tail -1
$OC -n demo-javia get pods
