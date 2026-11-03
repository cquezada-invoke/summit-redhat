#!/bin/bash
# Entorno de un cliente de demo en su propia VM de GCP: Ansible Automation Platform completo (contenedores, todo en uno:
# gateway, controller, hub, EDA, métricas y PostgreSQL) instalado desde el bundle de Invoke Lab.
# Se corre desde Invoke Lab: copia el bundle por la red privada, genera claves (quedan solo en
# ~/faceops/clientes/<codigo>.env, 0600, y en la VM del cliente) y lanza la instalación allá, en segundo plano.
# Uso: instalar-aap-cliente.sh <ip-privada> <codigo>          (idempotente: si AAP ya responde, no reinstala)
set -euo pipefail
IP=$1; COD=$2
B=$(ls -d ~/aap/ansible-automation-platform-containerized-setup-bundle-*/ | head -1); NB=$(basename "$B")
S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null cquezada@$IP"
mkdir -p ~/faceops/clientes; chmod 700 ~/faceops/clientes
ENV=~/faceops/clientes/$COD.env
if [ ! -s "$ENV" ]; then
  (umask 077; for v in ADMIN PG_ADMIN PG_GW PG_CTL PG_HUB PG_EDA PG_MET; do echo "$v=$(openssl rand -base64 24 | tr -dc A-Za-z0-9 | head -c 22)"; done > "$ENV")
fi
. "$ENV"
if $S "curl -sk -o /dev/null -w '%{http_code}' https://localhost/api/gateway/v1/ping/" 2>/dev/null | grep -q 200; then
  echo "· $COD ($IP): AAP ya responde"; exit 0
fi
H=$($S hostname -f)
echo "· $COD ($IP · $H): paquetes base"
$S "sudo dnf -q -y install ansible-core podman rsync tar firewalld >/dev/null 2>&1; sudo hostnamectl set-hostname $H; sudo loginctl enable-linger cquezada"
echo "· $COD: copiando el bundle ($(du -sh "$B" | cut -f1))"
rsync -a -e "ssh -i $HOME/.ssh/id_demo -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null" --exclude 'inventory-demo*' --exclude aap_install.log "$B" "cquezada@$IP:~/aap/$NB/"
$S "cat > ~/aap/$NB/inventory-cliente" <<INV
[automationgateway]
$H
[automationcontroller]
$H
[automationhub]
$H
[automationeda]
$H
[automationmetrics]
$H
[database]
$H
[all:vars]
ansible_connection=local
postgresql_admin_username=postgres
postgresql_admin_password=$PG_ADMIN
bundle_install=true
bundle_dir='{{ lookup("ansible.builtin.env", "PWD") }}/bundle'
redis_mode=standalone
gateway_admin_password=$ADMIN
gateway_pg_host=$H
gateway_pg_password=$PG_GW
controller_admin_password=$ADMIN
controller_pg_host=$H
controller_pg_password=$PG_CTL
controller_percent_memory_capacity=0.5
hub_admin_password=$ADMIN
hub_pg_host=$H
hub_pg_password=$PG_HUB
hub_seed_collections=false
eda_admin_password=$ADMIN
eda_pg_host=$H
eda_pg_password=$PG_EDA
automationmetrics_pg_host=$H
automationmetrics_pg_password=$PG_MET
automationmetrics_controller_read_pg_host=$H
automationmetrics_controller_read_pg_password=$PG_MET
INV
$S "chmod 600 ~/aap/$NB/inventory-cliente; cd ~/aap/$NB && setsid nohup ansible-playbook -i inventory-cliente ansible.containerized_installer.install > ~/aap/instalacion-aap.log 2>&1 < /dev/null & echo lanzado"
echo "· $COD: instalación de AAP en curso (log en la VM: ~/aap/instalacion-aap.log)"
