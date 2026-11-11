#!/bin/bash
# Arranque de una VM de «Demo de FaceOps» creada desde la imagen base (corre en cada arranque; lo de una sola vez lleva marca):
#  Una vez: pide a FaceAdmin el usuario y la clave del cliente (endpoint de un solo uso con el token de la VM), crea ese usuario en
#           FaceOps, AWX y EDA, y renueva TODAS las claves internas de la imagen (admin de AWX y EDA, token de FaceOps en AWX,
#           credencial de EDA hacia AWX): ninguna demo comparte claves con otra ni con la imagen.
#  Siempre: publica FaceOps, AWX y EDA con Caddy en https://<servicio>.<IP>.sslip.io (Let's Encrypt) con la IP externa actual.
# Nada de esto va a los logs: las claves solo pasan por variables y archivos 600.
set -euo pipefail
M=http://metadata.google.internal/computeMetadata/v1/instance
md() { curl -sf -H "Metadata-Flavor: Google" "$M/$1"; }
FA=$(md attributes/faceadmin-url); NUM=$(md attributes/demo-numero); TOK=$(md attributes/demo-token)
IP=$(md network-interfaces/0/access-configs/0/external-ip)
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=$PATH:/usr/local/bin
P=/root/prueba; ENV=/etc/demo/faceops.env; umask 077
log() { echo "demo: $*"; logger -t demo "$*"; }
setenv() { grep -v "^$1=" $ENV > $ENV.n || true; echo "$1=$2" >> $ENV.n; mv $ENV.n $ENV; chmod 600 $ENV; }

for i in $(seq 1 60); do curl -sf -m 5 http://127.0.0.1:30080/api/v2/ping/ >/dev/null && break; sleep 10; done
for i in $(seq 1 60); do kubectl -n eda get deploy eda-api >/dev/null 2>&1 && kubectl -n eda rollout status deploy/eda-api --timeout=10s >/dev/null 2>&1 && break; sleep 10; done

if [ ! -f /var/lib/demo/credenciales.listo ]; then
  C=""; for i in $(seq 1 30); do C=$(curl -skf -m 20 -H "X-Demo-Token: $TOK" "$FA/api/demo-gcp/$NUM/credenciales") && break; sleep 10; done
  U=$(echo "$C" | jq -r '.usuario // empty'); PC=$(echo "$C" | jq -r '.clave // empty'); unset C
  [ -n "$U" ] && [ -n "$PC" ] || { log "sin credenciales de FaceAdmin"; exit 1; }
  Q=$(openssl rand -hex 16)
  kubectl -n awx exec deploy/awx-web -c awx-web -- awx-manage update_password --username=admin --password="$Q" >/dev/null
  kubectl -n eda exec deploy/eda-api -- aap-eda-manage shell -c \
    "from aap_eda.core.models import User; u=User.objects.get(username='admin'); u.set_password('$Q'); u.save()" >/dev/null 2>&1
  printf '%s' "$Q" > $P/clave
  U="$U" PC="$PC" Q="$Q" python3 - <<'PY'
import base64, json, os, urllib.request, urllib.error
U, PC, Q = os.environ["U"], os.environ["PC"], os.environ["Q"]
B = "Basic " + base64.b64encode(f"admin:{Q}".encode()).decode()
def h(m, url, d=None):
    r = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Content-Type": "application/json", "Authorization": B})
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            b = x.read(); return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        print("demo:", m, url.split("?")[0], e.code); return None
A, E = "http://127.0.0.1:30080/api/v2/", "http://127.0.0.1:30081/api/eda/v1/"
# token nuevo de FaceOps en AWX; se revocan los de la imagen
t = h("POST", A + "users/1/personal_tokens/", {"description": "FaceOps demo", "scope": "write"})
for x in (h("GET", A + "tokens/?page_size=100") or {}).get("results", []):
    if x["id"] != t["id"]:
        h("DELETE", A + f"tokens/{x['id']}/")
