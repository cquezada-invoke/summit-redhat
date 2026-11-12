#!/bin/bash
# Prueba real de FaceOps en modo awx: deja en UNA máquina Rocky Linux 9 (como root) k3s + AWX 24.6.1 + EDA comunitario +
# validador local + FaceOps (rama indicada) en modo awx, y un repositorio git local con el playbook y el rulebook de prueba.
# Las claves se generan aquí y quedan en /root/prueba (600); nunca se imprimen.
set -euo pipefail
RAMA=${RAMA:-feature/faceops-awx}
REPO=${REPO:-https://github.com/cquezada-invoke/summit-redhat.git}
AWX_OP=${AWX_OP:-2.19.1}; EDA_OP=${EDA_OP:-9120da27dbd15c7d10245ecfbee5dd6d3df01c7e};   # commit de main fijado: su imagen exacta está en ghcr.io (la «main» de quay quedó atrás y entra en CrashLoop)
 K3S=${K3S:-v1.30.6+k3s1}
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=$PATH:/usr/local/bin
P=/root/prueba; install -d -m 700 $P
umask 077
paso() { echo "== $(date +%T) $*"; }

paso "paquetes"
setenforce 0 || true; sed -i 's/^SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config
dnf -y -q install git jq podman python3 tar >/dev/null
[ -f $P/clave ] || openssl rand -hex 16 > $P/clave
CLAVE=$(cat $P/clave)

paso "k3s $K3S"
if ! command -v k3s >/dev/null; then
  curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S" INSTALL_K3S_SKIP_SELINUX_RPM=true INSTALL_K3S_SELINUX_WARN=true \
    INSTALL_K3S_EXEC="--disable traefik --write-kubeconfig-mode 600" sh - >/dev/null
fi
until kubectl get nodes 2>/dev/null | grep -q " Ready"; do sleep 5; done
command -v kustomize >/dev/null || curl -sL https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.4.3/kustomize_v5.4.3_linux_amd64.tar.gz | tar xz -C /usr/local/bin

paso "AWX (awx-operator $AWX_OP → AWX 24.6.1)"
install -d /opt/prueba/awx /opt/prueba/eda
kubectl create ns awx --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n awx create secret generic awx-admin-password --from-literal=password="$CLAVE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
cat > /opt/prueba/awx/kustomization.yaml <<K
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: awx
resources:
  - github.com/ansible/awx-operator/config/default?ref=$AWX_OP
  - awx.yaml
images:
  - name: quay.io/ansible/awx-operator
    newTag: $AWX_OP
  - name: gcr.io/kubebuilder/kube-rbac-proxy   # gcr.io/kubebuilder ya no existe
    newName: quay.io/brancz/kube-rbac-proxy
    newTag: v0.15.0
K
cat > /opt/prueba/awx/awx.yaml <<K
apiVersion: awx.ansible.com/v1beta1
kind: AWX
metadata: {name: awx}
spec:
  service_type: NodePort
  nodeport_port: 30080
  admin_password_secret: awx-admin-password
  web_resource_requirements: {requests: {cpu: 100m, memory: 512Mi}}
  task_resource_requirements: {requests: {cpu: 100m, memory: 512Mi}}
  ee_resource_requirements: {requests: {cpu: 50m, memory: 128Mi}}
  postgres_storage_requirements: {requests: {storage: 8Gi}}
K
(cd /opt/prueba/awx && kustomize build . | kubectl apply -f - >/dev/null 2>&1) || (sleep 20; cd /opt/prueba/awx && kustomize build . | kubectl apply -f - >/dev/null)

paso "EDA comunitario (eda-server-operator $EDA_OP)"
kubectl create ns eda --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n eda create secret generic eda-admin-password --from-literal=password="$CLAVE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
# Sin config/default: su parche de métricas agrega --metrics-require-rbac, que la imagen publicada del operador no reconoce (CrashLoop)
install -d /opt/prueba/eda/op
cat > /opt/prueba/eda/op/kustomization.yaml <<K
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: eda
namePrefix: eda-server-operator-
resources:
  - github.com/ansible/eda-server-operator/config/crd?ref=$EDA_OP
  - github.com/ansible/eda-server-operator/config/rbac?ref=$EDA_OP
  - github.com/ansible/eda-server-operator/config/manager?ref=$EDA_OP
images:   # config/manager ya renombra «controller» a quay.io/ansible/eda-server-operator:0.0.1 (antigua): se reemplaza por la del commit
  - name: quay.io/ansible/eda-server-operator
    newName: ghcr.io/ansible/eda-server-operator
    newTag: $EDA_OP
K
cat > /opt/prueba/eda/kustomization.yaml <<K
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: eda
resources:
  - op
  - eda.yaml
K
cat > /opt/prueba/eda/eda.yaml <<K
apiVersion: eda.ansible.com/v1alpha1
kind: EDA
metadata: {name: eda}
spec:
  service_type: NodePort
  nodeport_port: 30081
  admin_password_secret: eda-admin-password
  automation_server_url: http://awx-service.awx.svc.cluster.local:80
  api: {replicas: 1, resource_requirements: {requests: {cpu: 50m, memory: 256Mi}}}
  default_worker: {replicas: 1, resource_requirements: {requests: {cpu: 50m, memory: 256Mi}}}
  activation_worker: {replicas: 1, resource_requirements: {requests: {cpu: 50m, memory: 256Mi}}}
  ui: {replicas: 1, resource_requirements: {requests: {cpu: 25m, memory: 128Mi}}}
K
(cd /opt/prueba/eda && kustomize build . | kubectl apply -f - >/dev/null 2>&1) || (sleep 20; cd /opt/prueba/eda && kustomize build . | kubectl apply -f - >/dev/null)

paso "repositorio git local (HTTP inteligente con git http-backend en :8088) con playbook y rulebook de prueba"
NODO=${NODO:-$(hostname -I | awk '{print $1}')}
install -d /srv/git && rm -rf /tmp/pr && mkdir -p /tmp/pr/rulebooks
cat > /tmp/pr/ping.yml <<'Y'
- name: FaceOps prueba · ping
  hosts: all
  gather_facts: true
  tasks:
    - ansible.builtin.ping:
    - ansible.builtin.debug: {msg: "FaceOps-AWX OK en {{ inventory_hostname }} ({{ ansible_distribution }} {{ ansible_distribution_version }})"}
Y
cat > /tmp/pr/rulebooks/alerta.yml <<'Y'
- name: FaceOps prueba · alerta
  hosts: all
  sources:
    - ansible.eda.webhook: {host: 0.0.0.0, port: 5000}
  rules:
    - name: Disco lleno
      condition: event.payload.alerta == "disco"
      action:
        run_job_template:
          name: "Prueba · Responder alerta"
          organization: Default
Y
(cd /tmp/pr && git init -q -b main && git add -A && git -c user.name=prueba -c user.email=prueba@local commit -qm prueba)
rm -rf /srv/git/prueba.git && git clone -q --bare /tmp/pr /srv/git/prueba.git && git -C /srv/git/prueba.git update-server-info
# HTTP «inteligente» (git http-backend): EDA clona con --depth, que el HTTP tonto no soporta
cat > /usr/local/sbin/git-http.py <<'G'
import http.server, os, subprocess, sys
class H(http.server.BaseHTTPRequestHandler):
    def _git(self):
        ruta, _, qs = self.path.partition("?")
        n = int(self.headers.get("Content-Length") or 0)
        cuerpo = self.rfile.read(n) if n else b""
        env = dict(os.environ, GIT_PROJECT_ROOT="/srv/git", GIT_HTTP_EXPORT_ALL="1", PATH_INFO=ruta, QUERY_STRING=qs,
                   REQUEST_METHOD=self.command, CONTENT_TYPE=self.headers.get("Content-Type", ""), CONTENT_LENGTH=str(len(cuerpo)),
                   REMOTE_ADDR=self.client_address[0], GIT_PROTOCOL=self.headers.get("Git-Protocol", ""))
        if self.headers.get("Content-Encoding") == "gzip":
            env["HTTP_CONTENT_ENCODING"] = "gzip"
        out = subprocess.run(["git", "http-backend"], input=cuerpo, env=env, capture_output=True).stdout
        cab, _, datos = out.partition(b"\r\n\r\n")
        if not _:
            cab, _, datos = out.partition(b"\n\n")
        estado = 200
        lineas = [l for l in cab.decode(errors="replace").splitlines() if ":" in l]
        for l in lineas:
            if l.lower().startswith("status:"):
                estado = int(l.split(":", 1)[1].split()[0])
        self.send_response(estado)
        for l in lineas:
            k, v = l.split(":", 1)
            if k.lower() != "status":
                self.send_header(k.strip(), v.strip())
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)
    do_GET = do_POST = _git
    def log_message(self, *a):
        pass
