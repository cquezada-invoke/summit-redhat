#!/bin/bash
# Portal de automatización (demo Claro PR): instala todo en el anfitrión del laboratorio. Idempotente.
#   1. Amdocs emulado en OpenShift (namespace amdocs-bss).
#   2. AAP y EDA (configurar_aap.py): repositorio, credencial desde Vault, plantillas, workflows con aprobación, activación EDA.
#   3. El portal como contenedor (Quadlet, 192.168.122.1:8120) publicado en https://ansible.<ip>.sslip.io:<puerto>/ con TLS.
set -euo pipefail
cd "$(dirname "$0")"
IP=${FACEOPS_IP_PUBLICA:-34.176.42.56}; PUERTO=${FACEOPS_PUERTO_PUBLICO:-8843}
DOM=ansible.$IP.sslip.io; D=/var/lib/faceops-publico
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc
paso() { echo "== $*"; }

paso "Amdocs emulado en OpenShift"
$OC apply -f amdocs-emulado/amdocs.yaml >/dev/null
$OC -n amdocs-bss start-build amdocs-emulado --from-dir=amdocs-emulado --wait 2>&1 | tail -1
for d in ordering customer catalog; do $OC -n amdocs-bss rollout restart deploy/amdocs-$d >/dev/null; done
for d in ordering customer catalog; do $OC -n amdocs-bss rollout status deploy/amdocs-$d --timeout=180s | tail -1; done
sudo firewall-cmd -q --zone=libvirt --query-port=5011/tcp || { sudo firewall-cmd -q --zone=libvirt --add-port=5011/tcp; sudo firewall-cmd -q --permanent --zone=libvirt --add-port=5011/tcp; }

paso "AAP y EDA"
python3 configurar_aap.py "$@"

paso "Portal (contenedor)"
sudo podman build -q -t localhost/portal-automatizacion:latest -f Containerfile . >/dev/null
S=/etc/portal-automatizacion.env
sudo test -s $S || { echo "PORTAL_SECRETO=$(head -c 32 /dev/urandom | base64 | tr -dc A-Za-z0-9)" | sudo tee $S >/dev/null; sudo chmod 600 $S; }
sudo tee /etc/containers/systemd/portal-automatizacion.container >/dev/null <<U
[Unit]
Description=Portal de automatización (catálogo de AAP con aprobación)
After=network-online.target
[Container]
Image=localhost/portal-automatizacion:latest
ContainerName=portal-automatizacion
Network=host
EnvironmentFile=$S
Environment=TZ=America/Santiago AAP_URL=https://localhost AMDOCS_URL=https://amdocs-ordering.sno.$IP.sslip.io
Exec=gunicorn --workers 1 --threads 16 --bind 192.168.122.1:8120 --access-logfile - portal:app
[Service]
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
U
sudo systemctl daemon-reload
sudo systemctl restart portal-automatizacion
sudo firewall-cmd -q --zone=libvirt --query-port=8120/tcp || sudo firewall-cmd -q --zone=libvirt --add-port=8120/tcp

paso "Publicación: https://$DOM:$PUERTO/"
sudo test -s $D/letsencrypt/live/$DOM/fullchain.pem || sudo podman run --rm -v $D/www:/var/www:z -v $D/letsencrypt:/etc/letsencrypt:z docker.io/certbot/certbot:v3.0.1 \
  certonly --webroot -w /var/www -d "$DOM" --agree-tos --register-unsafely-without-email --non-interactive -q
sudo tee $D/conf/portal-automatizacion.conf >/dev/null <<C
server {
    listen $PUERTO ssl;
    http2 on;
    server_name $DOM;
    ssl_certificate /etc/letsencrypt/live/$DOM/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOM/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    location = /ingresar {
        limit_req zone=login burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://192.168.122.1:8120;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$remote_addr;
    }
    location / {
        limit_req zone=general burst=80 nodelay;
        proxy_pass http://192.168.122.1:8120;
        proxy_read_timeout 120s;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$remote_addr;
    }
}
C
sudo podman exec faceops-publico nginx -t -q && sudo podman exec faceops-publico nginx -s reload
for i in $(seq 1 20); do curl -sk -o /dev/null -w "%{http_code}" https://$DOM:$PUERTO/ingresar | grep -q 200 && break; sleep 2; done
echo "Portal: https://$DOM:$PUERTO/ ($(curl -sk -o /dev/null -w '%{http_code}' https://$DOM:$PUERTO/ingresar))"
