"""Regla de prueba de automatizaciones: una automatización solo se puede usar si su versión vigente está certificada por un
plan de prueba ejecutado completo. La versión es una huella de lo que la define (playbook y los archivos que importa, su
ficha del catálogo y su formulario; en un workflow, la de cada nodo). Si algo cambia, la huella cambia y la automatización
queda bloqueada otra vez hasta volver a probarla. Los planes viven en datos/planes-prueba.yml (skill pruebas_automatizacion)."""
import hashlib
import json
import os
import re

import yaml

from .bd import ahora, auditar, ej, j, q, q1

RAIZ = os.path.dirname(os.path.dirname(__file__))
DIR_AAP = os.path.join(RAIZ, "aap")
_cache = {}


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS pruebas_automatizacion (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre_aap TEXT NOT NULL, huella TEXT NOT NULL, plan TEXT DEFAULT '',
      casos TEXT DEFAULT '[]', tickets TEXT DEFAULT '[]', resultado TEXT NOT NULL, por TEXT, fecha TEXT, nota TEXT DEFAULT '');""")


def _yaml(nombre):
    p = os.path.join(RAIZ, "datos", nombre)
    m = os.path.getmtime(p) if os.path.exists(p) else 0
    if _cache.get(nombre, (None,))[0] != m:
        _cache[nombre] = (m, (yaml.safe_load(open(p, encoding="utf-8")) or {}) if m else {})
    return _cache[nombre][1]


def _archivos(playbook, vistos=None):
    """El playbook y todo lo que importa o incluye (import_playbook, include_tasks, import_tasks, files/…)."""
    vistos = vistos if vistos is not None else []
    base = os.path.basename(playbook or "")
    ruta = os.path.join(DIR_AAP, base)
    if not base or base in vistos or not os.path.exists(ruta):
        return vistos
    vistos.append(base)
    txt = open(ruta, encoding="utf-8", errors="replace").read()
    for ref in re.findall(r"(?:import_playbook|include_tasks|import_tasks):\s*['\"]?([\w./-]+\.ya?ml)", txt):
        _archivos(ref, vistos)
    for ref in re.findall(r"files/([\w.-]+\.py)", txt):
        f = os.path.join(DIR_AAP, "files", ref)
        if os.path.exists(f) and "files/" + ref not in vistos:
            vistos.append("files/" + ref)
    return vistos


def huella(auto, _visit=None):
    """sha256 de lo que define la automatización hoy (corta, 12 caracteres)."""
    _visit = _visit or set()
    h = hashlib.sha256()
    n = auto["nombre_aap"]
    h.update(json.dumps(_yaml("catalogo.yml").get("fichas", _yaml("catalogo.yml")).get(n) if isinstance(_yaml("catalogo.yml"), dict) else None,
                        sort_keys=True, default=str, ensure_ascii=False).encode())
    h.update(json.dumps(_yaml("formularios.yml").get(n), sort_keys=True, default=str, ensure_ascii=False).encode())
    tec = j(auto.get("tecnico"), {})
    if tec.get("playbook"):
        fs = _archivos(tec["playbook"])
        h.update(tec["playbook"].encode())
        for f in fs:
            h.update(f.encode())
            h.update(open(os.path.join(DIR_AAP, f), "rb").read())
        if not fs:   # playbook de otro repositorio: su proyecto y rama
            h.update(json.dumps({k: tec.get(k) for k in ("proyecto", "repositorio", "rama")}, sort_keys=True).encode())
    for nodo in tec.get("nodos") or []:   # workflow: la huella de cada plantilla que lo compone
        nom = nodo.get("nombre") if isinstance(nodo, dict) else str(nodo)
        if nom and nom not in _visit:
            _visit.add(nom)
            a_ = q1("SELECT nombre_aap, tecnico FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", auto["cliente_id"], nom)
            if a_:
                h.update(huella(dict(a_, cliente_id=auto["cliente_id"]), _visit).encode())
    return h.hexdigest()[:12]


def estado(auto):
    """{'estado': 'certificada' | 'cambiada' | 'sin probar', 'huella', 'ultima': fila de la última certificación}"""
    if os.environ.get("FACEOPS_MAQUETA") == "1":
        return {"estado": "certificada", "huella": "", "ultima": None}
    _tabla()
    hu = huella(auto)
    ult = q1("SELECT * FROM pruebas_automatizacion WHERE cliente_id=? AND nombre_aap=? AND resultado='aprobada' ORDER BY id DESC LIMIT 1",
             auto["cliente_id"], auto["nombre_aap"])
    if ult and ult["huella"] == hu:
        return {"estado": "certificada", "huella": hu, "ultima": ult}
    return {"estado": "cambiada" if ult else "sin probar", "huella": hu, "ultima": ult}


def certificada(auto):
    return estado(auto)["estado"] == "certificada"


def certificar(cliente_id, nombre_aap, plan, casos, tickets, resultado, por, nota=""):
    """Registra la ejecución de un plan de prueba. Solo «aprobada» con todos los casos exitosos libera la versión vigente."""
    _tabla()
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, nombre_aap)
    if not a:
        raise ValueError(f"No existe «{nombre_aap}»")
    if resultado == "aprobada" and any(c.get("resultado") != "ok" for c in casos):
        raise ValueError("Un plan con casos fallidos no puede quedar aprobado")
    ej("INSERT INTO pruebas_automatizacion(cliente_id, nombre_aap, huella, plan, casos, tickets, resultado, por, fecha, nota) VALUES (?,?,?,?,?,?,?,?,?,?)",
       cliente_id, nombre_aap, huella(a), plan, json.dumps(casos, ensure_ascii=False), json.dumps(tickets), resultado, por, ahora(), nota[:400])
    auditar(por, cliente_id, f"prueba de automatización {resultado}", nombre_aap, {"plan": plan, "tickets": tickets})


def plan_de(nombre_aap):
    return (_yaml("planes-prueba.yml").get("planes") or {}).get(nombre_aap)


def resumen(cliente_id):
    """Estado de prueba de todo el catálogo del cliente."""
    out = []
    for a in q("SELECT * FROM automatizaciones WHERE cliente_id=? ORDER BY nombre", cliente_id):
        e = estado(a)
        out.append({"id": a["id"], "nombre": a["nombre"], "nombre_aap": a["nombre_aap"], "visible": a["visible"], "plan": bool(plan_de(a["nombre_aap"])), **e})
    return out
