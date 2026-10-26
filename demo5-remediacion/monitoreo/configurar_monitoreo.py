#!/usr/bin/python3
# Demo 5 · monitoreo: publica playbooks y rulebook en Gitea (invoke/operacion), crea en AAP las plantillas
# "Remediación · Cola de pagos" y "Remediación · Renovar certificado" (con ServiceNow y correo) y la activación
# de EDA "Monitoreo · Alertas de Prometheus" (fuente ansible.eda.alertmanager, :5004). Idempotente.
import base64, glob, json, os, re, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__)); OPER = os.path.join(os.path.dirname(AQUI), "operacion")
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context(); BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
ACT = "Monitoreo · Alertas de Prometheus"
paso = lambda t: print(f"   · {t}", flush=True)


def http(url, m, d=None, cab=None, tolerar=False):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers=dict({"Content-Type": "application/json"}, **(cab or {})))
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read(); return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {url} -> {e.code}: {e.read()[:500].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http(f"https://localhost/api/controller/v2/{p}", m, d, {"Authorization": BASIC}, t)
eda = lambda m, p, d=None, t=False: http(f"https://localhost/api/eda/v1/{p}", m, d, {"Authorization": BASIC}, t)


def uno(f, rec, **q):
    r = f("GET", f"{rec}/?" + urllib.parse.urlencode(q))["results"]
    return r[0] if r else None


# 1. Gitea: invoke/operacion (solo agrega/actualiza archivos; no borra lo de la Demo 5)
tmp = tempfile.mkdtemp(); url = f"http://invoke:{SEC['gitea_token']}@127.0.0.1:3000/invoke/operacion.git"
git = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True)
if subprocess.run(["git", "clone", "-q", url, tmp], capture_output=True).returncode:
    sys.exit("ERROR al clonar invoke/operacion")
for d in ("rulebooks", "playbooks"):
    shutil.copytree(os.path.join(OPER, d), os.path.join(tmp, d), dirs_exist_ok=True)
git("add", "-A")
if git("status", "--porcelain").stdout.strip():
    git("-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl", "commit", "-qm",
        "Monitoreo: remediaciones de cola de pagos y certificado, y rulebook de alertas")
    if git("push", "-q", url, "HEAD:main").returncode:
        sys.exit("ERROR al subir a invoke/operacion")
shutil.rmtree(tmp)
paso("Gitea: invoke/operacion con playbooks y rulebook de monitoreo")

# 2. AAP
org = uno(aap, "organizations", name="Operación")["id"]
proy = uno(aap, "projects", name="Operación · remediación")["id"]
u = aap("POST", f"projects/{proy}/update/", {})
while aap("GET", f"project_updates/{u['id']}/")["status"] not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
inv = uno(aap, "inventories", name="Nodos demo")["id"]
creds = [uno(aap, "credentials", name="Nodos demo (ansible)")["id"], uno(aap, "credentials", name="ServiceNow · ansible.automatizacion")["id"]]
nt = uno(aap, "notification_templates", name="Correo · aprobaciones de red")
correo = "no"
for nombre, pb, desc in (("Remediación · Cola de pagos", "playbooks/cola-pagos.yml",
                          "Monitoreo: la cola pagos.entrada acumula mensajes → reactiva el consumidor y espera que baje"),
                         ("Remediación · Renovar certificado", "playbooks/renovar-certificado.yml",
                          "Monitoreo: certificado TLS de Nginx por vencer → lo renueva y recarga sin corte")):
    datos = {"organization": org, "project": proy, "inventory": inv, "playbook": pb, "description": desc,
             "ask_variables_on_launch": True}
    o = uno(aap, "job_templates", name=nombre)
    jt = (aap("PATCH", f"job_templates/{o['id']}/", datos) if o else aap("POST", "job_templates/", dict(datos, name=nombre)))["id"]
    ya = {c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]}
    for c in creds:
        if c not in ya:
            aap("POST", f"job_templates/{jt}/credentials/", {"id": c})
    if nt:
        ok = all(aap("POST", f"job_templates/{jt}/notification_templates_{ev}/", {"id": nt["id"]}, True) is not None
                 for ev in ("success", "error"))
        correo = "sí" if ok else correo
    paso(f"AAP: plantilla '{nombre}' (id {jt})")
