#!/usr/bin/python3
# JBoss como código (dos nodos y manifiesto por ambiente). Idempotente. No imprime claves.
# 1) Gitea: repos invoke/jboss-config (configuración + playbooks) e invoke/ambientes (produccion.yml), webhook a EDA :5006.
# 2) AAP (org "Despliegues CI/CD"): credencial de escritura en Gitea, proyecto "JBoss · configuración" y plantillas
#    "JBoss · Aplicar configuración", "JBoss · Verificar configuración" (drift, cada 5 min) y "JBoss · Restablecer a versión".
#    La plantilla "JBoss · Desplegar app" también actualiza el manifiesto.
# 3) EDA: activación "JBoss · Configuración desde Git".
import base64, glob, json, os, re, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
IP = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
paso = lambda t: print(f"   · {t}", flush=True)


def http(url, m="GET", d=None, cab=None, tolerar=False):
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


aap = lambda m, p, d=None, t=False: http(f"https://localhost/api/controller/v2/{p}", m, d, {"Authorization": BASIC}, t)
eda = lambda m, p, d=None, t=False: http(f"https://localhost/api/eda/v1/{p}", m, d, {"Authorization": BASIC}, t)
gitea = lambda m, p, d=None, t=False: http(f"http://127.0.0.1:3000/api/v1/{p}", m, d, {"Authorization": "token " + SEC["gitea_token"]}, t)


