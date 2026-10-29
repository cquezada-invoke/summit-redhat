#!/bin/bash
# FaceOps · despliegue en la VM del stand (idempotente).
#   http://34.176.42.56:8090/faceops/   (nodo-nginx → 192.168.122.1:8110 → contenedor faceops)
# Claves iniciales de los usuarios: sudo cat /var/lib/faceops/credenciales-iniciales.txt
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
paso() { echo "== $*"; }

paso "Anfitrión KVM para crear servidores on-premise"
bash "$D/aap/preparar-anfitrion.sh"

paso "AAP: usuario de servicio, recolector y límites por segmento"
python3 "$D/aap/configurar.py"

paso "Commit de Git de cada arquitectura (la versión aprobada por Pull Request)"
( cd "$D/.." && for f in faceops/datos/arquitectura/*.yml; do
    echo "$(basename "$f")|$(git log -1 --format='%H|%an|%aI|%s' -- "$f")"; done ) > "$D/datos/arquitectura/commits.txt" || true

paso "Imagen del contenedor"
sudo podman build -q -t localhost/faceops:latest -f "$D/Containerfile" "$D" >/dev/null

paso "Servicio faceops"
sudo mkdir -p /var/lib/faceops && sudo chown 1001:0 /var/lib/faceops && sudo chmod 770 /var/lib/faceops
grep -q '^FACEOPS_AAP_PUBLICA=' "$HOME/faceops/faceops.env" || echo "FACEOPS_AAP_PUBLICA=https://34.176.42.56" >> "$HOME/faceops/faceops.env"
# Token de Gitea para publicar la arquitectura aprobada de cada segmento (invoke/arquitectura); no se imprime
if ! grep -q '^FACEOPS_GITEA_TOKEN=' "$HOME/faceops/faceops.env"; then
  echo "FACEOPS_GITEA_TOKEN=$(grep '^gitea_token=' "$HOME/demo-drift/cicd-secretos.txt" | cut -d= -f2-)" >> "$HOME/faceops/faceops.env"
fi
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

paso "Consola web (Apache Guacamole): SSH y escritorio remoto desde el inventario"
if ! sudo grep -q '^FACEOPS_GUAC_CLAVE=' /etc/faceops.env; then
  CLAVE=$(openssl rand -hex 16)
  echo "FACEOPS_GUAC_CLAVE=$CLAVE" >> "$HOME/faceops/faceops.env"
  sudo install -m 600 "$HOME/faceops/faceops.env" /etc/faceops.env
fi
CLAVE=$(sudo grep '^FACEOPS_GUAC_CLAVE=' /etc/faceops.env | cut -d= -f2)
printf 'JSON_SECRET_KEY=%s\nGUACD_HOSTNAME=faceops-guacd\nWEBAPP_CONTEXT=consola\n' "$CLAVE" | sudo tee /etc/faceops-guac.env >/dev/null
sudo chmod 600 /etc/faceops-guac.env
sudo podman network exists faceops-guac || sudo podman network create faceops-guac >/dev/null
sudo tee /etc/containers/systemd/faceops-guacd.container >/dev/null <<U
[Unit]
Description=FaceOps · guacd (conexiones SSH/RDP de la consola web)
[Container]
Image=docker.io/guacamole/guacd:1.5.5
ContainerName=faceops-guacd
Network=faceops-guac
[Service]
Restart=always
[Install]
WantedBy=multi-user.target
U
sudo tee /etc/containers/systemd/faceops-guacamole.container >/dev/null <<U
[Unit]
Description=FaceOps · Guacamole (consola web en el navegador)
After=faceops-guacd.service
Requires=faceops-guacd.service
[Container]
Image=docker.io/guacamole/guacamole:1.5.5
ContainerName=faceops-guacamole
Network=faceops-guac
PublishPort=192.168.122.1:8111:8080
EnvironmentFile=/etc/faceops-guac.env
[Service]
Restart=always
[Install]
WantedBy=multi-user.target
U
sudo podman pull -q docker.io/guacamole/guacd:1.5.5 >/dev/null; sudo podman pull -q docker.io/guacamole/guacamole:1.5.5 >/dev/null
sudo install -d -o 1001 -g 0 -m 0700 /var/lib/faceops/llaves
sudo install -o 1001 -g 0 -m 0600 "$HOME/.ssh/id_demo" /var/lib/faceops/llaves/id_demo
sudo systemctl daemon-reload
sudo systemctl restart faceops-guacd faceops-guacamole faceops
for p in 8111; do
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
# Consola web (Guacamole): SSH y escritorio remoto; FaceOps entrega la conexión cifrada y de un solo uso
location /consola/ {
    proxy_pass http://192.168.122.1:8111/consola/;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 3600s;
    access_log off;
}
C
nginx -t -q && systemctl reload nginx
N

paso "Esperando a FaceOps"
for i in $(seq 1 30); do curl -sf -o /dev/null http://192.168.122.1:8110/ingresar && break; sleep 2; done
curl -s -o /dev/null -w "FaceOps responde: %{http_code}\n" http://192.168.122.1:8110/ingresar

paso "ServiceNow de las demos como herramienta de tickets de Invoke Lab (credenciales desde Vault; no se imprimen)"
python3 - <<'S' | sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -
import json, os, sys
sys.path.insert(0, os.path.expanduser("~/summit-redhat/demo4-redes/servicenow"))
from sn import credenciales
url, usuario, clave = credenciales()
conf = json.dumps({"url": url, "usuario": usuario, "clave": clave})
print(f"""from app import create_app
from app.bd import q1, ej, auditar
create_app()
c = q1("SELECT * FROM clientes WHERE codigo='lab'")
if c and (c.get("tickets_tipo") or "") in ("", "faceops", "propio", "ninguno"):
    ej("UPDATE clientes SET tickets_tipo='servicenow', tickets_conf=? WHERE id=?", {conf!r}, c["id"])
    auditar("despliegue", c["id"], "activar conector", "ServiceNow", {{"url": {url!r}}})
    print("ServiceNow activado para Invoke Lab")
else:
    print("Herramienta de tickets ya definida:", c and c.get("tickets_tipo"))
""")
S

paso "Vigías de ServiceNow de las demos (dejan 25 s a FaceOps para tomar sus tickets)"
for u in vigia-servicenow vigia-nube; do sudo systemctl restart $u 2>/dev/null || systemctl --user restart $u 2>/dev/null || echo "   (no encontré $u)"; done

paso "Catálogo e inventario desde AAP"
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'P'
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
