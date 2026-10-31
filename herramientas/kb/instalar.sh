#!/bin/bash
# Base de conocimiento de incidentes de FaceOps (PostgreSQL). Idempotente; no imprime la clave.
#  · contenedor faceops-kb (PostgreSQL 16) en 127.0.0.1:5433, datos en /var/lib/faceops-kb
#  · la conexión queda en /etc/faceops.env (FACEOPS_KB_DSN); FaceOps crea el esquema y carga la semilla (datos/kb/*.yml)
set -euo pipefail
V=/var/lib/faceops-kb; IMG=docker.io/library/postgres:16.4
R=$(cd "$(dirname "$0")/../.." && pwd)
sudo install -d -m 0700 $V $V/datos
sudo test -s $V/clave || (umask 077; python3 -c "import secrets;print(secrets.token_urlsafe(24))" | sudo tee $V/clave >/dev/null)
sudo tee /etc/containers/systemd/faceops-kb.container >/dev/null <<U
[Unit]
Description=FaceOps · base de conocimiento de incidentes (PostgreSQL)
After=network-online.target
[Container]
Image=$IMG
ContainerName=faceops-kb
Network=host
Volume=$V/datos:/var/lib/postgresql/data:Z
Volume=$V/clave:/run/kb-clave:Z,ro
Environment=POSTGRES_USER=faceops POSTGRES_DB=kb POSTGRES_PASSWORD_FILE=/run/kb-clave PGPORT=5433
Exec=postgres -c listen_addresses=127.0.0.1 -c port=5433
[Service]
Restart=always
[Install]
WantedBy=multi-user.target
U
sudo systemctl daemon-reload && sudo systemctl restart faceops-kb
for i in $(seq 1 30); do sudo podman exec faceops-kb pg_isready -h 127.0.0.1 -p 5433 -q 2>/dev/null && break; sleep 2; done
echo "   · PostgreSQL listo en 127.0.0.1:5433"
DSN="postgresql://faceops:$(sudo cat $V/clave)@127.0.0.1:5433/kb"
sudo touch /etc/faceops.env && sudo chmod 600 /etc/faceops.env
sudo sed -i '/^FACEOPS_KB_DSN=/d' /etc/faceops.env
echo "FACEOPS_KB_DSN=$DSN" | sudo tee -a /etc/faceops.env >/dev/null
unset DSN
cd $R && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 15
sudo podman exec -i faceops python3 -c "
from app import create_app
app = create_app()
with app.app_context():
    from app import kb
    print('   · base de conocimiento:', kb.resumen())"
# laboratorio: el cliente Invoke Lab con soporte de incidentes en todos los productos de la base
sudo podman exec -i faceops python3 -c "
from app import create_app
app = create_app()
with app.app_context():
    from app import kb
    from app.bd import q
    for c in q('SELECT id FROM clientes'):
        if not kb.productos_cliente(c['id']):
            kb.fijar_productos(c['id'], [p['codigo'] for p in kb.productos()])
    print('   · productos con soporte en el laboratorio:', len(kb.productos()))"
