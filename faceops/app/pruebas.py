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
      casos TEXT DEFAULT '[]', tickets TEXT DEFAULT '[]', resultado TEXT NOT NULL, por TEXT, fecha TEXT, nota TEXT DEFAULT '');
      CREATE TABLE IF NOT EXISTS huellas_repo (url TEXT, rev TEXT, playbook TEXT, huella TEXT, PRIMARY KEY (url, rev, playbook));""")


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


# ── Playbooks de otros proyectos de AAP ────────────────────────────────────────────────────────────────────────────────
# Su contenido no vive en faceops/aap: la huella sale de lo que ejecuta AAP, es decir, la revisión SCM vigente del proyecto
# de la plantilla (project.scm_revision, consultada con el token de FaceOps y en caché) y el playbook con todo lo que
# incluye, leído del repositorio en esa revisión exacta (Gitea). Si el repositorio no se puede leer, la huella es la
# revisión del proyecto + el playbook (más conservadora: cualquier commit del repositorio la cambia).
PROYECTO_FACEOPS = "FaceOps · automatizaciones"
TTL_PROYECTOS = 120
_proy_cache = {}     # cliente_id → (t, {nombre proyecto: {rev, url, rama}})
_contenido = {}      # (url, rev, playbook) → huella del contenido


def es_externa(tec):
    pr = tec.get("proyecto") or ""
    return bool(pr) and pr != PROYECTO_FACEOPS


def _proyectos_aap(cliente_id):
    import time
    c = _proy_cache.get(cliente_id)
    if c and time.time() - c[0] < TTL_PROYECTOS:
        return c[1]
    try:
        from .aap import AAP
        aap = AAP.de_cliente(q1("SELECT * FROM clientes WHERE id=?", cliente_id))
        if not aap:
            raise RuntimeError("sin AAP")
        m = {p["name"]: {"rev": p.get("scm_revision") or "", "url": p.get("scm_url") or "", "rama": p.get("scm_branch") or ""}
             for p in aap.todos("projects/")}
    except Exception:
        m = c[1] if c else {}      # AAP no responde: se mantiene lo último conocido (o el commit de la sincronización)
    _proy_cache[cliente_id] = (time.time() - (0 if m else TTL_PROYECTOS - 15), m)
    return m


def _gitea(url):
    """(base, owner/repo) de una URL SCM de Gitea; None si no es http(s)."""
    m = re.match(r"(https?://)(?:[^@/]*@)?([^/]+)/([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return (m.group(1) + m.group(2), f"{m.group(3)}/{m.group(4)}") if m else None


def _leer(url, rev, ruta):
    import requests
    g, tok = _gitea(url), os.environ.get("FACEOPS_GITEA_TOKEN", "")
    if not g or not rev:
        return None
    r = requests.get(f"{g[0]}/api/v1/repos/{g[1]}/raw/{ruta}", params={"ref": rev}, timeout=5,
                     headers={"Authorization": "token " + tok} if tok else {})
    if r.status_code == 404:
        return b""                 # el include no existe en esa revisión: cuenta como vacío
    r.raise_for_status()
    return r.content


def archivos_repo(url, rev, playbook):
    """[(ruta, contenido)] del playbook y lo que importa o incluye, en la revisión dada (rutas relativas al playbook)."""
    out, pend = [], [playbook]
    while pend and len(out) < 40:
        ruta = os.path.normpath(pend.pop(0))
        if any(r == ruta for r, _ in out):
            continue
        cont = _leer(url, rev, ruta)
        if cont is None:
            raise RuntimeError("repositorio no legible")
        out.append((ruta, cont))
        base = os.path.dirname(ruta)
        txt = cont.decode("utf-8", "replace")
        for ref in re.findall(r"(?:import_playbook|include_tasks|import_tasks|include_vars|vars_files:\s*-)\s*:?\s*['\"]?([\w./-]+\.ya?ml)", txt):
            if "{{" not in ref:
                pend.append(os.path.join(base, ref))
        for rol in re.findall(r"(?:include_role|import_role):\s*\n\s*name:\s*['\"]?([\w.-]+)|^\s*-\s*role:\s*['\"]?([\w.-]+)", txt, re.M):
            n = rol[0] or rol[1]
            pend.append(os.path.join(base, "roles", n, "tasks", "main.yml"))
        for ref in re.findall(r"\bfiles/([\w./-]+\.(?:py|sh|j2|ya?ml|json|ps1))", txt):
            pend.append(os.path.join(base, "files", ref))
    return out


def huella_externa(cliente_id, tec):
    pr = _proyectos_aap(cliente_id).get(tec.get("proyecto") or "") or {}
    rev, url = pr.get("rev") or tec.get("commit") or "", pr.get("url") or tec.get("repositorio") or ""
    clave = (url, rev, tec["playbook"])
    if clave not in _contenido:
        _tabla()
        f = q1("SELECT huella FROM huellas_repo WHERE url=? AND rev=? AND playbook=?", *clave)
        if f:
            _contenido[clave] = f["huella"]
        else:
            try:
                h = hashlib.sha256(b"contenido")
                for ruta, cont in archivos_repo(url, rev, tec["playbook"]):
                    h.update(ruta.encode())
                    h.update(cont)
                _contenido[clave] = h.hexdigest()
                ej("INSERT OR IGNORE INTO huellas_repo(url, rev, playbook, huella) VALUES (?,?,?,?)", *clave, _contenido[clave])
            except Exception:
                # sin acceso al repositorio: revisión del proyecto + playbook (no se guarda, se reintenta en la próxima consulta)
                return hashlib.sha256(json.dumps({"proyecto": tec.get("proyecto"), "rev": rev, "playbook": tec["playbook"]}).encode()).hexdigest()
    return _contenido[clave]


def huella(auto, _visit=None):
    """sha256 de lo que define la automatización hoy (corta, 12 caracteres)."""
    _visit = _visit or set()
    h = hashlib.sha256()
    n = auto["nombre_aap"]
    h.update(json.dumps(_yaml("catalogo.yml").get("fichas", _yaml("catalogo.yml")).get(n) if isinstance(_yaml("catalogo.yml"), dict) else None,
                        sort_keys=True, default=str, ensure_ascii=False).encode())
    h.update(json.dumps(_yaml("formularios.yml").get(n), sort_keys=True, default=str, ensure_ascii=False).encode())
    tec = j(auto.get("tecnico"), {})
    if tec.get("playbook") and es_externa(tec):   # playbook de otro proyecto de AAP: lo que ese proyecto ejecuta hoy
        h.update(tec["playbook"].encode())
        h.update(huella_externa(auto["cliente_id"], tec).encode())
    elif tec.get("playbook"):
        fs = _archivos(tec["playbook"])
        h.update(tec["playbook"].encode())
        for f in fs:
            h.update(f.encode())
            h.update(open(os.path.join(DIR_AAP, f), "rb").read())
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


def exceptuada(nombre_aap):
    """Excepción a la regla de prueba autorizada explícitamente por el usuario (planes-prueba.yml › excepciones): la
    automatización se puede usar aunque su versión vigente aún no esté certificada; su plan sigue corriendo en paralelo."""
    return next((x for x in (_yaml("planes-prueba.yml").get("excepciones") or []) if x.get("automatizacion") == nombre_aap), None)


def plan_de(nombre_aap):
    return (_yaml("planes-prueba.yml").get("planes") or {}).get(nombre_aap)


def resumen(cliente_id):
    """Estado de prueba de todo el catálogo del cliente."""
    out = []
    for a in q("SELECT * FROM automatizaciones WHERE cliente_id=? ORDER BY nombre", cliente_id):
        e = estado(a)
        out.append({"id": a["id"], "nombre": a["nombre"], "nombre_aap": a["nombre_aap"], "visible": a["visible"], "plan": bool(plan_de(a["nombre_aap"])), **e})
    return out
