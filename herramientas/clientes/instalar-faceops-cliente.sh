#!/bin/bash
# FaceOps de un cliente en su propia VM de GCP (junto a su AAP, ya instalado con instalar-aap-cliente.sh).
# Se corre desde Invoke Lab. Deja en la VM del cliente:
#   · el código desde el repositorio central (GitHub) en ~/faceops-repo y la imagen localhost/faceops
#   · el servicio faceops (https://<ip-pública>:8843/faceops/, certificado propio) conectado a su AAP local
#   · faceops-actualizar.service: en cada encendido trae la última versión del repositorio central, reconstruye y reinicia
#   · virtualización (libvirt/KVM) para sus máquinas de laboratorio
# Uso: instalar-faceops-cliente.sh <ip-privada> <codigo> "<Nombre del cliente>"   (idempotente)
set -euo pipefail
IP=$1; COD=$2; NOM=$3
REPO=${FACEOPS_REPO:-https://github.com/cquezada-invoke/summit-redhat.git}
S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR cquezada@$IP"
ENV=~/faceops/clientes/$COD.env; . "$ENV"
H=aap-$COD.invoke.lab

echo "· $COD: paquetes (git, virtualización)"
$S "sudo dnf -q -y install git-core libvirt qemu-kvm virt-install >/dev/null 2>&1; sudo systemctl enable --now libvirtd >/dev/null 2>&1; true"

echo "· $COD: token de AAP para FaceOps"
if ! grep -q '^FACEOPS_AAP_TOKEN=' "$ENV"; then
  T=$($S "curl -sk -u admin:$ADMIN -H 'Content-Type: application/json' -d '{\"description\":\"FaceOps\",\"scope\":\"write\"}' https://localhost/api/gateway/v1/tokens/" \
      | python3 -c 'import json,sys;print(json.load(sys.stdin).get("token",""))')
  [ -n "$T" ] && (umask 077; echo "FACEOPS_AAP_TOKEN=$T" >> "$ENV")
  . "$ENV"
fi
[ -n "${FACEOPS_AAP_TOKEN:-}" ] || { echo "sin token de AAP"; exit 1; }

echo "· $COD: configuración de la instancia"
PUB=$($S 'curl -s -H "Metadata-Flavor: Google" http://metadata/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip')
$S "sudo install -d -m 700 /etc/faceops; sudo tee /etc/faceops.env >/dev/null; sudo chmod 600 /etc/faceops.env" <<E
FACEOPS_CLIENTE_CODIGO=$COD
FACEOPS_CLIENTE_NOMBRE=$NOM
FACEOPS_INSTANCIA=$COD
FACEOPS_ROL=cliente
FACEOPS_AAP_URL=https://$H
FACEOPS_AAP_TOKEN=$FACEOPS_AAP_TOKEN
FACEOPS_AAP_PUBLICA=https://$PUB
SCRIPT_NAME=/faceops
E

echo "· $COD: certificado de la consola (propio, para https://$PUB.sslip.io:8843)"
$S "sudo test -s /etc/faceops/tls.crt || sudo openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj '/CN=faceops-$COD' \
    -addext 'subjectAltName=DNS:faceops.$PUB.sslip.io,IP:$PUB' -keyout /etc/faceops/tls.key -out /etc/faceops/tls.crt >/dev/null 2>&1; \
    sudo chmod 644 /etc/faceops/tls.crt /etc/faceops/tls.key"

echo "· $COD: actualizador desde el repositorio central y servicio"
$S "sudo tee /usr/local/sbin/faceops-actualizar >/dev/null; sudo chmod 755 /usr/local/sbin/faceops-actualizar" <<'A'
#!/bin/bash
# Trae la última versión de FaceOps del repositorio central y, si cambió (o no hay imagen), reconstruye y reinicia.
set -u
R=/home/cquezada/faceops-repo; REPO=$(cat /etc/faceops/repo 2>/dev/null)
[ -d "$R/.git" ] || sudo -u cquezada git clone -q --depth 50 "$REPO" "$R"
antes=$(sudo -u cquezada git -C "$R" rev-parse HEAD 2>/dev/null)
for i in 1 2 3 4 5; do sudo -u cquezada git -C "$R" pull -q --ff-only && break; sleep 15; done
ahora=$(sudo -u cquezada git -C "$R" rev-parse HEAD)
if [ "$antes" != "$ahora" ] || ! podman image exists localhost/faceops:latest; then
  sudo -u cquezada bash "$R/faceops/version.sh" >/dev/null
  podman build -q -t localhost/faceops:latest -f "$R/faceops/Containerfile" "$R/faceops" >/dev/null && echo "FaceOps actualizado a $(git -C "$R" rev-parse --short HEAD)"
  systemctl try-restart faceops
fi
A
$S "echo $REPO | sudo tee /etc/faceops/repo >/dev/null; sudo install -d -o 1001 -g 0 -m 770 /var/lib/faceops"
$S "sudo tee /etc/systemd/system/faceops-actualizar.service >/dev/null" <<'U'
[Unit]
Description=FaceOps · actualizar desde el repositorio central al encender
Wants=network-online.target
After=network-online.target
Before=faceops.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/faceops-actualizar
TimeoutStartSec=1200
[Install]
WantedBy=multi-user.target
U
$S "sudo tee /etc/containers/systemd/faceops.container >/dev/null" <<'U'
[Unit]
Description=FaceOps · consola del cliente
After=network-online.target faceops-actualizar.service
[Container]
Image=localhost/faceops:latest
ContainerName=faceops
Network=host
Volume=/var/lib/faceops:/var/lib/faceops:Z
Volume=/etc/faceops/tls.crt:/etc/faceops/tls.crt:ro,Z
Volume=/etc/faceops/tls.key:/etc/faceops/tls.key:ro,Z
EnvironmentFile=/etc/faceops.env
Environment=TZ=America/Santiago
Exec=gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8843 --certfile /etc/faceops/tls.crt --keyfile /etc/faceops/tls.key --access-logfile - wsgi:app
[Service]
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
U
$S "grep -q ' $H' /etc/hosts || echo '$IP $H' | sudo tee -a /etc/hosts >/dev/null; sudo firewall-cmd -q --add-port=8843/tcp 2>/dev/null; sudo firewall-cmd -q --permanent --add-port=8843/tcp 2>/dev/null; true"
$S "sudo systemctl daemon-reload; sudo systemctl enable faceops-actualizar >/dev/null 2>&1; sudo systemctl start faceops-actualizar; sudo systemctl restart faceops"
sleep 20
echo "· $COD: $($S "curl -sk -o /dev/null -w '%{http_code}' https://localhost:8843/faceops/version") · https://faceops.$PUB.sslip.io:8843/faceops/"
$S "curl -sk https://localhost:8843/faceops/version; echo"
