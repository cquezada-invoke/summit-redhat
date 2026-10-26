#!/bin/bash
# Se ejecuta dentro del contenedor VyOS al desplegar: espera a que VyOS termine de arrancar y a que
# containerlab conecte los cables (eth1 WAN, eth2 LAN) y aplica la configuración base del firewall.
for i in $(seq 1 150); do
  systemctl is-active -q vyos-router.service && [ -e /sys/class/net/eth1 ] && [ -e /sys/class/net/eth2 ] && break
  sleep 2
done
sleep 3
echo "$(date) aplicando configuración base"
sg vyattacfg -c "/bin/vbash /usr/local/bin/fw-base.vbash"
echo "$(date) listo: $?"
touch /tmp/fw-listo
