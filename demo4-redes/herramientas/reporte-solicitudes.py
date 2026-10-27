#!/usr/bin/python3
# Demo 4 · reporte de las solicitudes de red atendidas por la automatización (últimos N días, por defecto 7):
# cuántas terminaron Completado / Rechazado / Timeout / Fallido y escalado, y cuáles fallidas siguen abiertas
# (asignadas al grupo "Automatización Ansible"). Lee la nota "Resultado: …" que deja cada ticket.
# Uso en la VM: python3 ~/summit-redhat/demo4-redes/herramientas/reporte-solicitudes.py [días]
import collections, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servicenow"))
from sn import URL, tabla, uno

DIAS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
ITEMS = ["Solicitar acceso de red", "Revocar acceso de red", "Preparar red para clúster OpenShift"]
cuenta, fallidos = collections.Counter(), []
for nombre in ITEMS:
    item = uno("sc_cat_item", f"name={nombre}")
    if not item:
        continue
    for r in tabla("sc_req_item", f"cat_item={item['sys_id']}^sys_created_on>=javascript:gs.daysAgoStart({DIAS})",
                   "sys_id,number,active,state,short_description", 500):
        notas = [n["value"] for n in tabla("sys_journal_field", f"element_id={r['sys_id']}^element=work_notes^valueLIKEResultado:", "value", 5)]
        res = next((n.split("Resultado:", 1)[1].split("\n")[0].split("(")[0].strip() for n in notas), "En curso")
        cuenta[(nombre, res)] += 1
        if res.startswith("Fallido") and r["active"] == "true":
            fallidos.append((r["number"], nombre))
print(f"Solicitudes de red de los últimos {DIAS} días ({URL})")
for nombre in ITEMS:
    fila = {res: n for (it, res), n in cuenta.items() if it == nombre}
    if fila:
        print(f"  {nombre}: " + " · ".join(f"{k} {v}" for k, v in sorted(fila.items())))
print("Fallidas y escaladas, todavía abiertas:" if fallidos else "Sin solicitudes fallidas abiertas.")
for n, it in fallidos:
    print(f"  {n} ({it}) · {URL}/sc_req_item.do?sysparm_query=number={n}")
