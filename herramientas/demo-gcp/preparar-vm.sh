#!/bin/bash
# Corre DENTRO de la VM constructora (Rocky Linux 9, como root) y deja la imagen base de la «Demo de FaceOps»:
#  k3s + AWX 24.6.1 + EDA comunitarios (prueba-awx/instalar.sh), FaceOps en modo awx + validador local (prueba-awx/configurar.sh),
#  2 VMs Rocky anidadas que FaceOps administra, Caddy (TLS con sslip.io), primer arranque y agente de uso.
#  Antes de generalizar corre la PRUEBA REAL (prueba-awx/e2e.py): si falla, no hay imagen.
# Pensada para n2-standard-4 (2 cores físicos, 16 GB). Las claves se generan aquí, viven en archivos 600 y nunca se imprimen.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd); R=$(cd "$D/../.." && pwd)
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=$PATH:/usr/local/bin
paso() { echo "== $(date +%T) $*"; }
P=/root/prueba; install -d -m 700 $P /etc/demo /var/lib/demo

paso "nombre fijo del nodo k3s (la imagen se clona con otro hostname e IP)"
# interfaz ficticia con IP propia: k3s no depende de la IP de GCP, que cambia en cada demo
nmcli -t -f NAME con show | grep -qx demo0 || nmcli -g UUID con add type dummy ifname demo0 con-name demo0 ipv4.method manual ipv4.addresses 10.254.254.1/32 ipv6.method disabled >/dev/null
nmcli con up demo0 >/dev/null
install -d /etc/rancher/k3s
printf 'node-name: faceops-demo\nnode-ip: 10.254.254.1\nflannel-iface: demo0\ndisable: [metrics-server]\ndisable-network-policy: true\n' > /etc/rancher/k3s/config.yaml
# AWX entra por SSH como root a esta misma máquina (nodo-prueba) y a las VMs anidadas: solo desde la red interna
printf 'Match Address 192.168.122.0/24,10.42.0.0/16,10.254.254.1\n    PermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/90-demo-awx.conf
systemctl reload sshd

paso "KVM/libvirt para las VMs anidadas"
grep -q vmx /proc/cpuinfo || { echo "sin virtualización anidada (vmx)"; exit 1; }
dnf -y -q install qemu-kvm libvirt virt-install jq tar git openssl >/dev/null
systemctl enable --now libvirtd >/dev/null 2>&1

paso "AWX + EDA comunitarios, validador y FaceOps (rama ${RAMA:-feature/faceops-awx})"
NODO=10.254.254.1 bash "$D/prueba-awx/instalar.sh"

paso "VMs anidadas (Rocky 9 GenericCloud, 1 vCPU, 1,5 GB, IP fija en virbr0)"
[ -f $P/id_awx ] || ssh-keygen -q -t ed25519 -N '' -f $P/id_awx
install -d /var/lib/libvirt/images/base
[ -f /var/lib/libvirt/images/base/rocky9.qcow2 ] || curl -sfL -o /var/lib/libvirt/images/base/rocky9.qcow2 \
  https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2
i=10
for n in nodo-web nodo-app; do
  i=$((i+1)); mac=52:54:00:de:00:$i
  virsh net-update default add ip-dhcp-host "<host mac='$mac' name='$n' ip='192.168.122.$i'/>" --live --config >/dev/null 2>&1 || true
  if ! virsh dominfo "$n" >/dev/null 2>&1; then
    qemu-img create -q -f qcow2 -F qcow2 -b /var/lib/libvirt/images/base/rocky9.qcow2 "/var/lib/libvirt/images/$n.qcow2" 15G
    printf '#cloud-config\nhostname: %s\nusers:\n  - name: root\n    ssh_authorized_keys: ["%s"]\ndisable_root: false\n' "$n" "$(cat $P/id_awx.pub)" > /tmp/ud-$n
    virt-install -q --name "$n" --memory 1536 --vcpus 1 --import --disk "/var/lib/libvirt/images/$n.qcow2" --os-variant rocky9 \
      --network network=default,mac=$mac --graphics none --noautoconsole --cloud-init user-data=/tmp/ud-$n
    virsh autostart "$n" >/dev/null
  fi
done
for i in 11 12; do for t in $(seq 1 40); do
  ssh -q -i $P/id_awx -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@192.168.122.$i true && break; sleep 10; done; done