fd = os.open("/root/prueba/awx_token", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, t["token"].encode()); os.close(fd)
# credencial de EDA hacia AWX con la clave nueva
for c in (h("GET", E + "eda-credentials/?page_size=100") or {}).get("results", []):
    if c["name"] == "Prueba AWX":
        h("PATCH", E + f"eda-credentials/{c['id']}/", {"inputs": {"host": "http://awx-service.awx.svc.cluster.local/api/v2/", "username": "admin",
                                                                 "password": Q, "verify_ssl": False}})
# el usuario del cliente en AWX y EDA
u = (h("GET", A + f"users/?username={U}") or {}).get("results") or []
if u:
    h("PATCH", A + f"users/{u[0]['id']}/", {"password": PC, "is_superuser": True})
else:
    h("POST", A + "users/", {"username": U, "password": PC, "is_superuser": True})
h("POST", E + "users/", {"username": U, "password": PC, "is_superuser": True, "roles": []})
PY
  setenv FACEOPS_AAP_TOKEN "$(cat $P/awx_token)"; setenv FACEOPS_EDA_CLAVE "$Q"; setenv FACEOPS_ADMIN "$U"; setenv FACEOPS_ADMIN_NOMBRE "$U"
  echo "$U" > /var/lib/demo/usuario; printf '%s' "$PC" > /var/lib/demo/clave.pendiente; unset Q PC
  touch /var/lib/demo/credenciales.listo
  log "credenciales del cliente recibidas y claves internas renovadas"
fi

setenv FACEOPS_URL_PUBLICA "https://faceops.$IP.sslip.io"; setenv FACEOPS_AAP_PUBLICA "https://awx.$IP.sslip.io"; setenv FACEOPS_EDA_PUBLICA "https://eda.$IP.sslip.io"
podman rm -f faceops >/dev/null 2>&1 || true
podman run -d --name faceops --restart=always --network host --env-file $ENV -v /var/lib/prueba/faceops:/var/lib/faceops:Z localhost/faceops:prueba >/dev/null
for i in $(seq 1 60); do curl -s -o /dev/null -m 3 http://127.0.0.1:8110/ingresar && break; sleep 3; done
if [ -f /var/lib/demo/clave.pendiente ]; then   # usuario del cliente en FaceOps (administrador); el usuario de la prueba queda sin acceso
  podman exec -i -e U="$(cat /var/lib/demo/usuario)" -e PC="$(cat /var/lib/demo/clave.pendiente)" faceops python3 - <<'PY' && rm -f /var/lib/demo/clave.pendiente
import os, secrets, sys
sys.path.insert(0, "/opt/faceops")
from werkzeug.security import generate_password_hash
from app import create_app
with create_app().app_context():
    from app.bd import ej, q1
    from app.semillas import crear_usuario
    U = os.environ["U"]
    if not q1("SELECT id FROM usuarios WHERE usuario=?", U):
        crear_usuario(U, U, es_admin=1, correo=f"{U}@demo.invoke")
    ej("UPDATE usuarios SET clave_hash=? WHERE usuario=?", generate_password_hash(os.environ["PC"]), U)
    ej("UPDATE usuarios SET clave_hash=? WHERE usuario='prueba'", generate_password_hash(secrets.token_urlsafe(24)))
print("demo: usuario del cliente listo en FaceOps")
PY
fi

install -d -m 755 /var/lib/demo/caddy
cat > /etc/demo/Caddyfile <<F
faceops.$IP.sslip.io {
	reverse_proxy 127.0.0.1:8110
}
awx.$IP.sslip.io {
	reverse_proxy 127.0.0.1:30080
}
eda.$IP.sslip.io {
	reverse_proxy 127.0.0.1:30081
}
F
chmod 644 /etc/demo/Caddyfile
podman rm -f caddy >/dev/null 2>&1 || true
podman run -d --name caddy --restart=always --network host -v /etc/demo/Caddyfile:/etc/caddy/Caddyfile:ro,Z -v /var/lib/demo/caddy:/data:Z \
  docker.io/library/caddy:2.8 >/dev/null
log "demo lista en https://faceops.$IP.sslip.io"
