#!/usr/bin/python3
# Demo 6 · ServiceNow: categoría "Nube" con los ítems "Solicitar VM en la nube" y "Dar de baja VM en la nube".
# Idempotente. El vigía de la nube entrega cada ticket a Event-Driven Ansible y AAP lo atiende y lo cierra.
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "demo4-redes", "servicenow"))
from sn import URL, asegurar, uno

paso = lambda t: print(f"   · {t}", flush=True)
cat = uno("sc_catalog", "title=Service Catalog")["sys_id"]
categ = asegurar("sc_category", f"title=Nube^sc_catalog={cat}", {
    "title": "Nube", "sc_catalog": cat, "active": "true", "description": "Servidores en la nube (Google Cloud)"})["sys_id"]
paso("Categoría 'Nube' en Service Catalog")

ITEMS = {
    "Solicitar VM en la nube": (
        "Pide un servidor Linux en la nube, listo y protegido.",
        "<p>Elige un nombre y un tamaño. Tu pedido pasa por aprobación y te avisaremos en este mismo ticket "
        "cuando el servidor esté listo, con su dirección.</p>",
        [("nombre", "Nombre del servidor", "6", "", True, "Solo minúsculas, números y guiones (3 a 30). Ej.: ventas-api"),
         ("tamano", "Tamaño", "5", "S", True, "S: 2 GB de memoria · M: 4 GB de memoria"),
         ("motivo", "¿Para qué lo necesitas?", "2", "", False, "")]),
    "Dar de baja VM en la nube": (
        "Elimina un servidor en la nube que ya no usas.",
        "<p>Indica el nombre del servidor. Lo eliminaremos con su disco y dejará de generar costo; "
        "te avisaremos en este ticket.</p>",
        [("nombre", "Nombre del servidor", "6", "", True, "El mismo nombre con que lo pediste. Ej.: ventas-api"),
         ("motivo", "Motivo", "2", "", False, "")]),
}
for nombre, (corta, desc, variables) in ITEMS.items():
    item = asegurar("sc_cat_item", f"name={nombre}", {
        "name": nombre, "active": "true", "sc_catalogs": cat, "category": categ, "no_quantity": "true",
        "short_description": corta, "description": desc})["sys_id"]
    for orden, (n, q, t, d, ob, ayuda) in enumerate(variables, start=1):
        v = asegurar("item_option_new", f"cat_item={item}^name={n}", {
            "cat_item": item, "name": n, "question_text": q, "type": t, "default_value": d, "order": str(orden * 100),
            "mandatory": "true" if ob else "false", "help_text": ayuda, "active": "true"})["sys_id"]
        if t == "5":  # Select box: opciones S y M
            for i, (val, txt) in enumerate((("S", "S · 1 vCPU, 2 GB"), ("M", "M · 1 vCPU, 4 GB")), start=1):
                asegurar("question_choice", f"question={v}^value={val}",
                         {"question": v, "value": val, "text": txt, "order": str(i * 100)})
    paso(f"Ítem '{nombre}' ({', '.join(x[0] for x in variables)})")
    print(f"   FORMULARIO {URL}/sp?id=sc_cat_item&sys_id={item}")
