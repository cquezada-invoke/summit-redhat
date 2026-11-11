"""Políticas de seguridad como automatizaciones versionadas. Cada política (datos/politicas.yml o editada en FaceOps)
tiene condiciones de aplicación por equipo y una versión: si cambia, los equipos que la tenían quedan desactualizados
y se vuelve a aplicar. La aplica (o verifica sin cambiar nada) «FaceOps · Aplicar política de seguridad» en AAP."""
import fnmatch
import functools
import hashlib
import json
import os

import yaml

from .bd import ahora, ej, j, q, q1

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "politicas.yml")
AUTO = "FaceOps · Aplicar política de seguridad"
CATEGORIAS = [("sistema operativo", "Sistema operativo"), ("contenedores", "Contenedores"), ("comunicaciones", "Comunicaciones"),
              ("redes", "Redes"), ("datos", "Datos"), ("aplicaciones", "Aplicaciones"), ("middleware", "Middleware y servidores de aplicación"),
              ("identidad", "Identidad y accesos"), ("auditoria", "Registro y auditoría"), ("respaldo", "Respaldo y continuidad")]
DESC_CAT = {"sistema operativo": "Acceso SSH, kernel, sesiones y endurecimiento base del servidor",
            "contenedores": "Podman y runtimes: registros confiables, límites y aislamiento",
            "comunicaciones": "Criptografía, protocolos y hora del sistema",
            "redes": "Firewall local, puertos expuestos y servicios de red",
            "datos": "Cifrado, permisos y protección de la información",
            "aplicaciones": "Servicios y software de aplicación expuestos",
            "middleware": "JBoss, Tomcat, servidores web y consolas de administración",
            "identidad": "Cuentas, contraseñas, sudo y autenticación",
            "auditoria": "Registro de eventos, auditd y retención de logs",
            "respaldo": "Respaldos, recuperación y continuidad"}
# prefijo del código de las políticas nuevas por categoría (SO-04, CON-01…)
PREFIJO = {"sistema operativo": "SO", "contenedores": "CON", "comunicaciones": "COM", "redes": "RED", "datos": "DAT", "aplicaciones": "APP",
           "middleware": "MID", "identidad": "IDE", "auditoria": "AUD", "respaldo": "RES"}


def codigo_nuevo(cliente_id, categoria):
    """Siguiente código libre de la categoría (p. ej. CON-03)."""
    import re as _re
    pre = PREFIJO.get(categoria, "POL")
    usados = [int(m.group(1)) for p in vigentes(cliente_id) for m in [_re.match(pre + r"-(\d+)$", p.get("codigo") or "")] if m]
    return f"{pre}-{(max(usados) + 1) if usados else 1:02d}"


PLANTILLA = {"nombre": "", "severidad": "media", "descripcion": "",
             "aplica": {"sistemas": ["linux"]},
             "reglas": [{"tipo": "comando", "verificar": "true  # 0 = cumple", "aplicar": "true"}],
             "recargar": []}
ESQUEMA = """
CREATE TABLE IF NOT EXISTS politicas_def (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, codigo TEXT NOT NULL, version INTEGER NOT NULL, contenido TEXT NOT NULL,
  hash TEXT NOT NULL, creado TEXT, autor TEXT DEFAULT '', motivo TEXT DEFAULT '', git_commit TEXT DEFAULT '', git_url TEXT DEFAULT '',
  UNIQUE(cliente_id, codigo, version));
CREATE TABLE IF NOT EXISTS politicas_estado (
  cliente_id INTEGER NOT NULL, codigo TEXT NOT NULL, equipo_id INTEGER NOT NULL, version INTEGER, estado TEXT, modo TEXT,
  detalle TEXT DEFAULT '[]', cuando TEXT, ticket_id INTEGER, PRIMARY KEY(cliente_id, codigo, equipo_id));
CREATE TABLE IF NOT EXISTS politicas_retiradas (
  cliente_id INTEGER NOT NULL, codigo TEXT NOT NULL, cuando TEXT, autor TEXT DEFAULT '', motivo TEXT DEFAULT '', git_commit TEXT DEFAULT '',
  PRIMARY KEY(cliente_id, codigo));
"""


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)
    for col in ("git_commit", "git_url"):
        try:
            con().execute(f"ALTER TABLE politicas_def ADD COLUMN {col} TEXT DEFAULT ''")
        except Exception:  # noqa: BLE001 - ya existe
            pass


