#!/usr/bin/python3
# Demo 4 · Aprobación antes de tocar el firewall.
# Workflow "Red · Solicitar acceso con aprobación" (organización Redes):
#   preparar (muestra los comandos, no aplica) → aprobación en AAP → aplicar y cerrar el ticket
#                                               ↳ rechazo o vencimiento → cerrar el ticket como no aprobado
# Usa la plantilla "Red · Solicitar acceso" con acceso_fase = preparar | aplicar | rechazado. Idempotente.
import base64, glob, json, os, re, ssl, sys, urllib.error, urllib.parse, urllib.request

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
WF = "Red · Solicitar acceso con aprobación"
VENCE_SEG = int(os.environ.get("APROBACION_SEG", str(4 * 3600)))
paso = lambda t: print(f"   · {t}", flush=True)


def aap(m, p, d=None):
    req = urllib.request.Request(f"https://localhost/api/controller/v2/{p}", method=m,
                                 data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": BASIC})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:500].decode(errors='replace')}")


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


jt = uno("job_templates", name="Red · Solicitar acceso")
datos = {"organization": jt["organization"], "inventory": jt["inventory"], "ask_variables_on_launch": True,
         "description": "Ticket de ServiceNow → comandos para revisar → aprobación en AAP → firewall y cierre del ticket"}
o = uno("workflow_job_templates", name=WF)
wf = (aap("PATCH", f"workflow_job_templates/{o['id']}/", datos) if o else
      aap("POST", "workflow_job_templates/", dict(datos, name=WF)))["id"]
for n in aap("GET", f"workflow_job_templates/{wf}/workflow_nodes/?page_size=50")["results"]:
    aap("DELETE", f"workflow_job_template_nodes/{n['id']}/")
nodo = lambda ident, d: aap("POST", f"workflow_job_templates/{wf}/workflow_nodes/", dict(d, identifier=ident))["id"]
n_prep = nodo("preparar", {"unified_job_template": jt["id"], "extra_data": {"acceso_fase": "preparar"}})
n_apr = nodo("aprobacion", {})
aap("POST", f"workflow_job_template_nodes/{n_apr}/create_approval_template/", {
    "name": "¿Aplicar el cambio en el firewall?",
    "description": "Revisa los comandos en la salida del paso 'preparar' (o en las notas del ticket) y aprueba o rechaza.",
    "timeout": VENCE_SEG})
n_apl = nodo("aplicar", {"unified_job_template": jt["id"], "extra_data": {"acceso_fase": "aplicar"}})
n_rec = nodo("no_aprobado", {"unified_job_template": jt["id"], "extra_data": {"acceso_fase": "rechazado"}})
aap("POST", f"workflow_job_template_nodes/{n_prep}/success_nodes/", {"id": n_apr})
aap("POST", f"workflow_job_template_nodes/{n_apr}/success_nodes/", {"id": n_apl})
aap("POST", f"workflow_job_template_nodes/{n_apr}/failure_nodes/", {"id": n_rec})
paso(f"Workflow '{WF}' (id {wf}): preparar → aprobación (vence en {VENCE_SEG // 60} min) → aplicar | no aprobado")
print(f"   WORKFLOW https://34.176.42.56/execution/templates/workflow-template/{wf}/details")

# Responder con motivo: plantilla con formulario (encuesta) que aprueba o rechaza el paso pendiente del ticket.
import subprocess
IP = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
tipo = uno("credential_types", name="Red Hat Ansible Automation Platform")["id"]
CRED = "AAP · responder aprobaciones"
datos = {"organization": jt["organization"], "credential_type": tipo,
         "description": "Lo usa 'Red · Responder solicitud' para aprobar o rechazar el paso pendiente",
         "inputs": {"host": f"https://{IP}", "username": "admin", "password": CLAVE, "verify_ssl": False}}
o = uno("credentials", name=CRED)
cred = (aap("PATCH", f"credentials/{o['id']}/", datos) if o else aap("POST", "credentials/", dict(datos, name=CRED)))["id"]
RESP = "Red · Responder solicitud"
datos = {"organization": jt["organization"], "project": jt["project"], "inventory": jt["inventory"],
         "playbook": "playbooks/responder-solicitud.yml", "survey_enabled": True, "ask_variables_on_launch": False,
         "description": "Aprobar o rechazar (con motivo) un cambio de red que espera aprobación"}
o = uno("job_templates", name=RESP)
resp = (aap("PATCH", f"job_templates/{o['id']}/", datos) if o else aap("POST", "job_templates/", dict(datos, name=RESP)))["id"]
ya = {c["id"] for c in aap("GET", f"job_templates/{resp}/credentials/")["results"]}
for cid in (cred, uno("credentials", name="ServiceNow · ansible.automatizacion")["id"]):
    if cid not in ya:
        aap("POST", f"job_templates/{resp}/credentials/", {"id": cid})
aap("POST", f"job_templates/{resp}/survey_spec/", {"name": "Responder", "description": "Decisión sobre el cambio de red", "spec": [
    {"question_name": "Ticket", "question_description": "Número del ticket, por ejemplo RITM0010010 (viene en el correo)",
     "variable": "resp_ticket", "type": "text", "required": True, "min": 10, "max": 20, "default": ""},
    {"question_name": "Decisión", "question_description": "", "variable": "resp_decision", "type": "multiplechoice",
     "choices": ["Aprobar", "Rechazar"], "required": True, "default": "Aprobar"},
    {"question_name": "Motivo", "question_description": "Obligatorio si rechazas: el usuario lo verá en su ticket",
     "variable": "resp_motivo", "type": "textarea", "required": False, "min": 0, "max": 1000, "default": ""}]})
paso(f"Plantilla '{RESP}' (id {resp}) con formulario Ticket / Decisión / Motivo")
print(f"   RESPONDER https://34.176.42.56/execution/templates/job-template/{resp}/details")
apr = aap("GET", f"workflow_job_template_nodes/{n_apr}/")["summary_fields"]["unified_job_template"]["id"]
aap("PATCH", f"workflow_approval_templates/{apr}/", {
    "description": f"Para rechazar con motivo usa la plantilla '{RESP}'. Los comandos están en el paso 'preparar'."})
