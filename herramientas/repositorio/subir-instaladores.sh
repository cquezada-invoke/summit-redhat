#!/bin/bash
# Sube al registro de paquetes de Gitea (genéricos de la organización «repositorio») las ISO, imágenes base e instaladores del laboratorio.
# Idempotente: lo que ya está (mismo nombre y versión) no se vuelve a subir. Uso: subir-instaladores.sh (corre en la VM).
set -u
T=$(cat ~/faceops/respaldo-token)
R=http://127.0.0.1:3000/api/packages/repositorio/generic
subir() {  # paquete versión archivo
  local p=$1 v=$2 f=$3 n; n=$(basename "$f")
  sudo test -f "$f" || { echo "falta $f"; return; }
  if curl -sf -o /dev/null -H "Authorization: token $T" "http://127.0.0.1:3000/api/v1/packages/repositorio/generic/$p/$v"; then echo "ya está: $p $v $n"; return; fi
  echo "subiendo $p $v $n ($(sudo du -h "$f" | cut -f1))…"
  # curl lee el archivo directo (con su tamaño): por tubería, los archivos de más de 4 GB se cortan
  sudo curl -sf -X PUT -H "Authorization: token $T" -T "$f" "$R/$p/$v/$n" -o /dev/null && echo "  listo" || echo "  ERROR $n"
}
subir imagen-rhel9-base         9.8      /var/lib/libvirt/images/rhel9-base.qcow2
subir imagen-ubuntu-2404-base   24.04    /var/lib/libvirt/images/ubuntu-2404-base.qcow2
subir iso-windows-server-2022   eval     /var/lib/libvirt/images/windows/ws2022-eval.iso
subir imagen-rhel9-kvm          9        ~/descargas/rhel9-kvm.qcow2
subir aap-bundle                2.7-11   ~/descargas/aap-2.7-bundle.tar.gz
subir fortigate-vm-kvm          7.6.8    ~/FGT_VM64_KVM-v7.6.8.M-build3744-FORTINET.out.kvm.zip
subir iso-vyos                  rolling  /opt/vyos-build/vyos.iso
subir github-actions-runner     2.338.0  ~/actions-runner/actions-runner-linux-x64-2.338.0.tar.gz
echo FIN
