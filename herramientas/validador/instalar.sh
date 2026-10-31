#!/bin/bash
# FaceOps Admin · validador de confianza «en la nube» (demo: misma cuenta GCP, dominio público propio). Idempotente.
#  · contenedor faceops-validador (127.0.0.1:8120, datos y secreto maestro en /var/lib/faceops-validador)
#  · https://validador.<ip>.sslip.io: certificado Let's Encrypt, enrutador SNI del 443
#  · FaceOps llega por el dominio público (DNS de internet); dentro de la VM el 443 público se resuelve en el enrutador
#  · registra esta instalación de FaceOps con un código de enrolamiento de un solo uso (no se imprime)
set -euo pipefail
IP=${IP_PUBLICA:-34.176.42.56}; DOM="validador.$IP.sslip.io"; P=/var/lib/faceops-publico; V=/var/lib/faceops-validador
R=$(cd "$(dirname "$0")/../.." && pwd)
paso() { echo "== $*"; }

paso "Imagen y contenedor del validador"
sudo podman build -q -t localhost/faceops-validador:latest -f $R/faceops/validador/Containerfile $R/faceops/validador >/dev/null
sudo install -d -m 0700 $V
sudo tee /etc/containers/systemd/faceops-validador.container >/dev/null <<U
[Unit]
Description=FaceOps Admin · validador de confianza
After=network-online.target
[Container]
Image=localhost/faceops-validador:latest
ContainerName=faceops-validador
Network=host
Volume=$V:/data:Z
User=0
[Service]
Restart=always
[Install]
WantedBy=multi-user.target
U
sudo systemctl daemon-reload && sudo systemctl restart faceops-validador && sleep 4
sudo podman exec faceops-validador python3 /opt/app-root/src/validador.py admin-token
curl -sf http://127.0.0.1:8120/v1/salud >/dev/null && echo "   · validador responde"

paso "Dominio público $DOM (Let's Encrypt + enrutador SNI)"
sudo test -s $P/letsencrypt/live/$DOM/fullchain.pem || sudo podman run --rm -v $P/www:/var/www:z -v $P/letsencrypt:/etc/letsencrypt:z \
  docker.io/certbot/certbot:v3.0.1 certonly --webroot -w /var/www -d "$DOM" --agree-tos --register-unsafely-without-email --non-interactive -q
sudo tee $P/conf/validador.conf >/dev/null <<C
server {
    listen 127.0.0.1:10445 ssl;
    http2 on;
    server_name $DOM;
    ssl_certificate /etc/letsencrypt/live/$DOM/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOM/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000" always;
    location / { proxy_pass http://127.0.0.1:8120; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header Host \$host; }
}
C
sudo podman exec faceops-publico nginx -t -q && sudo podman exec faceops-publico nginx -s reload
if ! sudo grep -q 10445 /var/lib/faceops-sni/nginx.conf; then
  sudo sed -i "s/ pki; default aap; }/ pki; $DOM val; default aap; }/; s/\"~^pki:\" 127.0.0.1:10444;/\"~^pki:\" 127.0.0.1:10444; \"~^val:\" 127.0.0.1:10445;/" /var/lib/faceops-sni/nginx.conf
  sudo systemctl restart faceops-sni && sleep 3
fi
# lo que sale de esta VM hacia su propia IP pública (FaceOps → dominio del validador) entra por el enrutador (sin hairpin en GCP)
regla="-d $IP -p tcp --dport 443 -j REDIRECT --to-ports 10443"
sudo firewall-cmd -q --direct --query-rule ipv4 nat OUTPUT 0 $regla || sudo firewall-cmd -q --direct --add-rule ipv4 nat OUTPUT 0 $regla
sudo firewall-cmd -q --permanent --direct --query-rule ipv4 nat OUTPUT 0 $regla || sudo firewall-cmd -q --permanent --direct --add-rule ipv4 nat OUTPUT 0 $regla
curl -s -o /dev/null -w "   · https://$DOM/v1/salud → %{http_code} (certificado %{ssl_verify_result})\n" https://$DOM/v1/salud

paso "FaceOps con la validación obligatoria"
cd $R && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 12

paso "Registro de esta instalación (código de un solo uso)"
if sudo test -s /var/lib/faceops/confianza/instancia.json; then
  echo "   · ya registrada"
else
  sudo podman exec faceops-validador python3 /opt/app-root/src/validador.py codigo "Invoke · Managed Service" /data/codigo-enrolamiento >/dev/null
  COD=$(sudo cat $V/codigo-enrolamiento); sudo rm -f $V/codigo-enrolamiento
  sudo podman exec -e COD="$COD" faceops python3 -c "
import os
from app.confianza import registrar
print('   · instancia', registrar(os.environ['COD'], 'FaceOps · Invoke Lab (demo-ansible-summit)'))"
  unset COD
fi

paso "Token del panel de administración en la bóveda (faceops/web/faceops-admin)"
TOK_FILE=$V/admin-token DOM=$DOM python3 - <<'PY'
import json, os, subprocess, tempfile, urllib.request
tok = subprocess.run(["sudo", "cat", os.environ["TOK_FILE"]], capture_output=True, text=True, check=True).stdout.strip()
llave = subprocess.run(["sudo", "podman", "exec", "faceops", "cat", "/var/lib/faceops/llaves/id_demo"], capture_output=True, text=True, check=True).stdout
fd, kf = tempfile.mkstemp(); os.write(fd, llave.encode()); os.close(fd); os.chmod(kf, 0o600)
try:
    out = subprocess.run(["ssh", "-i", kf, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                          "ansible@192.168.122.210", "sudo cat /root/vault-init.json"], capture_output=True, text=True, check=True).stdout
finally:
    os.remove(kf)
r = urllib.request.Request("http://192.168.122.210:8200/v1/faceops/data/web/faceops-admin", method="POST",
    data=json.dumps({"data": {"sitio": "FaceOps Admin · validador", "usuario": "token", "clave": tok,
                              "url": f"https://{os.environ['DOM']}/admin?t=<token>"}}).encode(),
    headers={"X-Vault-Token": json.loads(out)["root_token"], "Content-Type": "application/json"})
urllib.request.urlopen(r, timeout=15).read()
print("   · guardado")
PY
echo "LISTO · https://$DOM"
