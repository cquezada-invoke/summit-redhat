#!/bin/sh
# Arranque común de los equipos de red abiertos (frr-red): clave de netadmin, LLDP y SSH.
# Uso: arranque-red.sh "<descripción del equipo>" <ip de gestión>
DESC=$1; MGMT=$2
[ -s /run/red/clave ] && echo "netadmin:$(cat /run/red/clave)" | chpasswd >/dev/null 2>&1
printf '%s\n' "$DESC" > /etc/faceops-equipo
touch /etc/frr/vtysh.conf
lldpd -I 'eth*,!eth0' -m "$MGMT" -M 4 -S "$DESC" 2>/dev/null
/usr/sbin/sshd
