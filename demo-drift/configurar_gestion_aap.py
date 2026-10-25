#!/usr/bin/python3
# Demo 1 · AAP para la gestión de servidores desde el panel: inventario "Drift · Servidores" con
# grupo "gestionados", proyecto, plantillas Incorporar/Retirar y usuario panel-drift. Idempotente.
import base64, glob, json, os, re, secrets, ssl, sys, time, urllib.error, urllib.parse, urllib.request

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
SECRETOS = os.path.expanduser("~/demo-drift/cicd-secretos.txt")
AAP, GW = "https://localhost/api/controller/v2", "https://localhost/api/gateway/v1"
CTX = ssl._create_unverified_context()
AUTH = {"Authorization": "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()}


def http(m, url, d=None, tolerar=False):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers=dict({"Content-Type": "application/json"}, **AUTH))
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {url} -> {e.code}: {e.read()[:400].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http(m, f"{AAP}/{p}", d, t)
q = lambda s: urllib.parse.quote(s)
paso = lambda t: print(f"· {t}", flush=True)


def asegurar(rec, nombre, datos, filtro=""):
    o = aap("GET", f"{rec}/?name=" + q(nombre) + filtro)["results"]
    return aap("PATCH", f"{rec}/{o[0]['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


org = aap("GET", "organizations/?name=Default")["results"][0]["id"]
inv = asegurar("inventories", "Drift · Servidores", {"organization": org,
               "description": "Servidores de la demo de cumplimiento; el grupo 'gestionados' lo mantiene el panel"})["id"]
for nombre, ip in (("nodo-nginx", 11), ("nodo-so", 12), ("nodo-postgres", 13), ("nodo-tomcat", 14), ("nodo-jboss", 15)):
    if not aap("GET", f"hosts/?name={nombre}&inventory={inv}")["results"]:
        aap("POST", f"inventories/{inv}/hosts/", {"name": nombre, "variables": f"ansible_host: 192.168.122.{ip}"})
if not aap("GET", f"groups/?name=gestionados&inventory={inv}")["results"]:
    aap("POST", f"inventories/{inv}/groups/", {"name": "gestionados", "description": "Servidores bajo gestión de cumplimiento"})
paso("Inventario 'Drift · Servidores' con el grupo 'gestionados'")

base = aap("GET", "projects/?name=" + q("Despliegues de aplicaciones"))["results"][0]
cred_git = aap("GET", "credentials/?name=" + q("Gitea · lectura de playbooks") + f"&organization={org}")["results"]
proy = asegurar("projects", "Cumplimiento de servidores", {"organization": org, "scm_type": "git",
                "credential": cred_git[0]["id"] if cred_git else None,
                "scm_url": base["scm_url"], "scm_branch": base.get("scm_branch") or "", "scm_update_on_launch": True,
                "description": "Playbooks de la demo de cumplimiento (carpeta drift/)"})["id"]
upd = aap("POST", f"projects/{proy}/update/", {})
while aap("GET", f"project_updates/{upd['id']}/")["status"] not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
paso("Proyecto 'Cumplimiento de servidores' sincronizado")

maq = aap("GET", "credentials/?name=" + q("Servidores de aplicación (ansible)"))["results"][0]["id"]
jts = {}
for nombre, libro, desc in (("Drift · Incorporar servidor", "drift/incorporar.yml", "Pone un servidor bajo gestión: nivel de control y accesos"),
                            ("Drift · Retirar servidor", "drift/retirar.yml", "Saca un servidor de la gestión y quita accesos de visitantes")):
    jt = asegurar("job_templates", nombre, {"organization": org, "project": proy, "inventory": inv, "playbook": libro,
                  "ask_variables_on_launch": True, "ask_limit_on_launch": True, "ask_labels_on_launch": True,
                  "allow_simultaneous": True, "description": desc})["id"]
    if maq not in [c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]]:
        aap("POST", f"job_templates/{jt}/credentials/", {"id": maq})
    if "cumplimiento" not in [l["name"] for l in aap("GET", f"job_templates/{jt}/labels/")["results"]]:
        aap("POST", f"job_templates/{jt}/labels/", {"name": "cumplimiento", "organization": org})
    jts[nombre] = jt
paso("Plantillas 'Drift · Incorporar servidor' y 'Drift · Retirar servidor'")

clave = next((l.split("=", 1)[1].strip() for l in open(SECRETOS) if l.startswith("panel_aap_clave=")), None) if os.path.exists(SECRETOS) else None
if not clave:
    clave = secrets.token_urlsafe(18)
    with open(SECRETOS, "a") as f:
        f.write(f"panel_aap_clave={clave}\n")
u = http("GET", f"{GW}/users/?username=panel-drift")["results"]
if u:
    u = http("PATCH", f"{GW}/users/{u[0]['id']}/", {"password": clave})
else:
    u = http("POST", f"{GW}/users/", {"username": "panel-drift", "password": clave, "first_name": "Panel",
                                       "last_name": "de cumplimiento"})
aid = u["summary_fields"]["resource"]["ansible_id"]
defs = {d["name"]: d["id"] for d in aap("GET", "role_definitions/?page_size=200")["results"]}
for jt in jts.values():
    aap("POST", "role_user_assignments/", {"role_definition": defs["JobTemplate Execute"], "object_id": str(jt),
                                           "user_ansible_id": aid}, t=True)
rol_inv = next((n for n in ("Inventory Admin", "Inventory Update") if n in defs), None)
aap("POST", "role_user_assignments/", {"role_definition": defs[rol_inv], "object_id": str(inv), "user_ansible_id": aid}, t=True)
paso(f"Usuario 'panel-drift' (ejecuta las plantillas y administra el grupo; rol {rol_inv})")
print(json.dumps({"inventario": inv, "plantillas": jts}))
print("LISTO")
