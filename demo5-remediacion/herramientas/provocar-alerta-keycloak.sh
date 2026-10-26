#!/bin/bash
# Demo 5 · monitoreo · caso 3: alerta de seguridad "muchos logins fallidos en Keycloak" desde una IP de Internet.
# Se simula la alerta (no se generan intentos reales): Alertmanager → EDA → "Red · Bloquear IP" (caso de la Demo 4):
# la IP queda bloqueada en el firewall, se abre un incidente en ServiceNow y llega un correo.
IP=${1:-203.0.113.50}; N=${2:-60}
alcance(){ sudo podman exec clab-summit-red-isp timeout 4 nc -z -w3 -s "$IP" 10.20.20.2 22 >/dev/null 2>&1 && echo "LLEGA" || echo "BLOQUEADA"; }
echo "1) Desde Internet ($IP): $(alcance)"
FIN=$(date -u -d '+10 min' +%Y-%m-%dT%H:%M:%SZ)
curl -s -o /dev/null -w "2) Alerta enviada a Alertmanager (%{http_code}): $N logins fallidos en Keycloak desde $IP\n" \
  -H 'Content-Type: application/json' http://127.0.0.1:9093/api/v2/alerts -d "[{\"labels\":{\"alertname\":\"LoginsFallidosKeycloak\",\"ip\":\"$IP\",\"intentos\":\"$N\",\"producto\":\"Keycloak\",\"severidad\":\"alta\"},\"annotations\":{\"resumen\":\"$N logins fallidos en Keycloak desde $IP en 1 minuto\"},\"endsAt\":\"$FIN\"}]"
printf "3) Esperando el bloqueo"
for i in $(seq 1 30); do [ "$(alcance)" = BLOQUEADA ] && break; printf "."; sleep 2; done
echo; echo "4) Desde Internet ($IP): $(alcance)"
