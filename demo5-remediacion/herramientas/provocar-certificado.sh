#!/bin/bash
# Demo 5 · monitoreo · caso 2: instala en Nginx un certificado HTTPS que vence en pocos días.
# Prometheus (blackbox) lo detecta (< 15 días), EDA lanza AAP y el certificado se renueva sin corte.
DIAS=${1:-5}
ssh nodo-nginx "sudo openssl req -x509 -newkey rsa:2048 -nodes -days $DIAS -subj /CN=app.summit.lab \
  -keyout /etc/pki/nginx/private/summit.key -out /etc/pki/nginx/summit.crt 2>/dev/null && sudo systemctl reload nginx"
echo "1) Certificado de Nginx reemplazado: vence en $DIAS días"
echo "2) Míralo en Grafana: http://34.176.42.56:8090/grafana/  → en ~30 s AAP lo renueva por 90 días"
