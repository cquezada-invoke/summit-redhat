#!/bin/bash
# Certificados públicos (Let's Encrypt) para las aplicaciones del SNO publicadas en internet (*.sno.<IP>.sslip.io).
# El certificado de la CA interna (*.apps.sno.invoke.lab) sirve dentro de la red; en internet el navegador necesita uno
# público y con el nombre exacto de la ruta. Por cada ruta pública (edge o reencrypt, salvo las de consola, descargas y
# OAuth, que ya tienen el suyo por componentRoutes): certificado Let's Encrypt validado por HTTP en el 80 del proxy
# público y la ruta queda con él. Se instala un temporizador diario que renueva y vuelve a aplicar.
# Uso: certificados-publicos-sno.sh   (idempotente)
set -euo pipefail
IP=${IP_PUBLICA:-34.176.42.56}
SUF="sno.$IP.sslip.io"
D=$HOME/openshift/sno; export KUBECONFIG=$D/instalar/auth/kubeconfig; OC="$D/bin/oc"
P=/var/lib/faceops-publico
CERTBOT="sudo podman run --rm -v $P/www:/var/www:z -v $P/letsencrypt:/etc/letsencrypt:z docker.io/certbot/certbot:v3.0.1"

$OC get route -A -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{" "}{.spec.host}{" "}{.spec.tls.termination}{"\n"}{end}' |
while read -r ns nombre host term; do
  case "$host" in *."$SUF") ;; *) continue ;; esac
  case "$host" in console."$SUF"|downloads."$SUF"|oauth."$SUF") continue ;; esac
  [ "$term" = edge ] || [ "$term" = reencrypt ] || { echo "· $ns/$nombre ($term): sin TLS en el router, se omite"; continue; }
  # pedir o renovar (certbot solo renueva si faltan menos de 30 días)
  $CERTBOT certonly --webroot -w /var/www -d "$host" --agree-tos --register-unsafely-without-email --non-interactive \
    --keep-until-expiring -q
  crt=$(sudo cat $P/letsencrypt/live/$host/fullchain.pem); key=$(sudo cat $P/letsencrypt/live/$host/privkey.pem)
  actual=$($OC -n "$ns" get route "$nombre" -o jsonpath='{.spec.tls.certificate}')
  if [ "$actual" != "$crt" ]; then
    python3 - "$ns" "$nombre" <<PY | $OC -n "$ns" patch route "$nombre" --type=merge --patch-file=/dev/stdin >/dev/null
import json, sys
print(json.dumps({"spec": {"tls": {"certificate": """$crt""", "key": """$key"""}}}))
PY
    echo "· $ns/$nombre: certificado público aplicado ($host)"
  else
    echo "· $ns/$nombre: al día ($host)"
  fi
done

# Renovación diaria
if [ ! -f /etc/systemd/system/sno-certificados-publicos.timer ]; then
  sudo install -m 0755 "$(readlink -f "$0")" /usr/local/bin/sno-certificados-publicos.sh
  sudo tee /etc/systemd/system/sno-certificados-publicos.service >/dev/null <<U
[Unit]
Description=Certificados públicos (Let's Encrypt) de las rutas del SNO
After=network-online.target
[Service]
Type=oneshot
User=$USER
ExecStart=/usr/local/bin/sno-certificados-publicos.sh
U
  sudo tee /etc/systemd/system/sno-certificados-publicos.timer >/dev/null <<U
[Unit]
Description=Renovación diaria de los certificados públicos del SNO
[Timer]
OnCalendar=*-*-* 04:17
Persistent=true
[Install]
WantedBy=timers.target
U
  sudo systemctl daemon-reload && sudo systemctl enable --now sno-certificados-publicos.timer >/dev/null
  echo "· renovación diaria instalada (04:17)"
fi
echo LISTO
