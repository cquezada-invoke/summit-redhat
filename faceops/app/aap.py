"""Cliente de la API de Ansible Automation Platform (controller vía gateway). Una instancia por cliente de FaceOps.

Regla: FaceOps no espera a un AAP caído. Un vigía en segundo plano hace ping a cada AAP conocido (cada 30 s); mientras uno no
responde, el «cortacircuito» está abierto y toda llamada falla al instante con AAPNoDisponible (las páginas usan su caché y el
banner global avisa; los tickets quedan «En espera de AAP» y se lanzan al volver). Timeout corto: 2 s para conectar y 5 s
de lectura durante una página (20 s en tareas de fondo)."""
import os
import threading
import time

import requests
import urllib3

urllib3.disable_warnings()

REVISAR_CADA = 30
INICIO = time.time()   # arranque de FaceOps (para el aviso «sin contacto desde …»)
_SALUD = {}       # web → {"ok", "ultimo_ok", "desde", "error"}
_CONOCIDOS = {}   # web → (url de ping, verify)
_vigia = {"hilo": None}
_SESIONES = {}    # web → requests.Session con el token (solo en memoria; el sincronizador la usa)
TTL_LECTURA = 60  # s: cada cuánto el sincronizador renueva una lectura que alguna página usa
USO_VIGENTE = 2 * 3600   # s: lecturas que ninguna página usó en este tiempo dejan de sincronizarse
_pendientes = set()


class ErrorAAP(Exception):
    pass


class AAPNoDisponible(ErrorAAP):
    """AAP no responde (caído o sin red): la acción queda en espera, no fallida."""


def _marcar(web, ok, error=""):
    s = _SALUD.setdefault(web, {"ok": True, "ultimo_ok": None, "desde": None, "error": ""})
    if ok:
        s.update(ok=True, ultimo_ok=time.time(), desde=None, error="")
    else:
        if s["ok"]:
            s["desde"] = time.time()
        s.update(ok=False, error=str(error)[:200])


def _ping(web):
    url, verify = _CONOCIDOS[web]
    try:
        r = requests.get(url, timeout=(2, 4), verify=verify)
        _marcar(web, r.status_code < 500, f"ping → {r.status_code}")
    except requests.RequestException as e:
        _marcar(web, False, type(e).__name__)


def _iniciar_vigia():
    if _vigia["hilo"] and _vigia["hilo"].is_alive():
        return

    def bucle():
        while True:
            try:
                _cargar_clientes()
            except Exception:  # noqa: BLE001
                pass
            for w in list(_CONOCIDOS):
                try:
                    _ping(w)
                except Exception:  # noqa: BLE001
                    pass
            fin = time.time() + REVISAR_CADA
            while time.time() < fin:   # cada 2 s atiende lecturas nuevas; cada ciclo renueva las vencidas
                try:
                    sincronizar()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(1)
    _vigia["hilo"] = threading.Thread(target=bucle, daemon=True, name="faceops-salud-aap")
    _vigia["hilo"].start()


# ── Lecturas de AAP en la base de FaceOps ─────────────────────────────────────────────────────────────────────────────
# Regla: una página (petición GET) nunca llama a AAP. Toda lectura (GET a la API) que se hace al pintar una página se sirve
# desde la tabla aap_lecturas; el sincronizador de fondo (este vigía) la renueva cada TTL_LECTURA mientras alguna página la
# use. A AAP solo se llama en vivo al lanzar una acción (peticiones POST de FaceOps) y desde las tareas de fondo.
_tabla_ok = {}


def _tabla():
    from .bd import RUTA, con
    if _tabla_ok.get(RUTA):
        return
    _tabla_ok[RUTA] = True
    con().execute("CREATE TABLE IF NOT EXISTS aap_lecturas (clave TEXT PRIMARY KEY, web TEXT, url TEXT, params TEXT, datos TEXT, "
                  "actualizado REAL, usado REAL, error TEXT DEFAULT '')")


def _clave(web, url, params):
    import json
    return url + "?" + json.dumps(params or {}, sort_keys=True, default=str)


