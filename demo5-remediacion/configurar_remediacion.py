#!/usr/bin/python3
# Demo 5 · Autorremediación: repositorio invoke/operacion en Gitea y, en AAP, la organización "Operación" con
# proyecto, plantillas, workflow con aprobación y la activación de EDA "Remediación · Eventos de JBoss" (webhook :5001).
# Reutiliza el inventario "Nodos demo" y las credenciales existentes. No modifica lo de otras demos. Idempotente.
import base64, glob, json, os, re, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, G, E = "https://localhost/api/controller/v2", "https://localhost/api/gateway/v1", "https://localhost/api/eda/v1"
GITEA = "http://127.0.0.1:3000/api/v1"
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
ORG_N, REPO, ACT = "Operación", "operacion", "Remediación · Eventos de JBoss"
APROBACION_SEG = int(os.environ.get("APROBACION_SEG", "300"))
IP = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
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
        sys.exit(f"ERROR {m} {url} -> {e.code}: {e.read()[:500].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http(m, f"{C}/{p}", d, {"Authorization": BASIC}, t)
gw = lambda m, p, d=None, t=False: http(m, f"{G}/{p}", d, {"Authorization": BASIC}, t)
eda = lambda m, p, d=None, t=False: http(m, f"{E}/{p}", d, {"Authorization": BASIC}, t)
gitea = lambda m, p, d=None, t=False: http(m, f"{GITEA}/{p}", d, {"Authorization": "token " + SEC["gitea_token"]}, t)


def uno(f, rec, **filtro):
    r = f("GET", f"{rec}/?" + urllib.parse.urlencode(filtro))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(aap, rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


def etiquetar(rec, oid, org, nombres):
    tiene = [l["name"] for l in aap("GET", f"{rec}/{oid}/labels/")["results"]]
    for n in nombres:
        if n not in tiene:
            aap("POST", f"{rec}/{oid}/labels/", {"name": n, "organization": org})


# 1. Gitea: repositorio de operación (rulebooks, playbooks y políticas); se sincroniza en cada ejecución
DESC = "DEMO 5 · Autorremediación: políticas de riesgo y de logs, playbooks de remediación y rulebook de EDA"
if not gitea("GET", f"repos/invoke/{REPO}", t=True):
    gitea("POST", "user/repos", {"name": REPO, "private": False, "default_branch": "main", "description": DESC,
                                 "auto_init": True})
else:
    gitea("PATCH", f"repos/invoke/{REPO}", {"description": DESC})
tmp = tempfile.mkdtemp()
url = f"http://invoke:{SEC['gitea_token']}@127.0.0.1:3000/invoke/{REPO}.git"
git = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True)
if subprocess.run(["git", "clone", "-q", url, tmp], capture_output=True).returncode:
    sys.exit(f"ERROR al clonar invoke/{REPO}")
for d in ("rulebooks", "playbooks", "politicas"):
    shutil.rmtree(os.path.join(tmp, d), ignore_errors=True)
    shutil.copytree(os.path.join(AQUI, "operacion", d), os.path.join(tmp, d))
shutil.copy(os.path.join(AQUI, "operacion", "README.md"), tmp)
git("add", "-A")
if git("status", "--porcelain").stdout.strip():
    git("-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl", "commit", "-qm",
        "Remediación: rulebook, playbooks y políticas")
    if git("push", "-q", url, "HEAD:main").returncode:
        sys.exit(f"ERROR al subir a invoke/{REPO}")
shutil.rmtree(tmp)
for u in ("cquezada", "jperez"):
    gitea("PUT", f"repos/invoke/{REPO}/collaborators/{u}", {"permission": "write"}, t=True)
paso(f"Gitea: invoke/{REPO}")

# 2. Organización "Operación"
if not gw("GET", "organizations/?name=" + q(ORG_N))["results"]:
    gw("POST", "organizations/", {"name": ORG_N, "description": "Operación y autorremediación (Demo 5)"})
org = None
for _ in range(20):
    org = uno(aap, "organizations", name=ORG_N)
    if org:
        break
    time.sleep(3)
org = org["id"]
paso(f"Organización '{ORG_N}'")

# 3. Proyecto desde Gitea
proy = asegurar("projects", "Operación · remediación", {
    "organization": org, "scm_type": "git", "scm_url": f"http://192.168.122.1:3000/invoke/{REPO}.git",
    "scm_branch": "main", "scm_update_on_launch": True,
    "description": "Playbooks y políticas de remediación desde Gitea (invoke/operacion)"})["id"]
upd = aap("POST", f"projects/{proy}/update/", {})
while (st := aap("GET", f"project_updates/{upd['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
if st != "successful":
    sys.exit(f"ERROR al sincronizar el proyecto de operación: {st}")
paso("Proyecto 'Operación · remediación' sincronizado")

# 4. Plantillas (inventario y credenciales existentes de las demos anteriores)
inv = uno(aap, "inventories", name="Nodos demo")["id"]
maquina = uno(aap, "credentials", name="Nodos demo (ansible)")["id"]
jboss = uno(aap, "credentials", name="JBoss EAP · nodo-jboss")["id"]
jt = {}
for nombre, pb, desc, creds, caso in (
        ("Remediación · Reiniciar JBoss", "playbooks/reiniciar-jboss.yml",
         "Riesgo bajo: JBoss detenido → diagnóstico, reinicio y validación, sin aprobación", [maquina], "jboss-detenido"),
        ("Remediación · Limpiar logs", "playbooks/limpiar-logs.yml",
         "Riesgo bajo: logs llenos → borra lo que la política de Git permite y confirma el espacio", [maquina], "logs-llenos"),
        ("Remediación · Diagnosticar app", "playbooks/diagnosticar-app.yml",
         "Riesgo alto, paso 1: diagnóstico y propuesta (no cambia nada)", [maquina], "rollback"),
        ("Remediación · Volver a la versión validada", "playbooks/volver-version-validada.yml",
         "Riesgo alto, paso 3: solo corre si se aprobó en el workflow", [maquina, jboss], "rollback"),
        ("Remediación · Escalar sin acción", "playbooks/escalar.yml",
         "Riesgo alto: si la aprobación se rechaza o vence, no toca producción", [maquina], "rollback")):
    o = asegurar("job_templates", nombre, {"organization": org, "project": proy, "inventory": inv, "playbook": pb,
                                           "description": desc, "ask_variables_on_launch": True,
                                           "ask_labels_on_launch": True})
    ya = [c["id"] for c in aap("GET", f"job_templates/{o['id']}/credentials/")["results"]]
    for cid in creds:
        if cid not in ya:
            aap("POST", f"job_templates/{o['id']}/credentials/", {"id": cid})
    etiquetar("job_templates", o["id"], org, ["remediacion", caso])
    jt[nombre] = o["id"]
paso("Plantillas de remediación")

# 5. Workflow: diagnóstico → aprobación humana → rollback (rechazo o vencimiento → escalar)
wf = asegurar("workflow_job_templates", "Remediación · Rollback con aprobación", {
    "organization": org, "inventory": inv, "ask_variables_on_launch": True, "ask_labels_on_launch": True,
    "description": "Riesgo alto: la app responde con error → diagnóstico, aprobación en AAP y rollback validado"})["id"]
etiquetar("workflow_job_templates", wf, org, ["remediacion", "rollback"])
for n in aap("GET", f"workflow_job_templates/{wf}/workflow_nodes/?page_size=50")["results"]:
    aap("DELETE", f"workflow_job_template_nodes/{n['id']}/")
nodo = lambda d: aap("POST", f"workflow_job_templates/{wf}/workflow_nodes/", d)["id"]
n_diag = nodo({"unified_job_template": jt["Remediación · Diagnosticar app"], "identifier": "diagnostico"})
n_apr = nodo({"identifier": "aprobacion"})
aap("POST", f"workflow_job_template_nodes/{n_apr}/create_approval_template/", {
    "name": "¿Volver a la versión validada?",
    "description": "Riesgo alto: revisa el diagnóstico del paso anterior y aprueba o rechaza el rollback.",
    "timeout": APROBACION_SEG})
n_rb = nodo({"unified_job_template": jt["Remediación · Volver a la versión validada"], "identifier": "rollback"})
n_esc = nodo({"unified_job_template": jt["Remediación · Escalar sin acción"], "identifier": "escalar"})
aap("POST", f"workflow_job_template_nodes/{n_diag}/success_nodes/", {"id": n_apr})
aap("POST", f"workflow_job_template_nodes/{n_apr}/success_nodes/", {"id": n_rb})
aap("POST", f"workflow_job_template_nodes/{n_apr}/failure_nodes/", {"id": n_esc})
paso(f"Workflow 'Remediación · Rollback con aprobación' (aprobación vence en {APROBACION_SEG} s)")

# 6. El auditor también ve "Operación" (solo lectura)
u = gw("GET", "users/?username=auditor-cicd")["results"]
defs = {d["name"]: d["id"] for d in aap("GET", "role_definitions/?page_size=200")["results"]}
if u and "Organization Audit" in defs:
    aap("POST", "role_user_assignments/", {"role_definition": defs["Organization Audit"], "object_id": str(org),
                                           "user_ansible_id": u[0]["summary_fields"]["resource"]["ansible_id"]}, t=True)
    paso("auditor-cicd: solo lectura de 'Operación'")

# 7. EDA: credencial hacia AAP, proyecto y activación (se recrea para tomar el rulebook vigente)
eorg = (uno(eda, "organizations", name=ORG_N) or uno(eda, "organizations", name="Default"))["id"]
tipo = uno(eda, "credential-types", name="Red Hat Ansible Automation Platform")["id"]
datos = {"name": "AAP · lanzar remediaciones", "credential_type_id": tipo, "organization_id": eorg,
         "inputs": {"host": f"https://{IP}/api/controller/", "username": "admin", "password": CLAVE, "verify_ssl": False}}
c = uno(eda, "eda-credentials", name=datos["name"])
cred = eda("PATCH", f"eda-credentials/{c['id']}/", datos)["id"] if c else eda("POST", "eda-credentials/", datos)["id"]
p = uno(eda, "projects", name="Operación · remediación")
if p:
    eda("POST", f"projects/{p['id']}/sync/", {}, t=True)
    eproy = p["id"]
else:
    eproy = eda("POST", "projects/", {"name": "Operación · remediación", "organization_id": eorg, "verify_ssl": False,
                                      "url": f"http://192.168.122.1:3000/invoke/{REPO}.git",
                                      "description": "Rulebooks de autorremediación (Demo 5)"})["id"]
time.sleep(3)
for _ in range(60):
    st = eda("GET", f"projects/{eproy}/")["import_state"]
    if st in ("completed", "failed"):
        break
    time.sleep(3)
if st != "completed":
    sys.exit(f"ERROR al importar el proyecto EDA: {st}")
rb = None
for _ in range(10):
    rb = uno(eda, "rulebooks", project_id=eproy, name="remediacion-jboss.yml")
    if rb:
        break
    time.sleep(3)
if not rb:
    sys.exit("ERROR: el proyecto EDA no trae rulebooks/remediacion-jboss.yml")
des = eda("GET", "decision-environments/?page_size=50")["results"]
de = next((d for d in des if d["name"] == "Hub Default Decision Environment"), des[0])["id"]
a = uno(eda, "activations", name=ACT)
if a:
    eda("POST", f"activations/{a['id']}/disable/", {}, t=True)
    time.sleep(5)
    eda("DELETE", f"activations/{a['id']}/", t=True)
    for _ in range(20):
        if not uno(eda, "activations", name=ACT):
            break
        time.sleep(3)
act = eda("POST", "activations/", {
    "name": ACT, "description": "Recibe los avisos del vigía de nodo-jboss y aplica la política de riesgo",
    "is_enabled": True, "decision_environment_id": de, "rulebook_id": rb["id"], "eda_credentials": [cred],
    "organization_id": eorg, "restart_policy": "on-failure", "log_level": "info"})["id"]
for _ in range(60):
    st = eda("GET", f"activations/{act}/")["status"]
    if st in ("running", "failed", "error"):
        break
    time.sleep(5)
paso(f"Activación '{ACT}': {st}")
print("   LISTO")