if nt:  # el mismo notificador sirve a Redes (bloqueo de IP) y a las remediaciones: textos según la plantilla
    m = nt.get("messages") or {}
    L = "https://dev459364.service-now.com/incident_list.do?sysparm_query=short_descriptionSTARTSWITH%5BDemo%205%5D%5EORDERBYDESCsys_created_on"
    for ev in ("success", "error"):
        o = m.get(ev) or {}
        if "es_remed" in (o.get("message") or ""):
            continue
        n = "{{ job.name | replace('Remediación · ','') }}"
        gm = f"Corregido automáticamente: {n}" if ev == "success" else f"No se pudo corregir solo: {n} (escalado)"
        gb = (f"Hola Cristian,\n\nUna alerta de monitoreo (Prometheus) se corrigió automáticamente con Ansible: {n}.\n"
              f"El incidente quedó registrado y resuelto en ServiceNow:\n{L}\n\nDetalle técnico: {{{{ url }}}}\n") if ev == "success" else (
              f"Hola Cristian,\n\nAnsible no pudo corregir solo: {n}. El incidente quedó abierto en ServiceNow:\n{L}\n\nDetalle técnico: {{{{ url }}}}\n")
        pre = "{%- set es_remed = 'Remediación' in job.name -%}"
        o["message"] = pre + "{% if es_remed %}" + gm + "{% else %}" + o.get("message", "{{ job_friendly_name }}") + "{% endif %}"
        o["body"] = pre + "{% if es_remed %}" + gb + "{% else %}" + o.get("body", "{{ url }}") + "{% endif %}"
        m[ev] = o
    aap("PATCH", f"notification_templates/{nt['id']}/", {"messages": m})
paso(f"Correo con el notificador 'Correo · aprobaciones de red': {correo}")

# 3. EDA
eorg = (uno(eda, "organizations", name="Operación") or uno(eda, "organizations", name="Default"))["id"]
cred = uno(eda, "eda-credentials", name="AAP · lanzar remediaciones")["id"]
p = uno(eda, "projects", name="Operación · remediación")
eda("POST", f"projects/{p['id']}/sync/", {}, True)
rb = None
for _ in range(40):
    time.sleep(3)
    rb = uno(eda, "rulebooks", project_id=p["id"], name="monitoreo.yml")
    if rb and eda("GET", f"projects/{p['id']}/")["import_state"] in ("completed", "failed"):
        break
if not rb:
    sys.exit("ERROR: el proyecto EDA no trae rulebooks/monitoreo.yml")
des = eda("GET", "decision-environments/?page_size=50")["results"]
de = next((d for d in des if d["name"] == "Hub Default Decision Environment"), des[0])["id"]
a = uno(eda, "activations", name=ACT)
if a:
    eda("POST", f"activations/{a['id']}/disable/", {}, True); time.sleep(5)
    eda("DELETE", f"activations/{a['id']}/", t=True)
    for _ in range(20):
        if not uno(eda, "activations", name=ACT):
            break
        time.sleep(3)
act = eda("POST", "activations/", {
    "name": ACT, "description": "Alertas de Prometheus/Alertmanager → remediación en AAP (cola, certificado, seguridad)",
    "is_enabled": True, "decision_environment_id": de, "rulebook_id": rb["id"], "eda_credentials": [cred],
    "organization_id": eorg, "restart_policy": "on-failure", "log_level": "info"})["id"]
for _ in range(60):
    st = eda("GET", f"activations/{act}/")["status"]
    if st in ("running", "failed", "error"):
        break
    time.sleep(5)
paso(f"EDA: activación '{ACT}' {st} (Alertmanager → :5004)")
print("   LISTO")
