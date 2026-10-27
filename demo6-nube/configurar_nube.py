#!/usr/bin/python3
# Demo 6 · Nube (GCP). Deja todo listo en la VM del Summit, idempotente:
#  1. Llave SSH de Ansible para las VMs de la nube (Vault secret/nube/ssh; se crea una sola vez)
#  2. Gitea: repositorio invoke/nube (playbooks, rulebook, datos y la llave pública)
#  3. AAP: organización "Nube", proyecto, inventario "Nube GCP", credenciales, plantillas, workflows con aprobación,
#     notificador "Correo · nube" (sin clave: la pega Cristian) y, si la nube está conectada, la fuente de inventario
#     y las programaciones (huérfanos semanal, firewall cada 10 min)
#  4. EDA: proyecto y activación "ServiceNow · Solicitudes de nube" (webhook :5004)
#  5. ServiceNow: categoría "Nube" con sus dos ítems; vigía de la nube como servicio
#  6. Monitoreo: exportador :9420, trabajos de Prometheus y tablero "Summit · Nube"
# La credencial de GCP sale de Vault secret/gcp (campo "llave" = JSON de la cuenta de servicio). Si no está, todo lo
# demás queda listo y los playbooks responden "la nube todavía no está conectada". No imprime claves.
import base64, glob, json, os, re, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, G, E = "https://localhost/api/controller/v2", "https://localhost/api/gateway/v1", "https://localhost/api/eda/v1"
GITEA, VAULT = "http://127.0.0.1:3000/api/v1", "https://127.0.0.1:8200/v1"
VTOKEN = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"]
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
ORG_N, REPO, ACT = "Nube", "nube", "ServiceNow · Solicitudes de nube"
APROBACION_SEG = int(os.environ.get("APROBACION_SEG", str(4 * 3600)))
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
        sys.exit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:500].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http(m, f"{C}/{p}", d, {"Authorization": BASIC}, t)
gw = lambda m, p, d=None, t=False: http(m, f"{G}/{p}", d, {"Authorization": BASIC}, t)
eda = lambda m, p, d=None, t=False: http(m, f"{E}/{p}", d, {"Authorization": BASIC}, t)
gitea = lambda m, p, d=None, t=False: http(m, f"{GITEA}/{p}", d, {"Authorization": "token " + SEC["gitea_token"]}, t)
vault = lambda m, p, d=None, t=False: http(m, f"{VAULT}/{p}", d, {"X-Vault-Token": VTOKEN}, t)


def uno(f, rec, **filtro):
    r = f("GET", f"{rec}/?" + urllib.parse.urlencode(filtro))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(aap, rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


def esperar(url_estado, campo="status", fin=("successful", "failed", "error", "canceled")):
    for _ in range(200):
        st = aap("GET", url_estado)[campo]
        if st in fin:
            return st
        time.sleep(3)
    return "timeout"


# 1. Llave SSH (una sola vez; la privada solo vive en Vault y en la credencial de AAP)
ssh = (vault("GET", "secret/data/nube/ssh", t=True) or {}).get("data", {}).get("data")
if not ssh:
    tmp = tempfile.mkdtemp()
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "ansible@summit-cloud", "-f", f"{tmp}/k"], check=True)
    ssh = {"privada": open(f"{tmp}/k").read(), "publica": open(f"{tmp}/k.pub").read().strip()}
    shutil.rmtree(tmp)
    vault("POST", "secret/data/nube/ssh", {"data": ssh})
    paso("Llave SSH nueva en Vault (secret/nube/ssh)")
else:
    paso("Llave SSH en Vault (secret/nube/ssh)")
gcp = (vault("GET", "secret/data/gcp", t=True) or {}).get("data", {}).get("data")
gcp = json.loads(gcp["llave"]) if gcp and gcp.get("llave") else None
paso("Credencial de GCP: " + (f"sí ({gcp['client_email']})" if gcp else "todavía no (Vault secret/gcp vacío)"))

# 2. Gitea: invoke/nube
DESC = "DEMO 6 · Nube (GCP): VMs por catálogo, huérfanos y costos, reglas de firewall peligrosas"
if not gitea("GET", f"repos/invoke/{REPO}", t=True):
    gitea("POST", "user/repos", {"name": REPO, "private": False, "default_branch": "main", "description": DESC, "auto_init": True})
tmp = tempfile.mkdtemp()
url = f"http://invoke:{SEC['gitea_token']}@127.0.0.1:3000/invoke/{REPO}.git"
git = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True)
if subprocess.run(["git", "clone", "-q", url, tmp], capture_output=True).returncode:
    sys.exit(f"ERROR al clonar invoke/{REPO}")
