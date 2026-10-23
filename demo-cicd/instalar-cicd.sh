#!/usr/bin/env bash
# CI de la demo: k3s + Tekton (pipelines, triggers, dashboard) + Gitea con la app JBoss y su webhook.
set -euo pipefail
D=~/demo-cicd
HOST=$(hostname -I | awk '{print $1}')
SECRETOS=~/demo-drift/cicd-secretos.txt
log(){ echo "· $*"; }

# 0. Playbooks de JBoss al Git que usa AAP
cp -r "$D/jboss" ~/demo-summit-repo/
(cd ~/demo-summit-repo && git add -A && (git diff --cached --quiet || git commit -qm "Despliegue en JBoss EAP") && git push -q origin main)
log "Playbooks de JBoss en el Git de AAP"

# 1. Kubernetes liviano (sin traefik ni balanceador, para no chocar con AAP en 443)
if [ ! -f /etc/rancher/k3s/k3s.yaml ]; then
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable traefik --disable servicelb --write-kubeconfig-mode 644" sh - >/dev/null
fi
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=$PATH:/usr/local/bin
sudo firewall-cmd -q --permanent --zone=trusted --add-source=10.42.0.0/16
sudo firewall-cmd -q --permanent --zone=trusted --add-source=10.43.0.0/16
for p in 3000 30080 30097; do sudo firewall-cmd -q --permanent --add-port=$p/tcp; done
sudo firewall-cmd -q --permanent --zone=libvirt --add-port=3000/tcp
sudo firewall-cmd -q --reload
kubectl wait --for=condition=Ready node --all --timeout=180s >/dev/null
log "k3s listo"

# 2. Tekton
for u in pipeline/latest/release.yaml triggers/latest/release.yaml triggers/latest/interceptors.yaml dashboard/latest/release.yaml; do
  kubectl apply -f "https://storage.googleapis.com/tekton-releases/$u" >/dev/null
done
kubectl -n tekton-pipelines wait --for=condition=Available deploy --all --timeout=300s >/dev/null
kubectl -n tekton-pipelines patch cm feature-flags -p '{"data":{"coschedule":"disabled"}}' >/dev/null
kubectl -n tekton-pipelines patch svc tekton-dashboard -p '{"spec":{"type":"NodePort","ports":[{"port":9097,"nodePort":30097}]}}' >/dev/null
log "Tekton listo (dashboard en el puerto 30097)"

# 3. Gitea (contenedor del usuario, arranca con la máquina)
mkdir -p ~/.config/containers/systemd
cat > ~/.config/containers/systemd/gitea.container <<U
[Unit]
Description=Gitea de la demo
[Container]
Image=docker.io/gitea/gitea:1.22.6
ContainerName=gitea
PublishPort=3000:3000
Volume=gitea-data:/data
Environment=GITEA__security__INSTALL_LOCK=true
Environment=GITEA__server__DOMAIN=34-176-42-56.sslip.io
Environment=GITEA__server__ROOT_URL=http://34-176-42-56.sslip.io:3000/
Environment=GITEA__service__DISABLE_REGISTRATION=true
Environment=GITEA__webhook__ALLOWED_HOST_LIST=*
Environment=GITEA__packages__ENABLED=true
[Service]
Restart=always
[Install]
WantedBy=default.target
U
systemctl --user daemon-reload
systemctl --user start gitea
for i in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:3000/ && break; sleep 3; done
if [ ! -f "$SECRETOS" ]; then
  GP=$(openssl rand -hex 10)
  podman exec -u git gitea gitea admin user create --username invoke --password "$GP" \
    --email cquezada@invoke.cl --admin --must-change-password=false >/dev/null
  GT=$(podman exec -u git gitea gitea admin user generate-access-token --username invoke \
    --token-name tekton --scopes write:repository,write:package,write:user --raw)
  JP=$(openssl rand -hex 10)
  printf "gitea_usuario=invoke\ngitea_clave=%s\ngitea_token=%s\njboss_usuario=admin-aap\njboss_clave=%s\n" "$GP" "$GT" "$JP" > "$SECRETOS"
  chmod 600 "$SECRETOS"
fi
GT=$(sed -n 's/^gitea_token=//p' "$SECRETOS")
H="Authorization: token $GT"
curl -s -o /dev/null -H "$H" -H 'Content-Type: application/json' -X POST http://127.0.0.1:3000/api/v1/user/repos \
  -d '{"name":"app-jboss","description":"App de demo para JBoss EAP 8","private":false,"default_branch":"main"}'
cd "$D/app-jboss"
[ -d .git ] || git init -q -b main
git config user.name "Cristian Quezada"; git config user.email cquezada@invoke.cl
git add -A && (git diff --cached --quiet || git commit -qm "App de demo para JBoss EAP 8")
git push -q -f "http://invoke:$GT@127.0.0.1:3000/invoke/app-jboss.git" main
log "Gitea listo con el repositorio invoke/app-jboss"

# 4. Pipeline y disparo por push
kubectl create secret generic gitea-token --from-literal=token="$GT" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
sed "s/__HOST__/$HOST/g" "$D/tekton/pipeline.yaml" | kubectl apply -f - >/dev/null
kubectl -n tekton-pipelines wait --for=condition=Available deploy --all --timeout=300s >/dev/null
kubectl apply -f "$D/tekton/triggers.yaml" >/dev/null
if ! curl -s -H "$H" http://127.0.0.1:3000/api/v1/repos/invoke/app-jboss/hooks | grep -q 30080; then
  curl -s -o /dev/null -H "$H" -H 'Content-Type: application/json' -X POST \
    http://127.0.0.1:3000/api/v1/repos/invoke/app-jboss/hooks \
    -d "{\"type\":\"gitea\",\"active\":true,\"events\":[\"push\"],\"config\":{\"url\":\"http://192.168.122.1:30080\",\"content_type\":\"json\"}}"
fi
log "Pipeline app-jboss y webhook de push listos"
kubectl get pipeline,eventlistener