# ------------------------------------------------------------------ Git: repositorio/politicas-seguridad (fuente de verdad)
def _gitea():
    """Repositorio de políticas: FACEOPS_POLITICAS_GIT o, en el laboratorio de Invoke, su Gitea. Una instancia de cliente sin Git
    configurado usa el archivo base (no intenta conectarse)."""
    v = os.environ.get("FACEOPS_POLITICAS_GIT", "").rstrip("/")
    if v:
        return v
    from .semillas import limpia
    return "" if limpia() else "http://192.168.122.1:3000/api/v1/repos/repositorio/politicas-seguridad"


_caido = {"hasta": 0.0}


def _git(m, ruta, **kw):
    import time
    import requests
    base = _gitea()
    if not base or time.time() < _caido["hasta"]:
        return None
    tok = os.environ.get("FACEOPS_RESPALDO_TOKEN", "")
    try:
        r = requests.request(m, base + ruta, timeout=4, headers={"Authorization": f"token {tok}"} if tok else {}, **kw)
    except requests.RequestException:
        _caido["hasta"] = time.time() + 600   # Git no responde: no se vuelve a intentar en 10 minutos (nunca frena la consola)
        return None
    return r.json() if r.ok and r.content else (None if not r.ok else {})


def _texto(p):
    cab = (f"# {p['codigo']} · {p['nombre']}\n# Política de seguridad de FaceOps. Cada commit en este archivo es una versión:\n"
           "# los equipos que tenían la versión anterior quedan desactualizados y se vuelve a aplicar.\n")
    cuerpo = {k: p[k] for k in ("codigo", "nombre", "categoria", "severidad", "descripcion", "aplica", "reglas", "recargar") if k in p}
    return cab + yaml.safe_dump(cuerpo, allow_unicode=True, sort_keys=False, width=140)


def publicar(p, autor, motivo):
    """Commit del archivo de la política en Git. Devuelve (sha del commit, url) o ('', '') si Git no responde."""
    import base64
    ruta = f"/contents/politicas/{p['codigo']}.yml"
    actual = _git("GET", ruta)
    datos = {"content": base64.b64encode(_texto(p).encode()).decode(), "branch": "main",
             "message": f"{p['codigo']}: {motivo or 'actualización'} ({autor})",
             "author": {"name": autor or "FaceOps", "email": "politicas@faceops.invoke.cl"}}
    if actual and actual.get("sha"):
        datos["sha"] = actual["sha"]
    r = _git("PUT" if actual else "POST", ruta, json=datos)
    c = (r or {}).get("commit") or {}
    return c.get("sha", ""), c.get("html_url", "")


def retiradas(cliente_id):
    return {r["codigo"] for r in q("SELECT codigo FROM politicas_retiradas WHERE cliente_id=?", cliente_id)}


def eliminar(cliente_id, codigo, autor, motivo):
    """Retira la política: se borra su archivo en Git (el historial queda en el repositorio) y deja de asignarse a los equipos.
    Lo ya aplicado en las máquinas no se revierte."""
    actual = _git("GET", f"/contents/politicas/{codigo}.yml")
    sha = ""
    if actual and actual.get("sha"):
        r = _git("DELETE", f"/contents/politicas/{codigo}.yml", json={
            "sha": actual["sha"], "branch": "main", "message": f"{codigo}: retirada · {motivo or 'sin motivo'} ({autor})",
            "author": {"name": autor or "FaceOps", "email": "politicas@faceops.invoke.cl"}})
        if r is None:
            raise RuntimeError("No se pudo borrar la política en Git (repositorio/politicas-seguridad)")
        sha = ((r or {}).get("commit") or {}).get("sha", "")
    ej("INSERT OR REPLACE INTO politicas_retiradas(cliente_id, codigo, cuando, autor, motivo, git_commit) VALUES (?,?,?,?,?,?)",
       cliente_id, codigo, ahora(), autor, motivo, sha)


