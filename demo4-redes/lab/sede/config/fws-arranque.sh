#!/bin/bash
# Arranque de un firewall VyOS de la red corporativa: espera a VyOS y a los cables y aplica su configuración base.
# Uso: fwc-arranque.sh <archivo.vbash> <interfaces separadas por coma> <grupo que confirma que quedó aplicada>
CFG=$1; IFS=, read -ra IFS_ <<< "$2"; MARCA=$3
for i in $(seq 1 150); do
  ok=1; for x in "${IFS_[@]}"; do [ -e /sys/class/net/$x ] || ok=0; done
  systemctl is-active -q vyos-router.service && [ $ok = 1 ] && break
  sleep 2
done
ip route del default via 172.20.20.1 2>/dev/null   # el plano de datos no sale por la red de gestión
sed -i -e "s/cmdl(\['modprobe'/call(['modprobe'/" -e "s/cmdl(\['rmmod'/call(['rmmod'/" /usr/libexec/vyos/conf_mode/system_conntrack.py
for i in $(seq 1 30); do
  sleep 4
  echo "$(date) aplicando $CFG (intento $i)"
  sg vyattacfg -c "/bin/vbash $CFG" 2>&1 | tail -3
  /bin/vbash -c "source /opt/vyatta/etc/functions/script-template; cli-shell-api existsActive firewall group network-group $MARCA" && break
done
chgrp -R vyattacfg /opt/vyatta/config/tmp /opt/vyatta/config/active 2>/dev/null; chmod -R g+rwX /opt/vyatta/config/tmp /opt/vyatta/config/active 2>/dev/null; chmod g+s /opt/vyatta/config/tmp
echo "$(date) listo"; touch /tmp/fw-listo
