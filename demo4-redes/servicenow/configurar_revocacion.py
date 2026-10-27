#!/usr/bin/python3
# Demo 4 · Varios puertos/orígenes, vigencia y revocación de accesos. Idempotente. No imprime claves.
# 1) ServiceNow: "Solicitar acceso de red" acepta listas y rangos (sin la validación de número en Puerto) y suma
#    "Vigente hasta" (opcional). Ítem nuevo "Revocar acceso de red" (ticket original o origen/destino/puerto).
# 2) AAP: credenciales de Gitea y AAP en "Red · Solicitar acceso" (registro de accesos y detección de vencimiento),
#    plantilla "Red · Revocar acceso" + workflow con aprobación (y su aviso por correo),
#    plantilla "Red · Revocar accesos vencidos" con programación diaria.
# Después: configurar_eda_snow.py (rulebook con el evento ritm_revocar) y reiniciar el vigía de ServiceNow.
import base64, glob, json, os, re, ssl, sys, urllib.error, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sn import URL, asegurar, tabla, uno as sn_uno

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
ITEM, ITEM_RV = "Solicitar acceso de red", "Revocar acceso de red"
JT, WF, JT_V = "Red · Revocar acceso", "Red · Revocar acceso con aprobación", "Red · Revocar accesos vencidos"
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


def variables(item, lista):  # nombre, pregunta, tipo (6 texto, 2 multilínea, 9 fecha), defecto, obligatoria, ayuda, regex
    for orden, (n, q, t, d, ob, ayuda, r) in enumerate(lista, start=1):
        asegurar("item_option_new", f"cat_item={item}^name={n}", {
            "cat_item": item, "name": n, "question_text": q, "type": t, "default_value": d, "order": str(orden * 100),
            "mandatory": "true" if ob else "false", "help_text": ayuda, "active": "true", "validate_regex": r or ""})


# 1. ServiceNow
cat = sn_uno("sc_catalog", "title=Service Catalog")["sys_id"]
categ = sn_uno("sc_category", f"title=Red^sc_catalog={cat}")["sys_id"]
rx = {r["name"]: r["sys_id"] for r in tabla("question_regex", "", "sys_id,name", 100)}
item = sn_uno("sc_cat_item", f"name={ITEM}")["sys_id"]
asegurar("sc_cat_item", f"sys_id={item}", {
    "short_description": "Pide acceso desde una o más redes hacia un servidor por uno o más puertos.",
    "description": "<p>Indica desde dónde, hacia qué servidor y qué puertos necesitas. Puedes poner varios orígenes o puertos "
                   "separados por coma, o un rango de puertos (8000-8010). Si es temporal, indica hasta qué día. "
                   "Te avisaremos en este mismo ticket cuando quede habilitado.</p>"})
variables(item, [
    ("origen", "Origen (red o servidores)", "6", "10.40.40.0/24", True,
     "Una o más redes o IPs separadas por coma. Ej.: 10.40.40.0/24 · o 192.168.122.15, 192.168.122.18 · "
     "o el grupo «servidores-app» (los dos servidores de aplicación)", None),
    ("destino", "Servidor de destino (IP)", "6", "10.10.10.10", True, "Ej.: 10.10.10.10 (srv-app)", rx.get("IP Address")),
    ("puerto", "Puertos TCP", "6", "8080", True, "Uno, varios o un rango. Ej.: 8080 · 8080, 8443 · 8000-8010", None),
    ("motivo", "Motivo de la solicitud", "2", "Acceso de los usuarios a la app", False, "", None),
    ("vigente_hasta", "Vigente hasta (opcional)", "9", "", False,
     "Si el acceso es temporal, el último día que debe funcionar. Al día siguiente se retira solo.", None)])
paso(f"ServiceNow: '{ITEM}' acepta listas y rangos, con 'Vigente hasta'")

item_rv = asegurar("sc_cat_item", f"name={ITEM_RV}", {
    "name": ITEM_RV, "active": "true", "sc_catalogs": cat, "category": categ, "no_quantity": "true",
    "short_description": "Pide retirar un acceso de red que ya no se necesita.",
    "description": "<p>Indica el número del ticket con que se pidió el acceso (por ejemplo RITM0010023). Si no lo tienes, "
                   "indica el servidor de destino y, si quieres precisar, el origen y el puerto. "
                   "Te avisaremos en este mismo ticket cuando quede retirado.</p>"})["sys_id"]
variables(item_rv, [
    ("ticket_original", "Ticket con que se pidió el acceso", "6", "", False, "Ej.: RITM0010023", None),
    ("origen", "Origen (si no tienes el ticket)", "6", "", False, "Ej.: 10.40.40.0/24", None),
    ("destino", "Servidor de destino (si no tienes el ticket)", "6", "", False, "Ej.: 10.10.10.10", None),
    ("puerto", "Puerto (si no tienes el ticket)", "6", "", False, "Ej.: 8080", None),
    ("motivo", "Motivo", "2", "Ya no se necesita", False, "", None)])