def desde_git():
    """[(política, commit, url, autor, mensaje)] de los archivos del repositorio."""
    import base64
    out = []
    for f in _git("GET", "/contents/politicas") or []:
        if not f.get("name", "").endswith(".yml"):
            continue
        d = _git("GET", "/contents/" + f["path"])
        cs = _git("GET", "/commits", params={"path": f["path"], "limit": 1}) or []
        if not d or not cs:
            continue
        try:
            p = yaml.safe_load(base64.b64decode(d["content"]).decode())
        except yaml.YAMLError:
            continue
        c = cs[0]
        out.append((p, c["sha"], c["html_url"], c["commit"]["author"]["name"], c["commit"]["message"].strip().splitlines()[0]))
    return out


_visto = {"t": 0}


def sincronizar(cliente_id, forzar=False):
    """Git es la fuente de verdad: lo que falte en Git sale de datos/politicas.yml (primera vez); cada cambio en Git
    (hecho en FaceOps o directo en el repositorio) entra como versión nueva. Como máximo una vez por minuto."""
    import time
    if not forzar and time.time() - _visto["t"] < 60:
        return
    _visto["t"] = time.time()
    if not _gitea():
        return cargar(cliente_id)   # sin Git configurado: el archivo base, sin conexión a nada
    if not forzar:   # desde una página: en segundo plano, para no hacer esperar a nadie por Git
        import threading
        threading.Thread(target=sincronizar, args=(cliente_id, True), daemon=True).start()
        return
    if _git("GET", "") is None:
        return cargar(cliente_id)   # sin Git: el archivo base
    en_git = {p.get("codigo") or p.get("id"): (p, sha, url, autor, msg) for p, sha, url, autor, msg in desde_git()}
    try:
        base = yaml.safe_load(open(RUTA, encoding="utf-8")) or []
    except OSError:
        base = []
    fuera = retiradas(cliente_id)
    for p in base:
        p = _normalizar(p)
        if p["codigo"] not in en_git and p["codigo"] not in fuera:
            sha, url = publicar(p, "repositorio", "versión inicial")
            en_git[p["codigo"]] = (p, sha, url, "repositorio", "versión inicial")
    for cod, (p, sha, url, autor, msg) in en_git.items():
        if cod in fuera:   # retirada en FaceOps pero vuelta a subir directo a Git: vuelve a estar vigente
            ej("DELETE FROM politicas_retiradas WHERE cliente_id=? AND codigo=?", cliente_id, cod)
        p = _normalizar(p)
        if not q1("SELECT 1 FROM politicas_def WHERE cliente_id=? AND codigo=? AND git_commit=?", cliente_id, cod, sha):
            guardar(cliente_id, p, autor, msg, sha, url)


