#!/bin/bash
# Demo 4 · caso "IP sospechosa": simula la alerta que enviaría un SIEM (Splunk, QRadar, Wazuh…) cuando detecta
# muchos intentos de conexión desde una misma IP de Internet. Event-Driven Ansible la recibe y AAP bloquea la IP
# en el firewall, abre un incidente en ServiceNow y avisa por correo.
# Uso: simular-alerta-siem.sh [ip] [intentos]     (por defecto 203.0.113.50 y 45)
IP=${1:-203.0.113.50}; N=${2:-45}
alcance(){ sudo podman exec clab-summit-red-isp timeout 4 nc -z -w3 -s "$IP" 10.20.20.2 22 >/dev/null 2>&1 && echo "LLEGA" || echo "BLOQUEADA"; }
echo "1) Desde Internet ($IP) hacia el firewall: $(alcance)"
echo "2) SIEM: $N intentos de conexión SSH desde $IP en 30 s → alerta a Event-Driven Ansible"
curl -s -o /dev/null -w "   EDA respondió %{http_code}\n" -H 'Content-Type: application/json' http://127.0.0.1:5003/endpoint \
  -d "{\"tipo\":\"alerta_seguridad\",\"fuente\":\"SIEM (simulado)\",\"regla\":\"Fuerza bruta SSH\",\"ip\":\"$IP\",\"intentos\":$N,\"ventana_s\":30,\"destino\":\"firewall WAN 10.20.20.2:22\"}"
printf "3) Esperando el bloqueo"
for i in $(seq 1 30); do [ "$(alcance)" = BLOQUEADA ] && break; printf "."; sleep 2; done
echo; echo "4) Desde Internet ($IP) hacia el firewall: $(alcance)"
echo "   Incidente en ServiceNow: https://dev459364.service-now.com/incident_list.do?sysparm_query=short_descriptionSTARTSWITH%5BDemo%204%20%C2%B7%20Seguridad%5D%5EORDERBYDESCsys_created_on"
