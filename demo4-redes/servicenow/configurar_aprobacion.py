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
