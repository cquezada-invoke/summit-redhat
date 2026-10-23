#!/usr/bin/python3
# Vault guarda la clave de JBoss y AAP la consulta al ejecutar (AppRole de solo lectura).
# La credencial de JBoss en AAP deja de guardar la clave: solo apunta a Vault. Idempotente.
import base64, glob, json, os, re, ssl, sys, urllib.error, urllib.parse, urllib.request

INIT = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))
SECF = os.path.expanduser("~/demo-drift/cicd-secretos.txt")
SEC = dict(l.strip().split("=", 1) for l in open(SECF) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
VA = "http://192.168.122.1:8202/v1"
AAP = "https://localhost/api/controller/v2"
CTX = ssl._create_unverified_context()


def http(metodo, url, datos=None, cab=None, tolerar=False):
    req = urllib.request.Request(url, method=metodo, data=json.dumps(datos).encode() if datos is not None else None,
                                 headers=dict({"Content-Type": "application/json"}, **(cab or {})))
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            cuerpo = r.read()
            return json.loads(cuerpo) if cuerpo else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {metodo} {url} -> {e.code}: {e.read()[:400].decode(errors='replace')}")


vault = lambda m, p, d=None, t=False: http(m, f"{VA}/{p}", d, {"X-Vault-Token": INIT["root_token"]}, t)
aap = lambda m, p, d=None, t=False: http(m, f"{AAP}/{p}", d, {"Authorization": "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()}, t)
paso = lambda t: print(f"· {t}", flush=True)

if "secret/" not in vault("GET", "sys/mounts"):
    vault("POST", "sys/mounts/secret", {"type": "kv", "options": {"version": "2"}})
vault("POST", "secret/data/jboss", {"data": {"usuario": SEC["jboss_usuario"], "clave": SEC["jboss_clave"]}})
vault("POST", "secret/data/pipeline", {"data": {"gitea_token": SEC["gitea_token"]}})
paso("Secretos de JBoss y del pipeline guardados en Vault (secret/jboss, secret/pipeline)")

vault("PUT", "sys/policies/acl/aap-lectura", {"policy": 'path "secret/data/jboss" { capabilities = ["read"] }'})
if "approle/" not in vault("GET", "sys/auth"):
    vault("POST", "sys/auth/approle", {"type": "approle"})
vault("POST", "auth/approle/role/aap", {"token_policies": ["aap-lectura"], "token_ttl": "10m", "secret_id_ttl": "0"})
role_id = vault("GET", "auth/approle/role/aap/role-id")["data"]["role_id"]
secret_id = vault("POST", "auth/approle/role/aap/secret-id", {})["data"]["secret_id"]
paso("AppRole 'aap' con permiso solo de lectura sobre secret/jboss")

org = aap("GET", "organizations/?name=" + urllib.parse.quote("Despliegues CI/CD"))["results"][0]["id"]
tipo = aap("GET", "credential_types/?namespace=hashivault_kv")["results"][0]["id"]
nombre = "HashiCorp Vault · secretos"
inputs = {"url": "http://192.168.122.1:8202", "role_id": role_id, "secret_id": secret_id,
          "default_auth_path": "approle", "api_version": "v2"}
o = aap("GET", "credentials/?name=" + urllib.parse.quote(nombre))["results"]
vcred = (aap("PATCH", f"credentials/{o[0]['id']}/", {"inputs": inputs}) if o else
         aap("POST", "credentials/", {"name": nombre, "organization": org, "credential_type": tipo, "inputs": inputs,
                                      "description": "AAP consulta aquí las claves al momento de ejecutar"}))["id"]
paso("Credencial de búsqueda en Vault creada en AAP")

jb = aap("GET", "credentials/?name=" + urllib.parse.quote("JBoss EAP · nodo-jboss"))["results"][0]["id"]
for f in aap("GET", f"credentials/{jb}/input_sources/")["results"]:
    aap("DELETE", f"credential_input_sources/{f['id']}/")
for campo in ("usuario", "clave"):
    aap("POST", "credential_input_sources/", {"target_credential": jb, "input_field_name": campo,
                                              "source_credential": vcred,
                                              "metadata": {"secret_backend": "secret", "secret_path": "jboss",
                                                           "secret_key": campo}})
paso("La credencial de JBoss ahora toma usuario y clave desde Vault")
print("LISTO")
