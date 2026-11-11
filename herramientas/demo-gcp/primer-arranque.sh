#!/bin/bash
# Primer arranque de una VM de demo creada desde la imagen dorada (una sola vez):
#  1) pide a FaceAdmin el usuario y la clave del cliente (endpoint de un solo uso, con el token de la VM);
#  2) fija esas credenciales en FaceOps, AWX, Gitea y Grafana; las de servicio (MinIO, Vault) se generan aquí y quedan en Vault;
#  3) publica todo con Caddy en https://<servicio>.<IP>.sslip.io (certificado Let's Encrypt automático);
#  4) conecta FaceOps con AWX/EDA (FACEOPS_AAP_MODO=awx) y siembra el inventario con las VMs anidadas.
# Nada de esto queda en logs: las claves solo pasan por variables y archivos 600.
set -euo pipefail
M=http://metadata.google.internal/computeMetadata/v1/instance/attributes
md() { curl -sf -H "Metadata-Flavor: Google" "$M/$1"; }
FA=$(md faceadmin-url); NUM=$(md demo-numero); TOK=$(md demo-token); IP=$(md demo-ip)
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
umask 077; install -d -m 700 /etc/demo

for i in $(seq 1 30); do C=$(curl -sf -m 20 -H "X-Demo-Token: $TOK" "$FA/api/demo-gcp/$NUM/credenciales") && break; sleep 10; done
U=$(echo "$C" | jq -r .usuario); P=$(echo "$C" | jq -r .clave)
[ -n "$U" ] && [ -n "$P" ] && [ "$P" != null ] || { echo "sin credenciales de FaceAdmin"; exit 1; }

MINIO_P=$(openssl rand -hex 16); GRAF_P="$P"
printf 'MINIO_ROOT_USER=demo\nMINIO_ROOT_PASSWORD=%s\n' "$MINIO_P" > /etc/demo/minio.env
printf 'GF_SECURITY_ADMIN_USER=%s\nGF_SECURITY_ADMIN_PASSWORD=%s\nGF_SERVER_ROOT_URL=https://grafana.%s.sslip.io\n' "$U" "$GRAF_P" "$IP" > /etc/demo/grafana.env

# AWX: clave del admin y usuario del cliente
kubectl -n awx exec deploy/awx-web -c awx-web -- awx-manage update_password --username=admin --password="$P" >/dev/null
kubectl -n awx exec deploy/awx-web -c awx-web -- awx-manage shell -c \
  "from django.contrib.auth.models import User; u,_=User.objects.get_or_create(username='$U'); u.is_superuser=True; u.set_password('$P'); u.save()" >/dev/null
# EDA: misma clave para su admin (FaceOps lee EDA con usuario y clave: eda-server no usa el token de AWX)
kubectl -n eda exec deploy/eda-api -- aap-eda-manage shell -c \
  "from aap_eda.core.models import User; u=User.objects.get(username='admin'); u.set_password('$P'); u.save()" >/dev/null
AWX_TOKEN=$(kubectl -n awx exec deploy/awx-web -c awx-web -- awx-manage create_oauth2_token --user admin 2>/dev/null | tail -1)

cat > /etc/demo/faceops.env <<F
FACEOPS_PUBLICA=https://faceops.$IP.sslip.io
FACEOPS_AAP_MODO=awx
FACEOPS_AAP_URL=http://127.0.0.1:30080
FACEOPS_AAP_TOKEN=$AWX_TOKEN
FACEOPS_AAP_PUBLICA=https://awx.$IP.sslip.io
FACEOPS_EDA_URL=http://127.0.0.1:30081
FACEOPS_EDA_PUBLICA=https://eda.$IP.sslip.io
FACEOPS_EDA_USUARIO=admin
FACEOPS_EDA_CLAVE=$P
FACEOPS_ADMIN_USUARIO=$U
FACEOPS_ADMIN_CLAVE=$P
F

cat > /etc/demo/Caddyfile <<F
faceops.$IP.sslip.io { reverse_proxy 127.0.0.1:8443 { transport http { tls_insecure_skip_verify } } }
awx.$IP.sslip.io { reverse_proxy 127.0.0.1:30080 }
eda.$IP.sslip.io { reverse_proxy 127.0.0.1:30081 }
gitea.$IP.sslip.io { reverse_proxy 127.0.0.1:3000 }
grafana.$IP.sslip.io { reverse_proxy 127.0.0.1:3001 }
F
systemctl daemon-reload
systemctl start minio vault gitea prometheus alertmanager grafana faceops caddy
podman exec -u git gitea gitea admin user create --admin --username "$U" --password "$P" --email "$U@demo.invoke" --must-change-password=false >/dev/null 2>&1 || true
touch /var/lib/demo/credenciales.listo
unset P GRAF_P AWX_TOKEN
echo "Demo lista en https://faceops.$IP.sslip.io"
