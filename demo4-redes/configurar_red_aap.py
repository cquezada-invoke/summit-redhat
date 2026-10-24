#!/usr/bin/python3
# Demo 4 · Redes: repositorio de red en Gitea, llave del laboratorio en Vault y, en AAP, la organización
# "Redes" con su inventario, credencial (desde Vault), proyecto y plantillas. No modifica lo existente
# salvo agregar la ruta secret/red/* a la política de lectura de AAP en Vault. Idempotente.
import base64, glob, json, os, re, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
INIT = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))
SECF = os.path.expanduser("~/demo-drift/cicd-secretos.txt")
SEC = dict(l.strip().split("=", 1) for l in open(SECF) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
VA, C, G = "http://192.168.122.1:8202/v1", "https://localhost/api/controller/v2", "https://localhost/api/gateway/v1"
GITEA = "http://127.0.0.1:3000/api/v1"
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
ORG_N, REPO = "Redes", "red-laboratorio"


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
aap = lambda m, p, d=None, t=False: http(m, f"{C}/{p}", d, {"Authorization": BASIC}, t)
gw = lambda m, p, d=None, t=False: http(m, f"{G}/{p}", d, {"Authorization": BASIC}, t)
gitea = lambda m, p, d=None, t=False: http(m, f"{GITEA}/{p}", d, {"Authorization": "token " + SEC["gitea_token"]}, t)
q = urllib.parse.quote
paso = lambda t: print(f"   · {t}", flush=True)


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


# 1. Gitea: repositorio con el estado deseado de la red (se siembra solo la primera vez)
if not gitea("GET", f"repos/invoke/{REPO}", t=True):
    gitea("POST", "user/repos", {"name": REPO, "private": False, "default_branch": "main",
                                 "description": "Estado deseado de la red del laboratorio (Demo 4)"})
    tmp = tempfile.mkdtemp()
    shutil.copytree(os.path.join(AQUI, REPO), tmp, dirs_exist_ok=True)
    url = f"http://invoke:{SEC['gitea_token']}@127.0.0.1:3000/invoke/{REPO}.git"
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl",
                 "commit", "-qm", "Estado deseado inicial de la red"], ["git", "push", "-q", url, "main"]):
        if subprocess.run(cmd, cwd=tmp, capture_output=True).returncode:
            sys.exit(f"ERROR al sembrar {REPO}: {' '.join(cmd[:3])}")
    shutil.rmtree(tmp)
for u in ("cquezada", "jperez"):
    gitea("PUT", f"repos/invoke/{REPO}/collaborators/{u}", {"permission": "write"}, t=True)
paso(f"Gitea: invoke/{REPO} con la configuración deseada de la red")

# 2. Vault: llave del laboratorio y permiso de lectura para AAP
vault("POST", "secret/data/red/ssh", {"data": {"usuario": "root",
                                               "llave": open(os.path.expanduser("~/demo4/llave-lab")).read()}})
pol = vault("GET", "sys/policies/acl/aap-lectura")["data"]["policy"]
if "secret/data/red/" not in pol:
    vault("PUT", "sys/policies/acl/aap-lectura",
          {"policy": pol.rstrip() + '\npath "secret/data/red/*" { capabilities = ["read"] }\n'})
paso("Vault: secret/red/ssh y lectura de secret/red/* para AAP")

# 3. Organización "Redes"
if not gw("GET", "organizations/?name=" + q(ORG_N))["results"]:
    gw("POST", "organizations/", {"name": ORG_N, "description": "Automatización de red: laboratorio de la Demo 4"})
org = None
for _ in range(20):
    org = uno("organizations", name=ORG_N)
    if org:
        break
    time.sleep(3)
org = org["id"]
paso("Organización 'Redes'")

# 4. Credencial SSH del laboratorio: usuario y llave se leen desde Vault al ejecutar
maquina = aap("GET", "credential_types/?namespace=ssh")["results"][0]["id"]
cred = asegurar("credentials", "Red · laboratorio (ssh)", {
    "organization": org, "credential_type": maquina, "inputs": {},
    "description": "Usuario y llave desde Vault (secret/red/ssh)"})["id"]
vcred = uno("credentials", name="HashiCorp Vault · secretos")["id"]
for f in aap("GET", f"credentials/{cred}/input_sources/")["results"]:
    aap("DELETE", f"credential_input_sources/{f['id']}/")
for campo, clave in (("username", "usuario"), ("ssh_key_data", "llave")):
    aap("POST", "credential_input_sources/", {"target_credential": cred, "input_field_name": campo,
                                              "source_credential": vcred,
                                              "metadata": {"secret_backend": "secret", "secret_path": "red/ssh",
                                                           "secret_key": clave}})
