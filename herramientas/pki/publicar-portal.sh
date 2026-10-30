#!/bin/bash
# Portal público (solo lectura) de la CA interna: https://pki.<ip>.sslip.io
#   SNI del 443 → proxy del acceso público (127.0.0.1:10444, certificado Let's Encrypt) → Dogtag https://127.0.0.1:18443
# Solo GET/HEAD y solo las páginas de entidad final (ver la CA, buscar y listar certificados emitidos).
# Las páginas de agente y administración quedan bloqueadas aquí (exigen certificado de cliente; se usan dentro de la VM).
set -euo pipefail
IP=${IP_PUBLICA:-34.176.42.56}; DOM="pki.$IP.sslip.io"; P=/var/lib/faceops-publico
paso() { echo "== $*"; }
paso "Certificado Let's Encrypt para $DOM"
sudo test -s $P/letsencrypt/live/$DOM/fullchain.pem || sudo podman run --rm -v $P/www:/var/www:z -v $P/letsencrypt:/etc/letsencrypt:z \
  docker.io/certbot/certbot:v3.0.1 certonly --webroot -w /var/www -d "$DOM" --agree-tos --register-unsafely-without-email --non-interactive -q
paso "Proxy del portal (127.0.0.1:10444)"
sudo tee $P/conf/pki.conf >/dev/null <<C
server {
    listen 127.0.0.1:10444 ssl;
    http2 on;
    server_name $DOM;
    ssl_certificate /etc/letsencrypt/live/$DOM/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOM/privkey.pem;
    add_header X-Frame-Options SAMEORIGIN always;
    location = / { return 302 /ca/ee/ca/; }
    location ~ ^/ca/(agent|admin)/ { return 403; }
    location ~ ^/(ca/(ee|rest|ui|css|js|images|fonts|pki)|pki/|ca/ee) {
        limit_except GET HEAD { deny all; }
        proxy_pass https://127.0.0.1:18443;
        proxy_ssl_verify off;
        proxy_set_header Host \$host;
        proxy_redirect https://127.0.0.1:18443/ /;
        proxy_redirect http://127.0.0.1:18080/ /;
    }
    location / { return 404; }
}
C
sudo podman exec faceops-publico nginx -t -q && sudo podman exec faceops-publico nginx -s reload
paso "Enrutador SNI: $DOM → portal"
if ! sudo grep -q 10444 /var/lib/faceops-sni/nginx.conf; then
  sudo sed -i "s/ sno; default aap; }/ sno; $DOM pki; default aap; }/; s/\"~^sno:\" 192.168.122.50:443;/\"~^sno:\" 192.168.122.50:443; \"~^pki:\" 127.0.0.1:10444;/" /var/lib/faceops-sni/nginx.conf
fi
sudo systemctl restart faceops-sni && sleep 3  # sed -i cambia el inodo: el archivo montado solo se ve al reiniciar
sleep 2
for u in /ca/ee/ca/ /ca/ee/ca/listCerts /ca/agent/ca/; do
  curl -s -o /dev/null -w "$u → %{http_code}\n" --resolve $DOM:10443:127.0.0.1 https://$DOM:10443$u
done
echo "LISTO · https://$DOM"
