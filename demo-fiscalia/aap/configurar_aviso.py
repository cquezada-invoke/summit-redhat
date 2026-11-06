#!/usr/bin/python3
# Fiscalía (demo) · aviso de complemento por correo a través de AAP. Idempotente.
#  - Notificador «Correo · complemento Fiscalía»: copia del notificador Gmail ya configurado por Cristian
#    (la contraseña de aplicación queda cifrada dentro de AAP; este script nunca la ve).
#  - Plantilla «Fiscalía · Aviso de complemento» (playbooks/fiscalia-aviso-complemento.yml) con ese notificador al terminar bien.
#  - Usuario técnico fiscalia-app: solo ejecutar esa plantilla y editar ese notificador. Su token queda como secreto en OpenShift.
#  - Firewall: solo el nodo OpenShift (192.168.122.50) puede llegar al 443 del anfitrión (AAP).
import base64, glob, json, os, re, secrets, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
SECF = os.path.expanduser("~/demo-drift/cicd-secretos.txt")
SEC = dict(l.strip().split("=", 1) for l in open(SECF) if "=" in l)
CTX = ssl._create_unverified_context()
BASE, C, G = "https://localhost", "/api/controller/v2", "/api/gateway/v1"
OC = os.path.expanduser("~/openshift/sno/bin/oc")
KENV = dict(os.environ, KUBECONFIG=os.path.expanduser("~/openshift/sno/instalar/auth/kubeconfig"))
CORREO = "cquezada@invoke.cl"
NOTIF, JT, USUARIO = "Correo · complemento Fiscalía", "Fiscalía · Aviso de complemento", "fiscalia-app"
paso = lambda t: print(f"· {t}", flush=True)


def api(m, p, d=None, usuario="admin", clave=CLAVE, tolerar=False):
    auth = "Basic " + base64.b64encode(f"{usuario}:{clave}".encode()).decode()
    req = urllib.request.Request(BASE + p, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": auth})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:400].decode(errors='replace')}")


