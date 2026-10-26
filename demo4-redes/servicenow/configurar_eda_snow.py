#!/usr/bin/python3
# Demo 4 · ServiceNow → Event-Driven Ansible → AAP → el mismo ticket.
# 1) Gitea (invoke/red-laboratorio): playbook "Red · Solicitar acceso" (atiende tickets), tareas/snow_ritm.yml y el rulebook.
# 2) AAP: sincroniza el proyecto "Red · laboratorio".
# 3) EDA: activación "ServiceNow · Solicitudes de red" (webhook :5002) que lanza "Red · Solicitar acceso".
# 4) Vigía de ServiceNow como servicio (vigia-servicenow.service): entrega cada ticket nuevo a EDA.
# Idempotente. No imprime claves.
import base64, glob, json, os, re, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
REPO, ACT = "red-laboratorio", "ServiceNow · Solicitudes de red"
paso = lambda t: print(f"   · {t}", flush=True)


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


aap = lambda m, p, d=None, t=False: http(m, f"https://localhost/api/controller/v2/{p}", d, {"Authorization": BASIC}, t)
eda = lambda m, p, d=None, t=False: http(m, f"https://localhost/api/eda/v1/{p}", d, {"Authorization": BASIC}, t)
gitea = lambda m, p, d=None, t=False: http(m, f"http://127.0.0.1:3000/api/v1/{p}", d, {"Authorization": "token " + SEC["gitea_token"]}, t)


def uno(f, rec, **q):
    r = f("GET", f"{rec}/?" + urllib.parse.urlencode(q))["results"]
    return r[0] if r else None


# 1. Gitea
for ruta, msg in (("playbooks/solicitar-acceso.yml", "Solicitar acceso: atiende y cierra el ticket del usuario en ServiceNow"),
                  ("playbooks/tareas/snow_ritm.yml", "Actualización y cierre del ticket (RITM) de ServiceNow"),
                  ("rulebooks/solicitudes-servicenow.yml", "EDA: tickets de ServiceNow lanzan 'Red · Solicitar acceso'")):
    nuevo = open(os.path.join(RAIZ, REPO, ruta), "rb").read()
    act = gitea("GET", f"repos/invoke/{REPO}/contents/{ruta}?ref=main", t=True)
    if not act or base64.b64decode(act["content"]) != nuevo:
        datos = {"content": base64.b64encode(nuevo).decode(), "branch": "main", "message": msg,
                 "author": {"name": "Cristian Quezada", "email": "cquezada@invoke.cl"}}
        if act:
            gitea("PUT", f"repos/invoke/{REPO}/contents/{ruta}", dict(datos, sha=act["sha"]))
        else:
            gitea("POST", f"repos/invoke/{REPO}/contents/{ruta}", datos)
    paso(f"Gitea: {REPO}/{ruta} al día")

# 2. AAP: proyecto al día
proy = uno(aap, "projects", name="Red · laboratorio")["id"]
u = aap("POST", f"projects/{proy}/update/", {})
while (st := aap("GET", f"project_updates/{u['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
paso(f"AAP: proyecto 'Red · laboratorio' {st}")

# 3. EDA
org = (uno(eda, "organizations", name="Redes") or uno(eda, "organizations", name="Default"))["id"]
cred = uno(eda, "eda-credentials", name="AAP · lanzar plantillas de red")["id"]
p = uno(eda, "projects", name="Red · laboratorio")
eda("POST", f"projects/{p['id']}/sync/", {}, t=True)
for _ in range(60):
    if eda("GET", f"projects/{p['id']}/")["import_state"] in ("completed", "failed"):
        break
    time.sleep(3)
rb = None
for _ in range(20):
    rb = uno(eda, "rulebooks", project_id=p["id"], name="solicitudes-servicenow.yml")
    if rb:
        break
    time.sleep(3)
if not rb:
    sys.exit("ERROR: el proyecto EDA no trae rulebooks/solicitudes-servicenow.yml")
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
    "name": ACT, "description": "Cada ticket nuevo de 'Solicitar acceso de red' en ServiceNow lanza 'Red · Solicitar acceso'",
    "is_enabled": True, "decision_environment_id": de, "rulebook_id": rb["id"], "eda_credentials": [cred],
    "organization_id": org, "restart_policy": "on-failure", "log_level": "info"})["id"]
for _ in range(60):
    st = eda("GET", f"activations/{act}/")["status"]
    if st in ("running", "failed", "error"):
        break
    time.sleep(5)
paso(f"EDA: activación '{ACT}' {st}")

# 4. Vigía de ServiceNow como servicio
subprocess.run(["sudo", "cp", os.path.join(AQUI, "vigia-servicenow.service"), "/etc/systemd/system/"], check=True)
subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
subprocess.run(["sudo", "systemctl", "enable", "--now", "vigia-servicenow.service"], check=True)
subprocess.run(["sudo", "systemctl", "restart", "vigia-servicenow.service"], check=True)
paso("Vigía de ServiceNow: vigia-servicenow.service activo")
print("   LISTO")