http.server.ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
G
cat > /etc/systemd/system/git-http.service <<U
[Service]
ExecStart=/usr/bin/python3 /usr/local/sbin/git-http.py 8088
Restart=always
[Install]
WantedBy=multi-user.target
U
systemctl daemon-reload && systemctl enable git-http >/dev/null 2>&1 && systemctl restart git-http
echo "http://$NODO:8088/prueba.git" > $P/repo_url

paso "acceso SSH de AWX a esta misma máquina (host gestionado)"
install -d -m 700 /root/.ssh
[ -f $P/id_awx ] || ssh-keygen -q -t ed25519 -N '' -f $P/id_awx
grep -qf $P/id_awx.pub /root/.ssh/authorized_keys 2>/dev/null || cat $P/id_awx.pub >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

paso "validador local y FaceOps ($RAMA) en modo awx"
rm -rf /opt/prueba/repo && git clone -q --depth 1 -b "$RAMA" "$REPO" /opt/prueba/repo && chmod -R a+rX /opt/prueba/repo   # umask 077: sin esto el usuario 1001 del contenedor no lee el código
podman build -q -t localhost/faceops-validador:prueba /opt/prueba/repo/faceops/validador >/dev/null
podman build -q -t localhost/faceops:prueba -f /opt/prueba/repo/faceops/Containerfile /opt/prueba/repo/faceops >/dev/null
install -d -m 777 /var/lib/prueba/validador /var/lib/prueba/faceops
podman rm -f validador >/dev/null 2>&1 || true
podman run -d --name validador --network host -v /var/lib/prueba/validador:/data:Z localhost/faceops-validador:prueba >/dev/null
echo "Imágenes listas"
for i in $(seq 1 90); do
  kubectl -n awx get awx awx -o jsonpath='{.status.conditions[?(@.type=="Running")].status}' 2>/dev/null | grep -q True && \
    curl -sf -m 5 http://127.0.0.1:30080/api/v2/ping/ >/dev/null && break; sleep 20; done
paso "AWX responde: $(curl -sf -m 5 http://127.0.0.1:30080/api/v2/ping/ | jq -r .version 2>/dev/null || echo NO)"
for i in $(seq 1 60); do curl -sf -m 5 -u "admin:$CLAVE" http://127.0.0.1:30081/api/eda/v1/status/ >/dev/null && break; sleep 20; done
paso "EDA responde: $(curl -s -m 5 -o /dev/null -w '%{http_code}' -u "admin:$CLAVE" http://127.0.0.1:30081/api/eda/v1/users/me/)"
echo "INSTALACION TERMINADA"
