#!/usr/bin/python3
# Data center · deja AAP listo (idempotente):
#  · Gitea invoke/datacenter con playbooks, inventario (generado por dc.py) y colecciones
#  · AAP: organización "Data center", proyecto, inventario "Data center 1" (fuente: inventario/dc1.ini del proyecto),
#    credenciales (SSH de la demo y Windows del DC) y plantillas. "DC · Registrar en Red Hat" pide la clave de
#    activación en una encuesta (campo oculto): la escribe quien la lanza.
# Uso: python3 configurar_aap.py [dc1]
import base64, glob, json, os, re, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
DC = sys.argv[1] if len(sys.argv) > 1 else "dc1"
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
DCSEC = dict(l.rstrip("\n").split("=", 1) for l in open(os.path.expanduser(f"~/datacenter/secretos-{DC}.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, G = "https://localhost/api/controller/v2", "https://localhost/api/gateway/v1"
GITEA = "http://127.0.0.1:3000/api/v1"
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
ORG_N, REPO = "Data center", "datacenter"
paso = lambda t: print(f"   · {t}", flush=True)
q = urllib.parse.quote


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
        sys.exit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:500].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http(m, f"{C}/{p}", d, {"Authorization": BASIC}, t)
gw = lambda m, p, d=None, t=False: http(m, f"{G}/{p}", d, {"Authorization": BASIC}, t)
gitea = lambda m, p, d=None, t=False: http(m, f"{GITEA}/{p}", d, {"Authorization": "token " + SEC["gitea_token"]}, t)


def uno(f, rec, **filtro):
    r = f("GET", f"{rec}/?" + urllib.parse.urlencode(filtro))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(aap, rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


def esperar(url_estado, fin=("successful", "failed", "error", "canceled")):
    for _ in range(200):
        st = aap("GET", url_estado)["status"]
        if st in fin:
            return st
        time.sleep(3)
    return "timeout"


# 1. Gitea
if not gitea("GET", f"repos/invoke/{REPO}", t=True):
    gitea("POST", "user/repos", {"name": REPO, "private": False, "default_branch": "main", "auto_init": True,
                                 "description": "Data center: inventario único, redes, equipos e identidad (IGA)"})
tmp = tempfile.mkdtemp()
url = f"http://invoke:{SEC['gitea_token']}@127.0.0.1:3000/invoke/{REPO}.git"
git = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True)
if subprocess.run(["git", "clone", "-q", url, tmp], capture_output=True).returncode:
    sys.exit(f"ERROR al clonar invoke/{REPO}")
for d in ("playbooks", "inventario", "collections", "herramientas", "plantillas", "iga"):
    shutil.rmtree(os.path.join(tmp, d), ignore_errors=True)
    if os.path.isdir(os.path.join(AQUI, d)):
        shutil.copytree(os.path.join(AQUI, d), os.path.join(tmp, d))
for f in ("README.md",):
    if os.path.exists(os.path.join(AQUI, f)):
        shutil.copy(os.path.join(AQUI, f), tmp)
git("add", "-A")
if git("status", "--porcelain").stdout.strip():
    git("-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl", "commit", "-qm", "Data center: sincronización desde summit-redhat")
    if git("push", "-q", url, "HEAD:main").returncode:
        sys.exit(f"ERROR al subir a invoke/{REPO}")
shutil.rmtree(tmp)
gitea("PUT", f"repos/invoke/{REPO}/collaborators/cquezada", {"permission": "write"}, t=True)
paso(f"Gitea: invoke/{REPO}")

# 2. Organización, proyecto e inventario
if not gw("GET", "organizations/?name=" + q(ORG_N))["results"]:
    gw("POST", "organizations/", {"name": ORG_N, "description": "Data center: parque de infraestructura del cliente ficticio"})
org = None
for _ in range(20):
    org = uno(aap, "organizations", name=ORG_N)
    if org:
        break
    time.sleep(3)
org = org["id"]
ya = {c["id"] for c in aap("GET", f"organizations/{org}/galaxy_credentials/")["results"]}
ref = uno(aap, "organizations", name="Redes") or uno(aap, "organizations", name="Default")
for c in aap("GET", f"organizations/{ref['id']}/galaxy_credentials/")["results"]:
    if c["id"] not in ya:
        aap("POST", f"organizations/{org}/galaxy_credentials/", {"id": c["id"]})
paso(f"Organización '{ORG_N}'")

proy = asegurar("projects", "Data center", {
    "organization": org, "scm_type": "git", "scm_url": f"http://192.168.122.1:3000/invoke/{REPO}.git",
    "scm_branch": "main", "scm_update_on_launch": True, "description": "Playbooks del data center (Gitea invoke/datacenter)"})["id"]
st = esperar(f"project_updates/{aap('POST', f'projects/{proy}/update/', {})['id']}/")
if st != "successful":
    sys.exit(f"ERROR al sincronizar el proyecto Data center: {st}")
paso("Proyecto 'Data center' sincronizado")

NOMBRE_INV = {"dc1": "Data center 1"}.get(DC, f"Data center {DC}")
inv = asegurar("inventories", NOMBRE_INV, {"organization": org,
               "description": f"Generado desde inventario/{DC}.yml (fuente de verdad del parque)"})["id"]
fuente = asegurar("inventory_sources", f"{NOMBRE_INV} · inventario único", {
    "inventory": inv, "source": "scm", "source_project": proy, "source_path": f"inventario/{DC}.ini",
    "overwrite": True, "overwrite_vars": True, "update_on_launch": False})["id"]
st = esperar(f"inventory_updates/{aap('POST', f'inventory_sources/{fuente}/update/', {})['id']}/")
paso(f"Inventario '{NOMBRE_INV}': {st}, {aap('GET', f'inventories/{inv}/')['total_hosts']} equipos")


# 3. Credenciales
def cred(nombre, tipo, inputs, desc):
    t = uno(aap, "credential_types", name=tipo)["id"]
    return asegurar("credentials", nombre, {"organization": org, "credential_type": t, "inputs": inputs, "description": desc})["id"]


c_ssh = cred("DC · SSH ansible", "Machine",
             {"username": "ansible", "ssh_key_data": open(os.path.expanduser("~/.ssh/id_demo")).read(), "become_method": "sudo"},
             "Llave SSH de los equipos Linux del data center")
win = next((e for e in DCSEC if e.endswith("_ansible")), None)
c_win = cred(f"{DC.upper()} · Windows ansible", "Machine", {"username": "ansible", "password": DCSEC[win]},
             "Cuenta de servicio local 'ansible' de los Windows del data center") if win else None
# Secretos del DC como credencial de AAP (llegan al playbook como la variable dc_secretos, igual que correr.sh)
tipo = uno(aap, "credential_types", name="Secretos del data center")
datos_tipo = {"kind": "cloud", "description": "Claves del data center (JSON); llegan como dc_secretos",
              "inputs": {"fields": [{"id": "secretos_json", "label": "Secretos (JSON)", "type": "string", "secret": True, "multiline": True}],
                         "required": ["secretos_json"]},
              "injectors": {"extra_vars": {"dc_secretos": "{{ secretos_json }}"}}}
tipo = aap("PATCH", f"credential_types/{tipo['id']}/", datos_tipo) if tipo else aap("POST", "credential_types/", dict(datos_tipo, name="Secretos del data center"))
c_sec = asegurar("credentials", f"{DC.upper()} · Secretos", {"organization": org, "credential_type": tipo["id"],
                 "inputs": {"secretos_json": json.dumps(DCSEC)}, "description": f"Claves de {DC} (~/datacenter/secretos-{DC}.txt)"})["id"]
paso("Credenciales SSH, Windows y secretos")

# 4. Plantillas
EE = uno(aap, "execution_environments", name="Default execution environment")["id"]


def plantilla(nombre, pb, desc, creds, encuesta=None, limite=""):
    datos = {"organization": org, "project": proy, "inventory": inv, "playbook": pb, "description": desc,
             "execution_environment": EE, "ask_variables_on_launch": encuesta is None, "survey_enabled": bool(encuesta),
             "limit": limite}
    o = asegurar("job_templates", nombre, datos)
    ya = {c["id"] for c in aap("GET", f"job_templates/{o['id']}/credentials/")["results"]}
    for cid in creds:
        if cid and cid not in ya:
            aap("POST", f"job_templates/{o['id']}/credentials/", {"id": cid})
    if encuesta:
        aap("POST", f"job_templates/{o['id']}/survey_spec/", {"name": nombre, "description": desc, "spec": encuesta})
    return o["id"]


jt = {}
jt["registrar"] = plantilla("DC · Registrar en Red Hat", "playbooks/registrar.yml",
                            "Registra los equipos RHEL del data center con una clave de activación (BaseOS y AppStream)",
                            [c_ssh], encuesta=[
                                {"question_name": "Clave de activación", "variable": "clave_activacion", "type": "password",
                                 "required": True, "question_description": "Activation key de console.redhat.com"},
                                {"question_name": "Organización (org ID)", "variable": "org_id", "type": "text",
                                 "required": True, "default": "12665691"}])
for nombre, pb, desc in [
    ("DC · Base de los equipos", "playbooks/base.yml", "Nombres, hora, paquetes base y firewall en todos los equipos"),
    ("DC · Active Directory", "playbooks/ad.yml", "Bosque corp.invoke.lab, DNS, CA (LDAPS) y unidades organizativas"),
    ("DC · Red Hat IdM", "playbooks/idm.yml", "Servidor IdM de linux.invoke.lab con DNS integrado"),
    ("DC · Directory Server", "playbooks/directorio.yml", "389 Directory Server con el sufijo dc=invoke,dc=lab"),
    ("DC · midPoint", "playbooks/midpoint.yml", "midPoint + PostgreSQL en contenedores"),
    ("DC · Keycloak", "playbooks/keycloak.yml", "Keycloak federado al directorio corporativo"),
    ("DC · Configurar IGA", "playbooks/iga.yml", "Fuente RRHH, recursos (AD, IdM, Directory Server), roles y tareas de midPoint"),
    ("DC · Construir identidad (Linux)", "playbooks/sitio-linux.yml", "Base, Directory Server, IdM, midPoint, Keycloak e IGA en orden"),
]:
    if os.path.exists(os.path.join(AQUI, pb)):
        jt[pb] = plantilla(nombre, pb, desc, [c_win if pb.endswith("/ad.yml") else c_ssh, c_sec])
paso("Plantillas: " + ", ".join(o["name"] for o in aap("GET", f"job_templates/?organization={org}&page_size=50")["results"]))
print(f"LISTO · https://34.176.42.56/execution/templates/job-template/{jt['registrar']}/details")
