#!/bin/bash
# Despliega (o actualiza) el portal de Claro PR en OpenShift. Se corre en el laboratorio desde portal-automatizacion/.
# La imagen se construye en el laboratorio y se sube al registro interno (el nodo SNO no tiene memoria libre para builds).
set -e
export KUBECONFIG=~/openshift/sno/instalar/auth/kubeconfig; OC=~/openshift/sno/bin/oc
$OC apply -f openshift/portal.yaml >/dev/null
sudo cat /etc/portal-automatizacion.env | $OC -n portal-claro create secret generic portal-claro-env --from-env-file=/dev/stdin --dry-run=client -o yaml | $OC apply -f - >/dev/null
R=$($OC -n openshift-image-registry get route default-route -o jsonpath='{.spec.host}')
grep -q " $R" /etc/hosts || echo "192.168.122.50 $R" | sudo tee -a /etc/hosts >/dev/null
sudo podman build -q -t localhost/portal-automatizacion:latest -f Containerfile . >/dev/null
$OC -n portal-claro create token builder --duration=10m | sudo podman login --tls-verify=false -u builder --password-stdin $R >/dev/null
sudo podman tag localhost/portal-automatizacion:latest $R/portal-claro/portal:latest
sudo podman push -q --tls-verify=false $R/portal-claro/portal:latest
$OC -n portal-claro rollout status deploy/portal --timeout=180s | tail -1
