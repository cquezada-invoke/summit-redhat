#!/usr/bin/python3
"""Repositorio del laboratorio en Gitea (idempotente, corre en la VM del stand):
 · invoke/respaldo-servidores (privado): una rama por equipo con su configuración, paquetes y comandos (respaldo continuo)
 · Usuario de servicio «faceops-respaldo»: solo escribe en ese repositorio y en los paquetes de invoke (equipo «respaldo»)
   Su token queda en ~/faceops/respaldo-token (0600, no se imprime); AAP lo recibe como credencial cifrada.
 · Registro de paquetes de invoke: RPM (repositorio dnf) y genéricos (ISO, imágenes e instaladores)."""
import json, os, secrets, sys, urllib.error, urllib.request, base64

SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
API = "http://127.0.0.1:3000/api/v1"
TOK = os.path.expanduser("~/faceops/respaldo-token")


def http(m, p, d=None, auth=None, tolerar=False):
    req = urllib.request.Request(API + p, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": auth or "token " + SEC["gitea_token"]})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


paso = lambda t: print(f"   · {t}", flush=True)
if not http("GET", "/repos/invoke/respaldo-servidores", tolerar=True):
    http("POST", "/orgs/invoke/repos", {"name": "respaldo-servidores", "private": True, "auto_init": True, "default_branch": "main",
                                        "description": "Respaldo continuo de cada servidor: una rama por equipo (configuración, paquetes, servicios y comandos)"})
paso("Repositorio privado invoke/respaldo-servidores")

U = "faceops-respaldo"
if not http("GET", f"/users/{U}", tolerar=True):
    http("POST", "/admin/users", {"username": U, "email": "faceops-respaldo@invoke.cl", "password": secrets.token_urlsafe(24),
                                  "must_change_password": False, "full_name": "FaceOps · respaldo continuo", "visibility": "private"})
paso(f"Usuario de servicio {U}")

equipos = http("GET", "/orgs/invoke/teams") or []
t = next((x for x in equipos if x["name"] == "respaldo"), None)
datos = {"name": "respaldo", "description": "Respaldo continuo: escribe en respaldo-servidores y en el registro de paquetes",
         "permission": "write", "includes_all_repositories": False, "can_create_org_repo": False,
         "units_map": {"repo.code": "write", "repo.packages": "write"}}
t = http("PATCH", f"/teams/{t['id']}", datos) if t else http("POST", "/orgs/invoke/teams", datos)
http("PUT", f"/teams/{t['id']}/members/{U}", tolerar=True)
http("PUT", f"/teams/{t['id']}/repos/invoke/respaldo-servidores", tolerar=True)
paso("Equipo «respaldo» en invoke: escribe solo en respaldo-servidores y en los paquetes")

if not os.path.exists(TOK):
    clave = secrets.token_urlsafe(24)
    http("PATCH", f"/admin/users/{U}", {"login_name": U, "source_id": 0, "password": clave, "must_change_password": False})
    basic = "Basic " + base64.b64encode(f"{U}:{clave}".encode()).decode()
    tk = http("POST", f"/users/{U}/tokens", {"name": "respaldo-" + secrets.token_hex(3), "scopes": ["write:repository", "write:package", "read:organization"]}, auth=basic)
    os.makedirs(os.path.dirname(TOK), mode=0o700, exist_ok=True)
    fd = os.open(TOK, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, tk["sha1"].encode())
    os.close(fd)
    paso("Token del usuario de servicio en ~/faceops/respaldo-token")
else:
    paso("Token del usuario de servicio vigente")
print("LISTO")
