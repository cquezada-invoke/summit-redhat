#!/bin/bash
# Demo 4 · caso integrado: deja CERRADO el acceso de la aplicación (nodo-jboss .15 y nodo-jboss2 .18) a la base de datos
# (nodo-postgres 192.168.122.13:5432) con firewalld en el servidor de base de datos.
# Queda permitido: ssh y PostgreSQL desde la VM anfitriona (192.168.122.1: AAP y el pipeline).
# Después se abre con UN ticket de ServiceNow "Solicitar acceso de red": origen «servidores-app» (o "192.168.122.15, 192.168.122.18"),
# destino 192.168.122.13, puerto 5432 → una regla por JBoss en el servidor. Idempotente: también sirve para reiniciar el caso.
set -e
APPS="192.168.122.15 192.168.122.18"; BD=nodo-postgres
ssh $BD 'sudo bash -s' <<SH
rpm -q firewalld >/dev/null || dnf -q -y install firewalld >/dev/null
systemctl enable --now firewalld >/dev/null 2>&1
firewall-cmd -q --set-default-zone=public
firewall-cmd -q --permanent --zone=public --remove-service=postgresql 2>/dev/null || true
firewall-cmd -q --permanent --zone=public --add-service=ssh
firewall-cmd -q --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="192.168.122.1/32" port port="5432" protocol="tcp" accept'
for a in $APPS; do
  firewall-cmd --permanent --zone=public --list-rich-rules | grep "\"\$a/" | while read -r r; do firewall-cmd -q --permanent --zone=public --remove-rich-rule="\$r"; done
done
firewall-cmd -q --reload
# corta las conexiones que la app ya tenía abiertas (firewalld deja pasar las establecidas)
cd /tmp && sudo -u postgres psql -qAtc "select count(pg_terminate_backend(pid)) from pg_stat_activity where client_addr in ('192.168.122.15','192.168.122.18')" >/dev/null
SH
# llave del laboratorio para que AAP entre a los nodos (usuario ansible)
P=$(cat ~/demo4/llave-lab.pub)
for h in nodo-postgres nodo-jboss nodo-jboss2; do
  ssh $h "grep -qF '$P' ~/.ssh/authorized_keys || echo '$P' >> ~/.ssh/authorized_keys"
done
sleep 3
echo "Reglas en $BD:"; ssh $BD 'sudo firewall-cmd --list-rich-rules'
for a in $APPS; do echo -n "App en $a: "; curl -s -m 15 http://$a:8080/app-jboss/ | grep -o 'Base de datos: [^<]*' || echo "(sin respuesta)"; done