def uno(tipo, **f):
    r = api("GET", f"{C}/{tipo}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


# 1. Notificador (copia del de Gmail, conserva la contraseña cifrada)
n = uno("notification_templates", name=NOTIF)
if not n:
    base = uno("notification_templates", name="Correo · aprobaciones de red")
    if not base:
        sys.exit("ERROR: no existe el notificador de correo base")
    n = api("POST", f"{C}/notification_templates/{base['id']}/copy/", {"name": NOTIF})
cfg = dict(api("GET", f"{C}/notification_templates/{n['id']}/")["notification_configuration"], recipients=[CORREO], sender=CORREO)
api("PATCH", f"{C}/notification_templates/{n['id']}/", {"notification_configuration": cfg,
    "description": "Aviso a la persona denunciante con lo que falta y el enlace al formulario (demo Fiscalía)"})
paso(f"Notificador «{NOTIF}» (id {n['id']}) → {CORREO}")

# 2. Plantilla
proy = uno("projects", name="FaceOps · automatizaciones")
org = proy["organization"]
upd = api("POST", f"{C}/projects/{proy['id']}/update/", {})
while (st := api("GET", f"{C}/project_updates/{upd['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
if st != "successful":
    sys.exit(f"ERROR al sincronizar el proyecto: {st}")
inv = uno("inventories", name="Fiscalía · avisos") or api("POST", f"{C}/inventories/", {
    "name": "Fiscalía · avisos", "organization": org, "description": "Sin equipos: los avisos corren en el nodo de ejecución"})
ee = uno("execution_environments", name="Default execution environment")
datos = {"name": JT, "organization": org, "project": proy["id"], "playbook": "playbooks/fiscalia-aviso-complemento.yml",
         "inventory": inv["id"], "execution_environment": ee["id"], "ask_variables_on_launch": True, "allow_simultaneous": False,
         "description": "Valida el pedido de complemento; al terminar bien, el notificador envía el correo"}
jt = uno("job_templates", name=JT)
jt = api("PATCH", f"{C}/job_templates/{jt['id']}/", datos) if jt else api("POST", f"{C}/job_templates/", datos)
if n["id"] not in {x["id"] for x in api("GET", f"{C}/job_templates/{jt['id']}/notification_templates_success/")["results"]}:
    api("POST", f"{C}/job_templates/{jt['id']}/notification_templates_success/", {"id": n["id"]})
paso(f"Plantilla «{JT}» (id {jt['id']}) con el notificador al terminar bien")

# 3. Usuario técnico con permisos mínimos
u = api("GET", f"{G}/users/?username={USUARIO}")["results"]
clave_u = SEC.get("fiscalia_app_clave")
if not u:
    clave_u = secrets.token_hex(12)
    u = api("POST", f"{G}/users/", {"username": USUARIO, "password": clave_u, "first_name": "Aplicación", "last_name": "Fiscalía (demo)"})
    with open(SECF, "a") as f:
        f.write(f"fiscalia_app_clave={clave_u}\n")
else:
    u = u[0]
uc = None
for _ in range(10):
    uc = uno("users", username=USUARIO)
    if uc:
        break
    time.sleep(3)
defs = {d["name"]: d["id"] for d in api("GET", f"{C}/role_definitions/?page_size=200")["results"]}
quien = {"user": uc["id"]} if uc else {"user_ansible_id": u["summary_fields"]["resource"]["ansible_id"]}
for rol, oid in (("JobTemplate Execute", jt["id"]), ("NotificationTemplate Admin", n["id"])):
    api("POST", f"{C}/role_user_assignments/", dict(quien, role_definition=defs[rol], object_id=str(oid)), tolerar=True)
paso(f"Usuario {USUARIO}: solo ejecutar la plantilla y editar su notificador")

# 4. Secreto en OpenShift (token de AAP + clave de los enlaces firmados; la clave se conserva si ya existe)
ya = subprocess.run([OC, "-n", "demo-fiscalia", "get", "secret", "aviso-fiscalia", "-o", "json"], capture_output=True, text=True, env=KENV)
datos_s = json.loads(ya.stdout)["data"] if ya.returncode == 0 else {}
enlace = base64.b64decode(datos_s["ENLACE_CLAVE"]).decode() if "ENLACE_CLAVE" in datos_s else secrets.token_hex(24)
tok = api("POST", f"{G}/tokens/", {"description": "App de evaluación Fiscalía (demo)", "scope": "write"}, usuario=USUARIO, clave=clave_u)["token"]
yaml = subprocess.run([OC, "-n", "demo-fiscalia", "create", "secret", "generic", "aviso-fiscalia", "--from-literal=AAP_URL=https://192.168.122.1",
                       f"--from-literal=AAP_TOKEN={tok}", f"--from-literal=ENLACE_CLAVE={enlace}", "--dry-run=client", "-o", "yaml"],
                      capture_output=True, text=True, check=True, env=KENV).stdout
subprocess.run([OC, "apply", "-f", "-"], input=yaml, text=True, check=True, capture_output=True, env=KENV)
paso("Secreto aviso-fiscalia en demo-fiscalia (token de AAP y clave de enlaces)")

# 5. Firewall: el nodo OpenShift puede llegar a AAP (443) en el anfitrión; nadie más de la red libvirt
regla = 'rule family="ipv4" source address="192.168.122.50/32" port port="443" protocol="tcp" accept'
for extra in ([], ["--permanent"]):
    q = subprocess.run(["sudo", "firewall-cmd", "--zone=libvirt", *extra, "--query-rich-rule", regla], capture_output=True, text=True)
    if q.returncode != 0:
        subprocess.run(["sudo", "firewall-cmd", "--zone=libvirt", *extra, "--add-rich-rule", regla], check=True, capture_output=True)
paso("Firewall: 192.168.122.50 → 443 del anfitrión (AAP)")
print("LISTO")
