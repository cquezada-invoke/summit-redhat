#!/usr/bin/python3
# Red · Configurar VLAN: sube el playbook (y su registro en Git) a Gitea invoke/red-laboratorio, sincroniza el proyecto
# y deja la plantilla en AAP (organización «Redes», inventario «Red · laboratorio») con las credenciales del firewall
# VyOS y de escritura en Gitea. FaceOps la toma en su catálogo (formulario y compatibilidad: firewalls y routers
# VyOS o FortiGate). Idempotente. No imprime claves.
import base64, glob, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, GITEA = "https://localhost/api/controller/v2", "http://127.0.0.1:3000/api/v1"
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
REPO, JT = "red-laboratorio", "Red · Configurar VLAN"
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


aap = lambda m, p, d=None, t=False: http(m, f"{C}/{p}", d, {"Authorization": BASIC}, t)
gitea = lambda m, p, d=None, t=False: http(m, f"{GITEA}/{p}", d, {"Authorization": "token " + SEC["gitea_token"]}, t)


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


for ruta, msg in (("playbooks/configurar-vlan.yml", "Red · Configurar VLAN: VyOS y FortiGate, verificación y registro en Git"),
                  ("playbooks/tareas/registro_vlans.yml", "Registro de VLAN configuradas por AAP")):
    nuevo = open(os.path.join(AQUI, REPO, ruta), "rb").read()
    act = gitea("GET", f"repos/invoke/{REPO}/contents/{ruta}?ref=main", t=True)
    if not act or base64.b64decode(act["content"]) != nuevo:
        datos = {"content": base64.b64encode(nuevo).decode(), "branch": "main", "message": msg,
                 "author": {"name": "Cristian Quezada", "email": "cquezada@invoke.cl"}}
        gitea("PUT" if act else "POST", f"repos/invoke/{REPO}/contents/{ruta}", dict(datos, sha=act["sha"]) if act else datos)
    paso(f"Gitea: {REPO}/{ruta} al día")

base = uno("job_templates", name="Red · Solicitar acceso")
proy = base["project"]
upd = aap("POST", f"projects/{proy}/update/", {})
while (st := aap("GET", f"project_updates/{upd['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
paso(f"Proyecto sincronizado ({st})")
o = uno("job_templates", name=JT)
datos = {"organization": base["organization"], "project": proy, "inventory": base["inventory"], "playbook": "playbooks/configurar-vlan.yml",
         "ask_variables_on_launch": True, "ask_limit_on_launch": True,
         "description": "Crea, consulta o retira una VLAN (subinterfaz 802.1Q) en un firewall o router VyOS o FortiGate, la verifica y la registra en Git"}
jt = aap("PATCH", f"job_templates/{o['id']}/", datos) if o else aap("POST", "job_templates/", dict(datos, name=JT))
ya = {c["id"] for c in aap("GET", f"job_templates/{jt['id']}/credentials/")["results"]}
for n in ("Red · firewall VyOS", "Gitea · escritura red-laboratorio"):
    c = uno("credentials", name=n)
    if c and c["id"] not in ya:
        aap("POST", f"job_templates/{jt['id']}/credentials/", {"id": c["id"]})
paso(f"AAP: plantilla «{JT}» (id {jt['id']})")
print("LISTO")
