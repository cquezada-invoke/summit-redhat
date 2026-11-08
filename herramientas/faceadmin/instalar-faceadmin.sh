#!/bin/bash
# FaceAdmin (consola global de clientes, créditos y versiones) en su propia VM de GCP, con su propia base de datos.
# Se corre desde Invoke Lab. Deja en la VM:
#   · el código desde el repositorio central (GitHub) en ~/faceops-repo y la imagen localhost/faceadmin
#   · el servicio faceadmin (https://faceadmin.<ip-pública>.sslip.io:8843/, certificado propio), BD en /var/lib/faceadmin
#   · faceadmin-actualizar.service: en cada encendido trae la última versión, reconstruye y reinicia
# Uso: instalar-faceadmin.sh <host-o-ip-privada>          (idempotente; no imprime secretos)
set -euo pipefail
IP=$1
REPO=${FACEOPS_REPO:-https://github.com/cquezada-invoke/summit-redhat.git}
S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR cquezada@$IP"
pub() { $S "curl -s -H 'Metadata-Flavor: Google' http://metadata/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip"; }
fo() { curl -sk -o /dev/null -w '%{http_code}' "$1/version" 2>/dev/null || true; }

echo "· paquetes"
$S "sudo dnf -q -y install git-core podman firewalld >/dev/null 2>&1; sudo systemctl enable --now firewalld >/dev/null 2>&1; true"
PUB=$(pub)
LAB=https://faceops.34.176.42.56.sslip.io:8843/faceops
echo "· configuración (clientes iniciales y usuario administrador)"
$S "sudo install -d -m 700 /etc/faceadmin; sudo tee /etc/faceadmin.env >/dev/null; sudo chmod 600 /etc/faceadmin.env" <<E
FACEADMIN_ADMIN_USUARIO=cquezada
FACEADMIN_ADMIN_NOMBRE=Cristian Quezada
FACEADMIN_CLIENTES=lab|Invoke Lab|$LAB|demo-ansible-summit|n2-standard-32|laboratorio;bau|Banco Austral|https://faceops.34.176.9.136.sslip.io:8843/faceops|cliente-banco-austral|n2-standard-8|cliente;rpa|Retail Pacífico|https://faceops.34.176.81.141.sslip.io:8843/faceops|cliente-retail-pacifico|n2-standard-8|cliente
E
echo "· certificado (https://faceadmin.$PUB.sslip.io:8843)"
$S "sudo test -s /etc/faceadmin/tls.crt || sudo openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj '/CN=faceadmin' \
    -addext 'subjectAltName=DNS:faceadmin.$PUB.sslip.io,IP:$PUB' -keyout /etc/faceadmin/tls.key -out /etc/faceadmin/tls.crt >/dev/null 2>&1; \
    sudo chmod 644 /etc/faceadmin/tls.crt /etc/faceadmin/tls.key"
echo "· actualizador y servicio"
$S "sudo tee /usr/local/sbin/faceadmin-actualizar >/dev/null; sudo chmod 755 /usr/local/sbin/faceadmin-actualizar" <<'A'
#!/bin/bash
set -u
R=/home/cquezada/faceops-repo; REPO=$(cat /etc/faceadmin/repo 2>/dev/null)
[ -d "$R/.git" ] || sudo -u cquezada git clone -q "$REPO" "$R"
antes=$(sudo -u cquezada git -C "$R" rev-parse HEAD 2>/dev/null)
for i in 1 2 3 4 5; do sudo -u cquezada git -C "$R" pull -q --ff-only && break; sleep 15; done
ahora=$(sudo -u cquezada git -C "$R" rev-parse HEAD)
if [ "$antes" != "$ahora" ] || ! podman image exists localhost/faceadmin:latest; then
  sudo -u cquezada bash "$R/faceadmin/version.sh"
  podman build -q -t localhost/faceadmin:latest -f "$R/faceadmin/Containerfile" "$R/faceadmin" >/dev/null && echo "FaceAdmin actualizado a $(git -C "$R" rev-parse --short HEAD)"
  systemctl try-restart faceadmin
fi
A
$S "echo $REPO | sudo tee /etc/faceadmin/repo >/dev/null; sudo install -d -o 1001 -g 0 -m 770 /var/lib/faceadmin"
$S "sudo tee /etc/systemd/system/faceadmin-actualizar.service >/dev/null" <<'U'
[Unit]
Description=FaceAdmin · actualizar desde el repositorio central al encender
Wants=network-online.target
After=network-online.target
Before=faceadmin.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/faceadmin-actualizar
TimeoutStartSec=1200
[Install]
WantedBy=multi-user.target
U
$S "sudo tee /etc/containers/systemd/faceadmin.container >/dev/null" <<'U'
[Unit]
Description=FaceAdmin · consola global
After=network-online.target faceadmin-actualizar.service
[Container]
Image=localhost/faceadmin:latest
ContainerName=faceadmin
Network=host
Volume=/var/lib/faceadmin:/var/lib/faceadmin:Z
Volume=/etc/faceadmin/tls.crt:/etc/faceadmin/tls.crt:ro,Z
Volume=/etc/faceadmin/tls.key:/etc/faceadmin/tls.key:ro,Z
EnvironmentFile=/etc/faceadmin.env
Environment=TZ=America/Santiago
Exec=gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8843 --certfile /etc/faceadmin/tls.crt --keyfile /etc/faceadmin/tls.key --access-logfile - app:app
[Service]
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
U
$S "sudo firewall-cmd -q --add-port=8843/tcp; sudo firewall-cmd -q --permanent --add-port=8843/tcp; true"
$S "sudo systemctl daemon-reload; sudo systemctl enable faceadmin-actualizar >/dev/null 2>&1; sudo systemctl start faceadmin-actualizar; sudo systemctl restart faceadmin"
sleep 15
echo "· faceadmin: $($S "curl -sk -o /dev/null -w '%{http_code}' https://localhost:8843/version") · https://faceadmin.$PUB.sslip.io:8843/"
$S "curl -sk https://localhost:8843/version; echo"
