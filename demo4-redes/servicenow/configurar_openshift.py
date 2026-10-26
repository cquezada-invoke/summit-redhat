#!/usr/bin/python3
# Demo 4 · "Preparar red para clúster OpenShift". Idempotente. No imprime claves.
# 1) ServiceNow: ítem de catálogo (categoría Red) con clúster, nodos y motivo.
# 2) AAP: host "dns" en el inventario, credencial de escritura en Gitea (red-laboratorio),
#    plantilla "Red · Preparar red OpenShift" y workflow con aprobación.
# Después: configurar_aviso_aprobacion.py (correo de aprobación) y configurar_eda_snow.py (rulebook con el nuevo evento).
import base64, glob, json, os, re, ssl, sys, urllib.error, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sn import URL, asegurar, tabla, uno as sn_uno

SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
ITEM, JT, WF = "Preparar red para clúster OpenShift", "Red · Preparar red OpenShift", "Red · Preparar red OpenShift con aprobación"
VENCE_SEG = int(os.environ.get("APROBACION_SEG", str(4 * 3600)))
paso = lambda t: print(f"   · {t}", flush=True)


def aap(m, p, d=None, tolerar=False):
    req = urllib.request.Request(f"https://localhost/api/controller/v2/{p}", method=m,
                                 data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": BASIC})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:500].decode(errors='replace')}")


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


def asegurar_aap(rec, nombre, datos):
    o = uno(rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


# 1. ServiceNow
cat = sn_uno("sc_catalog", "title=Service Catalog")["sys_id"]
categ = sn_uno("sc_category", f"title=Red^sc_catalog={cat}")["sys_id"]
item = asegurar("sc_cat_item", f"name={ITEM}", {
    "name": ITEM, "active": "true", "sc_catalogs": cat, "category": categ, "no_quantity": "true",
    "short_description": "Pide la red para un clúster OpenShift nuevo: segmento, accesos y nombres DNS.",
    "description": "<p>Indica el nombre del clúster y cuántos nodos tendrá. Te avisaremos en este mismo ticket "
                   "con el segmento asignado y las direcciones de la API y de las aplicaciones.</p>"})["sys_id"]
rx = {r["name"]: r["sys_id"] for r in tabla("question_regex", "", "sys_id,name", 100)}
VARS = [("cluster", "Nombre del clúster", "6", "ocp-demo", True, "Minúsculas, números y guiones (3 a 20). Ej.: ocp-demo", None),
        ("nodos", "Cantidad de nodos", "6", "3", True, "Ej.: 3 (compacto) o 6", rx.get("Number")),
        ("motivo", "Motivo de la solicitud", "2", "Nuevo clúster para el equipo de desarrollo", False, "", None)]
for orden, (n, q, t, d, ob, ayuda, r) in enumerate(VARS, start=1):
    datos = {"cat_item": item, "name": n, "question_text": q, "type": t, "default_value": d, "order": str(orden * 100),
             "mandatory": "true" if ob else "false", "help_text": ayuda, "active": "true"}
    if r:
        datos["validate_regex"] = r
    asegurar("item_option_new", f"cat_item={item}^name={n}", datos)
paso(f"ServiceNow: ítem '{ITEM}' · {URL}/sp?id=sc_cat_item&sys_id={item}")

# 2. AAP
base = uno("job_templates", name="Red · Solicitar acceso")
org, inv = base["organization"], base["inventory"]
# El DNS (172.20.20.30) se administra delegando desde el playbook: no se agrega al inventario (licencia de nodos).

tipo = asegurar_aap("credential_types", "Gitea · API (token)", {
    "kind": "cloud", "description": "URL y token de la API de Gitea (extra vars gitea_url, gitea_token)",
    "inputs": {"fields": [{"id": "url", "label": "URL de Gitea", "type": "string"},
                          {"id": "token", "label": "Token", "type": "string", "secret": True}], "required": ["url", "token"]},
    "injectors": {"extra_vars": {"gitea_url": "{{ url }}", "gitea_token": "{{ token }}"}}})["id"]
cred = asegurar_aap("credentials", "Gitea · escritura red-laboratorio", {
    "organization": org, "credential_type": tipo, "description": "Commits de AAP en invoke/red-laboratorio (IPAM y router)",
    "inputs": {"url": "http://192.168.122.1:3000", "token": SEC["gitea_token"]}})["id"]
paso("AAP: credencial 'Gitea · escritura red-laboratorio'")

jt = asegurar_aap("job_templates", JT, {
    "organization": org, "project": base["project"], "inventory": inv, "playbook": "playbooks/preparar-red-openshift.yml",
    "ask_variables_on_launch": True, "description": "Red para un clúster OpenShift: IPAM, router (Git), firewall y DNS"})["id"]
ya = {c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]}
quiero = [c["id"] for c in aap("GET", f"job_templates/{base['id']}/credentials/")["results"]]
quiero += [cred, uno("credentials", name="AAP · responder aprobaciones")["id"]]
for cid in quiero:
    if cid not in ya:
        aap("POST", f"job_templates/{jt}/credentials/", {"id": cid})
paso(f"AAP: plantilla '{JT}' (id {jt})")

wf = asegurar_aap("workflow_job_templates", WF, {
    "organization": org, "inventory": inv, "ask_variables_on_launch": True,
    "description": "Ticket de ServiceNow → cambios para revisar → aprobación en AAP → red del clúster y cierre del ticket"})["id"]
for n in aap("GET", f"workflow_job_templates/{wf}/workflow_nodes/?page_size=50")["results"]:
    aap("DELETE", f"workflow_job_template_nodes/{n['id']}/")
nodo = lambda ident, d: aap("POST", f"workflow_job_templates/{wf}/workflow_nodes/", dict(d, identifier=ident))["id"]
n_prep = nodo("preparar", {"unified_job_template": jt, "extra_data": {"osh_fase": "preparar"}})
n_apr = nodo("aprobacion", {})
aap("POST", f"workflow_job_template_nodes/{n_apr}/create_approval_template/", {
    "name": "¿Preparar la red del clúster OpenShift?",
    "description": "Revisa los cambios en el paso 'preparar' (o en las notas del ticket). Para rechazar con motivo usa 'Red · Responder solicitud'.",
    "timeout": VENCE_SEG})
n_apl = nodo("aplicar", {"unified_job_template": jt, "extra_data": {"osh_fase": "aplicar"}})
n_rec = nodo("no_aprobado", {"unified_job_template": jt, "extra_data": {"osh_fase": "rechazado"}})
aap("POST", f"workflow_job_template_nodes/{n_prep}/success_nodes/", {"id": n_apr})
aap("POST", f"workflow_job_template_nodes/{n_apr}/success_nodes/", {"id": n_apl})
aap("POST", f"workflow_job_template_nodes/{n_apr}/failure_nodes/", {"id": n_rec})
paso(f"AAP: workflow '{WF}' (id {wf})")
print("   LISTO")