def _hash(p):
    base = {k: v for k, v in p.items() if k not in ("version",)}
    return hashlib.sha256(json.dumps(base, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _normalizar(p):
    p = dict(p)
    p["codigo"] = p.pop("id", None) or p.get("codigo")
    p.setdefault("aplica", {})
    p.setdefault("reglas", [])
    p.setdefault("recargar", [])
    return p


def validar(p):
    err = []
    if not p.get("codigo") or not p.get("nombre"):
        err.append("Falta id o nombre")
    if p.get("categoria") not in dict(CATEGORIAS):
        err.append("categoria debe ser: " + ", ".join(dict(CATEGORIAS)))
    tipos = {"linea", "sysctl", "servicio", "permisos", "archivo", "comando", "firewall"}
    for i, r in enumerate(p.get("reglas") or []):
        if r.get("tipo") not in tipos:
            err.append(f"regla {i + 1}: tipo debe ser {', '.join(sorted(tipos))}")
    if not p.get("reglas"):
        err.append("La política no tiene reglas")
    return err


def guardar(cliente_id, p, autor="", motivo="", git_commit="", git_url=""):
    """Nueva versión si el contenido cambió. Devuelve la versión vigente."""
    p = _normalizar(p)
    h = _hash(p)
    ult = q1("SELECT * FROM politicas_def WHERE cliente_id=? AND codigo=? ORDER BY version DESC LIMIT 1", cliente_id, p["codigo"])
    if ult and ult["hash"] == h:
        if git_commit and not ult["git_commit"]:
            ej("UPDATE politicas_def SET git_commit=?, git_url=? WHERE id=?", git_commit, git_url, ult["id"])
        return ult["version"]
    v = (ult["version"] + 1) if ult else 1
    ej("INSERT INTO politicas_def(cliente_id, codigo, version, contenido, hash, creado, autor, motivo, git_commit, git_url) VALUES (?,?,?,?,?,?,?,?,?,?)",
       cliente_id, p["codigo"], v, yaml.safe_dump({k: v_ for k, v_ in p.items() if k != "version"}, allow_unicode=True, sort_keys=False), h,
       ahora(), autor, motivo, git_commit, git_url)
    return v


def editar(cliente_id, p, autor, motivo):
    """Edición desde FaceOps: commit en Git y versión nueva."""
    sha, url = publicar(_normalizar(p), autor, motivo)
    if not sha:
        raise RuntimeError("No se pudo guardar en Git (repositorio/politicas-seguridad): no se creó la versión")
    return guardar(cliente_id, p, autor, motivo, sha, url)


def cargar(cliente_id):
    """Las políticas del producto (datos/politicas.yml): las nuevas entran y las que cambiaron suben de versión, salvo las que el
    cliente ya ajustó (su última versión no es del repositorio): esas son parámetros del cliente y no se pisan."""
    try:
        for p in yaml.safe_load(open(RUTA, encoding="utf-8")) or []:
            cod = p.get("id") or p.get("codigo")
            if cod in retiradas(cliente_id):
                continue
            ult = q1("SELECT autor FROM politicas_def WHERE cliente_id=? AND codigo=? ORDER BY version DESC LIMIT 1", cliente_id, cod)
            if ult and ult["autor"] != "repositorio":
                continue   # el cliente la ajustó: es dato suyo, la versión del producto no la pisa
            guardar(cliente_id, p, "repositorio", "datos/politicas.yml")
    except OSError:
        pass


@functools.lru_cache(maxsize=512)
def _parse_t(txt):
    return tuple((yaml.safe_load(txt) or {}).items())


def _parse(txt):
    import copy
    return copy.deepcopy(dict(_parse_t(txt)))


def vigentes(cliente_id):
    out = []
    fuera = retiradas(cliente_id)
    for r in q("SELECT d.* FROM politicas_def d JOIN (SELECT codigo, MAX(version) v FROM politicas_def WHERE cliente_id=? GROUP BY codigo) m "
               "ON m.codigo=d.codigo AND m.v=d.version WHERE d.cliente_id=? ORDER BY d.codigo", cliente_id, cliente_id):
        if r["codigo"] in fuera:
            continue
        p = dict(_parse(r["contenido"]))
        p.update(version=r["version"], creado=r["creado"], autor=r["autor"], motivo=r["motivo"], git_commit=r["git_commit"], git_url=r["git_url"])
        out.append(p)
    return out


def una(cliente_id, codigo):
    return next((p for p in vigentes(cliente_id) if p["codigo"] == codigo), None)


def historial(cliente_id, codigo):
    return q("SELECT version, creado, autor, motivo, contenido, git_commit, git_url FROM politicas_def WHERE cliente_id=? AND codigo=? ORDER BY version DESC",
             cliente_id, codigo)


def aplica(p, e):
    from .inventario import sistema_eq
    a = p.get("aplica") or {}
    if a.get("sistemas") and sistema_eq(e) not in a["sistemas"]:
        return False
    if a.get("categorias") and (e.get("categoria") or "servidor") not in a["categorias"]:
        return False
    if a.get("skills") and not set(a["skills"]) & set(e.get("skills") or []):
        return False
    if a.get("sin_skills") and set(a["sin_skills"]) & set(e.get("skills") or []):
        return False   # p. ej. el firewall local no se gestiona en OpenShift (RHCOS) ni en equipos de red
    if a.get("ambientes") and (e.get("ambiente") or "") not in a["ambientes"]:
        return False
    if a.get("segmentos") and not set(a["segmentos"]) & set(e.get("segmentos") or []):
        return False
    if a.get("nombres") and not any(fnmatch.fnmatch(e["nombre"], x) for x in a["nombres"]):
        return False
    if "kvm" in (e.get("skills") or []) or not e.get("ip"):   # el anfitrión de la plataforma no se gestiona por aquí
        return False
    return True


ETIQUETA = {"al_dia": "al día", "desactualizada": "desactualizada", "pendiente": "pendiente", "no_cumple": "no cumple", "fallida": "falló"}


def estado(p, e, filas):
    r = filas.get((p["codigo"], e["id"]))
    if not r:
        return {"estado": "pendiente", "version": None}
    if r["estado"] == "fallida":
        return {"estado": "fallida", "version": r["version"], "cuando": r["cuando"], "ticket_id": r["ticket_id"]}
    if (r["version"] or 0) < p["version"]:
        return {"estado": "desactualizada", "version": r["version"], "cuando": r["cuando"], "ticket_id": r["ticket_id"]}
    return {"estado": "no_cumple" if r["estado"] == "no_cumple" else "al_dia", "version": r["version"], "cuando": r["cuando"],
            "ticket_id": r["ticket_id"], "detalle": j(r["detalle"], [])}


def tablero(cliente_id, eqs):
    """Por política: equipos a los que aplica y su estado; por equipo: sus políticas."""
    filas = {(r["codigo"], r["equipo_id"]): r for r in q("SELECT * FROM politicas_estado WHERE cliente_id=?", cliente_id)}
    pols, por_eq = [], {}
    # tickets de política aún abiertos (aprobación, cola, ejecución): la política queda «en aplicación» hasta resolverse
    abiertos = {}
    for t in q("SELECT t.id, t.numero, t.estado, t.limite, t.parametros FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
               "WHERE t.cliente_id=? AND a.nombre_aap=? AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion','en curso','ejecutando')",
               cliente_id, AUTO):
        pr_ = j(t["parametros"], {})
        if pr_.get("politica_modo", "aplicar") != "aplicar":
            continue
        for n in (t["limite"] or "").split(":"):
            abiertos[(pr_.get("politica_codigo"), n)] = {"id": t["id"], "numero": t["numero"], "estado": t["estado"]}
    try:
        sincronizar(cliente_id)
    except Exception:  # noqa: BLE001 - si Git falla, se sigue con lo que hay
        pass
    for p in vigentes(cliente_id):
        cuenta = {k: 0 for k in ETIQUETA}
        lista = []
        for e in eqs:
            if not aplica(p, e):
                continue
            st = estado(p, e, filas)
            cuenta[st["estado"]] += 1
            lista.append(dict(st, equipo=e))
            por_eq.setdefault(e["id"], []).append(dict(st, codigo=p["codigo"], nombre=p["nombre"], categoria=p["categoria"],
                                                        severidad=p.get("severidad", "media"), vigente=p["version"],
                                                        en_curso=abiertos.get((p["codigo"], e["nombre"]))))
        pols.append(dict(p, equipos=lista, cuenta=cuenta, total=len(lista)))
    return pols, por_eq


def solicitar(usuario, cliente, p, equipos, modo="aplicar", motivo="", prueba=False):
    """Un ticket por inventario de AAP (cada uno con su credencial). Devuelve los tickets creados. Regla de prueba: si la
    automatización cambió desde su última certificación queda bloqueada (solo el arnés de prueba la lanza, prueba=True)."""
    from .aap import AAP
    from .operacion import acceso_equipo, ejecutar, numero_ticket
    from .bd import auditar
    from .pruebas import estado as estado_prueba
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], AUTO)
    if not a:
        raise PermissionError(f"Falta «{AUTO}» en el catálogo (sincroniza con AAP)")
    ep_ = estado_prueba(dict(a, cliente_id=cliente["id"]))["estado"]
    if ep_ != "certificada" and not prueba:
        raise PermissionError(f"«{AUTO}» está bloqueada: " + ("cambió desde su última prueba y debe probarse de nuevo" if ep_ == "cambiada"
                                                             else "su última prueba falló y debe pasar de nuevo" if ep_ == "fallida"
                                                             else "aún no tiene su plan de prueba ejecutado y aprobado"))
    aap = AAP.de_cliente(cliente)
    grupos = {}
    for e in equipos:
        acc = acceso_equipo(aap, cliente, e)
        grupos.setdefault(acc[1], []).append(e)
    out = []
    pol = {k: v for k, v in p.items() if k in ("codigo", "nombre", "categoria", "descripcion", "reglas", "recargar", "version")}
    pol["id"] = pol["codigo"]
    fw = next((r for r in p.get("reglas") or [] if r.get("tipo") == "firewall"), None)
    for inv_n, es in grupos.items():
        params = {"politica": pol, "politica_modo": modo, "politica_codigo": p["codigo"], "politica_version": p["version"]}
        if fw:   # puertos de cada equipo según su rol (skills), sus consolas registradas y los flujos declarados hacia él
            params["politica_firewall"] = {e["nombre"]: puertos_firewall(cliente, fw, e) for e in es}
        seg = next((s for e in es for s in (e.get("segmentos") or [])), "")
        tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, motivo, creado, "
                 "valor_min, tarifa, aap_job_tipo, filtros) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 cliente["id"], numero_ticket(cliente), a["id"], usuario["id"], "en_cola", seg, ":".join(e["nombre"] for e in es),
                 json.dumps(params, ensure_ascii=False), motivo or f"{'Verificar' if modo == 'verificar' else 'Aplicar'} {p['codigo']} v{p['version']} · {p['nombre']}",
                 ahora(), a["minutos_manual"], a["tarifa"], a["aap_tipo"], "{}")
        t = q1("SELECT * FROM tickets WHERE id=?", tid)
        auditar(usuario["usuario"], cliente["id"], "solicitar política", t["numero"], {"politica": p["codigo"], "version": p["version"], "modo": modo,
                                                                                      "equipos": [e["nombre"] for e in es]})
        try:   # también queda en la herramienta de tickets del cliente (ServiceNow, Odoo…), como todo ticket
            from .operacion import conector
            conector(cliente).crear(t, a, usuario)
        except Exception as x:  # noqa: BLE001 - el ticket sigue; el registro externo se reintenta
            print("Conector (política):", str(x)[:160])
        ejecutar(tid)
        out.append(q1("SELECT * FROM tickets WHERE id=?", tid))
    return out


