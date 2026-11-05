#!/bin/bash
# Publica la consola web de Red Hat Satellite por el proxy público de FaceOps (TLS de Let's Encrypt):
#   https://satellite.<ip>.sslip.io:<puerto>/  →  dc1-sat01 (443)
# Crea además un usuario nominal de Satellite para el dueño de la demo (la clave de admin sigue solo en la bóveda).
# Idempotente. Uso: bash publicar-satellite.sh [equipo] [usuario]
set -euo pipefail
EQ=${1:-dc1-sat01}; USR=${2:-cquezada}
IP=${FACEOPS_IP_PUBLICA:-34.176.42.56}; PUERTO=${FACEOPS_PUERTO_PUBLICO:-8843}
DOM=satellite.$IP.sslip.io; D=/var/lib/faceops-publico
SIP=$(sudo podman exec faceops python3 -c "import sqlite3;print(sqlite3.connect('/var/lib/faceops/faceops.db').execute(\"SELECT ip FROM equipos WHERE nombre='$EQ'\").fetchone()[0])")
echo "== Certificado para $DOM"
sudo test -s $D/letsencrypt/live/$DOM/fullchain.pem || sudo podman run --rm -v $D/www:/var/www:z -v $D/letsencrypt:/etc/letsencrypt:z docker.io/certbot/certbot:v3.0.1 \
  certonly --webroot -w /var/www -d "$DOM" --agree-tos --register-unsafely-without-email --non-interactive -q
echo "== Proxy $DOM:$PUERTO → $EQ ($SIP)"
sudo tee $D/conf/satellite.conf >/dev/null <<C
server {
    listen $PUERTO ssl;
    http2 on;
    server_name $DOM;
    ssl_certificate /etc/letsencrypt/live/$DOM/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOM/privkey.pem;
    client_max_body_size 50m;
    location / {
        limit_req zone=general burst=80 nodelay;
        proxy_pass https://$SIP:443;
        proxy_ssl_verify off;
        proxy_set_header Host $EQ.invoke.lab;
        proxy_set_header Origin https://$EQ.invoke.lab;   # la protección CSRF de Satellite compara el origen con su propio nombre
        proxy_set_header Referer https://$EQ.invoke.lab/;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_redirect https://$EQ.invoke.lab/ https://$DOM:$PUERTO/;
        proxy_cookie_domain $EQ.invoke.lab $DOM;
        proxy_read_timeout 300s;
    }
}
C
sudo podman exec faceops-publico nginx -t -q && sudo podman exec faceops-publico nginx -s reload
echo "== Usuario nominal en Satellite: $USR"
K=$(mktemp); sudo podman exec faceops cat /var/lib/faceops/llaves/id_demo > $K; chmod 600 $K
CL=/var/lib/faceops-publico/satellite-$USR.clave
sudo test -s $CL || { printf 'Sat-%s' "$(openssl rand -hex 5)" | sudo tee $CL >/dev/null; sudo chmod 600 $CL; }
CLAVE=$(sudo cat $CL)
ssh -i $K -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ansible@$SIP \
  "sudo bash -c 'H=\"hammer -u admin -p \$(cat /etc/satellite-faceops/clave)\"; \
   \$H user info --login $USR >/dev/null 2>&1 && \$H user update --login $USR --password \"$CLAVE\" >/dev/null || \
   \$H user create --login $USR --firstname Cristian --lastname Quezada --mail cquezada@invoke.cl --auth-source-id 1 --password \"$CLAVE\" --admin true --organizations Invoke --default-organization Invoke >/dev/null; echo usuario-ok'"
rm -f $K
curl -sk -o /dev/null -w "consola: %{http_code}\n" https://localhost:$PUERTO/users/login -H "Host: $DOM" --resolve $DOM:$PUERTO:127.0.0.1
