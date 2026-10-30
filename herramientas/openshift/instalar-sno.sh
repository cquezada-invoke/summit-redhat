#!/bin/bash
# OpenShift Single Node · paso 2: genera la ISO del agente con el pull secret (~/faceops/pull-secret.txt, no se imprime),
# crea la VM «sno» (8 vCPU, 24 GB, 120 GB) y espera a que termine la instalación (~60-90 min). Log: ~/openshift/sno/instalacion.log
set -euo pipefail
D=$HOME/openshift/sno; cd "$D"; export PATH="$D/bin:$PATH"
NOMBRE=sno; DOMINIO=invoke.lab; MAC=52:54:00:5e:00:5e; IP=192.168.122.50
PS=$HOME/faceops/pull-secret.txt
[ -s "$PS" ] || { echo "falta $PS"; exit 1; }
[ -f "$HOME/.ssh/id_ed25519_sno.pub" ] || ssh-keygen -q -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519_sno"
# el anfitrión entrega la hora a la red del laboratorio (las VM anidadas en GCP no alcanzan los NTP públicos)
grep -q '^allow 192.168.122.0/24' /etc/chrony.conf || { echo 'allow 192.168.122.0/24' | sudo tee -a /etc/chrony.conf >/dev/null; sudo systemctl restart chronyd; }
sudo firewall-cmd -q --zone=libvirt --add-service=ntp 2>/dev/null; sudo firewall-cmd -q --permanent --zone=libvirt --add-service=ntp 2>/dev/null || true
rm -rf instalar; mkdir instalar
python3 - "$PS" > instalar/install-config.yaml <<P
import json, sys
ps = json.dumps(json.load(open(sys.argv[1])))
print(f"""apiVersion: v1
baseDomain: $DOMINIO
metadata:
  name: $NOMBRE
compute:
- name: worker
  replicas: 0
controlPlane:
  name: master
  replicas: 1
networking:
  networkType: OVNKubernetes
  machineNetwork:
  - cidr: 192.168.122.0/24
  clusterNetwork:
  - cidr: 10.128.0.0/14
    hostPrefix: 23
  serviceNetwork:
  - 172.30.0.0/16
platform:
  none: {{}}
pullSecret: '{ps}'
sshKey: '$(cat $HOME/.ssh/id_ed25519_sno.pub)'""")
P
chmod 600 instalar/install-config.yaml
cat > instalar/agent-config.yaml <<A
apiVersion: v1beta1
kind: AgentConfig
metadata:
  name: $NOMBRE
rendezvousIP: $IP
additionalNTPSources:
  - 192.168.122.1
hosts:
  - hostname: $NOMBRE
    role: master
    interfaces:
      - name: enp1s0
        macAddress: $MAC
    rootDeviceHints:
      deviceName: /dev/vda
A
echo "== ISO del agente"; bin/openshift-install agent create image --dir instalar --log-level warn
sudo cp instalar/agent.x86_64.iso /var/lib/libvirt/images/sno-agent.iso
echo "== VM sno"
if ! sudo virsh dominfo $NOMBRE >/dev/null 2>&1; then
  sudo virt-install --name $NOMBRE --memory 24576 --vcpus 8 --cpu host-passthrough \
    --disk size=100,bus=virtio,sparse=yes,path=/var/lib/libvirt/images/sno.qcow2 --check disk_size=off --disk path=/var/lib/libvirt/images/sno-agent.iso,device=cdrom,readonly=on --import \
    --network network=default,mac=$MAC,model=virtio --osinfo rhel9.4 --graphics none --noautoconsole --boot hd,cdrom --autostart
fi
echo "== esperando la instalación (bootstrap)"; bin/openshift-install agent wait-for bootstrap-complete --dir instalar --log-level info
echo "== esperando la instalación (completa)"; bin/openshift-install agent wait-for install-complete --dir instalar --log-level info
chmod 600 instalar/auth/* 2>/dev/null || true
echo "LISTO: consola https://console-openshift-console.apps.$NOMBRE.$DOMINIO · kubeconfig $D/instalar/auth/kubeconfig · clave de kubeadmin en $D/instalar/auth/kubeadmin-password (no se imprime)"