_SERV = {"ssh": "22/tcp", "http": "80/tcp", "https": "443/tcp", "ldap": "389/tcp", "ldaps": "636/tcp", "bgp": "179/tcp",
         "winrm": "5985/tcp", "dns": "53/udp"}


def puertos_firewall(cliente, regla, e):
    """{'abrir': [...], 'restringir': ['9990/tcp@192.168.122.1', ...]} de un equipo: los de siempre (SSH para AAP), los de
    sus plataformas (regla.por_skill según sus skills), los de sus consolas registradas (datos.puertos_web) y los de los
    flujos declarados hacia él (arquitecturas y topología). Un puerto restringido a un origen no se abre a todos."""
    import re as _re
    from .inventario import skills_de
    skills = e.get("skills") or skills_de(e)
    ab, rs = set(regla.get("siempre") or []), set()
    for sk in skills:
        for x in (regla.get("por_skill") or {}).get(sk) or []:
            (rs if "@" in x else ab).add(x)
    d_ = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    for v in (d_.get("puertos_web") or {}).values():
        if str(v).isdigit():
            ab.add(f"{v}/tcp")
    try:
        from .arquitectura import flujos_de
        for f in flujos_de(cliente):
            if f.get("hacia") != e["nombre"]:
                continue
            for tok in _re.split(r"[\s·,;]+", str(f.get("puerto") or "")):
                tok = tok.strip().lower()
                if _re.fullmatch(r"\d{1,5}(/(tcp|udp))?", tok):
                    ab.add(tok if "/" in tok else tok + "/tcp")
                elif tok in _SERV:
                    ab.add(_SERV[tok])
    except Exception as x:  # noqa: BLE001 - sin flujos se abren los de su rol
        print("Firewall (flujos):", str(x)[:160])
    ab -= {x.split("@")[0] for x in rs}
    orden = lambda x: (int(x.split("/")[0]), x)  # noqa: E731
    return {"abrir": sorted(ab, key=orden), "restringir": sorted(rs, key=orden)}


