#!/usr/bin/python3
# AAP y Vault para la plataforma como código: AppRole de playbooks, credencial, inventario,
# plantilla "Plataforma · Preparar dependencias" y permiso para tekton-ci. Idempotente.
import base64, glob, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request

INIT = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
VA, AAP = "http://192.168.122.1:8202/v1", "https://localhost/api/controller/v2"
CTX = ssl._create_unverified_context()


def http(m, url, d=None, cab=None, tolerar=False):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers=dict({"Content-Type": "application/json"}, **(cab or {})))
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {url} -> {e.code}: {e.read()[:400].decode(errors='replace')}")


vault = lambda m, p, d=None: http(m, f"{VA}/{p}", d, {"X-Vault-Token": INIT["root_token"]})
aap = lambda m, p, d=None, t=False: http(m, f"{AAP}/{p}", d, {"Authorization": "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()}, t)
q = lambda s: urllib.parse.quote(s)
paso = lambda t: print(f"· {t}", flush=True)

vault("PUT", "sys/policies/acl/playbooks-plataforma", {"policy": (
    'path "secret/data/plataforma" { capabilities = ["read"] }\n'
    'path "secret/data/apps/*" { capabilities = ["create", "read", "update"] }')})
vault("POST", "auth/approle/role/playbooks", {"token_policies": ["playbooks-plataforma"], "token_ttl": "15m"})
role_id = vault("GET", "auth/approle/role/playbooks/role-id")["data"]["role_id"]
secret_id = vault("POST", "auth/approle/role/playbooks/secret-id", {})["data"]["secret_id"]
paso("Vault: AppRole 'playbooks' (lee claves de plataforma, guarda claves de cada app)")

org = aap("GET", "organizations/?name=" + q("Despliegues CI/CD"))["results"][0]["id"]


def asegurar(rec, nombre, datos):
    o = aap("GET", f"{rec}/?name=" + q(nombre))["results"]
    return aap("PATCH", f"{rec}/{o[0]['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


tipo = asegurar("credential_types", "Vault · AppRole para playbooks", {
    "kind": "cloud", "description": "Permite a un playbook leer y guardar secretos en Vault",
    "inputs": {"fields": [{"id": "role_id", "label": "Role ID", "type": "string"},
                          {"id": "secret_id", "label": "Secret ID", "type": "string", "secret": True}],
               "required": ["role_id", "secret_id"]},
    "injectors": {"extra_vars": {"vault_role_id": "{{ role_id }}", "vault_secret_id": "{{ secret_id }}"}}})["id"]
cred_v = asegurar("credentials", "Vault · playbooks de plataforma", {
    "organization": org, "credential_type": tipo, "inputs": {"role_id": role_id, "secret_id": secret_id}})["id"]
paso("Credencial de Vault para playbooks en AAP")

inv = aap("GET", "inventories/?name=" + q("Servidores de aplicación"))["results"][0]["id"]
for nombre, ip in (("nodo-postgres", 13), ("nodo-tomcat", 14)):
    if not aap("GET", f"hosts/?name={nombre}&inventory={inv}")["results"]:
        aap("POST", f"inventories/{inv}/hosts/", {"name": nombre, "variables": f"ansible_host: 192.168.122.{ip}"})
paso("Inventario con PostgreSQL y servicios de plataforma")

proy = aap("GET", "projects/?name=" + q("Despliegues de aplicaciones"))["results"][0]["id"]
upd = aap("POST", f"projects/{proy}/update/", {})
while aap("GET", f"project_updates/{upd['id']}/")["status"] not in ("successful", "failed", "error"):
    time.sleep(3)
maq = aap("GET", "credentials/?name=" + q("Servidores de aplicación (ansible)"))["results"][0]["id"]
jboss = aap("GET", "credentials/?name=" + q("JBoss EAP · nodo-jboss"))["results"][0]["id"]
jt = asegurar("job_templates", "Plataforma · Preparar dependencias", {
    "organization": org, "project": proy, "inventory": inv, "playbook": "plataforma/aplicar.yml",
    "ask_variables_on_launch": True, "ask_labels_on_launch": True,
    "description": "Base de datos, caché, colas y Keycloak según plataforma.yml de la app"})["id"]
actuales = [c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]]
for cid in (maq, jboss, cred_v):
    if cid not in actuales:
        aap("POST", f"job_templates/{jt}/credentials/", {"id": cid})
ya = [l["name"] for l in aap("GET", f"job_templates/{jt}/labels/")["results"]]
for e in ("plataforma", "app:app-jboss", "ambiente:prod"):
    if e not in ya:
        aap("POST", f"job_templates/{jt}/labels/", {"name": e, "organization": org})
paso("Plantilla 'Plataforma · Preparar dependencias'")

gu = http("GET", "https://localhost/api/gateway/v1/users/?username=tekton-ci", None,
          {"Authorization": "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()})["results"][0]
defs = {d["name"]: d["id"] for d in aap("GET", "role_definitions/?page_size=200")["results"]}
aap("POST", "role_user_assignments/", {"role_definition": defs["JobTemplate Execute"], "object_id": str(jt),
                                       "user_ansible_id": gu["summary_fields"]["resource"]["ansible_id"]}, t=True)
paso("tekton-ci puede pedir la plataforma")
print("LISTO")
