#!/bin/bash
# FaceOps · acceso público por identidad (sin listas de IPs):
#   https://faceops.<ip>.sslip.io:<puerto>/  → proxy nginx (contenedor) con certificado de Let's Encrypt → FaceOps
# Solo se publica FaceOps (y su consola web de un solo uso). FaceOps exige clave + doble factor (TOTP) a todo acceso que
# llega por aquí (encabezado X-FaceOps-Acceso: publico, que el proxy siempre pisa) y frena la fuerza bruta.
# El proxy además limita la tasa de peticiones al login. Idempotente. Renovación del certificado: timer semanal.
set -euo pipefail
IP=${FACEOPS_IP_PUBLICA:-34.176.42.56}
DOM=${FACEOPS_DOMINIO:-faceops.$IP.sslip.io}
PUERTO=${FACEOPS_PUERTO_PUBLICO:-8843}
D=/var/lib/faceops-publico
paso() { echo "== $*"; }
sudo install -d -m 0755 $D $D/www $D/letsencrypt $D/conf

paso "Firewall del anfitrión: 80 (solo validación del certificado y redirección) y $PUERTO (FaceOps con TLS)"
for p in 80 $PUERTO; do
  sudo firewall-cmd -q --query-port=$p/tcp || sudo firewall-cmd -q --add-port=$p/tcp
  sudo firewall-cmd -q --permanent --query-port=$p/tcp || sudo firewall-cmd -q --permanent --add-port=$p/tcp
done

escribir_conf() {  # $1: con o sin TLS
sudo tee $D/conf/faceops.conf >/dev/null <<C
limit_req_zone \$binary_remote_addr zone=login:10m rate=10r/m;
limit_req_zone \$binary_remote_addr zone=general:10m rate=20r/s;
server_tokens off;
server {
    listen 80;
    server_name _;
    location /.well-known/acme-challenge/ { root /var/www; }
    location / { return 301 https://$DOM:$PUERTO\$request_uri; }
}
C
if [ "$1" = tls ]; then
sudo tee -a $D/conf/faceops.conf >/dev/null <<C
server {
    listen $PUERTO ssl;
    http2 on;
    server_name $DOM;
    ssl_certificate /etc/letsencrypt/live/$DOM/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOM/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;
    client_max_body_size 20m;
    location = / { return 302 /faceops/; }
    location ~ ^/faceops/(ingresar|ingresar/codigo)\$ {
        limit_req zone=login burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://192.168.122.1:8110/\$1;
        include /etc/nginx/conf.d/faceops-cabeceras.inc;
    }
    location /faceops/ {
        limit_req zone=general burst=60 nodelay;
        proxy_pass http://192.168.122.1:8110/;
        include /etc/nginx/conf.d/faceops-cabeceras.inc;
    }
    location /consola/ {
        proxy_pass http://192.168.122.1:8111/consola/;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$http_connection;
        proxy_set_header Host \$http_host;
        proxy_read_timeout 3600s;
    }
    location / { return 404; }
}
C
fi
sudo tee $D/conf/faceops-cabeceras.inc >/dev/null <<'C'
proxy_set_header Host $http_host;
proxy_set_header X-Forwarded-Prefix /faceops;
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-FaceOps-Acceso publico;
proxy_read_timeout 120s;
C
}

arrancar() {  # quadlet: arranca con la VM
  sudo podman rm -f faceops-publico >/dev/null 2>&1 || true
  sudo tee /etc/containers/systemd/faceops-publico.container >/dev/null <<U
[Unit]
Description=FaceOps · proxy público con TLS
After=network-online.target faceops.service
[Container]
Image=docker.io/library/nginx:1.27-alpine
ContainerName=faceops-publico
Network=host
Volume=$D/conf:/etc/nginx/conf.d:z,ro
Volume=$D/www:/var/www:z,ro
Volume=$D/letsencrypt:/etc/letsencrypt:z,ro
[Service]
Restart=always
[Install]
WantedBy=multi-user.target
U
  sudo systemctl daemon-reload && sudo systemctl restart faceops-publico
  sleep 3; sudo podman exec faceops-publico nginx -t -q
}

if ! sudo test -s $D/letsencrypt/live/$DOM/fullchain.pem; then
  paso "Certificado de Let's Encrypt para $DOM (validación HTTP en el puerto 80)"
  escribir_conf sin-tls; arrancar
  sudo podman run --rm -v $D/www:/var/www:z -v $D/letsencrypt:/etc/letsencrypt:z docker.io/certbot/certbot:v3.0.1 \
    certonly --webroot -w /var/www -d "$DOM" --agree-tos --register-unsafely-without-email --non-interactive -q
fi
paso "Proxy público con TLS en :$PUERTO"
escribir_conf tls; arrancar

paso "Renovación automática del certificado (semanal)"
sudo tee /etc/systemd/system/faceops-publico-cert.service >/dev/null <<U
[Unit]
Description=FaceOps · renovar el certificado público
[Service]
Type=oneshot
ExecStart=/usr/bin/podman run --rm -v $D/www:/var/www:z -v $D/letsencrypt:/etc/letsencrypt:z docker.io/certbot/certbot:v3.0.1 renew -q --webroot -w /var/www
ExecStartPost=/usr/bin/podman exec faceops-publico nginx -s reload
U
sudo tee /etc/systemd/system/faceops-publico-cert.timer >/dev/null <<U
[Unit]
Description=FaceOps · renovar el certificado público (semanal)
[Timer]
OnCalendar=weekly
Persistent=true
[Install]
WantedBy=timers.target
U
sudo systemctl daemon-reload && sudo systemctl enable -q --now faceops-publico-cert.timer
curl -s -o /dev/null -w "FaceOps público: https://$DOM:$PUERTO/ → %{http_code}\n" --resolve "$DOM:$PUERTO:127.0.0.1" "https://$DOM:$PUERTO/faceops/ingresar"