for d in ("playbooks", "rulebooks", "datos", "inventario", "collections"):
    shutil.rmtree(os.path.join(tmp, d), ignore_errors=True)
    shutil.copytree(os.path.join(AQUI, "nube", d), os.path.join(tmp, d))
open(os.path.join(tmp, "datos", "ssh.pub"), "w").write(ssh["publica"] + "\n")
shutil.copy(os.path.join(AQUI, "README.md"), tmp)
git("add", "-A")
if git("status", "--porcelain").stdout.strip():
    git("-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl", "commit", "-qm",
        "Nube: playbooks, rulebook, datos e inventario")
    if git("push", "-q", url, "HEAD:main").returncode:
        sys.exit(f"ERROR al subir a invoke/{REPO}")
shutil.rmtree(tmp)
gitea("PUT", f"repos/invoke/{REPO}/collaborators/cquezada", {"permission": "write"}, t=True)
paso(f"Gitea: invoke/{REPO}")

# 3. AAP
if not gw("GET", "organizations/?name=" + q(ORG_N))["results"]:
    gw("POST", "organizations/", {"name": ORG_N, "description": "Nube pública (GCP): Demo 6"})
org = None
for _ in range(20):
    org = uno(aap, "organizations", name=ORG_N)
    if org:
        break
    time.sleep(3)
org = org["id"]
paso(f"Organización '{ORG_N}'")

proy = asegurar("projects", "Nube · GCP", {
    "organization": org, "scm_type": "git", "scm_url": f"http://192.168.122.1:3000/invoke/{REPO}.git",
    "scm_branch": "main", "scm_update_on_launch": True, "description": "Playbooks de la nube desde Gitea (invoke/nube)"})["id"]
st = esperar(f"project_updates/{aap('POST', f'projects/{proy}/update/', {})['id']}/")
if st != "successful":
    sys.exit(f"ERROR al sincronizar el proyecto Nube · GCP: {st}")
paso("Proyecto 'Nube · GCP' sincronizado")

inv = asegurar("inventories", "Nube GCP", {"organization": org,
               "description": "VMs creadas por Ansible en GCP (etiqueta demo=summit-cloud)"})["id"]

# Credenciales
def cred(nombre, tipo, inputs, desc):
    t = uno(aap, "credential_types", name=tipo)["id"]
    return asegurar("credentials", nombre, {"organization": org, "credential_type": t, "inputs": inputs, "description": desc})["id"]


c_ssh = cred("Nube · ssh (ansible)", "Machine", {"username": "ansible", "ssh_key_data": ssh["privada"], "become_method": "sudo"},
             "Llave de Ansible para las VMs de GCP (Vault secret/nube/ssh)")
c_aap = cred("AAP · Nube", "Red Hat Ansible Automation Platform",
             {"host": f"https://{IP}", "username": "admin", "password": CLAVE, "verify_ssl": False},
             "Para actualizar el inventario Nube GCP y lanzar el aviso por correo")
c_sn = uno(aap, "credentials", name="ServiceNow · ansible.automatizacion")["id"]
c_gcp = None
if gcp:
    c_gcp = cred("GCP · invoke-summit-cloud", "Google Compute Engine",
                 {"username": gcp["client_email"], "project": gcp["project_id"], "ssh_key_data": gcp["private_key"]},
                 "Cuenta de servicio del proyecto invoke-summit-cloud (Vault secret/gcp)")
paso("Credenciales: ssh, AAP, ServiceNow" + (", GCP" if c_gcp else " (GCP pendiente)"))

fuente = None
if c_gcp:
    fuente = asegurar("inventory_sources", "GCP · VMs de la demo", {
        "inventory": inv, "source": "scm", "source_project": proy, "source_path": "inventario/gcp.yml",
        "credential": c_gcp, "overwrite": True, "update_on_launch": False,
        "description": "Solo VMs con la etiqueta demo=summit-cloud"})["id"]
    paso("Inventario 'Nube GCP' con fuente dinámica gcp_compute")

