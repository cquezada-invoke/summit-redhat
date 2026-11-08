#!/bin/bash
# =====================================================================================================================
#  FaceOps · paquete de instalación para {{ c.nombre }}  (Linux: RHEL 9 o compatible, con Podman)
#  Instancia: {{ c.instancia_id }}      Versión fijada: {{ commit[:12] }}      Generado: {{ generado }}
#  Este archivo contiene la LLAVE PROPIA de la instancia: no lo publiques ni lo reutilices en otro cliente.
#  Uso (como root o con sudo):   bash faceops-{{ c.codigo }}-{{ commit[:7] }}.sh
#    Pide la URL y el token de Ansible Automation Platform del cliente (o usa AAP_URL y AAP_TOKEN del entorno).
# =====================================================================================================================
set -euo pipefail
INSTANCIA="{{ c.instancia_id }}"; CODIGO="{{ c.codigo }}"; NOMBRE="{{ c.nombre }}"
REPO="{{ repo }}"; COMMIT="{{ commit }}"; ADMIN="{{ admin }}"; LLAVE="{{ tok }}"
[ "$(id -u)" = 0 ] || { echo "Ejecuta con sudo"; exit 1; }
echo "· FaceOps para $NOMBRE ($INSTANCIA)"
dnf -q -y install git-core podman openssl firewalld >/dev/null
systemctl enable --now firewalld >/dev/null 2>&1 || true
AAP_URL=${AAP_URL:-}; AAP_TOKEN=${AAP_TOKEN:-}
[ -n "$AAP_URL" ] || read -rp "URL de Ansible Automation Platform (https://…): " AAP_URL
[ -n "$AAP_TOKEN" ] || { read -rsp "Token de AAP para FaceOps (no se muestra): " AAP_TOKEN; echo; }
PUB=${FACEOPS_IP_PUBLICA:-$(curl -s -m 3 -H "Metadata-Flavor: Google" http://metadata/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || true)}
[ -n "$PUB" ] || PUB=$(hostname -I | awk '{print $1}')
echo "· código fijado en la versión $COMMIT"
R=/opt/faceops-repo
[ -d "$R/.git" ] || git clone -q "$REPO" "$R"
git -C "$R" fetch -q origin && git -C "$R" checkout -q "$COMMIT"
bash "$R/faceops/version.sh" >/dev/null
podman build -q -t localhost/faceops:latest -f "$R/faceops/Containerfile" "$R/faceops" >/dev/null
echo "· configuración"
install -d -m 700 /etc/faceops; install -d -o 1001 -g 0 -m 770 /var/lib/faceops
umask 077
cat > /etc/faceops.env <<E
FACEOPS_INSTANCIA_ID=$INSTANCIA
FACEOPS_CLIENTE_CODIGO=$CODIGO
FACEOPS_CLIENTE_NOMBRE=$NOMBRE
FACEOPS_INSTANCIA=$CODIGO
FACEOPS_ROL=cliente
FACEOPS_AAP_URL=$AAP_URL
FACEOPS_AAP_TOKEN=$AAP_TOKEN
FACEOPS_ADMIN_URL=$ADMIN
FACEOPS_ADMIN_TOKEN=$LLAVE
FACEOPS_CONSULTOR_USUARIO={{ c.consultor_usuario or '' }}
FACEOPS_CONSULTOR_NOMBRE={{ c.consultor_nombre or '' }}
FACEOPS_CONSULTOR_CORREO={{ c.consultor_correo or '' }}
SCRIPT_NAME=/faceops
E
umask 022
echo "$REPO" > /etc/faceops/repo
[ -s /etc/faceops/tls.crt ] || openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=faceops-$CODIGO" \
  -addext "subjectAltName=DNS:faceops.$PUB.sslip.io,IP:$PUB" -keyout /etc/faceops/tls.key -out /etc/faceops/tls.crt >/dev/null 2>&1
chmod 644 /etc/faceops/tls.crt /etc/faceops/tls.key
cat > /usr/local/sbin/faceops-actualizar <<'A'
#!/bin/bash
# Trae la última versión del repositorio central y, si cambió, reconstruye y reinicia.
R=/opt/faceops-repo; antes=$(git -C "$R" rev-parse HEAD)
git -C "$R" checkout -q main 2>/dev/null; for i in 1 2 3; do git -C "$R" pull -q --ff-only && break; sleep 15; done
if [ "$antes" != "$(git -C "$R" rev-parse HEAD)" ] || ! podman image exists localhost/faceops:latest; then
  bash "$R/faceops/version.sh" >/dev/null; podman build -q -t localhost/faceops:latest -f "$R/faceops/Containerfile" "$R/faceops" >/dev/null && systemctl try-restart faceops
fi
A
chmod 755 /usr/local/sbin/faceops-actualizar
cat > /etc/systemd/system/faceops-actualizar.service <<'U'
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
mkdir -p /etc/containers/systemd
cat > /etc/containers/systemd/faceops.container <<'U'
[Unit]
Description=FaceOps
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
firewall-cmd -q --add-port=8843/tcp || true; firewall-cmd -q --permanent --add-port=8843/tcp || true
systemctl daemon-reload; systemctl enable faceops-actualizar >/dev/null 2>&1; systemctl restart faceops
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do curl -skf https://localhost:8843/faceops/version >/dev/null && break; sleep 10; done
URL="https://faceops.$PUB.sslip.io:8843/faceops"
curl -sk -X POST -H "X-FaceAdmin-Token: $LLAVE" -H "Content-Type: application/json" \
  -d "{\"instancia_id\": \"$INSTANCIA\", \"url\": \"$URL\", \"host\": \"$(hostname)\", \"commit\": \"$COMMIT\"}" "$ADMIN/api/instancia/registro" >/dev/null || true
echo "· FaceOps listo: $URL/"
{% if c.consultor_usuario %}T=$(podman exec faceops python3 -c 'from app import create_app; from app.activacion import nuevo
a = create_app()
with a.app_context(): print(nuevo("{{ c.consultor_usuario }}"))' 2>/dev/null | tail -1)
[ -n "$T" ] && echo "  Consultor {{ c.consultor_nombre }} ({{ c.consultor_usuario }}, cluster-admin): define tu clave aquí (un uso, 72 h): $URL/definir-clave/$T"
{% endif %}
echo "  Usuarios iniciales y sus claves: /var/lib/faceops/credenciales-iniciales.txt (solo root)"
