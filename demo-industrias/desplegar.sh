#!/bin/bash
# Despliega las aplicaciones de Banca y Telco en el SNO.
set -euo pipefail
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc; D=$(cd "$(dirname "$0")" && pwd)
$OC apply -f $D/openshift/industrias.yaml >/dev/null
$OC -n demo-industrias start-build industrias --from-dir=$D --follow --wait 2>&1 | tail -2
for d in banca telco; do $OC -n demo-industrias rollout status deploy/$d --timeout=300s | tail -1; done
$OC -n demo-industrias get pods
