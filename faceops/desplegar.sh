#!/bin/bash
# FaceOps · despliegue en la VM del stand (idempotente).
#   http://34.176.42.56:8090/faceops/   (nodo-nginx → 192.168.122.1:8110 → contenedor faceops)
# Claves iniciales de los usuarios: sudo cat /var/lib/faceops/credenciales-iniciales.txt
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
paso() { echo "== $*"; }

paso "AAP: usuario de servicio, recolector y límites por segmento"
python3 "$D/aap/configurar.py"

paso "Imagen del contenedor"
sudo podman build -q -t localhost/faceops:latest -f "$D/Containerfile" "$D" >/dev/null

paso "Servicio faceops"
sudo mkdir -p /var/lib/faceops && sudo chown 1001:0 /var/lib/faceops && sudo chmod 770 /var/lib/faceops
sudo install -m 600 "$HOME/faceops/faceops.env" /etc/faceops.env
sudo tee /etc/containers/systemd/faceops.container >/dev/null <<U
[Unit]
Description=FaceOps · consola del Managed Service
After=network-online.target libvirtd.service
[Container]
Image=localhost/faceops:latest
ContainerName=faceops
Network=host
Volume=/var/lib/faceops:/var/lib/faceops:Z
EnvironmentFile=/etc/faceops.env
Environment=TZ=America/Santiago
Exec=gunicorn --workers 1 --threads 8 --bind 192.168.122.1:8110 --access-logfile - wsgi:app
[Service]
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
U
sudo systemctl daemon-reload
sudo systemctl restart faceops
for p in 8110; do
  sudo firewall-cmd -q --zone=libvirt --query-port=$p/tcp || sudo firewall-cmd -q --zone=libvirt --add-port=$p/tcp
  sudo firewall-cmd -q --permanent --zone=libvirt --query-port=$p/tcp || sudo firewall-cmd -q --permanent --zone=libvirt --add-port=$p/tcp
done

paso "Nginx: /faceops/ en el puerto 8090"
ssh nodo-nginx 'sudo bash -s' <<'N'
cat > /etc/nginx/default.d/faceops.conf <<'C'
# FaceOps (contenedor en la VM anfitriona)
location /faceops/ {
    proxy_pass http://192.168.122.1:8110/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Prefix /faceops;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 120s;
}
C
nginx -t -q && systemctl reload nginx
N

paso "Esperando a FaceOps"
for i in $(seq 1 30); do curl -sf -o /dev/null http://192.168.122.1:8110/ingresar && break; sleep 2; done
curl -s -o /dev/null -w "FaceOps responde: %{http_code}\n" http://192.168.122.1:8110/ingresar

paso "Catálogo e inventario desde AAP"
sudo podman exec -e FACEOPS_VIGIA=0 faceops python3 - <<'P'
from app import create_app
from app.bd import q1
from app.operacion import sincronizar_catalogo
from app.inventario import sincronizar, recolectar
create_app()
c = q1("SELECT * FROM clientes WHERE codigo='lab'")
print("catálogo:", sincronizar_catalogo(c, "despliegue"), "automatizaciones")
print("recolector:", recolectar(c, "despliegue"))
print("inventario:", sincronizar(c, "despliegue"), "equipos (los datos llegan cuando termine el recolector)")
P
echo "LISTO · http://34.176.42.56:8090/faceops/"
