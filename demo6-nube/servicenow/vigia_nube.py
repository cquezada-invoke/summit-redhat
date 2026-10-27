#!/usr/bin/python3
# Vigía de ServiceNow (Demo 6 · nube): cada pocos segundos busca tickets nuevos de "Solicitar VM en la nube" y
# "Dar de baja VM en la nube" y los entrega como evento a Event-Driven Ansible (webhook :5010).
# Al tomarlo lo deja "En curso" con una nota para no atenderlo dos veces; si EDA no responde, lo devuelve.
# En producción este papel lo cumple la fuente de eventos servicenow.itsm.records o un mensaje saliente de ServiceNow.
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "demo4-redes", "servicenow"))
from sn import URL, api, tabla, uno

EDA = os.environ.get("EDA_URL", "http://127.0.0.1:5010/endpoint")
CADA = int(os.environ.get("CADA_S", "10"))
ITEMS = {"Solicitar VM en la nube": "vm_nueva", "Dar de baja VM en la nube": "vm_baja"}
log = lambda t: print(time.strftime("%H:%M:%S"), t, flush=True)


def variables(ritm):
    r = tabla("sc_item_option_mtom", f"request_item={ritm}",
              "sc_item_option.item_option_new.name,sc_item_option.value", 20)
    return {x["sc_item_option.item_option_new.name"]: x["sc_item_option.value"] for x in r}


def avisar_eda(evento):
    req = urllib.request.Request(EDA, data=json.dumps(evento).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


ids = {}
log(f"vigilando {', '.join(ITEMS)} en {URL}")
while True:
    try:
        for nombre, tipo in ITEMS.items():
            if not ids.get(nombre):
                o = uno("sc_cat_item", f"name={nombre}")
                ids[nombre] = o["sys_id"] if o else None
            if not ids[nombre]:
                continue
            for t in tabla("sc_req_item", f"cat_item={ids[nombre]}^active=true^stateIN-5,1^ORDERBYsys_created_on",
                           "sys_id,number,sys_created_on,requested_for.user_name,opened_by.user_name", 10):
                v = variables(t["sys_id"])
                quien = t.get("requested_for.user_name") or t.get("opened_by.user_name") or "desconocido"
                api("PATCH", f"/api/now/table/sc_req_item/{t['sys_id']}", {
                    "state": "2",
                    "work_notes": "Event-Driven Ansible detectó esta solicitud y la entregó a Ansible Automation Platform."})
                evento = {"tipo": tipo, "ritm": t["number"], "ritm_sys_id": t["sys_id"], "creado": t["sys_created_on"],
                          "solicitante": quien, "nombre": v.get("nombre", ""), "tamano": v.get("tamano", "S"),
                          "motivo": v.get("motivo", "")}
                try:
                    avisar_eda(evento)
                    log(f"{t['number']} → EDA ({tipo} {evento['nombre']} {evento['tamano'] if tipo == 'vm_nueva' else ''}, {quien})")
                except Exception as e:
                    api("PATCH", f"/api/now/table/sc_req_item/{t['sys_id']}", {
                        "state": "1", "work_notes": f"No se pudo entregar a Event-Driven Ansible ({e}). Se reintentará."})
                    log(f"{t['number']}: EDA no respondió ({e})")
    except SystemExit as e:
        log(f"ServiceNow: {e}")
    except Exception as e:
        log(f"aviso: {e}")
    time.sleep(CADA)
