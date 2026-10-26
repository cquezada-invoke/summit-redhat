#!/bin/bash
# Demo 5 · monitoreo: deja los tres casos listos para repetir (consumidor activo y cola vacía, certificado de
# 90 días, alerta de Keycloak terminada e IP desbloqueada). Los incidentes de ServiceNow se mantienen.
D=$(cd "$(dirname "$0")/../.." && pwd)
ssh nodo-tomcat 'sudo systemctl start consumidor-pagos'
ssh nodo-nginx "sudo openssl req -x509 -newkey rsa:2048 -nodes -days 90 -subj /CN=app.summit.lab \
  -keyout /etc/pki/nginx/private/summit.key -out /etc/pki/nginx/summit.crt 2>/dev/null && sudo systemctl reload nginx"
AHORA=$(date -u +%Y-%m-%dT%H:%M:%SZ)
curl -s -o /dev/null -H 'Content-Type: application/json' http://127.0.0.1:9093/api/v2/alerts \
  -d "[{\"labels\":{\"alertname\":\"LoginsFallidosKeycloak\",\"ip\":\"203.0.113.50\",\"intentos\":\"60\",\"producto\":\"Keycloak\",\"severidad\":\"alta\"},\"endsAt\":\"$AHORA\"}]"
bash "$D/demo4-redes/herramientas/reiniciar-seguridad.sh" >/dev/null 2>&1
for i in $(seq 1 30); do
  M=$(curl -s 127.0.0.1:9410/metrics | sed -n 's/^artemis_cola_mensajes{cola="pagos.entrada"} //p'); [ "${M:-0}" -lt 10 ] && break; sleep 3
done
echo "pagos.entrada: ${M:-?} mensajes · certificado: 90 días · alerta de Keycloak terminada · IP desbloqueada"
