#!/bin/bash
# Crea la VM nodo-windows (Windows Server 2022 de evaluación, 180 días) en libvirt con instalación desatendida.
# Deja WinRM (5985, NTLM, solo red interna 192.168.122.0/24 de libvirt) y el usuario de servicio "ansible".
# Claves aleatorias: ~/demo-drift/cicd-secretos.txt (windows_ansible_clave, windows_admin_clave) y Vault secret/windows.
# Uso: instalar-windows.sh            (idempotente: si la VM existe, no hace nada)
#      instalar-windows.sh --rehacer  (borra y vuelve a crear la VM)
set -e
D=$(cd "$(dirname "$0")" && pwd); W=/var/lib/libvirt/images/windows; S=~/demo-drift/cicd-secretos.txt
VM=nodo-windows; MAC=52:54:00:de:00:17; IP=192.168.122.17
ISO=$W/ws2022-eval.iso
URL="https://go.microsoft.com/fwlink/p/?LinkID=2195280&clcid=0x409&culture=en-us&country=US"
if [ "${1:-}" = --rehacer ]; then sudo virsh destroy $VM 2>/dev/null || true; sudo virsh undefine $VM --remove-all-storage 2>/dev/null || true; fi
if sudo virsh dominfo $VM >/dev/null 2>&1; then echo "$VM ya existe"; exit 0; fi
sudo mkdir -p $W && sudo chown "$USER" $W
[ -s $ISO ] && [ "$(stat -c %s $ISO)" -gt 4000000000 ] || curl -sL -o $ISO "$URL"
# Claves (se generan una vez; no se imprimen)
for k in windows_ansible_clave windows_admin_clave; do
  grep -q "^$k=" $S || echo "$k=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)Aa1!" >> $S
done
CA=$(sed -n 's/^windows_ansible_clave=//p' $S); CD=$(sed -n 's/^windows_admin_clave=//p' $S)
# Vault: secret/windows (usuario y clave de la cuenta de servicio)
VT=$(python3 -c "import json;print(json.load(open('$HOME/demo-drift/vault-init.json'))['root_token'])" 2>/dev/null || true)
[ -n "$VT" ] && curl -sk -o /dev/null -H "X-Vault-Token: $VT" -X POST https://127.0.0.1:8200/v1/secret/data/windows \
  -d "{\"data\":{\"usuario\":\"ansible\",\"clave\":\"$CA\"}}" && echo "Vault: secret/windows"
# ISO pequeña con autounattend.xml
T=$(mktemp -d); sed -e "s|__CLAVE_ADMIN__|$CD|g" -e "s|__CLAVE_ANSIBLE__|$CA|g" $D/autounattend.xml > $T/autounattend.xml
genisoimage -quiet -J -r -o $W/autounattend.iso $T/autounattend.xml; rm -rf $T; unset CA CD
# IP fija por reserva DHCP
sudo virsh net-update default add ip-dhcp-host "<host mac='$MAC' name='$VM' ip='$IP'/>" --live --config 2>/dev/null || true
grep -q " $VM" /etc/hosts || echo "$IP $VM.demo.local $VM" | sudo tee -a /etc/hosts >/dev/null
sudo virt-install --name $VM --memory 4096 --vcpus 2 --os-variant win2k22 \
  --disk path=$W/$VM.qcow2,size=40,bus=sata,format=qcow2 \
  --cdrom $ISO --disk path=$W/autounattend.iso,device=cdrom,bus=sata \
  --network network=default,mac=$MAC,model=e1000e --graphics vnc,listen=127.0.0.1 \
  --noautoconsole --boot hd,cdrom
echo "Instalando $VM (20 a 40 minutos). Avance: nc -z $IP 5985 && echo WinRM listo"
