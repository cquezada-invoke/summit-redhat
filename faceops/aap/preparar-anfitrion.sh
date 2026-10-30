#!/bin/bash
# FaceOps · prepara el anfitrión KVM (la VM del stand) para que AAP cree servidores on-premise. Idempotente.
#  · Usuario aap-kvm: AAP entra por SSH con su llave y solo puede usar virsh y virt-install con sudo.
#  · /var/lib/libvirt/images/faceops: discos, cloud-init y respuestas de Windows de los servidores que crea FaceOps.
#  · Imágenes base: RHEL 9 (ya existe) y Ubuntu 24.04 (imagen cloud oficial de Canonical).
set -euo pipefail
D=/var/lib/libvirt/images/faceops
id aap-kvm >/dev/null 2>&1 || sudo useradd -m -c "AAP · creación de servidores (FaceOps)" aap-kvm
sudo install -d -o aap-kvm -g aap-kvm -m 0755 $D
sudo install -d -o aap-kvm -g aap-kvm -m 0700 $D/claves
# OpenShift · Habilitar clúster: carpeta de cada clúster (instalador, kubeconfig) y PyYAML para el generador de manifiestos
sudo install -d -o aap-kvm -g aap-kvm -m 0700 /var/lib/faceops-openshift
python3 -c "import yaml" 2>/dev/null || sudo dnf -q -y install python3-pyyaml
echo 'aap-kvm ALL=(root) NOPASSWD: /usr/bin/virsh, /usr/bin/virt-install' | sudo tee /etc/sudoers.d/aap-kvm >/dev/null
sudo chmod 0440 /etc/sudoers.d/aap-kvm && sudo visudo -cq
# Llave de AAP para entrar como aap-kvm (la privada queda en ~/faceops, 0600, y se carga en AAP como credencial)
[ -f ~/faceops/aap-kvm ] || ssh-keygen -q -t ed25519 -N '' -C 'aap-kvm (FaceOps)' -f ~/faceops/aap-kvm
sudo install -d -o aap-kvm -g aap-kvm -m 0700 /home/aap-kvm/.ssh
sudo install -o aap-kvm -g aap-kvm -m 0600 ~/faceops/aap-kvm.pub /home/aap-kvm/.ssh/authorized_keys
# Llave pública de los nodos (usuario ansible): los servidores nuevos la reciben por cloud-init
sudo install -o aap-kvm -g aap-kvm -m 0644 ~/.ssh/id_demo.pub $D/llave.pub
sudo install -o aap-kvm -g aap-kvm -m 0644 "$(dirname "$0")/../../datacenter/plantillas/autounattend-windows.xml" $D/autounattend-windows.xml
command -v genisoimage >/dev/null || sudo dnf -q -y install genisoimage
# Imagen base de Ubuntu 24.04
U=/var/lib/libvirt/images/ubuntu-2404-base.qcow2
if [ ! -f $U ]; then
  curl -fsSL -o /tmp/ubuntu-2404.img https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
  sudo mv /tmp/ubuntu-2404.img $U && sudo chmod 0644 $U
fi
sudo chmod 0644 /var/lib/libvirt/images/rhel9-base.qcow2 2>/dev/null || true
echo "   · anfitrión KVM listo (usuario aap-kvm, imágenes RHEL 9 y Ubuntu 24.04)"
