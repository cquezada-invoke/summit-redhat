#!/bin/bash
# Data lake de FaceAdmin: MinIO en la misma VM de FaceAdmin, como contenedor con volumen persistente.
# Ahí llegan los pulsos de uso de FaceOps (s3://faceops-pulsos/…, ver faceadmin/docs/pulsos.md).
#   · escucha solo en 127.0.0.1:9000 (API) y 127.0.0.1:9001 (consola): no se publica hacia afuera
#   · datos en /var/lib/faceadmin-minio; credenciales generadas en la VM, en /etc/faceadmin/minio.env y /etc/faceadmin/s3.env (600)
#   · FaceAdmin las toma de /etc/faceadmin/s3.env (EnvironmentFile del servicio)
# Se corre desde Invoke Lab, igual que instalar-faceadmin.sh. Uso: instalar-minio-faceadmin.sh <host-o-ip>   (idempotente; no imprime secretos)
set -euo pipefail
IP=$1
S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR cquezada@$IP"
IMG=${MINIO_IMAGEN:-quay.io/minio/minio:latest}

echo "· credenciales (se generan en la VM una sola vez)"
$S 'sudo install -d -m 700 /etc/faceadmin; sudo install -d -m 700 /var/lib/faceadmin-minio
if ! sudo grep -qs "^FACEADMIN_S3_SECRET=." /etc/faceadmin/s3.env; then
  u=faceadmin-$(openssl rand -hex 4); p=$(openssl rand -hex 24)
  printf "MINIO_ROOT_USER=%s\nMINIO_ROOT_PASSWORD=%s\n" "$u" "$p" | sudo tee /etc/faceadmin/minio.env >/dev/null
  printf "FACEADMIN_S3_URL=http://127.0.0.1:9000\nFACEADMIN_S3_ACCESS=%s\nFACEADMIN_S3_SECRET=%s\n" "$u" "$p" | sudo tee /etc/faceadmin/s3.env >/dev/null
  unset u p; echo "  nuevas"
else echo "  ya existían"; fi
sudo chmod 600 /etc/faceadmin/minio.env /etc/faceadmin/s3.env'

echo "· imagen $IMG"
$S "sudo podman pull -q $IMG >/dev/null && echo '  ok'"

echo "· servicio faceadmin-minio"
$S "sudo tee /etc/containers/systemd/faceadmin-minio.container >/dev/null" <<U
[Unit]
Description=FaceAdmin · data lake (MinIO, solo local)
After=network-online.target
[Container]
Image=$IMG
ContainerName=faceadmin-minio
Network=host
Volume=/var/lib/faceadmin-minio:/data:Z
EnvironmentFile=/etc/faceadmin/minio.env
Exec=server /data --address 127.0.0.1:9000 --console-address 127.0.0.1:9001
[Service]
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
U

echo "· FaceAdmin toma las credenciales del lake"
$S 'Q=/etc/containers/systemd/faceadmin.container
sudo grep -q "^EnvironmentFile=/etc/faceadmin/s3.env" $Q || sudo sed -i "/^EnvironmentFile=\/etc\/faceadmin.env/a EnvironmentFile=/etc/faceadmin/s3.env" $Q
sudo grep -q "faceadmin-minio.service" $Q || sudo sed -i "0,/^After=/s//After=faceadmin-minio.service /" $Q
sudo systemctl daemon-reload; sudo systemctl restart faceadmin-minio
for i in $(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:9000/minio/health/live && break; sleep 2; done
echo "  minio: $(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:9000/minio/health/live)"
sudo systemctl restart faceadmin; sleep 12
echo "  faceadmin: $(curl -sk -o /dev/null -w %{http_code} https://localhost:8843/version)"
echo "  variables S3 en el contenedor: $(sudo podman exec faceadmin sh -c "env | grep -c ^FACEADMIN_S3_")"
sudo podman ps --format "{{.Names}} {{.Status}}" | grep faceadmin'
