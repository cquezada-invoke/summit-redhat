#!/usr/bin/python3
# Publica exportador-windows.yml en Gitea (invoke/operacion), sincroniza el proyecto de AAP y lanza
# "Monitoreo · Exportador en Windows" sobre el inventario "Servidores Windows". Idempotente. No imprime claves.
import base64, glob, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
RUTA = "playbooks/exportador-windows.yml"
JT = "Monitoreo · Exportador en Windows"


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
        sys.exit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http(m, f"https://localhost/api/controller/v2/{p}", d, {"Authorization": BASIC}, t)
gitea = lambda m, p, d=None, t=False: http(m, f"http://127.0.0.1:3000/api/v1/{p}", d, {"Authorization": "token " + SEC["gitea_token"]}, t)
uno = lambda rec, **q: (aap("GET", f"{rec}/?" + urllib.parse.urlencode(q))["results"] or [None])[0]

nuevo = open(os.path.join(os.path.dirname(AQUI), "operacion", RUTA), "rb").read()
act = gitea("GET", f"repos/invoke/operacion/contents/{RUTA}?ref=main", t=True)
if not act or base64.b64decode(act["content"]) != nuevo:
    d = {"content": base64.b64encode(nuevo).decode(), "branch": "main", "message": "Monitoreo: windows_exporter en nodo-windows",
         "author": {"name": "Cristian Quezada", "email": "cquezada@invoke.cl"}}
    gitea("PUT" if act else "POST", f"repos/invoke/operacion/contents/{RUTA}", dict(d, sha=act["sha"]) if act else d)
proy = uno("projects", name="Operación · remediación")
u = aap("POST", f"projects/{proy['id']}/update/", {})
while aap("GET", f"project_updates/{u['id']}/")["status"] not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
inv = uno("inventories", name="Servidores Windows")
cred = uno("credentials", name="Windows · ansible (WinRM)")
datos = {"organization": inv["organization"], "project": proy["id"], "inventory": inv["id"], "playbook": RUTA,
         "description": "Monitoreo por máquina: instala windows_exporter (9182, solo desde la VM anfitriona)"}
o = uno("job_templates", name=JT)
jt = (aap("PATCH", f"job_templates/{o['id']}/", datos) if o else aap("POST", "job_templates/", dict(datos, name=JT)))["id"]
if cred["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jt}/credentials/", {"id": cred["id"]})
j = aap("POST", f"job_templates/{jt}/launch/", {})
while (st := aap("GET", f"jobs/{j['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"):
    time.sleep(4)
print(f"   · AAP: '{JT}' (job {j['id']}) {st}")
if st != "successful":
    sys.exit(1)