paso(f"ServiceNow: ítem '{ITEM_RV}' · {URL}/sp?id=sc_cat_item&sys_id={item_rv}")

# 2. AAP
base = uno("job_templates", name="Red · Solicitar acceso")
org, inv = base["organization"], base["inventory"]
extra = [uno("credentials", name=n)["id"] for n in ("Gitea · escritura red-laboratorio", "AAP · responder aprobaciones")]
ya = {c["id"] for c in aap("GET", f"job_templates/{base['id']}/credentials/")["results"]}
for cid in extra:
    if cid not in ya:
        aap("POST", f"job_templates/{base['id']}/credentials/", {"id": cid})
quiero = [c["id"] for c in aap("GET", f"job_templates/{base['id']}/credentials/")["results"]]
paso("AAP: 'Red · Solicitar acceso' con credenciales de Gitea (registro de accesos) y AAP (vencimiento de la aprobación)")


def plantilla(nombre, playbook, desc):
    jt = asegurar_aap("job_templates", nombre, {"organization": org, "project": base["project"], "inventory": inv,
                                                "playbook": playbook, "ask_variables_on_launch": True, "description": desc})["id"]
    ya = {c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]}
    for cid in quiero:
        if cid not in ya:
            aap("POST", f"job_templates/{jt}/credentials/", {"id": cid})
    return jt


jt = plantilla(JT, "playbooks/revocar-acceso.yml", "Retira un acceso de red registrado y comprueba que quedó bloqueado")
paso(f"AAP: plantilla '{JT}' (id {jt})")
wf = asegurar_aap("workflow_job_templates", WF, {
    "organization": org, "inventory": inv, "ask_variables_on_launch": True,
    "description": "Ticket de ServiceNow → acceso a retirar → aprobación en AAP → firewall, prueba y cierre del ticket"})["id"]
for n in aap("GET", f"workflow_job_templates/{wf}/workflow_nodes/?page_size=50")["results"]:
    aap("DELETE", f"workflow_job_template_nodes/{n['id']}/")
nodo = lambda ident, d: aap("POST", f"workflow_job_templates/{wf}/workflow_nodes/", dict(d, identifier=ident))["id"]
n_prep = nodo("preparar", {"unified_job_template": jt, "extra_data": {"acceso_fase": "preparar"}})
n_apr = nodo("aprobacion", {})
aap("POST", f"workflow_job_template_nodes/{n_apr}/create_approval_template/", {
    "name": "¿Retirar el acceso de red?",
    "description": "Revisa el acceso y los comandos en el paso 'preparar' (o en las notas del ticket). Para rechazar con motivo usa 'Red · Responder solicitud'.",
    "timeout": VENCE_SEG})
n_apl = nodo("aplicar", {"unified_job_template": jt, "extra_data": {"acceso_fase": "aplicar"}})
n_rec = nodo("no_aprobado", {"unified_job_template": jt, "extra_data": {"acceso_fase": "rechazado"}})
aap("POST", f"workflow_job_template_nodes/{n_prep}/success_nodes/", {"id": n_apr})
aap("POST", f"workflow_job_template_nodes/{n_apr}/success_nodes/", {"id": n_apl})
aap("POST", f"workflow_job_template_nodes/{n_apr}/failure_nodes/", {"id": n_rec})
nt = uno("notification_templates", name="Correo · aprobaciones de red")
if nt:
    aap("POST", f"workflow_job_templates/{wf}/notification_templates_approvals/", {"id": nt["id"]}, tolerar=True)
paso(f"AAP: workflow '{WF}' (id {wf}) · preparar → aprobación → aplicar | no aprobado")

jv = plantilla(JT_V, "playbooks/revocar-vencidos.yml", "Todos los días: retira los accesos con 'vigente hasta' vencido")
asegurar_aap("schedules", "Diario · retirar accesos vencidos", {
    "unified_job_template": jv, "enabled": True, "description": "Todos los días a las 00:15 (hora de Chile)",
    "rrule": "DTSTART;TZID=America/Santiago:20261027T001500 RRULE:FREQ=DAILY;INTERVAL=1"} if not uno("schedules", name="Diario · retirar accesos vencidos")
    else {"enabled": True, "rrule": "DTSTART;TZID=America/Santiago:20261027T001500 RRULE:FREQ=DAILY;INTERVAL=1"})
paso(f"AAP: plantilla '{JT_V}' (id {jv}) con programación diaria 00:15")
print(f"   REVOCAR https://34.176.42.56/execution/templates/workflow-template/{wf}/details")
print("   LISTO")
