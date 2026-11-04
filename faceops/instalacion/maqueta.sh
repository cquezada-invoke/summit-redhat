#!/bin/bash
# FaceOps · maqueta (demo cáscara) para compartir: segunda instancia de la misma imagen, con login simple (sin doble factor), con una COPIA de la
# base de datos limpiada de secretos y sin conexión a AAP, la herramienta de tickets ni las máquinas. Las acciones se simulan.
#   https://faceops.<ip>.sslip.io:<puerto>/maqueta/   →  192.168.122.1:8112  →  contenedor faceops-maqueta
# Idempotente. Volver a correrlo refresca la maqueta con los datos actuales de la consola.
set -euo pipefail
D=/var/lib/faceops-maqueta
sudo install -d -o 1001 -g 0 -m 770 $D
echo "== Copia de la base de datos (consistente, en caliente)"
sudo systemctl stop faceops-maqueta 2>/dev/null || true
sudo rm -f $D/faceops.db-wal $D/faceops.db-shm
sudo podman exec faceops python3 -c "
import sqlite3; s = sqlite3.connect('/var/lib/faceops/faceops.db'); d = sqlite3.connect('/var/lib/faceops/maqueta.db'); s.backup(d); d.close()"
sudo mv /var/lib/faceops/maqueta.db $D/faceops.db
sudo chown 1001:0 $D/faceops.db
echo "== Clave de acceso a la maqueta (se genera una vez; usuario cquezada)"
sudo test -s $D/clave-acceso || { printf 'Maqueta-%s' "$(openssl rand -hex 4)" | sudo tee $D/clave-acceso >/dev/null; sudo chown 1001:0 $D/clave-acceso; sudo chmod 600 $D/clave-acceso; }
echo "== Limpieza de secretos y conexiones"
sudo podman run -i --rm -v $D:/var/lib/faceops:Z --user 0 localhost/faceops:latest python3 - <<'PY'
import json, re, secrets, sqlite3
USUARIO = "cquezada"
c = sqlite3.connect("/var/lib/faceops/faceops.db")
cols = lambda t: [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
tablas = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
c.execute("UPDATE clientes SET aap_url='', aap_token='', tickets_tipo='faceops', tickets_conf='{}'")
from werkzeug.security import generate_password_hash
h = generate_password_hash(secrets.token_hex(24))
c.execute("UPDATE usuarios SET clave_hash=?", (h,))
# acceso a la maqueta: un solo usuario con la clave de la maqueta (la demás gente no entra)
c.execute("UPDATE usuarios SET clave_hash=? WHERE usuario=?", (generate_password_hash(open("/var/lib/faceops/clave-acceso").read().strip()), USUARIO))
if "totp_secreto" in cols("usuarios"):
    c.execute("UPDATE usuarios SET totp_secreto='', totp_activo=0")
SENS = re.compile(r"(clave|password|passwd|secret|token|llave|api_?key|credencial)", re.I)
SENS_COL = re.compile(r"(password|passwd|secret|token|credencial)", re.I)
def limpiar(v):
    if isinstance(v, dict):
        return {k: ("***" if SENS.search(k) and not isinstance(x, (dict, list)) else limpiar(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [limpiar(x) for x in v]
    return v
n = 0
for t in tablas:
    for col in cols(t):
        if t == "usuarios" and col == "clave_hash":
            continue
        if SENS_COL.search(col) and t not in ("ajustes",):
            try:
                c.execute(f"UPDATE {t} SET {col}=''"); n += 1
            except sqlite3.Error:
                pass
        for rid, v in c.execute(f"SELECT rowid, {col} FROM {t} WHERE typeof({col})='text' AND ({col} LIKE '{{%' OR {col} LIKE '[%')").fetchall():
            try:
                o = json.loads(v)
            except ValueError:
                continue
            l = limpiar(o)
            if l != o:
                c.execute(f"UPDATE {t} SET {col}=? WHERE rowid=?", (json.dumps(l, ensure_ascii=False), rid)); n += 1
c.execute("DELETE FROM ajustes WHERE clave LIKE '%token%' OR clave LIKE '%clave%' OR clave LIKE '%secret%' OR clave LIKE '%key%'")
for t in ("agente_mensajes",):
    if t in tablas:
        c.execute(f"DELETE FROM {t}")
c.commit()
print("limpieza ok ·", n, "columnas/valores enmascarados")
PY
sudo tee /etc/containers/systemd/faceops-maqueta.container >/dev/null <<U
[Unit]
Description=FaceOps · maqueta (demo cáscara, login simple, datos emulados)
After=network-online.target
[Container]
Image=localhost/faceops:latest
ContainerName=faceops-maqueta
Network=host
Volume=$D:/var/lib/faceops:Z
Environment=TZ=America/Santiago FACEOPS_MAQUETA=1 FACEOPS_VIGIA=0
Exec=gunicorn --workers 1 --threads 8 --bind 192.168.122.1:8112 --access-logfile - wsgi:app
[Service]
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
U
sudo systemctl daemon-reload
sudo systemctl restart faceops-maqueta
for p in 8112; do
  sudo firewall-cmd -q --zone=libvirt --query-port=$p/tcp || sudo firewall-cmd -q --zone=libvirt --add-port=$p/tcp
done
echo "== Publicación en el proxy (/maqueta/)"
C=/var/lib/faceops-publico/conf/faceops.conf
if ! sudo grep -q "location /maqueta/" $C; then
  sudo python3 - "$C" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
bloque = """    location /maqueta/ {
        limit_req zone=general burst=60 nodelay;
        proxy_pass http://192.168.122.1:8112/;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Prefix /maqueta;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
"""
i = s.index("    location /faceops/ {")
open(p, "w").write(s[:i] + bloque + s[i:])
PY
fi
sudo podman exec faceops-publico nginx -t 2>&1 | tail -1 && sudo podman exec faceops-publico nginx -s reload
sleep 10
curl -sk -o /dev/null -w 'maqueta interna: %{http_code}\n' http://192.168.122.1:8112/
curl -sk -o /dev/null -w 'maqueta pública: %{http_code}\n' https://localhost:8843/maqueta/
