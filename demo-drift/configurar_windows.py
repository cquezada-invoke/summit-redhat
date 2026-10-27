#!/usr/bin/python3
# Demo 1 en Windows · plantilla de AAP "Drift · Windows" e incorporación de nodo-windows al panel. Idempotente.
#  - AAP: plantilla "Drift · Windows" (proyecto "Cumplimiento de servidores", inventario "Servidores Windows",
#    credenciales "Windows · ansible (WinRM)" y "Gitea · registro de estado") y permiso de ejecución para panel-drift.
#  - Panel: incorpora nodo-windows en "Vigilar y corregir" (instala el vigía, crea la base inicial en master si no existe
#    y toma la foto inicial en main).
# Uso en la VM:  python3 ~/demo-drift/configurar_windows.py [corregir|registrar|alertar]
# Nunca imprime claves ni tokens.
import base64, glob, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE_AAP = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
AAP, GW = "https://localhost/api/controller/v2", "https://localhost/api/gateway/v1"
PANEL = "http://127.0.0.1:8088"
CTX = ssl._create_unverified_context()
AUTH = "Basic " + base64.b64encode(f"admin:{CLAVE_AAP}".encode()).decode()
paso = lambda t: print(f"· {t}", flush=True)
q = urllib.parse.quote
NIVEL = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("corregir", "registrar", "alertar") else "corregir"


def http(m, url, d=None, auth=AUTH, tolerar=()):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": auth} if auth else {})})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if e.code in tolerar:
            return None
        sys.exit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


aap = lambda m, p, d=None, t=(): http(m, f"{AAP}/{p}", d, tolerar=t)
uno = lambda rec, nombre: aap("GET", f"{rec}/?name=" + q(nombre))["results"][0]["id"]

# ---------------- AAP ----------------
proy = uno("projects", "Cumplimiento de servidores")  # invoke/automatizacion (drift/windows.yml)
inv = uno("inventories", "Servidores Windows")
win = uno("credentials", "Windows · ansible (WinRM)")
git = uno("credentials", "Gitea · registro de estado")
org = aap("GET", f"projects/{proy}/")["organization"]
upd = aap("POST", f"projects/{proy}/update/", {})
while aap("GET", f"project_updates/{upd['id']}/")["status"] not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
datos = {"organization": org, "project": proy, "inventory": inv, "playbook": "drift/windows.yml",
         "ask_variables_on_launch": True, "ask_limit_on_launch": True, "allow_simultaneous": False, "diff_mode": True,
         "description": "Demo 1 en Windows: vigía, base aprobada en Git (servicios críticos y Administradores), corregir o registrar"}
o = aap("GET", "job_templates/?name=" + q("Drift · Windows"))["results"]
jt = (aap("PATCH", f"job_templates/{o[0]['id']}/", datos) if o else aap("POST", "job_templates/", dict(datos, name="Drift · Windows")))["id"]
actuales = [c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]]
for c in (win, git):
    if c not in actuales:
        aap("POST", f"job_templates/{jt}/credentials/", {"id": c})
if "cumplimiento" not in [l["name"] for l in aap("GET", f"job_templates/{jt}/labels/")["results"]]:
    aap("POST", f"job_templates/{jt}/labels/", {"name": "cumplimiento", "organization": org}, t=(400,))
paso(f"AAP: plantilla 'Drift · Windows' (id {jt})")

u = http("GET", f"{GW}/users/?username=panel-drift")["results"]
aid = u[0]["summary_fields"]["resource"]["ansible_id"]
defs = {d["name"]: d["id"] for d in aap("GET", "role_definitions/?page_size=200")["results"]}
aap("POST", "role_user_assignments/", {"role_definition": defs["JobTemplate Execute"], "object_id": str(jt),
                                       "user_ansible_id": aid}, t=(400,))
paso("AAP: panel-drift puede lanzar 'Drift · Windows'")

# ---------------- Panel ----------------
g = http("GET", f"{PANEL}/api/gestion", auth=None)["gestion"].get("nodo-windows", {})
if g.get("estado") in ("activo", "incorporando"):
    paso(f"Panel: nodo-windows ya está bajo gestión ({g.get('nivel')})")
else:
    http("POST", f"{PANEL}/api/gestion/agregar", {"servidor": "nodo-windows", "nivel": NIVEL, "visitantes": False}, auth=None)
    for _ in range(120):
        time.sleep(5)
        g = http("GET", f"{PANEL}/api/gestion", auth=None)["gestion"].get("nodo-windows", {})
        if g.get("estado") != "incorporando":
            break
    if g.get("estado") != "activo":
        sys.exit(f"ERROR al incorporar nodo-windows: {g.get('mensaje') or g.get('estado')} (job {g.get('job')})")
    paso(f"Panel: nodo-windows incorporado en '{NIVEL}' (job {g.get('job')})")
print("LISTO")
