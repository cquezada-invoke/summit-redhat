#!/bin/bash
# Demo 5 · monitoreo: deja los tres casos listos para repetir (consumidor activo y cola vacía, certificado de
# 90 días, alerta de Keycloak terminada e IP desbloqueada). Los incidentes de ServiceNow se mantienen.
D=$(cd "$(dirname "$0")/../.." && pwd)
ssh nodo-tomcat 'sudo systemctl start consumidor-pagos'
ssh nodo-nginx "sudo openssl req -x509 -newkey rsa:2048 -nodes -days 90 -subj /CN=app.summit.lab \
  -keyout /etc/pki/nginx/private/summit.key -out /etc/pki/nginx/summit.crt 2>/dev/null && sudo systemctl reload nginx"
ssh nodo-tomcat 'sudo rm -f /var/lib/node_exporter/textfile/keycloak.prom'   # sin logins fallidos: la alerta de Keycloak se cierra
bash "$D/demo4-redes/herramientas/reiniciar-seguridad.sh" >/dev/null 2>&1
for i in $(seq 1 30); do
  M=$(curl -s 127.0.0.1:9410/metrics | sed -n 's/^artemis_cola_mensajes{cola="pagos.entrada"} //p'); [ "${M:-0}" -lt 10 ] && break; sleep 3
done
echo "pagos.entrada: ${M:-?} mensajes · certificado: 90 días · alerta de Keycloak terminada · IP desbloqueada"
