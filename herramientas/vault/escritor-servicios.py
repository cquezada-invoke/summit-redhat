#!/usr/bin/python3
"""Bóveda: AppRole «faceops-servicios» que solo puede ESCRIBIR credenciales de servicios en faceops/web/* (no leer otras
rutas ni las de acceso a máquinas). Sus datos quedan en AAP como credencial cifrada «FaceOps · Bóveda (servicios)», que
reciben las skills que generan claves (Redis, …) para guardarlas en la bóveda y que FaceOps entre sin mostrarlas.
Idempotente; corre en la VM del stand; nunca imprime secretos."""
import glob, json, os, ssl, subprocess, tempfile, urllib.request

VAULT = "http://192.168.122.210:8200"
paso = lambda t: print(f"   · {t}", flush=True)
llave = subprocess.run(["sudo", "podman", "exec", "faceops", "cat", "/var/lib/faceops/llaves/id_demo"], capture_output=True, text=True, check=True).stdout
fd, kf = tempfile.mkstemp(); os.write(fd, llave.encode()); os.close(fd); os.chmod(kf, 0o600)
try:
    out = subprocess.run(["ssh", "-i", kf, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                          "-o", "LogLevel=ERROR", "ansible@192.168.122.210", "sudo cat /root/vault-init.json"], capture_output=True, text=True, check=True).stdout
finally:
    os.remove(kf)
ROOT = json.loads(out)["root_token"]


def v(m, p, d=None):
    req = urllib.request.Request(f"{VAULT}/v1/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"X-Vault-Token": ROOT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        b = r.read()
        return json.loads(b) if b else {}


v("PUT", "sys/policies/acl/faceops-escritura-servicios",
  {"policy": 'path "faceops/data/web/*" { capabilities = ["create", "update"] }\n'})
v("POST", "auth/approle/role/faceops-servicios", {"token_policies": ["faceops-escritura-servicios"], "token_ttl": "10m",
                                                   "token_max_ttl": "30m", "secret_id_ttl": "0"})
rid = v("GET", "auth/approle/role/faceops-servicios/role-id")["data"]["role_id"]
sid = v("POST", "auth/approle/role/faceops-servicios/secret-id", {})["data"]["secret_id"]
paso("AppRole «faceops-servicios» (solo escribe faceops/web/*)")

# AAP: tipo de credencial + credencial + plantillas que la usan
inv = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
clave = next(l.split("=", 1)[1].strip() for l in open(inv) if l.startswith("gateway_admin_password="))
import base64
AUT = "Basic " + base64.b64encode(f"admin:{clave}".encode()).decode()
CTX = ssl._create_unverified_context()


def aap(m, p, d=None):
    req = urllib.request.Request(f"https://127.0.0.1/api/controller/v2/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Authorization": AUT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        b = r.read()
        return json.loads(b) if b else {}


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + "&".join(f"{k}={urllib.parse.quote(str(x))}" for k, x in f.items()))["results"]
    return r[0] if r else None


import urllib.parse
ct = uno("credential_types", name="FaceOps · Bóveda (servicios)") or aap("POST", "credential_types/", {
    "name": "FaceOps · Bóveda (servicios)", "kind": "cloud", "description": "AppRole que solo escribe credenciales de servicios en faceops/web/*",
    "inputs": {"fields": [{"id": "addr", "label": "Dirección"}, {"id": "role_id", "label": "Role ID"},
                          {"id": "secret_id", "label": "Secret ID", "secret": True}], "required": ["addr", "role_id", "secret_id"]},
    "injectors": {"extra_vars": {"faceops_vault_addr": "{{ addr }}", "faceops_vault_role_id": "{{ role_id }}", "faceops_vault_secret_id": "{{ secret_id }}"}}})
org = uno("organizations", name="FaceOps")
datos = {"name": "FaceOps · Bóveda (servicios)", "organization": org["id"], "credential_type": ct["id"],
         "inputs": {"addr": VAULT, "role_id": rid, "secret_id": sid}}
cr = uno("credentials", name=datos["name"])
cr = aap("PATCH", f"credentials/{cr['id']}/", datos) if cr else aap("POST", "credentials/", datos)
for jt in ("Redis · Preparar servidor",):
    t = uno("job_templates", name=jt)
    if t and cr["id"] not in {c["id"] for c in aap("GET", f"job_templates/{t['id']}/credentials/")["results"]}:
        aap("POST", f"job_templates/{t['id']}/credentials/", {"id": cr["id"]})
paso("Credencial AAP «FaceOps · Bóveda (servicios)» en «Redis · Preparar servidor»")
print("LISTO")
