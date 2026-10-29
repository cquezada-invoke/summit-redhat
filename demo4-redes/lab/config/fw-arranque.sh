#!/bin/bash
# Se ejecuta dentro del contenedor VyOS al desplegar: espera a que VyOS termine de arrancar y a que
# containerlab conecte los cables (eth1 WAN, eth2 LAN) y aplica la configuración base del firewall.
for i in $(seq 1 150); do
  systemctl is-active -q vyos-router.service && [ -e /sys/class/net/eth1 ] && [ -e /sys/class/net/eth2 ] && break
  sleep 2
done
# En contenedor VyOS no puede cargar ni descargar módulos del kernel del host: se tolera (el host ya tiene conntrack)
sed -i -e "s/cmdl(\['modprobe'/call(['modprobe'/" -e "s/cmdl(\['rmmod'/call(['rmmod'/" /usr/libexec/vyos/conf_mode/system_conntrack.py
# VyOS hace su propio commit al arrancar: se reintenta mientras la configuración esté bloqueada
for i in $(seq 1 30); do
  sleep 4
  echo "$(date) aplicando configuración base (intento $i)"
  OUT=$(sg vyattacfg -c "/bin/vbash /usr/local/bin/fw-base.vbash" 2>&1); echo "$OUT"
  # Solo se da por aplicada si la configuración base quedó activa (al encender la VM, VyOS puede no tener
  # aún sesión de configuración y el script "termina" sin aplicar nada)
  /bin/vbash -c 'source /opt/vyatta/etc/functions/script-template; cli-shell-api existsActive firewall group network-group red-usuarios' && break
done
# Las sesiones de configuración de los usuarios (Ansible entra como vyos) se crean en config/tmp: si el arranque
# la dejó de root:root, "configure" falla con "Failed to set up config session"
chgrp -R vyattacfg /opt/vyatta/config/tmp && chmod -R g+rwX /opt/vyatta/config/tmp && chmod g+s /opt/vyatta/config/tmp
echo "$(date) listo"
touch /tmp/fw-listo
