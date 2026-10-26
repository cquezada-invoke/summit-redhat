#!/usr/bin/python3
# Demo 4 · caso "IP sospechosa" (seguridad). Idempotente.
# 1) ServiceNow: códigos de resolución de incidente "Falso positivo" y "Ataque confirmado".
# 2) AAP: plantilla "Red · Bloquear IP" (playbooks/bloquear-ip.yml) con correo a Cristian al terminar.
# 3) EDA: activación "Seguridad · Bloqueo de IP" (rulebooks/seguridad.yml, webhook :5003).
# 4) Reinicia el vigía de ServiceNow (también vigila los incidentes de seguridad resueltos).
import base64, glob, json, os, re, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sn import asegurar as sn_asegurar

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
JT, ACT = "Red · Bloquear IP", "Seguridad · Bloqueo de IP"
paso = lambda t: print(f"   · {t}", flush=True)


def http(base, m, p, d=None, tolerar=False):
    req = urllib.request.Request(f"https://localhost/api/{base}/{p}", method=m,
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


aap = lambda m, p, d=None, t=False: http("controller/v2", m, p, d, t)
eda = lambda m, p, d=None, t=False: http("eda/v1", m, p, d, t)


def uno(f, rec, **q):
    r = f("GET", f"{rec}/?" + urllib.parse.urlencode(q))["results"]
    return r[0] if r else None


# 1. ServiceNow
for orden, v in ((900, "Falso positivo"), (910, "Ataque confirmado")):
    sn_asegurar("sys_choice", f"name=incident^element=close_code^value={v}",
                {"name": "incident", "element": "close_code", "value": v, "label": v, "language": "en",
                 "sequence": str(orden), "inactive": "false"})
paso("ServiceNow: códigos de resolución 'Falso positivo' y 'Ataque confirmado'")

# 2. AAP
base = uno(aap, "job_templates", name="Red · Solicitar acceso")
datos = {"organization": base["organization"], "project": base["project"], "inventory": base["inventory"],
         "playbook": "playbooks/bloquear-ip.yml", "ask_variables_on_launch": True,
         "description": "Seguridad: bloquea una IP sospechosa en el firewall y abre un incidente (lo lanza EDA)"}
o = uno(aap, "job_templates", name=JT)
jt = (aap("PATCH", f"job_templates/{o['id']}/", datos) if o else aap("POST", "job_templates/", dict(datos, name=JT)))["id"]
ya = {c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]}
for c in aap("GET", f"job_templates/{base['id']}/credentials/")["results"]:
    if c["id"] not in ya:
        aap("POST", f"job_templates/{jt}/credentials/", {"id": c["id"]})
paso(f"AAP: plantilla '{JT}' (id {jt})")

nt = uno(aap, "notification_templates", name="Correo · aprobaciones de red")
if nt:
    def campo(n):
        return (f"(job_metadata.split('{n}\\\\\": \\\\\"')[1].split('\\\\\"')[0] "
                f"if '{n}\\\\\": \\\\\"' in job_metadata else '-')")
    S = "{%- set ip = " + campo("seg_ip") + " -%}{%- set f = " + campo("seg_fase") + " -%}{%- set fp = " + campo("seg_falso_positivo") + " -%}"
    LISTA = "https://dev459364.service-now.com/incident_list.do?sysparm_query=correlation_idSTARTSWITHsummit-bloqueo:{{ ip }}"
    msgs = nt.get("messages") or {}
    msgs["success"] = {
        "message": S + "{{ ('Bloqueada IP ' ~ ip ~ ' · incidente abierto') if f == 'bloquear' else (('Desbloqueada IP ' ~ ip ~ ' (falso positivo)') if fp in ['True', 'true'] else ('IP ' ~ ip ~ ' sigue bloqueada (ataque confirmado)')) }}",
        "body": S + "{% if f == 'bloquear' %}Hola Cristian,\n\nSe detectó actividad sospechosa desde la IP {{ ip }} y quedó bloqueada en el firewall.\n"
                    "Se abrió un incidente en ServiceNow para revisarlo:\n" + LISTA + "\n\n"
                    "Si es un falso positivo, resuélvelo con el código \"Falso positivo\" y el bloqueo se levanta solo.\n"
                    "{% else %}El incidente de la IP {{ ip }} se resolvió: "
                    "{{ 'falso positivo, el bloqueo se levantó.' if fp in ['True', 'true'] else 'ataque confirmado, la IP sigue bloqueada.' }}\n" + LISTA + "\n{% endif %}"
                    "\nDetalle técnico: {{ job_friendly_name }} {{ url }}\n"}
    msgs["error"] = {"message": S + "Falló el bloqueo de la IP {{ ip }}",
                     "body": S + "No se pudo completar el bloqueo de {{ ip }}. Revisa el job: {{ url }}\n"}
    aap("PATCH", f"notification_templates/{nt['id']}/", {"messages": msgs})
    for ev in ("success", "error"):
        aap("POST", f"job_templates/{jt}/notification_templates_{ev}/", {"id": nt["id"]}, True)
    paso("AAP: correo a cquezada@invoke.cl al bloquear o desbloquear")

# 3. EDA
org = (uno(eda, "organizations", name="Redes") or uno(eda, "organizations", name="Default"))["id"]
cred = uno(eda, "eda-credentials", name="AAP · lanzar plantillas de red")["id"]
p = uno(eda, "projects", name="Red · laboratorio")
eda("POST", f"projects/{p['id']}/sync/", {}, True)
rb = None
for _ in range(40):
    time.sleep(3)
    rb = uno(eda, "rulebooks", project_id=p["id"], name="seguridad.yml")
    if rb and eda("GET", f"projects/{p['id']}/")["import_state"] in ("completed", "failed"):
        break
if not rb:
    sys.exit("ERROR: el proyecto EDA no trae rulebooks/seguridad.yml")
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
    "name": ACT, "description": "Alerta de seguridad (SIEM) → bloquear IP; incidente resuelto → revisar el bloqueo",
    "is_enabled": True, "decision_environment_id": de, "rulebook_id": rb["id"], "eda_credentials": [cred],
    "organization_id": org, "restart_policy": "on-failure", "log_level": "info"})["id"]
for _ in range(60):
    st = eda("GET", f"activations/{act}/")["status"]
    if st in ("running", "failed", "error"):
        break
    time.sleep(5)
paso(f"EDA: activación '{ACT}' {st} (webhook :5003)")

# 4. Vigía
subprocess.run(["sudo", "systemctl", "restart", "vigia-servicenow.service"], check=True)
paso("Vigía de ServiceNow reiniciado (vigila también los incidentes de seguridad)")
print("   LISTO")
