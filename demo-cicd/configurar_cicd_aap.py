#!/usr/bin/python3
# AAP para el CD de JBoss: tipo y credencial de JBoss, plantillas, preparación del servidor
# y usuario técnico tekton-ci con permiso solo para desplegar. Su token queda como secreto en k3s.
import base64, glob, json, os, re, secrets, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
BASE = "https://localhost"
CTX = ssl._create_unverified_context()
C, G = "/api/controller/v2", "/api/gateway/v1"


def api(metodo, ruta, datos=None, usuario="admin", clave=CLAVE, tolerar=False):
    auth = "Basic " + base64.b64encode(f"{usuario}:{clave}".encode()).decode()
    req = urllib.request.Request(BASE + ruta, method=metodo,
                                 data=json.dumps(datos).encode() if datos is not None else None,
                                 headers={"Authorization": auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            cuerpo = r.read()
            return json.loads(cuerpo) if cuerpo else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {metodo} {ruta} -> {e.code}: {e.read()[:600].decode(errors='replace')}")


def buscar(rec, **f):
    r = api("GET", f"{C}/{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = buscar(rec, name=nombre)
    return api("PATCH", f"{C}/{rec}/{o['id']}/", datos) if o else api("POST", f"{C}/{rec}/", dict(datos, name=nombre))


def esperar(job_id):
    while True:
        j = api("GET", f"{C}/jobs/{job_id}/")
        if j["status"] in ("successful", "failed", "error", "canceled"):
            return j["status"]
        time.sleep(4)


paso = lambda t: print(f"· {t}", flush=True)
org = buscar("organizations", name="Default")["id"]
inv = buscar("inventories", name="Nodos demo")["id"]
proy = buscar("projects", name="Demo Summit")["id"]
maquina = buscar("credentials", name="Nodos demo (ansible)")["id"]

upd = api("POST", f"{C}/projects/{proy}/update/", {})
while api("GET", f"{C}/project_updates/{upd['id']}/")["status"] not in ("successful", "failed", "error"):
    time.sleep(3)
paso("Proyecto sincronizado")

tipo = asegurar("credential_types", "JBoss EAP (administración)", {
    "kind": "cloud", "description": "Usuario de administración de JBoss EAP; se entrega al playbook sin mostrarse",
    "inputs": {"fields": [{"id": "usuario", "label": "Usuario", "type": "string"},
                          {"id": "clave", "label": "Clave", "type": "string", "secret": True}],
               "required": ["usuario", "clave"]},
    "injectors": {"extra_vars": {"jboss_usuario": "{{ usuario }}", "jboss_clave": "{{ clave }}"}}})["id"]
jboss = asegurar("credentials", "JBoss EAP · nodo-jboss", {
    "organization": org, "credential_type": tipo,
    "inputs": {"usuario": SEC["jboss_usuario"], "clave": SEC["jboss_clave"]}})["id"]
paso("Credencial de JBoss (cifrada en AAP)")

jt = {}
for nombre, pb, preguntar in (("JBoss · Preparar servidor", "jboss/preparar.yml", False),
                              ("JBoss · Desplegar app", "jboss/desplegar.yml", True)):
    o = asegurar("job_templates", nombre, {"inventory": inv, "project": proy, "playbook": pb, "organization": org,
                                           "ask_variables_on_launch": preguntar})
    jt[nombre] = o["id"]
    actuales = [c["id"] for c in api("GET", f"{C}/job_templates/{o['id']}/credentials/")["results"]]
    for cid in (maquina, jboss):
        if cid not in actuales:
            api("POST", f"{C}/job_templates/{o['id']}/credentials/", {"id": cid})
paso("Plantillas de JBoss")

job = api("POST", f"{C}/job_templates/{jt['JBoss · Preparar servidor']}/launch/", {})
st = esperar(job["id"])
if st != "successful":
    sys.exit(f"ERROR preparando JBoss (job {job['id']}): {st}")
paso(f"JBoss preparado (job {job['id']})")

u = api("GET", f"{G}/users/?username=tekton-ci")["results"]
clave_ci = SEC.get("tekton_clave")
if not u:
    clave_ci = secrets.token_hex(12)
    u = api("POST", f"{G}/users/", {"username": "tekton-ci", "password": clave_ci,
                                     "first_name": "Pipeline", "last_name": "Tekton"})
    with open(os.path.expanduser("~/demo-drift/cicd-secretos.txt"), "a") as f:
        f.write(f"tekton_clave={clave_ci}\n")
else:
    u = u[0]
uc = None
for _ in range(10):
    uc = buscar("users", username="tekton-ci")
    if uc:
        break
    time.sleep(3)
defs = {d["name"]: d["id"] for d in api("GET", f"{C}/role_definitions/?page_size=200")["results"]}
datos = {"role_definition": defs["JobTemplate Execute"], "object_id": str(jt["JBoss · Desplegar app"])}
datos.update({"user": uc["id"]} if uc else {"user_ansible_id": u["summary_fields"]["resource"]["ansible_id"]})
api("POST", f"{C}/role_user_assignments/", datos, tolerar=True)
paso("Usuario tekton-ci solo con permiso para desplegar")

tok = api("POST", f"{G}/tokens/", {"description": "Pipeline Tekton app-jboss", "scope": "write"},
          usuario="tekton-ci", clave=clave_ci)["token"]
yaml = subprocess.run(["kubectl", "create", "secret", "generic", "aap-token", f"--from-literal=token={tok}",
                       "--dry-run=client", "-o", "yaml"], capture_output=True, text=True, check=True,
                      env=dict(os.environ, KUBECONFIG="/etc/rancher/k3s/k3s.yaml")).stdout
subprocess.run(["kubectl", "apply", "-f", "-"], input=yaml, text=True, check=True, capture_output=True,
               env=dict(os.environ, KUBECONFIG="/etc/rancher/k3s/k3s.yaml"))
paso("Token del pipeline guardado como secreto en Tekton")
print("LISTO")
