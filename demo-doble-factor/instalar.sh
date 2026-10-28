#!/bin/bash
# Demo doble factor (Keycloak + SMS/correo). Corre en la VM demo-ansible-summit. Idempotente.
# Keycloak APARTE del de las demos (no toca nodo-tomcat): contenedor kc-doble-factor en 192.168.122.1:8095,
# app "Mi App" + teléfono simulado en 192.168.122.1:8096, publicados por nodo-nginx en el puerto 8090:
#   http://34.176.42.56:8090/mi-app/            http://34.176.42.56:8090/doble-factor/admin/
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
B=/opt/doble-factor
C=$HOME/doble-factor/credenciales.txt
IMG=quay.io/keycloak/keycloak:26.0.7
paso() { echo "== $*"; }

paso "Credenciales (se generan una vez, quedan en $C)"
mkdir -p "$(dirname "$C")"; chmod 700 "$(dirname "$C")"
if [ ! -f "$C" ]; then
  ( umask 077; printf 'kc_admin=%s\nusuario_demo=Summit-%s\n' "$(openssl rand -hex 12)" "$(shuf -i 1000-9999 -n1)" > "$C" )
fi
KC_ADMIN=$(grep ^kc_admin= "$C" | cut -d= -f2-)

paso "Proveedor de Keycloak (código por SMS y por correo)"
mkdir -p "$HOME/.m2"
podman run --rm -v "$D/proveedor:/src:Z" -v "$HOME/.m2:/root/.m2:Z" -w /src \
  docker.io/library/maven:3.9-eclipse-temurin-17 mvn -q -B package -DskipTests
sudo mkdir -p $B/providers $B/datos
sudo chmod 755 $B $B/providers
sudo install -m 0644 "$D/proveedor/target/invoke-otp.jar" $B/providers/invoke-otp.jar
sudo chown -R 1000:0 $B/datos
echo "KC_BOOTSTRAP_ADMIN_PASSWORD=$KC_ADMIN" | sudo tee $B/kc.env >/dev/null; sudo chmod 600 $B/kc.env

paso "Contenedor kc-doble-factor"
sudo tee /etc/containers/systemd/kc-doble-factor.container >/dev/null <<U
[Unit]
Description=Keycloak de la demo de doble factor (aparte del de las demos)
[Container]
Image=$IMG
ContainerName=kc-doble-factor
Network=host
Volume=$B/providers:/opt/keycloak/providers:Z
Volume=$B/datos:/opt/keycloak/data:Z
EnvironmentFile=$B/kc.env
Environment=KC_BOOTSTRAP_ADMIN_USERNAME=admin
Environment=KC_HTTP_HOST=192.168.122.1
Environment=KC_HTTP_PORT=8095
Environment=KC_HTTP_MANAGEMENT_PORT=9095
Environment=KC_HTTP_RELATIVE_PATH=/doble-factor
Environment=KC_HOSTNAME=http://34.176.42.56:8090/doble-factor
Environment=KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true
Environment=KC_PROXY_HEADERS=xforwarded
Environment=KC_HTTP_ENABLED=true
Environment=KC_LOG_LEVEL=INFO,cl.invoke.otp:INFO
PodmanArgs=--memory=1200m
Exec=start-dev
[Service]
Restart=always
TimeoutStartSec=900
[Install]
WantedBy=multi-user.target
U
sudo systemctl daemon-reload
sudo systemctl restart kc-doble-factor

paso "Mi App y teléfono simulado"
sudo tee /etc/systemd/system/doble-factor-app.service >/dev/null <<U
[Unit]
Description=Demo doble factor · Mi App y teléfono simulado
After=network-online.target libvirtd.service
StartLimitIntervalSec=0
[Service]
RestartSec=10
User=$USER
ExecStart=/usr/bin/python3 $D/app/app.py
Restart=always
[Install]
WantedBy=multi-user.target
U
sudo systemctl daemon-reload
sudo systemctl enable -q doble-factor-app kc-doble-factor 2>/dev/null || sudo systemctl enable -q doble-factor-app
sudo systemctl restart doble-factor-app

paso "Firewall de la VM: nodo-nginx llega a 8095 y 8096 (sin recargar firewalld)"
for p in 8095 8096; do
  sudo firewall-cmd -q --zone=libvirt --query-port=$p/tcp || sudo firewall-cmd -q --zone=libvirt --add-port=$p/tcp
  sudo firewall-cmd -q --permanent --zone=libvirt --query-port=$p/tcp || sudo firewall-cmd -q --permanent --zone=libvirt --add-port=$p/tcp
done

paso "Nginx (nodo-nginx): /doble-factor/ y /mi-app/ en el puerto 8090"
ssh nodo-nginx 'sudo bash -s' <<'N'
cat > /etc/nginx/default.d/doble-factor.conf <<'C'
# Demo doble factor (Keycloak aparte + Mi App) en la VM anfitriona
location /doble-factor/ {
    proxy_pass http://192.168.122.1:8095;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Port 8090;
    proxy_buffer_size 128k;
    proxy_buffers 4 256k;
    proxy_busy_buffers_size 256k;
}
location /mi-app/ {
    proxy_pass http://192.168.122.1:8096;
    proxy_set_header Host $http_host;
}
C
nginx -t -q && systemctl reload nginx
N

paso "Esperando a Keycloak"
for i in $(seq 1 90); do
  curl -sf -o /dev/null http://192.168.122.1:8095/doble-factor/realms/master && break; sleep 5
done
paso "Realm, usuarios y flujo de login"
python3 "$D/configurar_realm.py"
