"""Cliente de la API de Ansible Automation Platform (controller vía gateway). Una instancia por cliente de FaceOps."""
import requests
import urllib3

urllib3.disable_warnings()


class ErrorAAP(Exception):
    pass


class AAP:
    def __init__(self, url, token, verificar=False):
        self.base = url.rstrip("/") + "/api/controller/v2/"
        self.web = url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.s.verify = bool(verificar)

    @classmethod
    def de_cliente(cls, c):
        if not c or not c.get("aap_url") or not c.get("aap_token"):
            raise ErrorAAP("El cliente no tiene configurada la conexión con AAP")
        return cls(c["aap_url"], c["aap_token"], c.get("aap_verificar"))

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
        datos = {"extra_vars": extra_vars or {}}
        if limite:
            datos["limit"] = limite
        if inventario:
            datos["inventory"] = inventario
        if credenciales:
            datos["credentials"] = credenciales
        ruta = f"{'job_templates' if tipo == 'job' else 'workflow_job_templates'}/{id_}/launch/"
        r = self.post(ruta, datos)
        return r.get("job") or r.get("workflow_job") or r.get("id")

    def trabajo(self, tipo, id_):
        return self.get(f"{'jobs' if tipo == 'job' else 'workflow_jobs'}/{id_}/")

    def salida(self, id_, lineas=40):
        try:
            r = self.s.get(self.base + f"jobs/{id_}/stdout/", params={"format": "txt"}, timeout=40)
            return "\n".join(r.text.splitlines()[-lineas:])
        except requests.RequestException:
            return ""

    def url_trabajo(self, tipo, id_):
        return f"{self.web}/execution/jobs/{'playbook' if tipo == 'job' else 'workflow'}/{id_}/output"
