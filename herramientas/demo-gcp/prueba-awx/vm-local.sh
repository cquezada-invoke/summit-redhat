#!/bin/bash
# VM de prueba Rocky Linux 9 aislada, en QEMU de usuario (sin libvirt, sin red del laboratorio: solo NAT de usuario y
# SSH en 127.0.0.1:2299). Alternativa a la VM de GCP cuando no se puede inyectar una llave SSH en la VM de GCP.
#   vm-local.sh crear | ssh <cmd> | estado | eliminar
set -euo pipefail
D=$HOME/awx-prueba; PUERTO=2299
mkdir -p "$D"; cd "$D"
QEMU=$(command -v qemu-kvm || echo /usr/libexec/qemu-kvm)
s() { ssh -q -i "$D/id" -p $PUERTO -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o BatchMode=yes prueba@127.0.0.1 "$@"; }
case "${1:-estado}" in
crear)
  [ -f base.qcow2 ] || curl -sfL -o base.qcow2 https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2
  [ -f id ] || ssh-keygen -q -t ed25519 -N '' -f id -C prueba-awx
  [ -f disco.qcow2 ] || qemu-img create -q -f qcow2 -F qcow2 -b base.qcow2 disco.qcow2 80G
  mkdir -p seed
  printf 'instance-id: awx-prueba\nlocal-hostname: awx-prueba\n' > seed/meta-data
  printf '#cloud-config\nusers:\n  - name: prueba\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    ssh_authorized_keys: ["%s"]\ngrowpart: {mode: auto, devices: ["/"]}\n' "$(cat id.pub)" > seed/user-data
  (setsid python3 -m http.server 8799 --bind 127.0.0.1 --directory seed >/dev/null 2>&1 & echo $! > seed.pid)
  [ -f qemu.pid ] && kill -0 "$(cat qemu.pid)" 2>/dev/null || nice -n 10 "$QEMU" -name awx-prueba -machine accel=kvm -cpu host -smp 4 -m 14336 \
    -drive file=disco.qcow2,if=virtio -netdev user,id=n0,hostfwd=tcp:127.0.0.1:$PUERTO-:22 -device virtio-net-pci,netdev=n0 \
    -smbios "type=1,serial=ds=nocloud-net;s=http://10.0.2.2:8799/" -display none -daemonize -pidfile qemu.pid
  for i in $(seq 1 60); do s true && break; sleep 5; done
  s true && echo "VM lista (ssh)" || { echo "VM sin SSH"; exit 1; }
  kill "$(cat seed.pid)" 2>/dev/null || true ;;
ssh) shift; s "$@" ;;
estado) [ -f qemu.pid ] && kill -0 "$(cat qemu.pid)" 2>/dev/null && echo "VM encendida" || echo "VM apagada" ;;
eliminar)
  [ -f qemu.pid ] && kill "$(cat qemu.pid)" 2>/dev/null || true; sleep 3
  rm -rf "$D"; echo "VM y disco eliminados" ;;
esac
