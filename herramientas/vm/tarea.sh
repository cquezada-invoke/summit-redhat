cd ~/summit-redhat
sudo podman exec -i faceops python3 -c "
from app import bd
for e in bd.q(\"SELECT nombre, ip FROM equipos WHERE cliente_id=(SELECT id FROM clientes WHERE lower(codigo)='lab') AND estado!='retirado' AND energia='encendido'\"): print(e['nombre'], e['ip'])
" 2>/dev/null > /tmp/ips.txt
cat /tmp/ips.txt
ls ~/.ssh/ 2>/dev/null | head
for n in nodo-storage nodo-nginx nodo-tomcat nodo-jboss nodo-vault nodo-postgres nodo-so; do ip=$(awk -v n=$n '$1==n{print $2}' /tmp/ips.txt); [ -z "$ip" ] && continue
 echo "=== $n $ip"
 timeout 20 ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5 cloud-user@$ip 'sudo -n true 2>/dev/null && echo SUDO; systemctl list-units --type=service --state=running --no-legend 2>/dev/null | awk "{print \$1}" | grep -Ev "^(systemd|dbus|getty|serial|sshd|chronyd|crond|auditd|NetworkManager|rsyslog|polkit|irqbalance|tuned|user|qemu|gssproxy|rpcbind|sssd|firewalld|kdump|lvm|dracut|nis|atd|rhsm|insights|cloud|node_exporter|podman)" | tr "\n" " "; echo; sudo -n ss -ltnp 2>/dev/null | awk "NR>1{print \$4, \$6}" | sed "s/users:((\"//;s/\",pid.*//" | sort -u | tr "\n" ";" ; echo; for u in kafka artemis keycloak lake-s3 redis vault; do systemctl cat $u 2>/dev/null | grep -E "^ExecStart|^User|^Environment" | head -3; done' 2>&1 | head -30
done