def uno(f, rec, **q):
    r = f("GET", f"{rec}/?" + urllib.parse.urlencode(q))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(aap, rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


def sembrar(repo, carpeta, desc):
    if not gitea("GET", f"repos/invoke/{repo}", t=True):
        gitea("POST", "user/repos", {"name": repo, "private": False, "default_branch": "main", "description": desc})
    url = f"http://invoke:{SEC['gitea_token']}@127.0.0.1:3000/invoke/{repo}.git"
    tmp = tempfile.mkdtemp()
    run = lambda *c: subprocess.run(list(c), cwd=tmp, capture_output=True, text=True)
    if run("git", "clone", "-q", url, ".").returncode or not os.path.exists(os.path.join(tmp, ".git")):
        run("git", "init", "-q", "-b", "main")
    subprocess.run(["cp", "-r", f"{carpeta}/.", tmp])
    run("git", "add", "-A")
    if run("git", "diff", "--cached", "--quiet").returncode:
        run("git", "-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl", "commit", "-qm",
            f"{repo}: actualización desde summit-redhat")
        if run("git", "push", "-q", url, "HEAD:main").returncode:
            sys.exit(f"ERROR al publicar {repo}")
    sha = run("git", "rev-parse", "HEAD").stdout.strip()
    subprocess.run(["rm", "-rf", tmp])
    for u in ("cquezada", "jperez"):
        gitea("PUT", f"repos/invoke/{repo}/collaborators/{u}", {"permission": "write"}, t=True)
    return sha


# 1. Gitea
sha_cfg = sembrar("jboss-config", os.path.join(AQUI, "jboss-config"),
                  "Configuración de JBoss EAP como código: fuente única para todos los JBoss")
paso(f"Gitea: invoke/jboss-config @ {sha_cfg[:12]}")
man = gitea("GET", "repos/invoke/ambientes/contents/produccion.yml?ref=main", t=True)
if not man:
    est = gitea("GET", "repos/invoke/estado-servidores/branches/master", t=True)
    sha_est = est["commit"]["id"][:12] if est else "-"
    salud = urllib.request.urlopen("http://192.168.122.15:8080/app-jboss/salud", timeout=10).read().decode().split()
    version = salud[1] if len(salud) > 1 else "1.0.0"
    texto = f"""# Manifiesto del ambiente produccion: la combinación exacta que está en producción.
# Lo actualiza AAP en cada despliegue o cambio de configuración. Para volver a un estado anterior:
# plantilla "JBoss · Restablecer a versión" con el commit de este archivo.
ambiente: produccion
aplicacion:
  nombre: app-jboss
  version: '{version}'
  commit: ''
  artefacto: registro de paquetes de Gitea (invoke/generic/app-jboss)
jboss_config:
  repo: invoke/jboss-config
  ref: {sha_cfg[:12]}
servidores:
  nodo-jboss:
    estado_servidores: {sha_est}
  nodo-jboss2:
    estado_servidores: {sha_est}
"""
    os.makedirs("/tmp/ambientes-semilla", exist_ok=True)
    subprocess.run(["cp", "-r", os.path.join(AQUI, "ambientes") + "/.", "/tmp/ambientes-semilla"])
    open("/tmp/ambientes-semilla/produccion.yml", "w").write(texto)
    sembrar("ambientes", "/tmp/ambientes-semilla", "Manifiesto de cada ambiente: qué versión exacta de cada pieza está en producción")
    subprocess.run(["rm", "-rf", "/tmp/ambientes-semilla"])
else:
    sembrar("ambientes", os.path.join(AQUI, "ambientes"), "Manifiesto de cada ambiente")
paso("Gitea: invoke/ambientes (produccion.yml)")
hooks = gitea("GET", "repos/invoke/jboss-config/hooks")
if not any("5006" in h["config"]["url"] for h in hooks):
    gitea("POST", "repos/invoke/jboss-config/hooks", {"type": "gitea", "active": True, "events": ["push"],
          "branch_filter": "main", "config": {"url": "http://192.168.122.1:5006/endpoint", "content_type": "json"}})
paso("Gitea: webhook de jboss-config → EDA :5006")

# 2. AAP
org = uno(aap, "organizations", name="Despliegues CI/CD")["id"]
inv = uno(aap, "inventories", name="Servidores de aplicación")["id"]
tipo_g = uno(aap, "credential_types", name="Gitea · API (token)")["id"]
cred_g = asegurar("credentials", "Gitea · escritura jboss-config y ambientes", {
    "organization": org, "credential_type": tipo_g, "description": "Commits del manifiesto (repo ambientes)",
    "inputs": {"url": "http://192.168.122.1:3000", "token": SEC["gitea_token"]}})["id"]
proy = asegurar("projects", "JBoss · configuración", {
    "organization": org, "scm_type": "git", "scm_url": "http://192.168.122.1:3000/invoke/jboss-config.git",
    "scm_branch": "main", "scm_update_on_launch": True, "scm_update_cache_timeout": 0,
    "description": "jboss-config: configuración de JBoss como código"})["id"]
u = aap("POST", f"projects/{proy}/update/", {})
while (st := aap("GET", f"project_updates/{u['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
paso(f"AAP: proyecto 'JBoss · configuración' {st}")
creds = [uno(aap, "credentials", name=n)["id"] for n in ("JBoss EAP · nodo-jboss", "Servidores de aplicación (ansible)")] + [cred_g]
jts = {}
for nombre, pb, extra, desc in (
        ("JBoss · Aplicar configuración", "playbooks/aplicar.yml", "modo: aplicar", "Commit en jboss-config → todos los JBoss, uno a la vez"),
        ("JBoss · Verificar configuración", "playbooks/aplicar.yml", "modo: drift", "Drift: cambios manuales en JBoss → se corrigen o registran según jboss-config"),
        ("JBoss · Restablecer a versión", "playbooks/restablecer-version.yml", "ambiente: produccion", "Deja todos los JBoss como en un commit del manifiesto (app + configuración)")):
    o = asegurar("job_templates", nombre, {"organization": org, "project": proy, "inventory": inv, "playbook": pb,
                                            "extra_vars": extra, "ask_variables_on_launch": True, "description": desc})
    ya = {c["id"] for c in aap("GET", f"job_templates/{o['id']}/credentials/")["results"]}
    for c in creds:
        if c not in ya:
            aap("POST", f"job_templates/{o['id']}/credentials/", {"id": c})
    jts[nombre] = o["id"]
aap("POST", f"job_templates/{jts['JBoss · Restablecer a versión']}/survey_spec/", {"name": "Restablecer", "description": "", "spec": [
    {"question_name": "Ambiente", "variable": "ambiente", "type": "multiplechoice", "choices": ["produccion"], "default": "produccion", "required": True},
    {"question_name": "Commit o tag del manifiesto", "question_description": "Del repo ambientes (vacío = el vigente)",
     "variable": "manifiesto_ref", "type": "text", "required": False, "default": "", "min": 0, "max": 64}]})
aap("PATCH", f"job_templates/{jts['JBoss · Restablecer a versión']}/", {"survey_enabled": True})
d = jts["JBoss · Verificar configuración"]
if not aap("GET", f"job_templates/{d}/schedules/")["results"]:
    aap("POST", f"job_templates/{d}/schedules/", {"name": "Drift de JBoss cada 5 minutos",
        "rrule": "DTSTART;TZID=America/Santiago:20261026T000000 RRULE:FREQ=MINUTELY;INTERVAL=5"})
dep = uno(aap, "job_templates", name="JBoss · Desplegar app")["id"]
if cred_g not in {c["id"] for c in aap("GET", f"job_templates/{dep}/credentials/")["results"]}:
    aap("POST", f"job_templates/{dep}/credentials/", {"id": cred_g})
paso("AAP: plantillas JBoss · Aplicar / Verificar (cada 5 min) / Restablecer, y Desplegar app actualiza el manifiesto")

# 3. EDA
eorg = uno(eda, "organizations", name="Default")["id"]
tipo = uno(eda, "credential-types", name="Red Hat Ansible Automation Platform")["id"]
datos = {"name": "AAP · lanzar plantillas de JBoss", "credential_type_id": tipo, "organization_id": eorg,
         "inputs": {"host": f"https://{IP}/api/controller/", "username": "admin", "password": CLAVE, "verify_ssl": False}}
c = uno(eda, "eda-credentials", name=datos["name"])
ecred = eda("PATCH", f"eda-credentials/{c['id']}/", datos)["id"] if c else eda("POST", "eda-credentials/", datos)["id"]
p = uno(eda, "projects", name="JBoss · configuración")
if p:
    eda("POST", f"projects/{p['id']}/sync/", {}, True)
    eproy = p["id"]
else:
    eproy = eda("POST", "projects/", {"name": "JBoss · configuración", "organization_id": eorg, "verify_ssl": False,
                                      "url": "http://192.168.122.1:3000/invoke/jboss-config.git"})["id"]
rb = None
for _ in range(40):
    time.sleep(3)
    rb = uno(eda, "rulebooks", project_id=eproy, name="configuracion.yml")
    if rb and eda("GET", f"projects/{eproy}/")["import_state"] in ("completed", "failed"):
        break
if not rb:
    sys.exit("ERROR: el proyecto EDA no trae rulebooks/configuracion.yml")
ACT = "JBoss · Configuración desde Git"
des = eda("GET", "decision-environments/?page_size=50")["results"]
de = next((x for x in des if x["name"] == "Hub Default Decision Environment"), des[0])["id"]
a = uno(eda, "activations", name=ACT)
if a:
    eda("POST", f"activations/{a['id']}/disable/", {}, True); time.sleep(5)
    eda("DELETE", f"activations/{a['id']}/", t=True)
    for _ in range(20):
        if not uno(eda, "activations", name=ACT):
            break
        time.sleep(3)
act = eda("POST", "activations/", {"name": ACT, "description": "Push a main en jboss-config → JBoss · Aplicar configuración",
                                   "is_enabled": True, "decision_environment_id": de, "rulebook_id": rb["id"],
                                   "eda_credentials": [ecred], "organization_id": eorg, "restart_policy": "on-failure", "log_level": "info"})["id"]
for _ in range(60):
    st = eda("GET", f"activations/{act}/")["status"]
    if st in ("running", "failed", "error"):
        break
    time.sleep(5)
paso(f"EDA: activación '{ACT}' {st} (webhook :5006)")
print("   LISTO")
