#!/usr/bin/env bash
# Demo 3 · Plataforma como código: más recursos para nodo-tomcat, claves de administración en Vault,
# servicios compartidos (Keycloak, Artemis, Valkey, PostgreSQL remoto, driver JBoss), AAP y pipeline.
set -euo pipefail
export PATH=$PATH:/usr/local/bin KUBECONFIG=/etc/rancher/k3s/k3s.yaml
D=~/demo-cicd
log(){ echo "· $*"; }
ROOT=$(python3 -c "import json;print(json.load(open('/home/cquezada/demo-drift/vault-init.json'))['root_token'])")
VA=http://192.168.122.1:8202/v1

# 1. nodo-tomcat con 6 GB y 2 vCPU para Keycloak, Artemis y Valkey
if [ "$(sudo virsh dominfo nodo-tomcat | awk '/Max memory/{print $3}')" -lt 6000000 ]; then
  sudo virsh shutdown nodo-tomcat >/dev/null
  for i in $(seq 1 60); do sudo virsh domstate nodo-tomcat | grep -q 'shut off' && break; sleep 3; done
  sudo virsh setmaxmem nodo-tomcat 6G --config && sudo virsh setmem nodo-tomcat 6G --config
  sudo virsh setvcpus nodo-tomcat 2 --maximum --config && sudo virsh setvcpus nodo-tomcat 2 --config
  sudo virsh start nodo-tomcat >/dev/null
  for i in $(seq 1 60); do ssh -o BatchMode=yes -o ConnectTimeout=3 nodo-tomcat true 2>/dev/null && break; sleep 5; done
fi
log "nodo-tomcat: $(ssh nodo-tomcat 'free -g | awk "/Mem/{print \$2}"') GB"

# 2. Claves de administración de la plataforma en Vault (se generan una sola vez)
if ! curl -sf -H "X-Vault-Token: $ROOT" $VA/secret/data/plataforma >/dev/null; then
  curl -sf -H "X-Vault-Token: $ROOT" -X POST $VA/secret/data/plataforma -d "{\"data\":{\"keycloak_admin\":\"$(openssl rand -hex 12)\",\"artemis_admin\":\"$(openssl rand -hex 12)\",\"valkey_admin\":\"$(openssl rand -hex 12)\"}}" >/dev/null
fi
VARS=$(mktemp); chmod 600 "$VARS"
curl -s -H "X-Vault-Token: $ROOT" $VA/secret/data/plataforma | python3 -c "
import json,sys;d=json.load(sys.stdin)['data']['data']
s=dict(l.strip().split('=',1) for l in open('/home/cquezada/demo-drift/cicd-secretos.txt') if '=' in l)
d.update(jboss_usuario=s['jboss_usuario'], jboss_clave=s['jboss_clave']); print(json.dumps(d))" > "$VARS"
log "Claves de administración en Vault (secret/plataforma)"

# 3. Playbooks al Git de AAP y servicios compartidos
cp -r "$D/plataforma" ~/demo-summit-repo/
(cd ~/demo-summit-repo && git add -A && (git diff --cached --quiet || git commit -qm "Plataforma como código") && git push -q origin main)
cd ~/demo-summit-repo
ansible-playbook -i ~/demo-drift/inventario.ini plataforma/servicios.yml -e @"$VARS" | tail -8
rm -f "$VARS"
log "Servicios de plataforma instalados"

# 4. AAP, Vault y pipeline
python3 "$D/configurar_plataforma.py"
sed "s/__HOST__/$(hostname -I | awk '{print $1}')/g" "$D/tekton/pipeline.yaml" | kubectl apply -f - >/dev/null
log "Pipeline con el paso preparar-plataforma"
