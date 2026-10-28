#!/usr/bin/python3
# FaceOps · deja AAP listo para FaceOps (idempotente, corre en la VM del stand):
#  · Usuario de servicio "faceops" en AAP y su token (queda en ~/faceops/faceops.env, 0600; no se imprime)
#  · Gitea invoke/faceops-automatizaciones con el recolector; organización y proyecto "FaceOps"
#  · Plantilla "FaceOps · Recolectar inventario" (caché de facts; inventario, credencial y límite se eligen al lanzar)
#  · "Preguntar límite al lanzar" en las plantillas que actúan sobre equipos (FaceOps las aplica por segmento)
import base64, glob, json, os, re, secrets, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, G, GITEA = "https://localhost/api/controller/v2", "https://localhost/api/gateway/v1", "http://127.0.0.1:3000/api/v1"
CTX = ssl._create_unverified_context()
ADMIN = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
ENV = os.path.expanduser("~/faceops/faceops.env")
INVENTARIOS_DE_EQUIPOS = ["Nodos demo", "Servidores de aplicación", "Drift · Servidores", "Servidores Windows", "Data center 1"]
paso = lambda t: print(f"   · {t}", flush=True)


def http(m, url, d=None, auth=ADMIN, tolerar=False):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": auth})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:500].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http(m, f"{C}/{p}", d, tolerar=t)
gw = lambda m, p, d=None, t=False, auth=ADMIN: http(m, f"{G}/{p}", d, auth=auth, tolerar=t)
gitea = lambda m, p, d=None, t=False: http(m, f"{GITEA}/{p}", d, auth="token " + SEC["gitea_token"], tolerar=t)


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


def esperar(url):
    for _ in range(100):
        st = aap("GET", url)["status"]
        if st in ("successful", "failed", "error", "canceled"):
            return st
        time.sleep(3)


# 1. Usuario de servicio y token
os.makedirs(os.path.dirname(ENV), mode=0o700, exist_ok=True)
env = dict(l.strip().split("=", 1) for l in open(ENV) if "=" in l) if os.path.exists(ENV) else {}
token_ok = env.get("FACEOPS_AAP_TOKEN") and http("GET", f"{C}/me/", auth="Bearer " + env["FACEOPS_AAP_TOKEN"], tolerar=True)
if not token_ok:
    clave = secrets.token_urlsafe(18)
    u = gw("GET", "users/?username=faceops")["results"]
    datos = {"username": "faceops", "password": clave, "first_name": "FaceOps", "last_name": "(servicio)", "is_superuser": True,
             "email": "faceops@invoke.cl"}
    u = gw("PATCH", f"users/{u[0]['id']}/", datos) if u else gw("POST", "users/", datos)
    basic = "Basic " + base64.b64encode(f"faceops:{clave}".encode()).decode()
    tok = gw("POST", "tokens/", {"description": "FaceOps · consola del Managed Service", "scope": "write"}, auth=basic)
    env.update({"FACEOPS_AAP_TOKEN": tok["token"], "FACEOPS_AAP_URL": "https://127.0.0.1"})
    fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, "".join(f"{k}={v}\n" for k, v in env.items()).encode())
    os.close(fd)
    paso("Usuario de servicio «faceops» en AAP y token nuevo (en ~/faceops/faceops.env)")
else:
    paso("Usuario de servicio «faceops» y token vigentes")

# 2. Gitea y proyecto
REPO = "faceops-automatizaciones"
if not gitea("GET", f"repos/invoke/{REPO}", t=True):
    gitea("POST", "user/repos", {"name": REPO, "private": False, "default_branch": "main", "auto_init": True,
                                 "description": "FaceOps: automatizaciones propias (recolector del inventario inteligente)"})
tmp = tempfile.mkdtemp()
url = f"http://invoke:{SEC['gitea_token']}@127.0.0.1:3000/invoke/{REPO}.git"
subprocess.run(["git", "clone", "-q", url, tmp], check=True, capture_output=True)
os.makedirs(f"{tmp}/playbooks", exist_ok=True)
shutil.copy(f"{AQUI}/recolectar.yml", f"{tmp}/playbooks/recolectar.yml")
g = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True)
g("add", "-A")
if g("status", "--porcelain").stdout.strip():
    g("-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl", "commit", "-qm", "FaceOps: recolector del inventario")
    g("push", "-q", url, "HEAD:main")
shutil.rmtree(tmp)
paso(f"Gitea: invoke/{REPO}")

if not gw("GET", "organizations/?name=FaceOps")["results"]:
    gw("POST", "organizations/", {"name": "FaceOps", "description": "Automatizaciones propias de FaceOps"})
org = None
for _ in range(20):
    org = uno("organizations", name="FaceOps")
    if org:
        break
    time.sleep(3)
proy = asegurar("projects", "FaceOps · automatizaciones", {
    "organization": org["id"], "scm_type": "git", "scm_url": f"http://192.168.122.1:3000/invoke/{REPO}.git",
    "scm_branch": "main", "scm_update_on_launch": True})["id"]
st = esperar(f"project_updates/{aap('POST', f'projects/{proy}/update/', {})['id']}/")
paso(f"Proyecto «FaceOps · automatizaciones»: {st}")

# 3. Plantilla del recolector
inv = uno("inventories", name="Nodos demo")
ee = uno("execution_environments", name="Default execution environment")
jt = asegurar("job_templates", "FaceOps · Recolectar inventario", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/recolectar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "use_fact_cache": True, "ask_inventory_on_launch": True,
    "ask_credential_on_launch": True, "ask_limit_on_launch": True, "forks": 10,
    "description": "Inventario inteligente de FaceOps: sistema, recursos, servicios, registro y parches de cada equipo"})
cred = uno("credentials", name="Nodos demo (ansible)")
ya = {c["id"] for c in aap("GET", f"job_templates/{jt['id']}/credentials/")["results"]}
if cred and cred["id"] not in ya:
    aap("POST", f"job_templates/{jt['id']}/credentials/", {"id": cred["id"]})
paso(f"Plantilla «FaceOps · Recolectar inventario» (id {jt['id']})")

# 4. Límite al lanzar en las plantillas que actúan sobre equipos
n = 0
for t in aap("GET", "job_templates/?page_size=200")["results"]:
    inv_n = (t.get("summary_fields", {}).get("inventory") or {}).get("name")
    if inv_n in INVENTARIOS_DE_EQUIPOS and not t["ask_limit_on_launch"]:
        aap("PATCH", f"job_templates/{t['id']}/", {"ask_limit_on_launch": True})
        n += 1
paso(f"«Preguntar límite al lanzar» activado en {n} plantillas más")
print("LISTO")