def registrar(aap, cliente, t, est):
    """Al cerrar el ticket: el estado de la política en cada equipo (qué versión quedó, si cumple)."""
    params = j(t["parametros"], {})
    codigo, version, modo = params.get("politica_codigo"), params.get("politica_version"), params.get("politica_modo", "aplicar")
    if not codigo:
        return ""
    from .operacion import artefactos_ticket
    art = artefactos_ticket(aap, t)   # también dentro del flujo de aprobación (workflow): antes quedaba «falló» aunque se aplicara
    res = art.get("faceops_politica") or {}
    lineas = []
    for n in [x for x in (t["limite"] or "").split(":") if x]:
        e = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], n)
        if not e:
            continue
        r = res.get(n)
        if r is None:
            if modo == "verificar":
                continue
            st, det = "fallida", []
        elif modo == "verificar":
            st, det = ("al_dia" if r.get("cumple") else "no_cumple"), r.get("incumplia", [])
        else:
            st, det = "al_dia", r.get("incumplia", [])
        prev = q1("SELECT * FROM politicas_estado WHERE cliente_id=? AND codigo=? AND equipo_id=?", cliente["id"], codigo, e["id"])
        # verificar no sube la versión aplicada: si estaba en una versión anterior, sigue desactualizada aunque hoy cumpla
        ver = version if (modo == "aplicar" or not prev or prev["estado"] == "fallida") else prev["version"]
        # una verificación que confirma lo aplicado conserva el ticket que la resolvió («Aplicada · FO-…» no cambia)
        tid_ = prev["ticket_id"] if (modo == "verificar" and prev and prev["estado"] == "al_dia" and st == "al_dia") else t["id"]
        ej("INSERT INTO politicas_estado(cliente_id, codigo, equipo_id, version, estado, modo, detalle, cuando, ticket_id) VALUES (?,?,?,?,?,?,?,?,?) "
           "ON CONFLICT(cliente_id, codigo, equipo_id) DO UPDATE SET version=excluded.version, estado=excluded.estado, modo=excluded.modo, "
           "detalle=excluded.detalle, cuando=excluded.cuando, ticket_id=excluded.ticket_id",
           cliente["id"], codigo, e["id"], ver, st, modo, json.dumps(det, ensure_ascii=False), ahora(), tid_)
        lineas.append(f"{n}: {ETIQUETA[st]}" + (f" ({', '.join(det[:4])})" if det else ""))
    return f"Política {codigo} v{version} ({modo}): " + " · ".join(lineas)
