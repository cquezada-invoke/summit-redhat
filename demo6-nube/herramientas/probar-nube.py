#!/usr/bin/python3
# Demo 6 · prueba de punta a punta del catálogo "Nube": pide en ServiceNow como un usuario, espera la aprobación
# en AAP, la responde y muestra el estado final y lo que vio el usuario.
# Uso en la VM:
#   probar-nube.py vm nombre=prueba-web tamano=S [--decision=aprobar|rechazar|ninguna] [--limpiar]
#   probar-nube.py baja nombre=prueba-web [--limpiar]
#   probar-nube.py limpiar RITM0010030 [RITM...]     (borra RITM, REQ y tareas de esos tickets)
#   probar-nube.py incidentes                        (borra los incidentes "[Demo 6 · GCP]" de prueba)
import base64, glob, json, os, re, ssl, sys, time, urllib.parse, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "demo4-redes", "servicenow"))
from sn import api as sn, tabla, uno

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, CTX = "https://localhost/api/controller/v2", ssl._create_unverified_context()
H = {"Authorization": "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode(), "Content-Type": "application/json"}
ITEMS = {"vm": "Solicitar VM en la nube", "baja": "Dar de baja VM en la nube"}


def aap(m, p, d=None):
    r = urllib.request.urlopen(urllib.request.Request(f"{C}/{p}", method=m, headers=H,
                               data=json.dumps(d).encode() if d is not None else None), context=CTX, timeout=60)
    b = r.read()
    return json.loads(b) if b else {}


def limpiar(numero):
    r = uno("sc_req_item", f"number={numero}", "sys_id,request")
    if not r:
        return
    for t in tabla("sc_task", f"request_item={r['sys_id']}", "sys_id", 20):
        sn("DELETE", f"/api/now/table/sc_task/{t['sys_id']}", tolerar=True)
    sn("DELETE", f"/api/now/table/sc_req_item/{r['sys_id']}", tolerar=True)
    if r.get("request", {}).get("value"):
        sn("DELETE", f"/api/now/table/sc_request/{r['request']['value']}", tolerar=True)
    print(f"   borrado {numero}")


args = [a for a in sys.argv[1:] if not a.startswith("--")]
op = dict(a[2:].split("=", 1) if "=" in a else (a[2:], "1") for a in sys.argv[1:] if a.startswith("--"))
if args[0] == "limpiar":
    for n in args[1:]:
        limpiar(n)
    sys.exit(0)
if args[0] == "incidentes":
    for i in tabla("incident", "short_descriptionSTARTSWITH[Demo 6 · GCP]", "sys_id,number", 100):
        sn("DELETE", f"/api/now/table/incident/{i['sys_id']}", tolerar=True)
        print(f"   borrado {i['number']}")
    sys.exit(0)
item = uno("sc_cat_item", f"name={ITEMS[args[0]]}")["sys_id"]
variables = dict(a.split("=", 1) for a in args[1:])
yo = uno("sys_user", "user_name=admin")["sys_id"]
req = sn("POST", f"/api/sn_sc/servicecatalog/items/{item}/order_now",
         {"sysparm_quantity": "1", "sysparm_requested_for": yo, "variables": variables})["result"]
time.sleep(2)
ritm = uno("sc_req_item", f"request={req['sys_id']}", "sys_id,number")
print(f"== {ritm['number']} · {ITEMS[args[0]]} · {variables}", flush=True)
decision, t0, respondido = op.get("decision", "aprobar"), time.time(), False
while time.time() - t0 < 1500:
    time.sleep(10)
    r = uno("sc_req_item", f"sys_id={ritm['sys_id']}", "state,active")
    if r["active"] == "false":
        break
    if not respondido:
        for a in aap("GET", "workflow_approvals/?status=pending&order_by=-id&page_size=20")["results"]:
            wj = a.get("summary_fields", {}).get("source_workflow_job", {}).get("id")
            ev = json.loads(aap("GET", f"workflow_jobs/{wj}/").get("extra_vars") or "{}") if wj else {}
            if ev.get("snow_ritm") == ritm["number"]:
                print(f"   aprobación pendiente a los {int(time.time() - t0)} s → {decision}", flush=True)
                if decision in ("aprobar", "rechazar"):
                    aap("POST", f"workflow_approvals/{a['id']}/{'approve' if decision == 'aprobar' else 'deny'}/", {})
                respondido = True
r = uno("sc_req_item", f"sys_id={ritm['sys_id']}", "state,active,stage,close_notes")
print(f"   ESTADO: {r}  ({int(time.time() - t0)} s)")
for x in tabla("sys_journal_field", f"element_id={ritm['sys_id']}^ORDERBYsys_created_on", "element,value", 50):
    print(f"    {x['element']:9} | {x['value'][:300].replace(chr(10), ' / ')}")
if "limpiar" in op:
    limpiar(ritm["number"])
