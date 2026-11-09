#!/bin/bash
# Despliega la Fábrica de código en el SNO y la conecta con FaceOps, Git y Tekton. Ningún secreto se imprime:
#  · token compartido FaceOps ↔ Fábrica en ~/demo-drift/cicd-secretos.txt (fabrica_token) y en /etc/faceops.env
#  · clave de Anthropic y token de Git: los mismos de FaceOps (/etc/faceops.env y ~/faceops/faceops.env)
#  · Secret «fabrica-ocp» en k3s (default) con el token de la cuenta fabrica-pipeline del SNO, para que el pipeline despliegue
set -euo pipefail
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc; D=$(cd "$(dirname "$0")" && pwd)
K="sudo /usr/local/bin/kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml"
SEC=$HOME/demo-drift/cicd-secretos.txt
grep -q '^fabrica_token=' $SEC || echo "fabrica_token=$(openssl rand -hex 32)" >> $SEC
grep -q '^fabrica_sesion=' $SEC || echo "fabrica_sesion=$(openssl rand -hex 32)" >> $SEC
TK=$(grep '^fabrica_token=' $SEC | cut -d= -f2-); SS=$(grep '^fabrica_sesion=' $SEC | cut -d= -f2-)
AK=$(sudo grep -h '^ANTHROPIC_API_KEY=' /etc/faceops.env $HOME/faceops/faceops.env 2>/dev/null | head -1 | cut -d= -f2- || true)
GT=$(grep -h '^FACEOPS_GITEA_ORG_TOKEN=' $HOME/faceops/faceops.env | head -1 | cut -d= -f2-)
[ -n "$AK" ] || AK=$(sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops faceops python3 -c 'from app.boveda import leer; print((leer("integraciones/anthropic") or {}).get("api_key",""))' 2>/dev/null | tail -1 || true)
[ -n "$AK" ] || echo "AVISO: no se encontró ANTHROPIC_API_KEY en FaceOps (la Fábrica no podrá generar código)"
# 1) OpenShift
$OC apply -f $D/openshift/fabrica.yaml >/dev/null
$OC -n fabrica-codigo create secret generic fabrica --from-literal=ANTHROPIC_API_KEY="$AK" --from-literal=FACEOPS_FABRICA_TOKEN="$TK" \
  --from-literal=GITEA_TOKEN="$GT" --from-literal=FABRICA_SESION="$SS" --dry-run=client -o yaml | $OC apply -f - >/dev/null
$OC -n fabrica-codigo start-build fabrica --from-dir=$D --follow --wait 2>&1 | tail -1
$OC -n fabrica-codigo rollout restart deploy/fabrica >/dev/null
$OC -n fabrica-codigo rollout status deploy/fabrica --timeout=300s | tail -1 || { $OC -n fabrica-codigo get pods -o wide | tail -4; $OC -n fabrica-codigo get events --sort-by=.lastTimestamp | tail -8 | cut -c1-220; $OC -n fabrica-codigo logs deploy/fabrica --tail=15 2>&1 | cut -c1-200; }
# 2) Tekton (k3s): credencial del pipeline para construir y desplegar en fabrica-dev
for i in $(seq 1 20); do ST=$($OC -n fabrica-dev get secret fabrica-pipeline-token -o jsonpath='{.data.token}' 2>/dev/null | base64 -d); [ -n "$ST" ] && break; sleep 3; done
SNO_IP=${SNO_IP:-$(sudo virsh domifaddr sno 2>/dev/null | awk '/ipv4/{split($4,a,"/");print a[1]}' | head -1)}
SRV=$($OC whoami --show-server | sed "s#//[^:/]*#//${SNO_IP:-192.168.122.50}#")   # Tekton (k3s) no resuelve el nombre interno del SNO: va por IP
$K -n default create secret generic fabrica-ocp --from-literal=servidor="$SRV" --from-literal=token="$ST" --from-literal=namespace=fabrica-dev \
  --from-literal=dominio=sno.34.176.42.56.sslip.io --dry-run=client -o yaml | $K apply -f - >/dev/null && echo "secret fabrica-ocp en k3s"
$K -n default create secret generic fabrica-git --from-literal=token="$GT" --dry-run=client -o yaml | $K apply -f - >/dev/null && echo "secret fabrica-git en k3s"
# 3) Git: organización de la Fábrica
curl -s -o /dev/null -w "org fabrica %{http_code}\n" -H "Authorization: token $GT" -H "Content-Type: application/json" -X POST http://127.0.0.1:3000/api/v1/orgs -d '{"username":"fabrica","visibility":"private"}' || true
# 4) FaceOps: token, URL y pipeline
for kv in "FACEOPS_FABRICA_TOKEN=$TK" "FACEOPS_FABRICA_URL=https://fabrica.sno.34.176.42.56.sslip.io" "FACEOPS_FABRICA_CLIENTE=lab" "FACEOPS_FABRICA_USUARIO=cquezada"; do
  k=${kv%%=*}; sudo sed -i "/^$k=/d" /etc/faceops.env; echo "$kv" | sudo tee -a /etc/faceops.env >/dev/null
done
sudo systemctl restart faceops && echo "FaceOps con la Fábrica conectada"