# Plantillas
jt = {}
for nombre, pb, desc, creds, encuesta in (
        ("Nube · Solicitar VM", "playbooks/vm-solicitud.yml",
         "Valida, crea en GCP, endurece, monitorea, registra y cierra el ticket (usa vm_fase)", [c_ssh, c_sn, c_aap], None),
        ("Nube · Dar de baja VM", "playbooks/vm-baja.yml",
         "Borra una VM creada por Ansible (solo con etiqueta demo=summit-cloud) y cierra el ticket", [c_sn, c_aap], None),
        ("Nube · Reporte de huérfanos", "playbooks/huerfanos-reporte.yml",
         "Solo lectura: discos, IPs, snapshots y VMs fuera de horario con costo estimado; incidente y correo", [c_sn, c_aap], None),
        ("Nube · Limpiar huérfanos", "playbooks/huerfanos-limpiar.yml",
         "Después de la aprobación: limpia solo lo etiquetado demo=summit-cloud", [c_sn, c_aap], None),
        ("Nube · Vigilar firewall", "playbooks/firewall-vigilar.yml",
         "0.0.0.0/0 en 22/3389: restringe las reglas summit-cloud-* a los rangos de Git; incidente y correo", [c_sn, c_aap], None),
        ("Nube · Avisar por correo", "playbooks/aviso.yml", "La lanzan los otros playbooks; el correo lo manda su notificador", [], None),
        ("Nube · Prueba: crear huérfanos", "playbooks/prueba-huerfanos.yml",
         "Demo en vivo: disco e IP sin uso etiquetados (o borrarlos)", [], "huerfanos"),
        ("Nube · Prueba: regla peligrosa", "playbooks/prueba-regla-peligrosa.yml",
         "Demo en vivo: regla summit-cloud-prueba-ssh-abierto con 0.0.0.0/0 (o borrarla)", [], "regla")):
    datos = {"organization": org, "project": proy, "inventory": inv, "playbook": pb, "description": desc,
             "ask_variables_on_launch": True, "execution_environment": uno(aap, "execution_environments", name="Default execution environment")["id"]}
    if nombre == "Nube · Solicitar VM" and fuente:
        datos["extra_vars"] = json.dumps({"nube_fuente_inventario": fuente})
    if encuesta:
        datos.update(survey_enabled=True, ask_variables_on_launch=False)
    o = asegurar("job_templates", nombre, datos)
    ya = {c["id"] for c in aap("GET", f"job_templates/{o['id']}/credentials/")["results"]}
    for cid in creds + ([c_gcp] if c_gcp and nombre != "Nube · Avisar por correo" else []):
        if cid not in ya:
            aap("POST", f"job_templates/{o['id']}/credentials/", {"id": cid})
    if encuesta:
        aap("POST", f"job_templates/{o['id']}/survey_spec/", {"name": "Prueba", "description": "", "spec": [
            {"question_name": "Acción", "question_description": "crear para la demo; borrar para dejar limpio",
             "variable": "prueba_accion", "type": "multiplechoice", "choices": ["crear", "borrar"], "required": True,
             "default": "crear"}]})
    jt[nombre] = o["id"]
paso(f"Plantillas: {len(jt)}")


def workflow(nombre, desc, pasos, aprobacion, vence, despues, rechazo=None, inventario=True):
    datos = {"organization": org, "ask_variables_on_launch": True, "description": desc}
    if inventario:
        datos["inventory"] = inv
    wf = asegurar("workflow_job_templates", nombre, datos)["id"]
    for n in aap("GET", f"workflow_job_templates/{wf}/workflow_nodes/?page_size=50")["results"]:
        aap("DELETE", f"workflow_job_template_nodes/{n['id']}/")
    nodo = lambda ident, d: aap("POST", f"workflow_job_templates/{wf}/workflow_nodes/", dict(d, identifier=ident))["id"]
    n1 = nodo(pasos[0], pasos[1])
    na = nodo("aprobacion", {})
    aap("POST", f"workflow_job_template_nodes/{na}/create_approval_template/",
        {"name": aprobacion[0], "description": aprobacion[1], "timeout": vence})
    n2 = nodo(despues[0], despues[1])
    aap("POST", f"workflow_job_template_nodes/{n1}/success_nodes/", {"id": na})
    aap("POST", f"workflow_job_template_nodes/{na}/success_nodes/", {"id": n2})
    if rechazo:
        aap("POST", f"workflow_job_template_nodes/{na}/failure_nodes/", {"id": nodo(rechazo[0], rechazo[1])})
    return wf


vm = jt["Nube · Solicitar VM"]
wf_vm = workflow("Nube · Solicitar VM con aprobación",
                 "Ticket de ServiceNow → plan para revisar → aprobación en AAP → VM en GCP y cierre del ticket",
                 ("preparar", {"unified_job_template": vm, "extra_data": {"vm_fase": "preparar"}}),
                 ("¿Crear la VM en GCP?", "Revisa el plan en la salida del paso 'preparar' (tamaño, costo, reglas) y aprueba o rechaza."),
                 APROBACION_SEG, ("aplicar", {"unified_job_template": vm, "extra_data": {"vm_fase": "aplicar"}}),
                 ("no_aprobado", {"unified_job_template": vm, "extra_data": {"vm_fase": "rechazado"}}))
