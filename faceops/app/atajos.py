"""Atajos de teclado: acciones disponibles, teclas por defecto y las que cada usuario configura (se guardan en su usuario)."""
import json

from flask import url_for

# (id, título, endpoint, argumentos)
ACCIONES = [
    ("nueva_maquina", "Nueva máquina", "consola.inventario", {"nueva": 1}),
    ("tickets", "Buscar tickets", "consola.tickets", {"buscar": 1}),
    ("inventario", "Buscar en el inventario", "consola.inventario", {"buscar": 1}),
    ("automatizaciones", "Buscar automatizaciones", "consola.automatizaciones", {"buscar": 1}),
    ("panel", "Panel", "consola.inicio", {}),
    ("incidentes", "Incidentes", "consola.incidentes", {"buscar": 1}),
    ("certificados", "Certificados", "consola.certificados", {}),
    ("ciclo_vida", "Ciclo de vida", "consola.ciclo_vida", {}),
    ("buscar", "Ir al buscador de esta página", None, {}),
    ("ayuda", "Ver los atajos", None, {}),
]
DEFECTO = {"nueva_maquina": ["F1", "Alt+1"], "tickets": ["F2", "Alt+2"], "inventario": ["F3", "Alt+3"],
           "automatizaciones": ["F4", "Alt+4"], "buscar": ["/"], "ayuda": ["?"]}
MAX_TECLAS = 2


def del_usuario(u):
    """[{id, titulo, url, teclas}] con las teclas del usuario (o las por defecto)."""
    try:
        propios = json.loads((u or {}).get("atajos") or "{}")
    except (ValueError, TypeError):
        propios = {}
    out = []
    for k, t, ep, args in ACCIONES:
        try:
            url = url_for(ep, **args) if ep else ""
        except Exception:  # noqa: BLE001
            url = ""
        out.append({"id": k, "titulo": t, "url": url, "teclas": propios.get(k, DEFECTO.get(k, []))[:MAX_TECLAS]})
    return out


def validar(datos):
    """{id: [combinación, …]} limpio; error si una combinación se repite."""
    ids = {a[0] for a in ACCIONES}
    limpio, usadas = {}, {}
    for k, v in (datos or {}).items():
        if k not in ids:
            continue
        teclas = []
        for x in (v or [])[:MAX_TECLAS]:
            x = str(x).strip()[:24]
            if not x or x in teclas:
                continue
            if x in usadas:
                raise ValueError(f"«{x}» ya está asignada a otra acción")
            usadas[x] = k
            teclas.append(x)
        limpio[k] = teclas
    return limpio
