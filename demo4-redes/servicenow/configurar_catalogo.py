#!/usr/bin/python3
# Demo 4 · ServiceNow: crea (idempotente) el ítem de catálogo "Solicitar acceso de red" en el Service Catalog,
# categoría "Red", con sus variables (origen, destino, puerto, motivo). El usuario lo pide como cualquier ticket;
# el vigía de ServiceNow lo entrega a Event-Driven Ansible y AAP lo atiende y lo cierra.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sn import URL, asegurar, tabla, uno

NOMBRE = "Solicitar acceso de red"
paso = lambda t: print(f"   · {t}", flush=True)

cat = uno("sc_catalog", "title=Service Catalog")["sys_id"]
categ = asegurar("sc_category", f"title=Red^sc_catalog={cat}", {
    "title": "Red", "sc_catalog": cat, "active": "true",
    "description": "Accesos y conectividad de red"})["sys_id"]
paso("Categoría 'Red' en Service Catalog")

item = asegurar("sc_cat_item", f"name={NOMBRE}", {
    "name": NOMBRE, "active": "true", "sc_catalogs": cat, "category": categ, "no_quantity": "true",
    "short_description": "Pide acceso desde una red hacia un servidor por un puerto.",
    "description": "<p>Indica desde dónde, hacia qué servidor y qué puerto necesitas. "
                   "Te avisaremos en este mismo ticket cuando quede habilitado.</p>"})["sys_id"]
paso(f"Ítem de catálogo '{NOMBRE}'")

# Validaciones simples con las expresiones regulares estándar de ServiceNow (si existen en la instancia)
rx = {r["name"]: r["sys_id"] for r in tabla("question_regex", "", "sys_id,name", 100)}
VARS = [  # nombre, pregunta, tipo (6 = texto de una línea, 2 = texto multilínea), defecto, obligatoria, ayuda, regex
    ("origen", "Red de origen (CIDR)", "6", "10.40.40.0/24", True, "Ej.: 10.40.40.0/24 (usuarios detrás del firewall)", None),
    ("destino", "Servidor de destino (IP)", "6", "10.10.10.10", True, "Ej.: 10.10.10.10 (srv-app)", rx.get("IP Address")),
    ("puerto", "Puerto TCP", "6", "8080", True, "Ej.: 8080", rx.get("Number")),
    ("motivo", "Motivo de la solicitud", "2", "Acceso de los usuarios a la app", False, "", None),
]
for orden, (n, q, t, d, ob, ayuda, r) in enumerate(VARS, start=1):
    datos = {"cat_item": item, "name": n, "question_text": q, "type": t, "default_value": d, "order": str(orden * 100),
             "mandatory": "true" if ob else "false", "help_text": ayuda, "active": "true"}
    if r:
        datos["validate_regex"] = r
    asegurar("item_option_new", f"cat_item={item}^name={n}", datos)
paso("Variables: origen, destino, puerto, motivo")

print(f"   ITEM {item}")
print(f"   FORMULARIO {URL}/sp?id=sc_cat_item&sys_id={item}")
print(f"   FORMULARIO_CLASICO {URL}/com.glideapp.servicecatalog_cat_item_view.do?v=1&sysparm_id={item}")
print(f"   MIS_SOLICITUDES {URL}/sp?id=requests")
