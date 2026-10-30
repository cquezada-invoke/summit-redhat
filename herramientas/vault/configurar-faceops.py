#!/usr/bin/python3
"""Conecta FaceOps con la bóveda de nodo-vault (idempotente; corre en la VM del stand). Nunca imprime secretos.
 · kv v2 «faceops/», AppRole «faceops» con política de solo lectura; su role_id/secret_id quedan en ~/faceops/faceops.env (0600)
 · faceops/maquinas/<equipo>: cómo entrar a cada máquina (SSH: usuario y llave · RDP: usuario y clave)
 · faceops/web/<equipo>: credencial de su consola web (si tiene)
El token raíz y la llave de desellado quedan solo en nodo-vault (/root/vault-init.json)."""
import json, os, subprocess, sys, tempfile, urllib.request

VAULT = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.122.210:8200"
IP = VAULT.split("//")[1].split(":")[0]
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
ENV = os.path.expanduser("~/faceops/faceops.env")
paso = lambda t: print(f"   · {t}", flush=True)

llave = subprocess.run(["sudo", "podman", "exec", "faceops", "cat", "/var/lib/faceops/llaves/id_demo"], capture_output=True, text=True, check=True).stdout
fd, kf = tempfile.mkstemp(); os.write(fd, llave.encode()); os.close(fd); os.chmod(kf, 0o600)
try:
    out = subprocess.run(["ssh", "-i", kf, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                          "-o", "LogLevel=ERROR", f"ansible@{IP}", "sudo cat /root/vault-init.json"], capture_output=True, text=True, check=True).stdout
finally:
    os.remove(kf)
ROOT = json.loads(out)["root_token"]


def v(m, p, d=None, tolerar=(), tok=None):
    req = urllib.request.Request(f"{VAULT}/v1/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"X-Vault-Token": tok or ROOT, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if e.code in tolerar:
            return None
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:200].decode(errors='replace')}")


montajes = v("GET", "sys/mounts")
if "faceops/" not in montajes.get("data", montajes):
    v("POST", "sys/mounts/faceops", {"type": "kv", "options": {"version": "2"}, "description": "Credenciales de acceso de FaceOps"})
if "approle/" not in v("GET", "sys/auth").get("data", {}):
    v("POST", "sys/auth/approle", {"type": "approle"})
v("PUT", "sys/policies/acl/faceops-lectura", {"policy": 'path "faceops/data/*" { capabilities = ["read"] }\npath "faceops/metadata/*" { capabilities = ["read", "list"] }\n'})
v("POST", "auth/approle/role/faceops", {"token_policies": ["faceops-lectura"], "token_ttl": "1h", "token_max_ttl": "4h", "secret_id_ttl": "0"})
paso("kv «faceops/», AppRole «faceops» (solo lectura)")

env = dict(l.rstrip("\n").split("=", 1) for l in open(ENV) if "=" in l) if os.path.exists(ENV) else {}
if env.get("FACEOPS_VAULT_ADDR") != VAULT or not env.get("FACEOPS_VAULT_SECRET_ID"):
    env["FACEOPS_VAULT_ADDR"] = VAULT
    env["FACEOPS_VAULT_ROLE_ID"] = v("GET", "auth/approle/role/faceops/role-id")["data"]["role_id"]
    env["FACEOPS_VAULT_SECRET_ID"] = v("POST", "auth/approle/role/faceops/secret-id", {})["data"]["secret_id"]
    fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, "".join(f"{k}={x}\n" for k, x in env.items()).encode())
    os.close(fd)
    paso("Credencial AppRole de FaceOps en ~/faceops/faceops.env")

# equipos (desde FaceOps) y sus credenciales
eqs = json.loads(subprocess.run(["sudo", "podman", "exec", "-i", "-e", "FACEOPS_VIGIA=0", "faceops", "python3", "-c",
    "import json; from app import create_app; from app.bd import q; create_app(); "
    "print(json.dumps([dict(nombre=e['nombre'], ip=e['ip'], so=e['so'] or '', skills=e['skills_auto'] or '[]') for e in q(\"SELECT * FROM equipos WHERE estado!='retirado'\")]))"],
    capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1])
n = 0
for e in eqs:
    ip = e["ip"] or ""
    if not ip.startswith("192.168.122.") or ip == "192.168.122.1":
        continue
    if "windows" in e["so"].lower():
        if e["nombre"] == "nodo-windows":
            v("POST", f"faceops/data/maquinas/{e['nombre']}", {"data": {"metodo": "rdp", "usuario": "ansible", "clave": SEC["windows_ansible_clave"]}})
            n += 1
        continue
    v("POST", f"faceops/data/maquinas/{e['nombre']}", {"data": {"metodo": "ssh", "usuario": "ansible", "llave_privada": llave}})
    n += 1
    if "jboss" in e["skills"]:
        v("POST", f"faceops/data/web/{e['nombre']}", {"data": {"sitio": "JBoss EAP (administración)", "usuario": SEC["jboss_usuario"], "clave": SEC["jboss_clave"]}})
v("POST", "faceops/data/web/anfitrion-stand", {"data": {"sitio": "Gitea", "usuario": "cquezada", "clave": SEC["gitea_cquezada_clave"]}})
paso(f"{n} máquinas con su acceso en faceops/maquinas/ y credenciales web en faceops/web/")
print("LISTO")
