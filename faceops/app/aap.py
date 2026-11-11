"""Cliente de la API de Ansible Automation Platform (controller vía gateway). Una instancia por cliente de FaceOps.

Modo `aap` (por defecto, el producto): AAP 2.7 con gateway. Modo `awx` (solo demos instantáneas en GCP): AWX + EDA comunitarios.
El modo sale de `aap_modo` del cliente o de FACEOPS_AAP_MODO; toda diferencia entre ambos vive aquí (rutas, enlaces, licencia, EDA)."""
import os

import requests
import urllib3

urllib3.disable_warnings()


class ErrorAAP(Exception):
    pass


MODOS = ("aap", "awx")
API = {"aap": "/api/controller/v2/", "awx": "/api/v2/"}
YO = {"aap": "/api/gateway/v1/me/", "awx": "/api/v2/me/"}   # validar usuario y clave contra la plataforma
UI = {   # enlaces que abre el navegador del usuario
    "aap": {"job": "/execution/jobs/playbook/{id}/output", "workflow_job": "/execution/jobs/workflow/{id}/output",
            "aprobacion": "/execution/workflow-approvals/{id}/details",
            "job_template": "/execution/templates/job-template/{id}/details",
            "workflow_template": "/execution/templates/workflow-job-template/{id}/details",
            "inventario_hosts": "/execution/infrastructure/inventories/inventory/{id}/hosts",
            "eda_regla": "/eda/rule-audits/{id}/details", "eda_activacion": "/eda/rulebook-activations/{id}/details"},
    "awx": {"job": "/#/jobs/playbook/{id}/output", "workflow_job": "/#/jobs/workflow/{id}/output",
            "aprobacion": "/#/workflow_approvals/{id}/details",
            "job_template": "/#/templates/job_template/{id}/details",
            "workflow_template": "/#/templates/workflow_job_template/{id}/details",
            "inventario_hosts": "/#/inventories/inventory/{id}/hosts",
            "eda_regla": "/eda/rule-audits/{id}/details", "eda_activacion": "/eda/rulebook-activations/{id}/details"}}
NOMBRE = {"aap": "AAP", "awx": "AWX"}


def modo(cliente=None):
    """«aap» (Ansible Automation Platform, por defecto) o «awx» (AWX/EDA comunitarios de las demos instantáneas)."""
    m = ((cliente or {}).get("aap_modo") or os.environ.get("FACEOPS_AAP_MODO") or "aap").strip().lower()
    return m if m in MODOS else "aap"


def ruta_ui(clase, id_, m=None):
    """Ruta (sin la URL base) de un objeto en la interfaz web de la plataforma según el modo."""
    return UI[m or modo()][clase].format(id=id_)


def ruta_trabajo(tipo, id_, m=None):
    return ruta_ui("workflow_job" if tipo in ("workflow", "workflow_job") else "job", id_, m)


class AAP:
    def __init__(self, url, token, verificar=False, modo_=None):
        self.modo = modo_ if modo_ in MODOS else modo()
        self.base = url.rstrip("/") + API[self.modo]
        self.web = url.rstrip("/")
        self.publica = (os.environ.get("FACEOPS_AAP_PUBLICA") or self.web).rstrip("/")  # la que abre el navegador del usuario
        # EDA: en AAP vive detrás del gateway (misma URL); en AWX es un servicio aparte (eda-server) con su propia URL
        self.eda_web = (os.environ.get("FACEOPS_EDA_URL") or self.web).rstrip("/")
        self.eda_publica = (os.environ.get("FACEOPS_EDA_PUBLICA") or (self.publica if self.eda_web == self.web else self.eda_web)).rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.s.verify = bool(verificar)

    @classmethod
    def de_cliente(cls, c):
        if not c or not c.get("aap_url") or not c.get("aap_token"):
            raise ErrorAAP("El cliente no tiene configurada la conexión con AAP")
        a = cls(c["aap_url"], c["aap_token"], c.get("aap_verificar"), modo(c))
        a.cliente = c
        return a

    def _r(self, metodo, ruta, **kw):
        try:
            r = self.s.request(metodo, ruta if ruta.startswith("http") else self.base + ruta.lstrip("/"), timeout=40, **kw)
        except requests.RequestException as e:
            raise ErrorAAP(f"AAP no responde: {e}") from e
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

    @property
    def nombre(self):
        return NOMBRE[self.modo]

    def url_ui(self, clase, id_):
        return self.publica + ruta_ui(clase, id_, self.modo)

    # --- Event-Driven Ansible (misma API /api/eda/v1/ en ambos modos; cambia la URL base y, en AWX, la autenticación)
    def eda(self, metodo, ruta, **kw):
        url = self.eda_web + "/api/eda/v1/" + ruta.lstrip("/")
        if self.eda_web != self.web:
            u, c, t = (os.environ.get(k, "") for k in ("FACEOPS_EDA_USUARIO", "FACEOPS_EDA_CLAVE", "FACEOPS_EDA_TOKEN"))
            try:
                r = requests.request(metodo, url, timeout=40, verify=self.s.verify, auth=(u, c) if u and c else None,
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {t}"} if t and not (u and c) else {})},
                                     **kw)
            except requests.RequestException as e:
                raise ErrorAAP(f"EDA no responde: {e}") from e
            if r.status_code >= 400:
                raise ErrorAAP(f"EDA {metodo} {ruta} → {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}
        return self._r(metodo, url, **kw)

    def url_eda(self, clase, id_):
        return self.eda_publica + ruta_ui(clase, id_, self.modo)

    # --- licencia: AAP la trae en config/ → license_info; AWX no tiene suscripción (no bloquea nada)
    def licencia(self):
        """license_info de AAP, o {"comunitario": True} en AWX (sin suscripción ni tope de nodos)."""
        if self.modo == "awx":
            return {"comunitario": True}
        return (self.get("config/") or {}).get("license_info") or {}

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
        try:
            r = self.s.get(self.base + f"jobs/{id_}/stdout/", params={"format": "txt"}, timeout=40)
            return "\n".join(r.text.splitlines()[-lineas:])
        except requests.RequestException:
            return ""

    def url_trabajo(self, tipo, id_):
        return self.publica + ruta_trabajo(tipo, id_, self.modo)
