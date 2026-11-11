#!/bin/bash
# Construye la IMAGEN DORADA de la Demo instantánea en GCP (familia «faceops-demo»). NO se ha ejecutado: crea recursos con costo.
#
#   PROYECTO=master-ia-320218 ZONA=southamerica-west1-a bash herramientas/demo-gcp/construir-imagen.sh
#
# 1) VM temporal Rocky Linux 9 (sin costo de licencia) con virtualización anidada, n2-standard-8, 100 GB pd-balanced.
# 2) Copia y corre preparar-vm.sh dentro (k3s + AWX + EDA comunitarios, FaceOps, Gitea, Vault, MinIO, Prometheus/Grafana,
#    KVM/libvirt con 3 VMs Rocky anidadas, Caddy y el agente de uso). ~40–60 min.
# 3) Generaliza (borra llaves SSH, tokens, machine-id, credenciales de la construcción), apaga y crea la imagen en la familia.
# 4) Elimina la VM temporal. Cada imagen nueva queda como la última de la familia: FaceAdmin usa «familia:faceops-demo».
set -euo pipefail
PROYECTO=${PROYECTO:?indica PROYECTO}; ZONA=${ZONA:-southamerica-west1-a}; TIPO=${TIPO:-n2-standard-8}
FAMILIA=${FAMILIA:-faceops-demo}; VM=${VM:-demo-constructor-$(date +%Y%m%d%H%M)}; D=$(cd "$(dirname "$0")" && pwd)
G="gcloud --project=$PROYECTO"

echo "· VM temporal $VM ($TIPO, Rocky Linux 9, anidada)"
$G compute instances create "$VM" --zone="$ZONA" --machine-type="$TIPO" --min-cpu-platform="Intel Cascade Lake" \
  --enable-nested-virtualization --image-family=rocky-linux-9-optimized-gcp --image-project=rocky-linux-cloud \
  --boot-disk-size=100GB --boot-disk-type=pd-balanced --labels=faceadmin-demo=constructor --tags=faceadmin-demo \
  --max-run-duration=6h --instance-termination-action=DELETE   # tope de seguridad: si algo falla, no queda corriendo

trap '$G compute instances delete "$VM" --zone="$ZONA" --quiet >/dev/null 2>&1 || true' EXIT
for i in $(seq 1 30); do $G compute ssh "$VM" --zone="$ZONA" --command=true --quiet 2>/dev/null && break; sleep 10; done

echo "· preparación (k3s, AWX, EDA, FaceOps, servicios, KVM y agente)"
$G compute scp --zone="$ZONA" "$D/preparar-vm.sh" "$D/agente-uso.sh" "$D/primer-arranque.sh" "$VM":/tmp/
$G compute ssh "$VM" --zone="$ZONA" --command="sudo bash /tmp/preparar-vm.sh"

echo "· generalizar y apagar"
$G compute ssh "$VM" --zone="$ZONA" --command="sudo bash -c 'rm -f /etc/ssh/ssh_host_* /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; \
  truncate -s0 /etc/machine-id; rm -f /var/lib/demo/credenciales.listo; cloud-init clean --logs 2>/dev/null || true; history -c; sync'" || true
$G compute instances stop "$VM" --zone="$ZONA"

IMG="$FAMILIA-$(date +%Y%m%d-%H%M)"
echo "· imagen $IMG (familia $FAMILIA)"
$G compute images create "$IMG" --source-disk="$VM" --source-disk-zone="$ZONA" --family="$FAMILIA" \
  --storage-location=southamerica-west1 --guest-os-features=GVNIC,UEFI_COMPATIBLE --labels=faceadmin-demo=imagen \
  --licenses="https://www.googleapis.com/compute/v1/projects/vm-options/global/licenses/enable-vmx"
echo "Lista: imagen $IMG. En FaceAdmin › Demo instantánea en GCP usa «familia:$FAMILIA»."