paso "VMs anidadas con SSH: $(for i in 11 12; do ssh -q -i $P/id_awx -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@192.168.122.$i hostname 2>/dev/null || echo NO; done | xargs)"

paso "esperando AWX y EDA (hasta 30 min)"
for i in $(seq 1 90); do   # AWX responde HTML mientras migra: se espera JSON con versión y la organización Default
  curl -sf -m 5 http://127.0.0.1:30080/api/v2/ping/ | jq -e .version >/dev/null 2>&1 && \
    curl -sf -m 5 -u "admin:$(cat $P/clave)" "http://127.0.0.1:30080/api/v2/organizations/?name=Default" | jq -e '.count > 0' >/dev/null 2>&1 && curl -s -m 5 -o /dev/null -w '%{http_code}' -u "admin:$(cat $P/clave)" \
    http://127.0.0.1:30081/api/eda/v1/users/me/ | grep -q 200 && break; sleep 20; done
for d in awx/awx-task awx/awx-web eda/eda-api eda/eda-activation-worker; do
  kubectl -n ${d%%/*} rollout status deploy/${d#*/} --timeout=1200s >/dev/null 2>&1 || echo "· $d no quedó lista"; done
paso "AWX $(curl -sf -m 5 http://127.0.0.1:30080/api/v2/ping/ | jq -r .version 2>/dev/null || echo NO) · EDA HTTP $(curl -s -m 5 -o /dev/null -w '%{http_code}' -u "admin:$(cat $P/clave)" http://127.0.0.1:30081/api/eda/v1/users/me/)"
paso "AWX/EDA: inventario, plantillas, workflow con aprobación y activación EDA; FaceOps en modo awx"
HOSTS="nodo-prueba=192.168.122.1 nodo-web=192.168.122.11 nodo-app=192.168.122.12" bash "$D/prueba-awx/configurar.sh"

paso "PRUEBA REAL de FaceOps en modo awx (login, inventario, ticket con aprobación, workflow, EDA, licencia)"
openssl rand -hex 12 > $P/clave_e2e
WH=$(kubectl -n eda get svc -o json | jq -r '.items[] | select(.spec.ports[]?.port==5000) | .spec.clusterIP' | head -1)
podman exec -i -e CLAVE_PRUEBA="$(cat $P/clave_e2e)" -e EDA_WEBHOOK="http://$WH:5000/" faceops python3 - < "$D/prueba-awx/e2e.py" | tee $P/e2e.txt
grep -q "^[0-9]*/[0-9]* verificaciones OK" $P/e2e.txt && ! grep -q "  ✗ " $P/e2e.txt || { echo "PRUEBA REAL FALLIDA"; exit 1; }
paso "PRUEBA REAL OK: $(grep 'verificaciones OK' $P/e2e.txt)"

paso "Caddy, primer arranque y agente de uso"
podman pull -q docker.io/library/caddy:2.8 >/dev/null
install -m 755 "$D/agente-uso.sh" /usr/local/sbin/demo-agente-uso
install -m 755 "$D/primer-arranque.sh" /usr/local/sbin/demo-primer-arranque
install -m 600 "$P/faceops.env" /etc/demo/faceops.env
cat > /etc/systemd/system/demo-primer-arranque.service <<U
[Unit]
Description=Demo FaceOps · primer arranque (credenciales del cliente, claves internas nuevas, URL sslip.io y TLS)
After=network-online.target k3s.service
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/demo-primer-arranque
[Install]
WantedBy=multi-user.target
U
cat > /etc/systemd/system/demo-agente-uso.service <<U
[Unit]
Description=Demo FaceOps · informa el uso a FaceAdmin y se apaga sin uso, fuera de horario o vencida
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/demo-agente-uso
U
cat > /etc/systemd/system/demo-agente-uso.timer <<U
[Unit]
Description=Demo FaceOps · agente de uso cada 5 minutos
[Timer]
OnBootSec=4min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
U
# Los contenedores vuelven solos (podman restart-policy) y los ve systemd
podman update --restart=always faceops validador >/dev/null 2>&1 || true
systemctl enable podman-restart.service >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl enable demo-primer-arranque.service demo-agente-uso.timer >/dev/null 2>&1
paso "Preparación terminada"
