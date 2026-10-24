#!/usr/bin/python3
# Demo 4 · Redes: detección de drift por eventos con Event-Driven Ansible.
# 1) Actualiza en Gitea (invoke/red-laboratorio) los playbooks y el rulebook, sin tocar config/ (estado deseado).
# 2) En EDA: credencial hacia AAP, proyecto, y la activación "Red · Drift por eventos" (webhook :5000).
# Idempotente: la activación se recrea en cada ejecución para tomar el rulebook vigente.
import base64, glob, json, os, re, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
E = "https://localhost/api/eda/v1"
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
REPO, ACT = "red-laboratorio", "Red · Drift por eventos"
IP = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
paso = lambda t: print(f"   · {t}", flush=True)


def eda(m, p, d=None, tolerar=False):
    req = urllib.request.Request(f"{E}/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": BASIC})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:500].decode(errors='replace')}")


def uno(rec, **f):
    r = eda("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


# 1. Gitea: playbooks, tareas y rulebook vigentes (config/ no se toca: es el estado deseado que se edita en la demo)
tmp = tempfile.mkdtemp()
url = f"http://invoke:{SEC['gitea_token']}@127.0.0.1:3000/invoke/{REPO}.git"
git = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True)
if subprocess.run(["git", "clone", "-q", url, tmp], capture_output=True).returncode:
    sys.exit("ERROR al clonar invoke/red-laboratorio")
for d in ("playbooks", "tareas", "rulebooks"):
    shutil.rmtree(os.path.join(tmp, d), ignore_errors=True)
    shutil.copytree(os.path.join(AQUI, REPO, d), os.path.join(tmp, d))
shutil.copy(os.path.join(AQUI, REPO, "README.md"), tmp)
git("add", "-A")
if git("status", "--porcelain").stdout.strip():
    git("-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl", "commit", "-qm",
        "Playbooks y rulebook de drift por eventos")
    if git("push", "-q", url, "HEAD:main").returncode:
        sys.exit("ERROR al subir a invoke/red-laboratorio")
shutil.rmtree(tmp)
paso("Gitea: invoke/red-laboratorio con playbooks y rulebooks/drift-red.yml")

# 2. Organización
org = uno("organizations", name="Redes") or uno("organizations", name="Default")
org = org["id"]

# 3. Credencial de EDA hacia AAP (para lanzar la plantilla)
tipo = uno("credential-types", name="Red Hat Ansible Automation Platform")["id"]
datos = {"name": "AAP · lanzar plantillas de red", "credential_type_id": tipo, "organization_id": org,
         "inputs": {"host": f"https://{IP}/api/controller/", "username": "admin", "password": CLAVE,
                    "verify_ssl": False}}
c = uno("eda-credentials", name=datos["name"])
cred = eda("PATCH", f"eda-credentials/{c['id']}/", datos)["id"] if c else eda("POST", "eda-credentials/", datos)["id"]
paso("Credencial EDA → AAP")

# 4. Proyecto desde Gitea
p = uno("projects", name="Red · laboratorio")
if p:
    eda("POST", f"projects/{p['id']}/sync/", {}, tolerar=True)
    proy = p["id"]
else:
    proy = eda("POST", "projects/", {"name": "Red · laboratorio", "organization_id": org, "verify_ssl": False,
                                     "url": f"http://192.168.122.1:3000/invoke/{REPO}.git",
                                     "description": "Rulebooks de la red del laboratorio"})["id"]
for _ in range(60):
    st = eda("GET", f"projects/{proy}/")["import_state"]
    if st in ("completed", "failed"):
        break
    time.sleep(3)
if st != "completed":
    sys.exit(f"ERROR al importar el proyecto EDA: {st}")
rb = None
for _ in range(10):
    rb = uno("rulebooks", project_id=proy, name="drift-red.yml")
    if rb:
        break
    time.sleep(3)
if not rb:
    sys.exit("ERROR: el proyecto EDA no trae rulebooks/drift-red.yml")
paso("Proyecto EDA 'Red · laboratorio' con drift-red.yml")

# 5. Activación (se recrea para tomar el rulebook vigente)
# El entorno de decisión servido por el Automation Hub local (los de registry.redhat.io piden credenciales)
des = eda("GET", "decision-environments/?page_size=50")["results"]
de = next((d for d in des if d["name"] == "Hub Default Decision Environment"), des[0])["id"]
a = uno("activations", name=ACT)
if a:
    eda("POST", f"activations/{a['id']}/disable/", {}, tolerar=True)
    time.sleep(5)
    eda("DELETE", f"activations/{a['id']}/", tolerar=True)
    for _ in range(20):
        if not uno("activations", name=ACT):
            break
        time.sleep(3)
act = eda("POST", "activations/", {
    "name": ACT, "description": "Recibe los avisos del vigía de cada equipo y lanza 'Red · Verificar drift'",
    "is_enabled": True, "decision_environment_id": de, "rulebook_id": rb["id"], "eda_credentials": [cred],
    "organization_id": org, "restart_policy": "on-failure", "log_level": "info"})["id"]
for _ in range(60):
    st = eda("GET", f"activations/{act}/")["status"]
    if st in ("running", "failed", "error"):
        break
    time.sleep(5)
paso(f"Activación '{ACT}': {st}")
print("   LISTO EDA")
