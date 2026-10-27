#!/usr/bin/python3
# Demo 4 · "Plataforma · Actualizar firmware" en AAP (idempotente). Se ejecuta en la VM después de herramientas/instalar-sucursales.sh.
#   1) Inventario "Red · laboratorio": grupo sucursales (suc1-3, operados en el anfitrión con el usuario aap-lab, sudo limitado
#      a /usr/local/sbin/lab-sucursal) y grupo servidores_parches (nodo-so, nodo-nginx). No entran en "gestionados" (drift).
#   2) Plantilla "Red · Actualizar firmware" (playbooks/actualizar-firmware.yml).
#   3) Workflow "Plataforma · Actualizar firmware": preparar → aprobación (4 h) → aplicar | no aprobado, con formulario.
#   4) Notificador "Correo · aprobaciones de plataforma": copia del de red (misma cuenta y contraseña) con su propio texto.
import base64, glob, json, os, re, ssl, sys, urllib.error, urllib.parse, urllib.request

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
JT, WF, NOTIF = "Red · Actualizar firmware", "Plataforma · Actualizar firmware", "Correo · aprobaciones de plataforma"


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
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:600].decode(errors='replace')}")


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos, **filtro):
    o = uno(rec, name=nombre, **filtro)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


def paso(t):
    print(f"   · {t}")


# 1. Inventario
inv = uno("inventories", name="Red · laboratorio")
org = inv["organization"]


def grupo(nombre, descripcion, hosts):
    g = asegurar("groups", nombre, {"inventory": inv["id"], "description": descripcion}, inventory=inv["id"])["id"]
    ya = {h["name"] for h in aap("GET", f"groups/{g}/hosts/?page_size=100")["results"]}
    for n, v in hosts.items():
        h = asegurar("hosts", n, {"inventory": inv["id"], "variables": json.dumps(v)}, inventory=inv["id"])
        if n not in ya:
            aap("POST", f"groups/{g}/hosts/", {"id": h["id"]})
    paso(f"inventario: grupo {nombre} ({', '.join(hosts)})")


grupo("sucursales", "Routers de sucursal FRR (containerlab, topología summit-suc). Se operan en el anfitrión con lab-sucursal.",
      {f"suc{n}": {"ansible_host": "192.168.122.1", "ansible_user": "aap-lab", "lab_nodo": f"suc{n}",
                   "lab_ip": f"172.20.20.4{n}", "lab_red": f"10.30.{n}.0/24"} for n in (1, 2, 3)})
grupo("servidores_parches", "Servidores RHEL de las demos que reciben parches de seguridad del SO (firmware)",
      {"nodo-so": {"ansible_host": "192.168.122.12", "ansible_user": "ansible"},
       "nodo-nginx": {"ansible_host": "192.168.122.11", "ansible_user": "ansible"}})

# 2. Plantilla
base = uno("job_templates", name="Red · Aplicar configuración")
jt = asegurar("job_templates", JT, {
    "organization": org, "inventory": inv["id"], "project": base["project"], "playbook": "playbooks/actualizar-firmware.yml",
    "ask_variables_on_launch": True, "execution_environment": base.get("execution_environment"),
    "description": "Firmware del parque (routers de sucursal FRR y parches de servidores RHEL) con cambio en ServiceNow"})["id"]
ya = {c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]}
for nombre in ("Red · laboratorio (ssh)", "ServiceNow · ansible.automatizacion", "Gitea · registro de estado"):
    c = uno("credentials", name=nombre)
    if not c:
        sys.exit(f"ERROR falta la credencial '{nombre}'")
    if c["id"] not in ya:
        aap("POST", f"job_templates/{jt}/credentials/", {"id": c["id"]})
paso(f"plantilla '{JT}' (id {jt})")

# 3. Workflow con aprobación y formulario
wf = asegurar("workflow_job_templates", WF, {
    "organization": org, "survey_enabled": True, "ask_variables_on_launch": False,
    "description": "Cambio en ServiceNow → plan para revisar → aprobación en AAP → canario de cada tipo y luego el resto"})["id"]
for n in aap("GET", f"workflow_job_templates/{wf}/workflow_nodes/?page_size=50")["results"]:
    aap("DELETE", f"workflow_job_template_nodes/{n['id']}/")