wf_h = workflow("Nube · Reporte de huérfanos con limpieza",
                "Semanal: reporte con costo (incidente y correo) → aprobación → limpia solo lo etiquetado demo=summit-cloud",
                ("reporte", {"unified_job_template": jt["Nube · Reporte de huérfanos"]}),
                ("¿Limpiar los huérfanos etiquetados?", "Solo se limpia lo etiquetado demo=summit-cloud. El detalle está en el paso 'reporte' y en el incidente."),
                24 * 3600, ("limpiar", {"unified_job_template": jt["Nube · Limpiar huérfanos"]}))
paso(f"Workflows: 'Nube · Solicitar VM con aprobación' (id {wf_vm}) y 'Nube · Reporte de huérfanos con limpieza' (id {wf_h})")

# Notificador de correo propio (mismo servidor que el de la Demo 4, sin clave: la pega Cristian)
base = aap("GET", "notification_templates/?name=" + q("Correo · aprobaciones de red"))["results"]
conf = {k: v for k, v in (base[0]["notification_configuration"] if base else {}).items() if k != "password"}
conf.setdefault("host", "smtp.gmail.com"); conf.setdefault("port", 587); conf.setdefault("use_tls", True)
conf.setdefault("use_ssl", False); conf.setdefault("timeout", 30)
conf.setdefault("username", "cquezada@invoke.cl"); conf.setdefault("sender", "cquezada@invoke.cl")
conf.setdefault("recipients", ["cquezada@invoke.cl"])
_T = "{%- set m = job_metadata -%}{%- set k = 'aviso_titulo\\\\\": \\\\\"' -%}{%- set x = 'aviso_texto\\\\\": \\\\\"' -%}"
MSG = {"success": {
    "message": _T + "[Summit · Nube] {{ m.split(k)[1].split('\\\\\"')[0] if k in m else job.name }}",
    "body": _T + "Hola Cristian,\n\n{{ (m.split(x)[1].split('\\\\\", ')[0] | replace('\\\\\\\\n', '\n')) if x in m else job.name }}"
            "\n\nDetalle en AAP: {{ url }}\n\nAnsible Automation Platform · Demo 6 (nube)"}}
o = aap("GET", "notification_templates/?name=" + q("Correo · nube"))["results"]
datos = {"organization": org, "notification_type": "email", "description": "Avisos de la Demo 6 (falta pegar la clave de aplicación de Gmail)",
         "notification_configuration": dict(conf, password=("$encrypted$" if o else "")), "messages": MSG}
nt = (aap("PATCH", f"notification_templates/{o[0]['id']}/", datos) if o else
      aap("POST", "notification_templates/", dict(datos, name="Correo · nube")))["id"]
aap("POST", f"job_templates/{jt['Nube · Avisar por correo']}/notification_templates_success/", {"id": nt}, t=True)
paso("Notificador 'Correo · nube' en 'Nube · Avisar por correo'")

# Programaciones (solo con la nube conectada; sin ella no tiene sentido pedir aprobaciones semanales)
for nombre, ujt, rrule, desc in (
        ("Nube · huérfanos los lunes 08:00", f"workflow_job_templates/{wf_h}",
         "DTSTART;TZID=America/Santiago:20261026T080000 RRULE:FREQ=WEEKLY;BYDAY=MO", "Reporte semanal de huérfanos"),
        ("Nube · firewall cada 10 min", f"job_templates/{jt['Nube · Vigilar firewall']}",
         "DTSTART;TZID=America/Santiago:20261026T000000 RRULE:FREQ=MINUTELY;INTERVAL=10", "Vigila reglas abiertas a internet")):
    o = uno(aap, "schedules", name=nombre)
    datos = {"rrule": rrule, "description": desc, "enabled": bool(c_gcp)}
    if o:
        aap("PATCH", f"schedules/{o['id']}/", datos)
    elif c_gcp:
        aap("POST", f"{ujt}/schedules/", dict(datos, name=nombre))
paso("Programaciones " + ("activas: huérfanos semanal y firewall cada 10 min" if c_gcp else "pendientes (sin credencial de GCP)"))

# 4. EDA
eorg = (uno(eda, "organizations", name=ORG_N) or uno(eda, "organizations", name="Default"))["id"]
tipo = uno(eda, "credential-types", name="Red Hat Ansible Automation Platform")["id"]
datos = {"name": "AAP · lanzar plantillas de nube", "credential_type_id": tipo, "organization_id": eorg,
         "inputs": {"host": f"https://{IP}/api/controller/", "username": "admin", "password": CLAVE, "verify_ssl": False}}
