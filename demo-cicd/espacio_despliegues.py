#!/usr/bin/python3
# Espacio exclusivo de despliegues en AAP: organización propia, recursos de JBoss, etiquetas
# y un usuario auditor que solo ve despliegues. Idempotente.
import base64, glob, json, os, re, secrets, ssl, sys, time, urllib.error, urllib.parse, urllib.request

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
SECF = os.path.expanduser("~/demo-drift/cicd-secretos.txt")
SEC = dict(l.strip().split("=", 1) for l in open(SECF) if "=" in l)
CTX = ssl._create_unverified_context()
AUTH = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
C, G = "/api/controller/v2", "/api/gateway/v1"


def api(metodo, ruta, datos=None, tolerar=False):
    req = urllib.request.Request("https://localhost" + ruta, method=metodo,
                                 data=json.dumps(datos).encode() if datos is not None else None,
                                 headers={"Authorization": AUTH, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            cuerpo = r.read()
            return json.loads(cuerpo) if cuerpo else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {metodo} {ruta} -> {e.code}: {e.read()[:600].decode(errors='replace')}")


def buscar(rec, base=C, **f):
    r = api("GET", f"{base}/{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos, base=C):
    o = buscar(rec, base, name=nombre)
    return api("PATCH", f"{base}/{rec}/{o['id']}/", datos) if o else api("POST", f"{base}/{rec}/", dict(datos, name=nombre))


paso = lambda t: print(f"· {t}", flush=True)
ORG_N = "Despliegues CI/CD"

# Organización (se administra en el gateway y se sincroniza al controlador)
asegurar("organizations", ORG_N, {"description": "Solo despliegues de aplicaciones lanzados por pipelines"}, base=G)
org = None
for _ in range(20):
    org = buscar("organizations", name=ORG_N)
    if org:
        break
    time.sleep(3)
org = org["id"]
paso(f"Organización '{ORG_N}'")

# Recursos propios del espacio
maq_def = buscar("credentials", name="Nodos demo (ansible)")
tipo_ssh = maq_def["credential_type"]
maq = asegurar("credentials", "Servidores de aplicación (ansible)", {
    "organization": org, "credential_type": tipo_ssh,
    "inputs": {"username": "ansible", "ssh_key_data": open(os.path.expanduser("~/.ssh/id_demo")).read(),
               "become_method": "sudo"}})["id"]
jboss = buscar("credentials", name="JBoss EAP · nodo-jboss")["id"]
api("PATCH", f"{C}/credentials/{jboss}/", {"organization": org})

inv = asegurar("inventories", "Servidores de aplicación", {"organization": org,
                                                           "description": "JBoss y balanceador Nginx"})["id"]
for nombre, ip in (("nodo-jboss", 15), ("nodo-nginx", 11)):
    if not buscar("hosts", name=nombre, inventory=inv):
        api("POST", f"{C}/inventories/{inv}/hosts/", {"name": nombre, "variables": f"ansible_host: 192.168.122.{ip}"})

# Playbooks en Gitea (invoke/automatizacion), leídos con la credencial de solo lectura de la organización
cred_git = buscar("credentials", name="Gitea · lectura de playbooks", organization=org)
proy = asegurar("projects", "Despliegues de aplicaciones", {
    "organization": org, "scm_type": "git", "scm_url": "http://192.168.122.1:3000/invoke/automatizacion.git",
    "credential": cred_git["id"] if cred_git else None,
    "scm_branch": "main", "scm_update_on_launch": True})["id"]
upd = api("POST", f"{C}/projects/{proy}/update/", {})
while api("GET", f"{C}/project_updates/{upd['id']}/")["status"] not in ("successful", "failed", "error"):
    time.sleep(3)
paso("Credenciales, inventario y proyecto propios")

ETIQ = {"JBoss · Desplegar app": ["despliegue", "app:app-jboss", "ambiente:prod"],
        "JBoss · Preparar servidor": ["preparacion", "app:app-jboss", "ambiente:prod"]}
for nombre, etiquetas in ETIQ.items():
    jt = buscar("job_templates", name=nombre)
    api("PATCH", f"{C}/job_templates/{jt['id']}/", {"organization": org, "project": proy, "inventory": inv})
    actuales = api("GET", f"{C}/job_templates/{jt['id']}/credentials/")["results"]
    for c in actuales:
        if c["id"] not in (maq, jboss):
            api("POST", f"{C}/job_templates/{jt['id']}/credentials/", {"id": c["id"], "disassociate": True})
    for cid in (maq, jboss):
        if cid not in [c["id"] for c in actuales]:
            api("POST", f"{C}/job_templates/{jt['id']}/credentials/", {"id": cid})
    ya = [l["name"] for l in api("GET", f"{C}/job_templates/{jt['id']}/labels/")["results"]]
    for e in etiquetas:
        if e not in ya:
            api("POST", f"{C}/job_templates/{jt['id']}/labels/", {"name": e, "organization": org})
paso("Plantillas de JBoss movidas al espacio, con etiquetas")

# Usuario auditor: ve todo lo de la organización, no ejecuta ni ve claves
u = api("GET", f"{G}/users/?username=auditor-cicd")["results"]
if not u:
    clave = secrets.token_hex(8)
    u = api("POST", f"{G}/users/", {"username": "auditor-cicd", "password": clave,
                                     "first_name": "Auditor", "last_name": "Despliegues"})
    with open(SECF, "a") as f:
        f.write(f"auditor_clave={clave}\n")
else:
    u = u[0]
uc = None
for _ in range(10):
    uc = buscar("users", username="auditor-cicd")
    if uc:
        break
    time.sleep(3)
defs = {d["name"]: d["id"] for d in api("GET", f"{C}/role_definitions/?page_size=200")["results"]}
rol = defs.get("Organization Audit")  # rol del controlador: lectura de todo lo de la organización
if not rol:
    sys.exit(f"ERROR sin rol Organization Audit: {[n for n in defs if 'Organization' in n]}")
datos = {"role_definition": rol, "object_id": str(org)}
datos["user_ansible_id"] = u["summary_fields"]["resource"]["ansible_id"]
api("POST", f"{C}/role_user_assignments/", datos, tolerar=True)
paso("Usuario auditor-cicd: solo lectura del espacio de despliegues")
print("LISTO")
