#!/bin/bash
# OpenShift Single Node (SNO) en el KVM del stand, con el instalador Agent-based. Paso 1: preparar (sin pull secret):
# binarios, red y DNS en libvirt (IP fija por MAC), carpeta de instalación. El paso 2 (instalar-sno.sh) necesita el pull secret.
set -euo pipefail
VER=${OCP_VERSION:-stable-4.19}
D=$HOME/openshift/sno; mkdir -p "$D/bin"; cd "$D"
NOMBRE=sno; DOMINIO=invoke.lab; MAC=52:54:00:5e:00:5e; IP=192.168.122.50
echo "== binarios ($VER)"
for f in openshift-install-linux.tar.gz openshift-client-linux.tar.gz; do
  [ -f "$f" ] || curl -sSfLO "https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/$VER/$f"
  tar xzf "$f" -C bin
done
bin/openshift-install version | head -2
sudo dnf -y -q install nmstate >/dev/null 2>&1 || true
echo "== red: IP fija $IP para $MAC y DNS api / *.apps"
if ! sudo virsh net-dumpxml default | grep -q "$MAC"; then
  sudo virsh net-update default add ip-dhcp-host "<host mac='$MAC' name='$NOMBRE' ip='$IP'/>" --live --config
fi
# libvirt no tiene comodín ni varios registros por IP: un solo registro con todos los nombres que usa el clúster
NOMBRES=""
for h in api api-int console-openshift-console.apps oauth-openshift.apps canary-openshift-ingress-canary.apps downloads-openshift-console.apps \
         grafana-openshift-monitoring.apps prometheus-k8s-openshift-monitoring.apps; do NOMBRES="$NOMBRES<hostname>$h.$NOMBRE.$DOMINIO</hostname>"; done
sudo virsh net-update default delete dns-host "<host ip='$IP'/>" --live --config >/dev/null 2>&1 || true
sudo virsh net-update default add dns-host "<host ip='$IP'>$NOMBRES</host>" --live --config
grep -q "api.$NOMBRE.$DOMINIO" /etc/hosts || echo "$IP api.$NOMBRE.$DOMINIO console-openshift-console.apps.$NOMBRE.$DOMINIO oauth-openshift.apps.$NOMBRE.$DOMINIO" | sudo tee -a /etc/hosts >/dev/null
echo "== listo para instalar: falta el pull secret en ~/faceops/pull-secret.txt"
