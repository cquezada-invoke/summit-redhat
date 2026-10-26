#!/usr/bin/python3
# Vigía de ServiceNow (Demo 4): cada pocos segundos busca tickets nuevos del ítem "Solicitar acceso de red"
# y los entrega como evento a Event-Driven Ansible (webhook :5002). EDA decide y lanza "Red · Solicitar acceso".
# Para no atender dos veces el mismo ticket, al tomarlo lo deja "En curso" (Work in Progress) con una nota.
# También vigila los incidentes de seguridad que abre "Red · Bloquear IP": cuando se resuelven, avisa a EDA
# (webhook :5003) para que AAP levante el bloqueo si es "Falso positivo".
# En producción este papel lo cumple la fuente de eventos servicenow.itsm.records o un mensaje saliente de ServiceNow.
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sn import URL, api, tabla, uno

EDA = os.environ.get("EDA_URL", "http://127.0.0.1:5002/endpoint")
EDA_SEG = os.environ.get("EDA_SEG_URL", "http://127.0.0.1:5003/endpoint")
CADA = int(os.environ.get("CADA_S", "10"))
ITEM = "Solicitar acceso de red"
log = lambda t: print(time.strftime("%H:%M:%S"), t, flush=True)


def variables(ritm):
    r = tabla("sc_item_option_mtom", f"request_item={ritm}",
              "sc_item_option.item_option_new.name,sc_item_option.value", 20)
    return {x["sc_item_option.item_option_new.name"]: x["sc_item_option.value"] for x in r}


def avisar_eda(evento, url=None):
    req = urllib.request.Request(url or EDA, data=json.dumps(evento).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


item = None
while True:
    try:
        if not item:
            item = uno("sc_cat_item", f"name={ITEM}")["sys_id"]
            log(f"vigilando '{ITEM}' en {URL}")
        nuevos = tabla("sc_req_item", f"cat_item={item}^active=true^stateIN-5,1^ORDERBYsys_created_on",
                       "sys_id,number,sys_created_on,request,requested_for.user_name,requested_for.name,opened_by.user_name", 10)
        for t in nuevos:
            v = variables(t["sys_id"])
            quien = t.get("requested_for.user_name") or t.get("opened_by.user_name") or "desconocido"
            # 1) tomarlo: En curso + nota (evita la doble ejecución)
            api("PATCH", f"/api/now/table/sc_req_item/{t['sys_id']}", {
                "state": "2",
                "work_notes": "Event-Driven Ansible detectó esta solicitud y la entregó a Ansible Automation Platform."})
            evento = {"tipo": "ritm_nuevo", "ritm": t["number"], "ritm_sys_id": t["sys_id"], "creado": t["sys_created_on"],
                      "solicitante": quien, "solicitante_nombre": t.get("requested_for.name", ""),
                      "origen": v.get("origen", ""), "destino": v.get("destino", ""), "puerto": v.get("puerto", ""),
                      "motivo": v.get("motivo", "")}
            try:
                avisar_eda(evento)
                log(f"{t['number']} → EDA ({evento['origen']} → {evento['destino']}:{evento['puerto']}, {quien})")
            except Exception as e:  # EDA no disponible: el ticket vuelve a quedar abierto para reintentar
                api("PATCH", f"/api/now/table/sc_req_item/{t['sys_id']}", {
                    "state": "1", "work_notes": f"No se pudo entregar a Event-Driven Ansible ({e}). Se reintentará."})
                log(f"{t['number']}: EDA no respondió ({e})")
        # Incidentes de seguridad resueltos (Demo 4): se entregan una sola vez (correlation_id → :revisando)
        for inc in tabla("incident", "correlation_idSTARTSWITHsummit-bloqueo:^correlation_idNOT LIKE:revis^stateIN6,7",
                         "sys_id,number,correlation_id,close_code,resolved_by.user_name", 10):
            ip = inc["correlation_id"].split(":", 1)[1]
            api("PATCH", f"/api/now/table/incident/{inc['sys_id']}", {"correlation_id": f"summit-bloqueo:{ip}:revisando"})
            fp = (inc.get("close_code") or "").strip().lower() == "falso positivo"
            try:
                avisar_eda({"tipo": "incidente_seguridad_resuelto", "incidente": inc["number"], "incidente_sys_id": inc["sys_id"],
                            "ip": ip, "falso_positivo": fp, "resuelto_por": inc.get("resolved_by.user_name", "")}, EDA_SEG)
                log(f"{inc['number']} resuelto ({inc.get('close_code')}) → EDA · {ip}")
            except Exception as e:
                api("PATCH", f"/api/now/table/incident/{inc['sys_id']}", {"correlation_id": f"summit-bloqueo:{ip}"})
                log(f"{inc['number']}: EDA seguridad no respondió ({e})")
    except SystemExit as e:
        log(f"ServiceNow: {e}")
    except Exception as e:
        log(f"aviso: {e}")
    time.sleep(CADA)
