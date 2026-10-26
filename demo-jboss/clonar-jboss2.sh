#!/bin/bash
# Crea nodo-jboss2 (.18) como copia de nodo-jboss (JBoss EAP 8 ya instalado). Idempotente.
# Apaga nodo-jboss ~1 min para copiar su disco. Usa el SSH de los nodos (~/.ssh/config: Host nodo-* → ansible, id_demo).
set -e
log(){ echo "$(date +%T) $*"; }
if sudo virsh dominfo nodo-jboss2 >/dev/null 2>&1; then log "nodo-jboss2 ya existe"; exit 0; fi
sudo virsh net-update default add ip-dhcp-host "<host mac='52:54:00:de:00:18' name='nodo-jboss2' ip='192.168.122.18'/>" --live --config >/dev/null 2>&1 || true
grep -q " nodo-jboss2$" /etc/hosts || echo "192.168.122.18 nodo-jboss2.demo.local nodo-jboss2" | sudo tee -a /etc/hosts >/dev/null
log "apagando nodo-jboss para copiar su disco"
sudo virsh shutdown nodo-jboss >/dev/null
for i in $(seq 1 60); do sudo virsh domstate nodo-jboss | grep -q "shut off" && break; sleep 2; done
sudo virsh domstate nodo-jboss | grep -q "shut off" || { sudo virsh destroy nodo-jboss; sleep 2; }
sudo virt-clone --original nodo-jboss --name nodo-jboss2 --file /var/lib/libvirt/images/nodo-jboss2.qcow2 --mac 52:54:00:de:00:18 >/dev/null
log "clon listo; encendiendo ambos"
sudo virsh start nodo-jboss >/dev/null; sudo virsh start nodo-jboss2 >/dev/null; sudo virsh autostart nodo-jboss2 >/dev/null
ssh-keygen -R 192.168.122.18 >/dev/null 2>&1; ssh-keygen -R nodo-jboss2 >/dev/null 2>&1
for i in $(seq 1 60); do ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no nodo-jboss2 true 2>/dev/null && break; sleep 3; done
log "nodo-jboss2 responde por SSH; personalizando"
ssh -o StrictHostKeyChecking=no nodo-jboss2 'sudo bash -s' <<'SH'
hostnamectl set-hostname nodo-jboss2.demo.local
touch /etc/cloud/cloud-init.disabled
rm -f /etc/machine-id && systemd-machine-id-setup >/dev/null
rm -f /etc/ssh/ssh_host_* && ssh-keygen -A >/dev/null
subscription-manager clean >/dev/null 2>&1 || true
# Los vigías de las Demos 1 y 5 se quedan solo en nodo-jboss
systemctl disable --now vigia.service vigia-jboss.service >/dev/null 2>&1 || true
(sleep 2; systemctl restart sshd) >/dev/null 2>&1 &
systemctl restart eap8-standalone
SH
ssh-keygen -R 192.168.122.18 >/dev/null 2>&1; ssh-keygen -R nodo-jboss2 >/dev/null 2>&1
for i in $(seq 1 40); do curl -s -m3 http://192.168.122.18:8080/app-jboss/salud | grep -q ok && break; sleep 3; done
log "salud jboss2: $(curl -s -m3 http://192.168.122.18:8080/app-jboss/salud)"
# PostgreSQL acepta al segundo JBoss (firewalld en nodo-postgres)
ssh nodo-postgres "sudo firewall-cmd -q --permanent --add-rich-rule='rule family=ipv4 source address=192.168.122.18/32 port port=5432 protocol=tcp accept' && sudo firewall-cmd -q --reload" || true