nodo = lambda ident, d: aap("POST", f"workflow_job_templates/{wf}/workflow_nodes/", dict(d, identifier=ident))["id"]
n_prep = nodo("preparar", {"unified_job_template": jt, "extra_data": {"fw_fase": "preparar"}})
n_apr = nodo("aprobacion", {})
aap("POST", f"workflow_job_template_nodes/{n_apr}/create_approval_template/", {
    "name": "¿Actualizar firmware del parque?", "timeout": 14400,
    "description": "Revisa el plan y el cambio de ServiceNow en el paso 'preparar'. Si vence, el cambio se cancela sin tocar equipos."})
n_apl = nodo("aplicar", {"unified_job_template": jt, "extra_data": {"fw_fase": "aplicar"}})
n_rec = nodo("no_aprobado", {"unified_job_template": jt, "extra_data": {"fw_fase": "rechazado"}})
aap("POST", f"workflow_job_template_nodes/{n_prep}/success_nodes/", {"id": n_apr})
aap("POST", f"workflow_job_template_nodes/{n_apr}/success_nodes/", {"id": n_apl})
aap("POST", f"workflow_job_template_nodes/{n_apr}/failure_nodes/", {"id": n_rec})
aap("POST", f"workflow_job_templates/{wf}/survey_spec/", {
    "name": "Actualizar firmware", "description": "Parque mixto: routers de sucursal y servidores RHEL", "spec": [
        {"question_name": "Versión de FRR para los routers de sucursal", "variable": "fw_version", "type": "multiplechoice",
         "choices": ["10.2.1", "10.2.1 defectuosa (para mostrar la reversión)"], "default": "10.2.1", "required": True,
         "question_description": "La imagen defectuosa no levanta BGP: el canario falla, vuelve a 9.1 y se detiene el resto."},
        {"question_name": "¿Incluir los servidores RHEL (nodo-so, nodo-nginx)?", "variable": "fw_servidores",
         "type": "multiplechoice", "choices": ["Sí", "No"], "default": "Sí", "required": True},
        {"question_name": "Parches de los servidores", "variable": "fw_parches", "type": "multiplechoice",
         "choices": ["Críticos", "Todos los de seguridad"], "default": "Críticos", "required": True,
         "question_description": "Críticos es lo más rápido para la demo en vivo."}]})
paso(f"workflow '{WF}' (id {wf}) · https://34.176.42.56/execution/templates/workflow-template/{wf}/details")

# 4. Aviso por correo de la aprobación (copia del notificador de red: no se toca el original ni su contraseña)
mensajes = {"workflow_approval": {
    "running": {"message": "Por aprobar: actualización de firmware del parque",
                "body": "Hola Cristian,\n\nHay una actualización de firmware esperando tu aprobación (routers de sucursal y, si se "
                        "eligió, servidores RHEL). El plan, el orden (canario de cada tipo primero) y el cambio de ServiceNow están en el "
                        "paso 'preparar':\n{{ workflow_url }}\n\nAprueba o rechaza en Ansible Automation Platform → Workflow Approvals.\n"
                        "Si nadie responde en 4 horas, el cambio se cancela sin tocar equipos.\n"},
    "approved": {"message": "Aprobado: actualización de firmware del parque",
                 "body": "Aprobaste la actualización. Va equipo por equipo y el cambio de ServiceNow se cerrará solo.\n\n{{ workflow_url }}\n"},
    "denied": {"message": "Rechazado: actualización de firmware del parque",
               "body": "Rechazaste la actualización. No se tocó ningún equipo y el cambio de ServiceNow quedó cancelado.\n\n{{ workflow_url }}\n"},
    "timed_out": {"message": "Vencido: actualización de firmware del parque",
                  "body": "Nadie aprobó a tiempo. No se tocó ningún equipo y el cambio de ServiceNow quedó cancelado.\n\n{{ workflow_url }}\n"}}}
nt = uno("notification_templates", name=NOTIF)
if not nt:
    orig = uno("notification_templates", name="Correo · aprobaciones de red")
    if orig:
        nt = aap("POST", f"notification_templates/{orig['id']}/copy/", {"name": NOTIF}, tolerar=True)
if nt:
    aap("PATCH", f"notification_templates/{nt['id']}/", {"messages": mensajes,
                                                          "description": "Avisa a Cristian cuando una actualización de firmware espera aprobación"})
    aap("POST", f"workflow_job_templates/{wf}/notification_templates_approvals/", {"id": nt["id"]}, tolerar=True)
    paso(f"notificador '{NOTIF}' (id {nt['id']}) en las aprobaciones del workflow")
else:
    paso("AVISO: no se pudo crear el notificador (falta 'Correo · aprobaciones de red')")
print("LISTO")
