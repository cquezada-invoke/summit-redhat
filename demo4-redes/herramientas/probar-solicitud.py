#!/usr/bin/python3
# Demo 4 · prueba de punta a punta de un ticket de red: lo pide en ServiceNow como un usuario, espera la aprobación,
# responde (Aprobar / Rechazar / nada) y muestra el estado final y lo que vio el usuario.
# Uso en la VM:
#   probar-solicitud.py acceso origen=10.40.40.0/24 destino=10.10.10.10 puerto="8080, 8443" [vigente_hasta=2026-12-31]
#   probar-solicitud.py revocar ticket_original=RITM0010023
#   opciones: --decision=Aprobar|Rechazar|ninguna  --motivo=texto  --limpiar (borra los registros del ticket al final)
#   probar-solicitud.py limpiar RITM0010023 [RITM...]   (borra RITM, REQ y tareas de esos tickets)
import base64, glob, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servicenow"))
from sn import api as sn, tabla, uno

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, CTX = "https://localhost/api/controller/v2", ssl._create_unverified_context()
H = {"Authorization": "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode(), "Content-Type": "application/json"}
ITEMS = {"acceso": "Solicitar acceso de red", "revocar": "Revocar acceso de red"}


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


def diario(sid):
    return tabla("sys_journal_field", f"element_id={sid}^ORDERBYsys_created_on", "element,value", 50)


args = [a for a in sys.argv[1:] if not a.startswith("--")]
op = dict(a[2:].split("=", 1) if "=" in a else (a[2:], "1") for a in sys.argv[1:] if a.startswith("--"))
if args[0] == "limpiar":
    for n in args[1:]:
        limpiar(n)
    sys.exit(0)
item = uno("sc_cat_item", f"name={ITEMS[args[0]]}")["sys_id"]
variables = dict(a.split("=", 1) for a in args[1:])
yo = uno("sys_user", "user_name=admin")["sys_id"]
req = sn("POST", f"/api/sn_sc/servicecatalog/items/{item}/order_now",
         {"sysparm_quantity": "1", "sysparm_requested_for": yo, "variables": variables})["result"]
time.sleep(2)
ritm = uno("sc_req_item", f"request={req['sys_id']}", "sys_id,number")
print(f"== {ritm['number']} · {ITEMS[args[0]]} · {variables}", flush=True)
decision, t0, respondido = op.get("decision", "Aprobar"), time.time(), False
while time.time() - t0 < 900:
    time.sleep(10)
    r = uno("sc_req_item", f"sys_id={ritm['sys_id']}", "state,active,stage")
    notas = " ".join(x["value"] for x in diario(ritm["sys_id"]))
    if not respondido and "Pendiente de aprobación" in notas:
        print(f"   aprobación pendiente a los {int(time.time() - t0)} s → {decision}", flush=True)
        if decision != "ninguna":
            jt = aap("GET", "job_templates/?name=" + urllib.parse.quote("Red · Responder solicitud"))["results"][0]["id"]
            j = aap("POST", f"job_templates/{jt}/launch/", {"extra_vars": {
                "resp_ticket": ritm["number"], "resp_decision": decision, "resp_motivo": op.get("motivo", "" if decision == "Aprobar" else "prueba")}})["job"]
            print(f"   responder: job {j}", flush=True)
        respondido = True
    if "Resultado:" in notas:
        time.sleep(5)
        break
r = uno("sc_req_item", f"sys_id={ritm['sys_id']}", "state,active,stage,assignment_group.name,calendar_duration")
print(f"   ESTADO: {r}  ({int(time.time() - t0)} s)")
for x in diario(ritm["sys_id"]):
    print(f"    {x['element']:9} | {x['value'][:400].replace(chr(10), ' / ')}")
print(f"   RITM {ritm['number']}")
if "limpiar" in op:
    limpiar(ritm["number"])
