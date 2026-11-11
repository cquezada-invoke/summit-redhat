#!/bin/bash
# Corre DENTRO de la VM constructora (Rocky Linux 9) y deja todo instalado y detenido listo para la imagen dorada.
# Versiones fijadas y parametrizables; revisar antes de construir (AWX pausó sus versiones públicas en 24.6.1 / awx-operator 2.19.1).
set -euo pipefail
AWX_OP=${AWX_OP:-2.19.1}; EDA_OP=${EDA_OP:-main}   # eda-server-operator: 1.0.2 es el último tag (2024); eda-server solo publica main; K3S=${K3S:-v1.30.6+k3s1}
FACEOPS_REPO=${FACEOPS_REPO:-https://github.com/cquezada-invoke/summit-redhat.git}
VMS=${VMS:-"nodo-web nodo-app nodo-db"}          # VMs anidadas (Rocky 9, 1 vCPU, 2 GB) que FaceOps administra en la demo
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

echo "== paquetes base"
dnf -y -q install epel-release
dnf -y -q install podman git jq python3-pip qemu-kvm libvirt virt-install guestfs-tools cloud-utils-growpart tar iproute
systemctl enable --now libvirtd
grep -q vmx /proc/cpuinfo || { echo "sin virtualización anidada (vmx)"; exit 1; }

echo "== k3s (sin traefik: Caddy publica todo en 443)"
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S" INSTALL_K3S_EXEC="--disable traefik --write-kubeconfig-mode 600" sh -
until kubectl get nodes 2>/dev/null | grep -q " Ready"; do sleep 5; done
dnf -y -q install https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.4.3/kustomize_v5.4.3_linux_amd64.tar.gz 2>/dev/null || \
  (curl -sL https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.4.3/kustomize_v5.4.3_linux_amd64.tar.gz | tar xz -C /usr/local/bin)

echo "== AWX (awx-operator $AWX_OP) — upstream comunitario de automation controller"
mkdir -p /opt/demo/awx && cd /opt/demo/awx
cat > kustomization.yaml <<K
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: awx
resources:
  - github.com/ansible/awx-operator/config/default?ref=$AWX_OP
  - awx.yaml
images:
  - name: quay.io/ansible/awx-operator
    newTag: $AWX_OP
K
cat > awx.yaml <<K
apiVersion: awx.ansible.com/v1beta1
kind: AWX
metadata: {name: awx}
spec:
  service_type: NodePort
  nodeport_port: 30080
  web_resource_requirements: {requests: {cpu: 250m, memory: 1Gi}}
  task_resource_requirements: {requests: {cpu: 250m, memory: 1Gi}}
  ee_resource_requirements: {requests: {cpu: 100m, memory: 256Mi}}
  postgres_storage_requirements: {requests: {storage: 8Gi}}
K
kubectl create ns awx --dry-run=client -o yaml | kubectl apply -f -
kustomize build . | kubectl apply -f -

echo "== EDA comunitario (eda-server-operator $EDA_OP)"
mkdir -p /opt/demo/eda && cd /opt/demo/eda
cat > kustomization.yaml <<K
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: eda
resources:
  - github.com/ansible/eda-server-operator/config/default?ref=$EDA_OP
  - eda.yaml
images:
  - name: controller
    newName: quay.io/ansible/eda-server-operator
    newTag: $EDA_OP
K
cat > eda.yaml <<K
apiVersion: eda.ansible.com/v1alpha1
kind: EDA
metadata: {name: eda}
spec:
  service_type: NodePort
  nodeport_port: 30081   # eda-ui: sirve la interfaz y hace de proxy de /api/eda/v1
  automation_server_url: http://awx-service.awx.svc.cluster.local
K
kubectl create ns eda --dry-run=client -o yaml | kubectl apply -f -
kustomize build . | kubectl apply -f -
for i in $(seq 1 120); do kubectl -n awx get awx awx -o jsonpath='{.status.conditions[?(@.type=="Running")].status}' 2>/dev/null | grep -q True && break; sleep 10; done

echo "== servicios en contenedores (podman + quadlet): FaceOps, Gitea, Vault, MinIO, Prometheus, Alertmanager, Grafana, Caddy"
install -d /etc/containers/systemd /var/lib/demo/{gitea,vault,minio,prometheus,grafana,caddy,faceops}
git clone -q --depth 1 "$FACEOPS_REPO" /opt/demo/repo
podman build -q -t localhost/faceops:demo -f /opt/demo/repo/faceops/Containerfile /opt/demo/repo/faceops
c() { printf '[Container]\nImage=%s\nContainerName=%s\nNetwork=host\n%s\n[Service]\nRestart=always\n[Install]\nWantedBy=multi-user.target\n' "$2" "$1" "$3" > "/etc/containers/systemd/$1.container"; }
c faceops   localhost/faceops:demo                      "Volume=/var/lib/demo/faceops:/var/lib/faceops:Z
EnvironmentFile=-/etc/demo/faceops.env"
c gitea     docker.io/gitea/gitea:1.22                  "Volume=/var/lib/demo/gitea:/data:Z
Environment=GITEA__server__HTTP_PORT=3000"
c vault     docker.io/hashicorp/vault:1.17              "Volume=/var/lib/demo/vault:/vault/file:Z
Environment=VAULT_LOCAL_CONFIG={\"storage\":{\"file\":{\"path\":\"/vault/file\"}},\"listener\":{\"tcp\":{\"address\":\"127.0.0.1:8200\",\"tls_disable\":true}},\"ui\":true}
Exec=server"
c minio     quay.io/minio/minio:RELEASE.2024-10-13T13-34-11Z "Volume=/var/lib/demo/minio:/data:Z
EnvironmentFile=-/etc/demo/minio.env
Exec=server /data --address 127.0.0.1:9000 --console-address 127.0.0.1:9001"
c prometheus docker.io/prom/prometheus:v2.54.1          "Volume=/var/lib/demo/prometheus:/prometheus:Z"
c alertmanager docker.io/prom/alertmanager:v0.27.0      ""
c grafana   docker.io/grafana/grafana:11.2.2            "Volume=/var/lib/demo/grafana:/var/lib/grafana:Z
EnvironmentFile=-/etc/demo/grafana.env"
c caddy     docker.io/library/caddy:2.8                 "Volume=/etc/demo/Caddyfile:/etc/caddy/Caddyfile:ro,Z
Volume=/var/lib/demo/caddy:/data:Z"
for im in localhost/faceops:demo docker.io/gitea/gitea:1.22 docker.io/hashicorp/vault:1.17 quay.io/minio/minio:RELEASE.2024-10-13T13-34-11Z \
          docker.io/prom/prometheus:v2.54.1 docker.io/prom/alertmanager:v0.27.0 docker.io/grafana/grafana:11.2.2 docker.io/library/caddy:2.8; do
  podman image exists "$im" || podman pull -q "$im" >/dev/null; done

echo "== VMs anidadas (Rocky 9 GenericCloud, sin licencia): $VMS"
install -d /var/lib/libvirt/images/base
curl -sL -o /var/lib/libvirt/images/base/rocky9.qcow2 https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2
for n in $VMS; do
  qemu-img create -q -f qcow2 -F qcow2 -b /var/lib/libvirt/images/base/rocky9.qcow2 "/var/lib/libvirt/images/$n.qcow2" 20G
  virt-install --name "$n" --memory 2048 --vcpus 1 --import --disk "/var/lib/libvirt/images/$n.qcow2" --os-variant rocky9 \
    --network network=default --graphics none --noautoconsole --cloud-init root-ssh-key=/root/.ssh/id_demo.pub 2>/dev/null || \
    { [ -f /root/.ssh/id_demo ] || ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/id_demo; \
      virt-install --name "$n" --memory 2048 --vcpus 1 --import --disk "/var/lib/libvirt/images/$n.qcow2" --os-variant rocky9 \
        --network network=default --graphics none --noautoconsole --cloud-init root-ssh-key=/root/.ssh/id_demo.pub; }
  virsh autostart "$n" >/dev/null
done

echo "== agente de uso y primer arranque"
install -m 755 /tmp/agente-uso.sh /usr/local/sbin/demo-agente-uso
install -m 755 /tmp/primer-arranque.sh /usr/local/sbin/demo-primer-arranque
cat > /etc/systemd/system/demo-primer-arranque.service <<U
[Unit]
Description=Demo · primer arranque (credenciales del cliente, URL sslip.io y TLS)
After=network-online.target k3s.service
Wants=network-online.target
ConditionPathExists=!/var/lib/demo/credenciales.listo
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/demo-primer-arranque
[Install]
WantedBy=multi-user.target
U
cat > /etc/systemd/system/demo-agente-uso.service <<U
[Unit]
Description=Demo · informa el uso a FaceAdmin y apaga la VM sin uso, fuera de horario o vencida
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/demo-agente-uso
U
cat > /etc/systemd/system/demo-agente-uso.timer <<U
[Unit]
Description=Demo · agente de uso cada 5 minutos
[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
U
systemctl daemon-reload
systemctl enable demo-primer-arranque.service demo-agente-uso.timer
echo "Preparación terminada"