paso("Credencial 'Red · laboratorio (ssh)' enlazada a Vault")

# 5. Inventario
inv = asegurar("inventories", "Red · laboratorio", {
    "organization": org, "description": "Equipos de red emulados con containerlab (gestión 172.20.20.0/24)",
    "variables": "ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'"})["id"]
grupos = {}
for g, desc in (("gestionados", "Equipos cuyo estado se declara en Git"), ("fortigate", "Firewall (fase 2)"),
                ("arista", "Switches cEOS (fase 2)")):
    o = aap("GET", f"groups/?name={g}&inventory={inv}")["results"]
    grupos[g] = o[0]["id"] if o else aap("POST", f"inventories/{inv}/groups/", {"name": g, "description": desc})["id"]
h = aap("GET", f"hosts/?name=rtr&inventory={inv}")["results"]
hv = "ansible_host: 172.20.20.11\nbgp_vecino: 10.0.0.1\ndescripcion: Router de borde (FRR)"
hid = aap("PATCH", f"hosts/{h[0]['id']}/", {"variables": hv})["id"] if h else \
    aap("POST", f"inventories/{inv}/hosts/", {"name": "rtr", "variables": hv})["id"]
aap("POST", f"groups/{grupos['gestionados']}/hosts/", {"id": hid}, t=True)
paso("Inventario 'Red · laboratorio' (rtr en gestionados; grupos fortigate y arista listos)")

# 6. Proyecto desde Gitea
proy = asegurar("projects", "Red · laboratorio", {
    "organization": org, "scm_type": "git", "scm_url": f"http://192.168.122.1:3000/invoke/{REPO}.git",
    "scm_branch": "main", "scm_update_on_launch": True, "description": "Playbooks y configuración de red desde Gitea"})["id"]
upd = aap("POST", f"projects/{proy}/update/", {})
while (st := aap("GET", f"project_updates/{upd['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
if st != "successful":
    sys.exit(f"ERROR al sincronizar el proyecto de red: {st}")
paso("Proyecto 'Red · laboratorio' sincronizado")

# 7. Plantillas
etiquetas = ("red", "laboratorio")
plantillas = {}
for nombre, pb, desc, creds in (
        ("Red · Aplicar configuración", "playbooks/aplicar.yml", "Lleva la red al estado declarado en Git", [cred]),
        ("Red · Verificar drift", "playbooks/drift.yml", "Detecta cambios manuales y los revierte al estado de Git", [cred]),
        ("Red · Solicitar acceso", "playbooks/solicitar-acceso.yml",
         "PREPARADA (fase 2): crea en el FortiGate la regla pedida por la app", [])):
    jt = asegurar("job_templates", nombre, {"organization": org, "project": proy, "inventory": inv, "playbook": pb,
                                            "description": desc, "ask_variables_on_launch": True,
                                            "ask_labels_on_launch": True})["id"]
    ya = [c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]]
    for cid in creds:
        if cid not in ya:
            aap("POST", f"job_templates/{jt}/credentials/", {"id": cid})
    tiene = [l["name"] for l in aap("GET", f"job_templates/{jt}/labels/")["results"]]
    for e in etiquetas:
        if e not in tiene:
            aap("POST", f"job_templates/{jt}/labels/", {"name": e, "organization": org})
    plantillas[nombre] = jt
paso("Plantillas: Aplicar configuración, Verificar drift y Solicitar acceso (preparada)")

# 8. Verificación de drift programada cada 10 minutos
jt = plantillas["Red · Verificar drift"]
if not aap("GET", f"job_templates/{jt}/schedules/")["results"]:
    aap("POST", f"job_templates/{jt}/schedules/", {
        "name": "Drift de red cada 10 minutos", "enabled": True,
        "rrule": "DTSTART;TZID=America/Santiago:20261023T000000 RRULE:FREQ=MINUTELY;INTERVAL=10"})
paso("Programación: verificación de drift cada 10 minutos")

# 9. El auditor también ve la organización de redes (solo lectura)
u = gw("GET", "users/?username=auditor-cicd")["results"]
defs = {d["name"]: d["id"] for d in aap("GET", "role_definitions/?page_size=200")["results"]}
if u and "Organization Audit" in defs:
    aap("POST", "role_user_assignments/", {"role_definition": defs["Organization Audit"], "object_id": str(org),
                                           "user_ansible_id": u[0]["summary_fields"]["resource"]["ansible_id"]}, t=True)
    paso("auditor-cicd: solo lectura de 'Redes'")
print("   LISTO AAP")
