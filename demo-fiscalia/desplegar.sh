#!/bin/bash
# Despliega la demo de denuncias en el SNO: build de la imagen, carga del histórico al lake y las dos aplicaciones.
set -euo pipefail
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc; D=$(cd "$(dirname "$0")" && pwd)
$OC apply -f $D/openshift/demo.yaml >/dev/null
$OC -n demo-fiscalia start-build denuncias --from-dir=$D --follow --wait 2>&1 | tail -3
$OC -n demo-fiscalia delete job carga --ignore-not-found >/dev/null; $OC apply -f $D/openshift/carga-job.yaml >/dev/null
$OC -n demo-fiscalia wait --for=condition=complete job/carga --timeout=400s | tail -1; $OC -n demo-fiscalia logs job/carga | tail -12
$OC -n demo-fiscalia rollout status deploy/registro --timeout=300s | tail -1; $OC -n demo-fiscalia rollout status deploy/inteligencia --timeout=300s | tail -1
$OC -n demo-fiscalia get pods; $OC adm top node | tail -1
