"""Cliente de la API de Ansible Automation Platform (controller vía gateway). Una instancia por cliente de FaceOps."""
import os

import requests
import urllib3

urllib3.disable_warnings()


class ErrorAAP(Exception):
    pass


class AAP:
    def __init__(self, url, token, verificar=False):
        self.base = url.rstrip("/") + "/api/controller/v2/"
        self.web = url.rstrip("/")
        self.publica = (os.environ.get("FACEOPS_AAP_PUBLICA") or self.web).rstrip("/")  # la que abre el navegador del usuario
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.s.verify = bool(verificar)

    @classmethod
    def de_cliente(cls, c):
        if not c or not c.get("aap_url") or not c.get("aap_token"):
            raise ErrorAAP("El cliente no tiene configurada la conexión con AAP")
        a = cls(c["aap_url"], c["aap_token"], c.get("aap_verificar"))
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
        cola = [(nid, "inicio") for nid in sorted(nodos) if nid not in hijos]
        vistos, out = set(), []
        while cola:
            nid, cuando = cola.pop(0)
            if nid in vistos or nid not in nodos:
                continue
            vistos.add(nid)
            n = nodos[nid]
            u = (n.get("summary_fields") or {}).get("unified_job_template") or {}
            tipo = {"workflow_approval": "aprobacion", "workflow_job": "workflow", "job": "plantilla",
                    "project_update": "proyecto", "inventory_update": "inventario"}.get(u.get("unified_job_type"), "otro")
            out.append({"nombre": u.get("name", "?"), "tipo": tipo, "cuando": cuando, "ujt_id": u.get("id"),
                        "descripcion": u.get("description", "")})
            for k, et in (("success_nodes", "si sale bien"), ("failure_nodes", "si falla o se rechaza"), ("always_nodes", "siempre")):
                cola += [(h, et) for h in n.get(k, [])]
        return out

    def nodos_trabajo_workflow(self, id_):
        """Estado de los nodos de un workflow en ejecución (incluye aprobaciones pendientes)."""
        return [((n.get("summary_fields") or {}).get("job") or {}) for n in self.todos(f"workflow_jobs/{id_}/workflow_nodes/")]

    def aprobacion(self, id_):
        return self.get(f"workflow_approvals/{id_}/")

    def responder_aprobacion(self, id_, aprobar):
        return self.post(f"workflow_approvals/{id_}/{'approve' if aprobar else 'deny'}/")

    def plantilla(self, tipo, id_):
        return self.get(f"{'job_templates' if tipo == 'job' else 'workflow_job_templates'}/{id_}/")

    def asegurar_flujo_aprobacion(self, auto, horas=8, inventario=None, credenciales=None, sufijo=""):
        """Workflow en AAP «FaceOps · Aprobar › <plantilla>»: nodo de aprobación → la plantilla original.
        Así la aprobación vive en AAP (se ve en Aprobaciones de AAP) y FaceOps solo la muestra y la responde."""
        nombre = (f"FaceOps · Aprobar › {auto['nombre_aap']}" + (f" · {sufijo}" if sufijo else ""))[:500]
        orig = self.plantilla(auto["aap_tipo"], auto["aap_id"])
        org = (orig.get("summary_fields", {}).get("organization") or {}).get("id")
        r = self.get("workflow_job_templates/", name=nombre).get("results", [])
        datos = {"organization": org, "ask_variables_on_launch": True, "ask_limit_on_launch": True,
                 "description": f"Creado por FaceOps: aprobación en AAP antes de «{auto['nombre_aap']}»"}
        wf = self._r("PATCH", f"workflow_job_templates/{r[0]['id']}/", json=datos) if r else self.post("workflow_job_templates/", dict(datos, name=nombre))
        if not self.todos(f"workflow_job_templates/{wf['id']}/workflow_nodes/"):
            ap = self.post(f"workflow_job_templates/{wf['id']}/workflow_nodes/", {})
            self.post(f"workflow_job_template_nodes/{ap['id']}/create_approval_template/",
                      {"name": f"¿Ejecutar «{auto['nombre']}»?"[:500], "timeout": horas * 3600,
                       "description": "Aprobación pedida desde FaceOps; se puede responder en AAP o en FaceOps"})
            hijo = self.post(f"workflow_job_templates/{wf['id']}/workflow_nodes/",
                             dict({"unified_job_template": auto["aap_id"]}, **({"inventory": inventario} if inventario else {})))
            if credenciales and auto["aap_tipo"] == "job":  # las credenciales fijas de otro tipo (snapshots, llaves) se repiten en el nodo
                dadas = {self.get(f"credentials/{cid}/").get("credential_type") for cid in credenciales}
                credenciales = list(credenciales) + [c["id"] for c in self.todos(f"job_templates/{auto['aap_id']}/credentials/")
                                                     if c["credential_type"] not in dadas and c["id"] not in credenciales]
            for cr in credenciales or []:
                self.post(f"workflow_job_template_nodes/{hijo['id']}/credentials/", {"id": cr})
            self.post(f"workflow_job_template_nodes/{ap['id']}/success_nodes/", {"id": hijo["id"]})
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
        return f"{self.publica}/execution/jobs/{'playbook' if tipo == 'job' else 'workflow'}/{id_}/output"