def _guardar(web, url, params, datos=None, error=""):
    import json
    from .bd import ej
    _tabla()
    k = _clave(web, url, params)
    if error:
        ej("UPDATE aap_lecturas SET error=? WHERE clave=?", error[:300], k)
        return
    ej("INSERT INTO aap_lecturas(clave, web, url, params, datos, actualizado, usado, error) VALUES (?,?,?,?,?,?,?,'') "
       "ON CONFLICT(clave) DO UPDATE SET datos=excluded.datos, actualizado=excluded.actualizado, error=''",
       k, web, url, json.dumps(params or {}, default=str), json.dumps(datos, ensure_ascii=False, default=str), time.time(), time.time())


def _leer_bd(web, url, params):
    import json
    from .bd import ej, q1
    _tabla()
    k = _clave(web, url, params)
    f = q1("SELECT datos, actualizado, usado FROM aap_lecturas WHERE clave=?", k)
    if f:
        if time.time() - (f.get("usado") or 0) > 30:
            try:
                ej("UPDATE aap_lecturas SET usado=? WHERE clave=?", time.time(), k)
            except Exception:  # noqa: BLE001 - p. ej. disco lleno: la lectura sigue sirviendo
                pass
        return json.loads(f["datos"]) if f["datos"] else {}
    return None


def _traer(web, url, params):
    """Lectura en vivo desde el sincronizador (fondo): la guarda en la base."""
    ses = _SESIONES.get(web)
    if not ses or not disponible(web):
        return False
    try:
        r = ses.get(url, params=params or None, timeout=(2, 20))
    except requests.RequestException as e:
        if isinstance(e, (requests.ConnectionError, requests.ConnectTimeout)):
            _marcar(web, False, type(e).__name__)
        _guardar(web, url, params, error=type(e).__name__)
        return False
    if r.status_code in (502, 503, 504):
        _marcar(web, False, f"GET → {r.status_code}")
        return False
    _marcar(web, True)
    if r.status_code >= 400:
        _guardar(web, url, params, error=f"{r.status_code}: {r.text[:200]}")
        if r.status_code == 404:   # ya no existe: se guarda vacío para que la página no espere
            _guardar(web, url, params, {"_no_existe": True, "results": [], "count": 0})
        return False
    es_json = "json" in (r.headers.get("Content-Type") or "")
    _guardar(web, url, params, (r.json() if r.content else {}) if es_json else {"_txt": r.text[-200000:]})
    return True


def sincronizar(solo_pendientes=False):
    """Trae las lecturas nuevas que pidió una página y renueva las vigentes (solo en el hilo de fondo)."""
    import json
    from .bd import q
    for web, url, params in list(_pendientes):
        _pendientes.discard((web, url, params))
        _traer(web, url, json.loads(params))
    if solo_pendientes:
        return
    _tabla()
    ahora_ = time.time()
    # lo que una página está mirando ahora (usado en los últimos 2 min) se renueva cada 5 s; el resto, cada TTL_LECTURA
    for f in q("SELECT web, url, params FROM aap_lecturas WHERE (usado > ? AND actualizado < ?) OR (usado > ? AND actualizado < ?) "
               "ORDER BY usado DESC LIMIT 300", ahora_ - 120, ahora_ - 5, ahora_ - USO_VIGENTE, ahora_ - TTL_LECTURA):
        _traer(f["web"], f["url"], json.loads(f["params"] or "{}"))


def _cargar_clientes():
    """Sesiones de cada AAP configurado (tras un reinicio, el sincronizador no espera a que una página las cree)."""
    from .bd import q
    for c in q("SELECT * FROM clientes WHERE COALESCE(aap_url,'')<>'' AND COALESCE(aap_token,'')<>''"):
        if c["aap_url"].rstrip("/") not in _SESIONES:
            AAP.de_cliente(c)


def _en_pagina():
    """¿Estamos pintando una página (petición GET/HEAD de FaceOps)? Ahí no se llama a AAP."""
    try:
        from flask import has_request_context, request
        return has_request_context() and request.method in ("GET", "HEAD")
    except Exception:  # noqa: BLE001
        return False


