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
    ult_v = q1("SELECT * FROM pruebas_automatizacion WHERE cliente_id=? AND nombre_aap=? AND huella=? ORDER BY id DESC LIMIT 1",
               auto["cliente_id"], auto["nombre_aap"], hu)
    if ult and ult["huella"] == hu:
        if ult_v and ult_v["resultado"] != "aprobada":   # la última corrida de esta misma versión falló: vuelve a quedar bloqueada
            return {"estado": "fallida", "huella": hu, "ultima": ult}
        return {"estado": "certificada", "huella": hu, "ultima": ult}
    return {"estado": "cambiada" if ult else "sin probar", "huella": hu, "ultima": ult}


_cert_cache = {}


def no_certificadas(cliente_id, ttl=30):
    """Ids de automatizaciones del cliente cuya versión vigente no pasó su plan completo (caché corta: se consulta en cada página)."""
    import time
    if os.environ.get("FACEOPS_MAQUETA") == "1":
        return set()
    c = _cert_cache.get(cliente_id)
    if c and time.time() - c[0] < ttl:
        return c[1]
    ids = {a["id"] for a in q("SELECT * FROM automatizaciones WHERE cliente_id=?", cliente_id) if estado(a)["estado"] != "certificada"}
    _cert_cache[cliente_id] = (time.time(), ids)
    return ids


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
    _cert_cache.pop(cliente_id, None)
    auditar(por, cliente_id, f"prueba de automatización {resultado}", nombre_aap, {"plan": plan, "tickets": tickets})


def ultima_corrida(cliente_id, nombre_aap):
    """La última ejecución del plan (aprobada o rechazada), con el resultado de cada caso por id."""
    _tabla()
    r = q1("SELECT * FROM pruebas_automatizacion WHERE cliente_id=? AND nombre_aap=? ORDER BY id DESC LIMIT 1", cliente_id, nombre_aap)
    if not r:
        return None
    casos = j(r.get("casos"), [])
    return dict(r, por_caso={str(c.get("id")): c for c in casos}, casos_l=casos, tickets_l=j(r.get("tickets"), []))


TIPOS_CASO = (("Idempotencia", r"repetir|de nuevo|otra vez|idempot|ya convertida|ya registrad"),
              ("Error controlado", r"inexistente|inv[aá]lid|no v[aá]lid|menor|sin disco|sin respaldo|sin jboss|falla|rechaz|error|no aplica|ya tiene datos|pol[ií]tica|destino no"),
              ("Aprobación", r"aprob"),
              ("Seguridad", r"secreto|contrase|acceso|revocar|firewall|permis"))


def tipo_caso(c):
    """Funcional, idempotencia, error controlado… (el plan puede declararlo con «tipo»; si no, se deduce del caso)."""
    if c.get("tipo"):
        return c["tipo"]
    t = f"{c.get('caso', '')} {c.get('verificar', '')}".lower()
    return next((n for n, rx in TIPOS_CASO if re.search(rx, str(c.get("caso", "")).lower())), None) or \
        next((n for n, rx in TIPOS_CASO[:2] if re.search(rx, t)), "Funcional")


def calidad(auto):
    """Ficha de calidad de una automatización: su plan caso a caso (qué se prueba, cómo se verifica, resultado y evidencia de
    cada corrida), cobertura por tipo de caso, historial de corridas y un nivel de calidad legible por el administrador."""
    _tabla()
    plan = plan_de(auto["nombre_aap"]) or {}
    est = estado(auto)
    filas = q("SELECT * FROM pruebas_automatizacion WHERE cliente_id=? AND nombre_aap=? ORDER BY id DESC", auto["cliente_id"], auto["nombre_aap"])
    hist = []
    for r in filas:
        cs = j(r.get("casos"), [])
        hist.append(dict(r, casos_l=cs, tickets_l=j(r.get("tickets"), []), ok=sum(1 for c in cs if c.get("resultado") == "ok"),
                         vigente=r["huella"] == est["huella"], por_caso={str(c.get("id")): c for c in cs}))
    ult = hist[0] if hist else None
    casos = []
    for c in plan.get("casos") or []:
        r_ = None
        if ult:
            r_ = ult["por_caso"].get(str(c["id"])) or ult["por_caso"].get(f"{c['id']}a")
        cert = next((h["por_caso"].get(str(c["id"])) for h in hist if h["resultado"] == "aprobada" and h["vigente"]), None)
        casos.append(dict(c, tipo=tipo_caso(c), ultimo=r_, certificado=cert))
    tipos = {}
    for c in casos:
        tipos[c["tipo"]] = tipos.get(c["tipo"], 0) + 1
    n, ok_ = len(casos), sum(1 for c in casos if c["ultimo"] and c["ultimo"].get("resultado") == "ok")
    aprob = [h for h in hist if h["resultado"] == "aprobada"]
    if not casos:
        nivel, por_que = "Sin plan", "No tiene plan de prueba: queda bloqueada hasta escribirlo y ejecutarlo completo."
    elif est["estado"] != "certificada":
        nivel = "Bloqueada"
        por_que = ("Cambió desde su última certificación: la versión vigente no está probada." if est["estado"] == "cambiada"
                   else "Su última corrida del plan falló en esta misma versión: vuelve a quedar bloqueada hasta que pase completo." if est["estado"] == "fallida"
                   else f"Su plan aún no pasa completo ({ok_} de {n} casos ok en la última corrida).")
    else:
        falta = [t for t in ("Funcional", "Error controlado", "Idempotencia") if t not in tipos]
        nivel = "Alta" if not falta and n >= 3 else "Media"
        por_que = ("Certificada en su versión vigente; el plan cubre el camino normal, los errores esperados y la repetición."
                   if nivel == "Alta" else "Certificada en su versión vigente; al plan le falta cubrir: " + ", ".join(falta).lower() + ".")
    nums = sorted({t for h in hist for t in h["tickets_l"] if isinstance(t, str)})
    tk = {r["numero"]: r["id"] for r in q(f"SELECT id, numero FROM tickets WHERE cliente_id=? AND numero IN ({','.join('?' * len(nums))})",
                                          auto["cliente_id"], *nums)} if nums else {}
    return {"tk": tk, "estado": est, "plan": plan, "casos": casos, "tipos": tipos, "n": n, "ok": ok_, "historial": hist, "ultima": ult,
            "aprobadas": len(aprob), "primera_cert": aprob[-1]["fecha"] if aprob else None, "nivel": nivel, "por_que": por_que}


def plan_de(nombre_aap):
    return (_yaml("planes-prueba.yml").get("planes") or {}).get(nombre_aap)


def resumen(cliente_id):
    """Estado de prueba de todo el catálogo del cliente."""
    out = []
    for a in q("SELECT * FROM automatizaciones WHERE cliente_id=? ORDER BY nombre", cliente_id):
        e = estado(a)
        out.append({"id": a["id"], "nombre": a["nombre"], "nombre_aap": a["nombre_aap"], "visible": a["visible"], "plan": bool(plan_de(a["nombre_aap"])), **e})
    return out