c = uno(eda, "eda-credentials", name=datos["name"])
ecred = eda("PATCH", f"eda-credentials/{c['id']}/", datos)["id"] if c else eda("POST", "eda-credentials/", datos)["id"]
p = uno(eda, "projects", name="Nube · GCP")
if p:
    eda("POST", f"projects/{p['id']}/sync/", {}, t=True)
    ep = p["id"]
else:
    ep = eda("POST", "projects/", {"name": "Nube · GCP", "organization_id": eorg, "verify_ssl": False,
                                   "url": f"http://192.168.122.1:3000/invoke/{REPO}.git",
                                   "description": "Rulebook de la nube (Demo 6)"})["id"]
for _ in range(60):
    if eda("GET", f"projects/{ep}/")["import_state"] in ("completed", "failed"):
        break
    time.sleep(3)
rb = None
for _ in range(20):
    rb = uno(eda, "rulebooks", project_id=ep, name="nube.yml")
    if rb:
        break
    time.sleep(3)
if not rb:
    sys.exit("ERROR: el proyecto EDA no trae rulebooks/nube.yml")
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
    "name": ACT, "description": "Tickets del catálogo 'Nube' de ServiceNow → AAP (organización Nube)",
    "is_enabled": True, "decision_environment_id": de, "rulebook_id": rb["id"], "eda_credentials": [ecred],
    "organization_id": eorg, "restart_policy": "on-failure", "log_level": "info"})["id"]
for _ in range(60):
    st = eda("GET", f"activations/{act}/")["status"]
    if st in ("running", "failed", "error"):
        break
    time.sleep(5)
paso(f"EDA: activación '{ACT}' {st}")

# 5. ServiceNow y vigía
subprocess.run([sys.executable, os.path.join(AQUI, "servicenow", "configurar_catalogo_nube.py")], check=True)
sh = lambda *c: subprocess.run(["sudo", *c], check=True, capture_output=True)
for u in ("vigia-nube.service",):
    sh("cp", os.path.join(AQUI, "servicenow", u), "/etc/systemd/system/")

# 6. Monitoreo
M = "/opt/summit-monitoreo"
sh("mkdir", "-p", f"{M}/prometheus/nube")
sh("chown", "cquezada:cquezada", f"{M}/prometheus/nube")
sh("cp", os.path.join(AQUI, "monitoreo", "prometheus-nube.yml"), f"{M}/prometheus/nube/scrape.yml")
sh("cp", os.path.join(AQUI, "monitoreo", "summit-nube.json"), f"{M}/grafana/dashboards/")
sh("cp", os.path.join(AQUI, "monitoreo", "summit-nube-exportador.service"), "/etc/systemd/system/")
prom = subprocess.run(["sudo", "cat", f"{M}/prometheus/prometheus.yml"], capture_output=True, text=True).stdout
if "scrape_config_files" not in prom:
    nuevo = prom.replace("scrape_configs:", "scrape_config_files: [/etc/prometheus/nube/*.yml]   # Demo 6 · nube\nscrape_configs:", 1)
    subprocess.run(["sudo", "tee", f"{M}/prometheus/prometheus.yml"], input=nuevo, text=True, capture_output=True, check=True)
for z in ("libvirt",):  # los jobs de AAP (contenedores) informan al exportador por 192.168.122.1:9420
    if "9420/tcp" not in subprocess.run(["sudo", "firewall-cmd", f"--zone={z}", "--list-ports"], capture_output=True, text=True).stdout:
        sh("firewall-cmd", "-q", f"--zone={z}", "--add-port=9420/tcp")
        sh("firewall-cmd", "-q", f"--zone={z}", "--add-port=9420/tcp", "--permanent")
sh("systemctl", "daemon-reload")
for u in ("summit-nube-exportador.service", "vigia-nube.service"):
    sh("systemctl", "enable", "-q", "--now", u)
    sh("systemctl", "restart", u)
time.sleep(3)
http("POST", "http://127.0.0.1:9090/-/reload", tolerar=True)
paso("Monitoreo: exportador :9420, Prometheus (nube, nube-vms) y tablero 'Summit · Nube'; vigía de la nube activo")
print(f"   WORKFLOW_VM https://34.176.42.56/execution/templates/workflow-template/{wf_vm}/details")
print(f"   WORKFLOW_HUERFANOS https://34.176.42.56/execution/templates/workflow-template/{wf_h}/details")
print("   TABLERO http://34.176.42.56:8090/grafana/d/summit-nube")
print("   LISTO")