def salud():
    """None si todo AAP conocido responde; si no, {desde, ultimo_ok, error} del caído (para el banner global)."""
    caidos = [s for s in _SALUD.values() if not s["ok"]]
    return dict(caidos[0], inicio=INICIO) if caidos else None


def disponible(web):
    s = _SALUD.get((web or "").rstrip("/"))
    return not s or s["ok"]


class AAP:
    def __init__(self, url, token, verificar=False):
        self.base = url.rstrip("/") + "/api/controller/v2/"
        self.web = url.rstrip("/")
        self.publica = (os.environ.get("FACEOPS_AAP_PUBLICA") or self.web).rstrip("/")  # la que abre el navegador del usuario
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.s.verify = bool(verificar)
        _SESIONES[self.web] = self.s
        if self.web not in _CONOCIDOS:
            _CONOCIDOS[self.web] = (self.base + "ping/", bool(verificar))
            _iniciar_vigia()

    @classmethod
    def de_cliente(cls, c):
        if not c or not c.get("aap_url") or not c.get("aap_token"):
            raise ErrorAAP("El cliente no tiene configurada la conexión con AAP")
        a = cls(c["aap_url"], c["aap_token"], c.get("aap_verificar"))
        a.cliente = c
        return a

    def _r(self, metodo, ruta, **kw):
        url_ = ruta if ruta.startswith("http") else self.base + ruta.lstrip("/")
        if metodo == "GET" and _en_pagina() and not getattr(self, "en_vivo", False):
            params_ = {k: v for k, v in (kw.get("params") or {}).items() if v is not None}
            d = _leer_bd(self.web, url_, params_)
            if d is None and not disponible(self.web):
                raise AAPNoDisponible("AAP no disponible y el dato aún no está en FaceOps")
            if d is None:   # primera vez: el sincronizador la trae; la página espera a lo más 1 s
                import json
                _pendientes.add((self.web, url_, json.dumps(params_, sort_keys=True, default=str)))
                _iniciar_vigia()
                from flask import g as _g
                espera_ = 0 if _g.get("_aap_espero") else 10   # una sola espera de 1 s por página
                _g._aap_espero = True
                for _ in range(espera_):
                    time.sleep(0.1)
                    d = _leer_bd(self.web, url_, params_)
                    if d is not None:
                        break
                if d is None:
                    raise (ErrorAAP if disponible(self.web) else AAPNoDisponible)("Sincronizando con AAP: el dato aún no está en FaceOps")
            if d.get("_no_existe"):
                raise ErrorAAP(f"AAP GET {ruta} → 404")
            return d
        if not disponible(self.web):   # cortacircuito abierto: no se espera a un AAP caído
            raise AAPNoDisponible(f"AAP no disponible desde las {time.strftime('%H:%M', time.localtime(_SALUD[self.web]['desde'] or time.time()))}")
        to = getattr(self, "timeout", None)
        if to is None:
            try:
                from flask import has_request_context
                to = (2, 5) if has_request_context() else (2, 20)
            except Exception:  # noqa: BLE001
                to = (2, 20)
        try:
            r = self.s.request(metodo, url_, timeout=to, **kw)
        except (requests.ConnectionError, requests.ConnectTimeout) as e:
            _marcar(self.web, False, type(e).__name__)
            raise AAPNoDisponible(f"AAP no responde: {type(e).__name__}") from e
        except requests.RequestException as e:   # lectura lenta: el vigía confirma en segundo plano si está caído
            threading.Thread(target=_ping, args=(self.web,), daemon=True).start()
            raise ErrorAAP(f"AAP no responde: {e}") from e
        if r.status_code in (502, 503, 504):
            _marcar(self.web, False, f"{metodo} → {r.status_code}")
            raise AAPNoDisponible(f"AAP no disponible ({r.status_code})")
        _marcar(self.web, True)
        if metodo == "GET" and r.status_code < 400:
            try:
                params_ = {k: v for k, v in (kw.get("params") or {}).items() if v is not None}
                from .bd import q1
                _tabla()
                if q1("SELECT 1 FROM aap_lecturas WHERE clave=?", _clave(self.web, url_, params_)):
                    _guardar(self.web, url_, params_, r.json() if r.content else {})
            except Exception:  # noqa: BLE001
                pass
        if r.status_code >= 400:
            raise ErrorAAP(f"AAP {metodo} {ruta} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def get(self, ruta, **params):
        return self._r("GET", ruta, params=params)

    def post(self, ruta, datos=None):
        return self._r("POST", ruta, json=datos or {})

    def patch(self, ruta, datos=None):
        return self._r("PATCH", ruta, json=datos or {})

    def todos(self, ruta, **params):
        params.setdefault("page_size", 200)
        datos = self.get(ruta, **params)
        res = list(datos.get("results", []))
        while datos.get("next"):
            datos = self._r("GET", self.web + datos["next"]) if datos["next"].startswith("/") else self._r("GET", datos["next"])
            res += datos.get("results", [])
        return res

    # --- consultas usadas por FaceOps
    def ping(self):
        return self.get("ping/")

    def yo(self):
        return self.get("me/")["results"][0]

    def plantillas(self):
        return self.todos("job_templates/")

    def workflows(self):
        return self.todos("workflow_job_templates/")

    def encuesta(self, tipo, id_):
        ruta = f"{'job_templates' if tipo == 'job' else 'workflow_job_templates'}/{id_}/survey_spec/"
        try:
            return self.get(ruta).get("spec", [])
        except ErrorAAP:
            return []

    def proyecto(self, id_):
        return self.get(f"projects/{id_}/")

    def inventarios(self):
        return self.todos("inventories/")

    def hosts(self, inventario_id):
        return self.todos(f"inventories/{inventario_id}/hosts/")

    def facts(self, host_id):
        try:
            return self.get(f"hosts/{host_id}/ansible_facts/")
        except ErrorAAP:
            return {}

    def credencial(self, nombre):
        r = self.get("credentials/", name=nombre).get("results", [])
        return r[0]["id"] if r else None

    def lanzar(self, tipo, id_, extra_vars=None, limite=None, inventario=None, credenciales=None):
        # Ninguna automatización sale sin autorización de FaceOps Admin (validador en la nube): ID único + huella + token firmado
        from .confianza import CONTEXTO, autorizar
        ev = dict(extra_vars or {})
        ctx = CONTEXTO.get() or {}
        if ctx.get("ticket") and getattr(self, "cliente", None) and self.cliente.get("id"):   # sin créditos, nada sale a AAP
            from .creditos import saldo
            if saldo(self.cliente["id"]) <= 0:
                raise ErrorAAP("El cliente no tiene créditos disponibles: no se lanzan automatizaciones hasta una recarga")
        plantilla = f"{tipo}:{id_}"
        token, _v = autorizar(getattr(self, "cliente", None) or {"nombre": "?"}, ctx.get("automatizacion") or self._nombre(tipo, id_), plantilla,
                              dict(ev, _limite=limite or ""), ticket=ctx.get("ticket"), usuario=ctx.get("usuario", "FaceOps"),
                              origen="ticket" if ctx.get("ticket") else "faceops")
        ev["faceops_autorizacion"] = token
        datos = {"extra_vars": ev}
        if limite:
            datos["limit"] = limite
        if inventario:
            datos["inventory"] = inventario
        if credenciales:
            if tipo == "job":  # AAP exige repetir las credenciales fijas de la plantilla de otro tipo (p. ej. el token de Gitea)
                dadas = {(c.get("credential_type")) for c in (self.get(f"credentials/{cid}/") for cid in credenciales)}
                credenciales = list(credenciales) + [c["id"] for c in self.todos(f"job_templates/{id_}/credentials/")
                                                     if c["credential_type"] not in dadas and c["id"] not in credenciales]
            datos["credentials"] = credenciales
        ruta = f"{'job_templates' if tipo == 'job' else 'workflow_job_templates'}/{id_}/launch/"
        r = self.post(ruta, datos)
        return r.get("job") or r.get("workflow_job") or r.get("id")

    def _nombre(self, tipo, id_):
        try:
            return self.get(f"{'job_templates' if tipo == 'job' else 'workflow_job_templates'}/{id_}/").get("name", str(id_))
        except ErrorAAP:
            return str(id_)

    def nodos_workflow(self, id_):
        """Nodos de una plantilla de workflow en orden de ejecución: [{nombre, tipo, cuando, ujt_id}]."""
        nodos = {n["id"]: n for n in self.todos(f"workflow_job_templates/{id_}/workflow_nodes/")}
        hijos = {h for n in nodos.values() for k in ("success_nodes", "failure_nodes", "always_nodes") for h in n.get(k, [])}
        cola = [(nid, "inicio", 0) for nid in sorted(nodos) if nid not in hijos]
        nivel = {}
        for nid, _c, nv in list(cola):   # nivel = el camino más largo desde el inicio: nodos del mismo nivel corren en paralelo
            nivel[nid] = 0
        cambio = True
        while cambio:
            cambio = False
            for nid, n in nodos.items():
                for k in ("success_nodes", "failure_nodes", "always_nodes"):
                    for h in n.get(k, []):
                        if h in nodos and nid in nivel and nivel.get(h, -1) < nivel[nid] + 1 and nivel[nid] < len(nodos):
                            nivel[h] = nivel[nid] + 1
                            cambio = True
        vistos, out = set(), []
        while cola:
            nid, cuando, _nv = cola.pop(0)
            if nid in vistos or nid not in nodos:
                continue
            vistos.add(nid)
            n = nodos[nid]
            u = (n.get("summary_fields") or {}).get("unified_job_template") or {}
            tipo = {"workflow_approval": "aprobacion", "workflow_job": "workflow", "job": "plantilla",
                    "project_update": "proyecto", "inventory_update": "inventario"}.get(u.get("unified_job_type"), "otro")
            out.append({"nombre": u.get("name", "?"), "tipo": tipo, "cuando": cuando, "ujt_id": u.get("id"),
                        "descripcion": u.get("description", ""), "nivel": nivel.get(nid, 0)})
            for k, et in (("success_nodes", "si sale bien"), ("failure_nodes", "si falla o se rechaza"), ("always_nodes", "siempre")):
                cola += [(h, et, 0) for h in n.get(k, [])]
        return sorted(out, key=lambda x: x["nivel"])

    def nodos_trabajo_workflow(self, id_):
        """Estado de los nodos de un workflow en ejecución (incluye aprobaciones pendientes)."""
        return [((n.get("summary_fields") or {}).get("job") or {}) for n in self.todos(f"workflow_jobs/{id_}/workflow_nodes/")]

    def aprobacion(self, id_):
        return self.get(f"workflow_approvals/{id_}/")

    def responder_aprobacion(self, id_, aprobar):
        return self.post(f"workflow_approvals/{id_}/{'approve' if aprobar else 'deny'}/")

    def plantilla(self, tipo, id_):
        return self.get(f"{'job_templates' if tipo == 'job' else 'workflow_job_templates'}/{id_}/")

    def asegurar_flujo_aprobacion(self, auto, horas=0, inventario=None, credenciales=None, sufijo="", etapas=None):
        """Workflow en AAP «FaceOps · Aprobar › <plantilla>»: un nodo de aprobación por etapa (área) → la plantilla original.
        Así la aprobación vive en AAP (se ve en Aprobaciones de AAP) y FaceOps solo la muestra y la responde.
        Las aprobaciones no vencen (timeout 0): quedan pendientes hasta que alguien las aprueba o las rechaza."""
        if etapas is None:
            from .aprobacion_areas import grupos_de, nombre
            etapas = [[nombre(x) for x in gr] for gr in grupos_de(auto)]
        grupos = [g if isinstance(g, list) else [g] for g in etapas]
        firma = " → ".join(" + ".join(g) for g in grupos)
        nombre_wf = (f"FaceOps · Aprobar › {auto['nombre_aap']}" + (f" · {sufijo}" if sufijo else "") + f" [{firma}]")[:500]
        orig = self.plantilla(auto["aap_tipo"], auto["aap_id"])
        org = (orig.get("summary_fields", {}).get("organization") or {}).get("id")
        r = self.get("workflow_job_templates/", name=nombre_wf).get("results", [])
        datos = {"organization": org, "ask_variables_on_launch": True, "ask_limit_on_launch": True, "allow_simultaneous": True,   # una aprobación pendiente no frena a las demás
                 "description": f"Creado por FaceOps: aprobación en AAP antes de «{auto['nombre_aap']}» · etapas: {firma}"}
        wf = self._r("PATCH", f"workflow_job_templates/{r[0]['id']}/", json=datos) if r else self.post("workflow_job_templates/", dict(datos, name=nombre_wf))
        if not self.todos(f"workflow_job_templates/{wf['id']}/workflow_nodes/"):
            previos = []
            n = len(grupos)
            for k, gr in enumerate(grupos, 1):   # las áreas de un mismo grupo aprueban en paralelo; el siguiente espera a todas
                nuevos = []
                for area in gr:
                    ap = self.post(f"workflow_job_templates/{wf['id']}/workflow_nodes/", {"all_parents_must_converge": len(previos) > 1})
                    self.post(f"workflow_job_template_nodes/{ap['id']}/create_approval_template/",
                              {"name": f"Etapa {k}/{n} · {area} · ¿Ejecutar «{auto['nombre']}»?"[:500], "timeout": 0,
                               "description": f"Aprobación del área {area}, pedida desde FaceOps; se puede responder en AAP o en FaceOps"})
                    for pr in previos:
                        self.post(f"workflow_job_template_nodes/{pr}/success_nodes/", {"id": ap["id"]})
                    nuevos.append(ap["id"])
                previos = nuevos
            hijo = self.post(f"workflow_job_templates/{wf['id']}/workflow_nodes/",
                             dict({"unified_job_template": auto["aap_id"], "all_parents_must_converge": len(previos) > 1},
                                  **({"inventory": inventario} if inventario else {})))
            if credenciales and auto["aap_tipo"] == "job":  # las credenciales fijas de otro tipo (snapshots, llaves) se repiten en el nodo
                dadas = {self.get(f"credentials/{cid}/").get("credential_type") for cid in credenciales}
                credenciales = list(credenciales) + [c["id"] for c in self.todos(f"job_templates/{auto['aap_id']}/credentials/")
                                                     if c["credential_type"] not in dadas and c["id"] not in credenciales]
            propias = {c["id"] for c in self.todos(f"job_templates/{auto['aap_id']}/credentials/")} if auto["aap_tipo"] == "job" else set()
            for cr in credenciales or []:
                if cr in propias:   # AAP no deja repetir en el nodo una credencial que la plantilla ya tiene (y igual la usa)
                    continue
                self.post(f"workflow_job_template_nodes/{hijo['id']}/credentials/", {"id": cr})
            for pr in previos:
                self.post(f"workflow_job_template_nodes/{pr}/success_nodes/", {"id": hijo["id"]})
        return wf["id"]

    def trabajo(self, tipo, id_):
        return self.get(f"{'jobs' if tipo == 'job' else 'workflow_jobs'}/{id_}/")

    def salida(self, id_, lineas=40):
        url_, p_ = self.base + f"jobs/{id_}/stdout/", {"format": "txt"}
        if _en_pagina():   # en una página: la salida sincronizada en la base (se pide al sincronizador si aún no está)
            d = _leer_bd(self.web, url_, p_)
            if d is None:
                import json
                _pendientes.add((self.web, url_, json.dumps(p_, sort_keys=True)))
                _iniciar_vigia()
                return ""
            return "\n".join((d.get("_txt") or "").splitlines()[-lineas:])
        if not disponible(self.web):
            return ""
        try:
            r = self.s.get(url_, params=p_, timeout=(2, 20))
            return "\n".join(r.text.splitlines()[-lineas:])
        except requests.RequestException:
            return ""

    def url_trabajo(self, tipo, id_):
        return f"{self.publica}/execution/jobs/{'playbook' if tipo == 'job' else 'workflow'}/{id_}/output"
