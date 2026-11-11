"""Prueba unitaria de la capa AAP | AWX de FaceOps con respuestas SIMULADAS de ambas APIs (no toca ninguna plataforma).

    python3 herramientas/demo-gcp/prueba_faceops_awx.py

Recorre, en modo aap (por defecto) y en modo awx: rutas del controller, paginación, usuario, lanzamiento con autorización,
workflow con aprobación (crear, aprobar), salida y enlaces a la interfaz, EDA (URL base y autenticación propias en AWX),
licencia (AWX = sin suscripción, no bloquea), prueba de conexión, validación de usuario y alertas de FaceOps."""
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="fo-awx-")
os.environ.update(FACEOPS_BD=os.path.join(TMP, "fo.db"), FACEOPS_CONFIANZA_DIR=os.path.join(TMP, "confianza"))
for k in ("FACEOPS_AAP_MODO", "FACEOPS_EDA_URL", "FACEOPS_EDA_PUBLICA", "FACEOPS_AAP_PUBLICA", "FACEOPS_EDA_USUARIO", "FACEOPS_EDA_CLAVE"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "faceops"))

import requests  # noqa: E402

from app import aap as M  # noqa: E402
from app import bd  # noqa: E402

OK = []


def chk(cond, txt):
    OK.append(bool(cond))
    print(("  ✓ " if cond else "  ✗ ") + txt)
    if not cond:
        raise SystemExit(f"FALLÓ: {txt}")


class Resp:
    def __init__(self, code, data=None, texto=None):
        self.status_code, self._d = code, data
        self.text = texto if texto is not None else json.dumps(data or {})
        self.content = self.text.encode()

    def json(self):
        return self._d


class Servidor:
    """API simulada: AAP 2.7 (gateway + /api/controller/v2 + /api/eda/v1 en la misma URL) o AWX 24 (/api/v2) + eda-server aparte."""

    def __init__(self, modo):
        self.modo, self.log, self.ids = modo, [], iter(range(100, 10000))
        self.api = M.API[modo]

    def __call__(self, metodo, url, **kw):
        self.log.append((metodo, url, kw))
        ruta = url.split("://", 1)[1].split("/", 1)[1]
        ruta = "/" + ruta
        p = kw.get("params") or {}
        # EDA
        if "/api/eda/v1/" in ruta:
            host = url.split("/api/eda/v1/")[0]
            esperado = "http://eda.local:30081" if self.modo == "awx" else "https://aap.local"
            if host != esperado:
                return Resp(404, {"detail": f"EDA no vive en {host}"})
            if self.modo == "awx" and kw.get("auth") != ("admin", "clave-eda"):
                return Resp(401, {"detail": "sin credenciales EDA"})
            if ruta.endswith("audit-rules/"):
                return Resp(200, {"results": [{"id": 7, "name": "Disco lleno", "status": "successful", "fired_at": "2099-01-01T00:00:00Z",
                                               "ruleset_name": "monitoreo", "activation_instance": {"name": "faceops-alertas"}}]})
            if ruta.endswith("activations/"):
                return Resp(200, {"results": [{"id": 3, "name": "faceops-alertas", "status": "failed", "is_enabled": True,
                                               "status_message": "error", "modified_at": "2099-01-01T00:00:00Z"}]})
        if self.modo == "aap" and ruta.startswith("/api/v2/"):
            return Resp(404, {"detail": "AAP 2.7 no expone /api/v2"})
        if self.modo == "awx" and (ruta.startswith("/api/controller/") or ruta.startswith("/api/gateway/")):
            return Resp(404, {"detail": "AWX no tiene gateway"})
        if not ruta.startswith(self.api) and not ruta.startswith(M.YO[self.modo]):
            return Resp(404, {"detail": ruta})
        r = ruta[len(self.api):] if ruta.startswith(self.api) else "me/"
        if r == "ping/":
            return Resp(200, {"version": "4.6.0" if self.modo == "aap" else "24.6.1"})
        if r == "me/":
            return Resp(200, {"results": [{"id": 1, "username": "faceops"}]})
        if r == "config/":
            return Resp(200, {"license_info": {"subscription_name": "Red Hat AAP, Premium", "sku": "MCT3695", "instance_count": 100,
                                               "automated_instances": 40, "free_instances": 60, "license_date": 4102444800}}
                        if self.modo == "aap" else {"license_info": {}, "version": "24.6.1"})
        if r == "job_templates/" and "page" not in p:
            return Resp(200, {"count": 3, "next": f"{self.api}job_templates/?page=2", "results": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]})
        if r == "job_templates/" or r.startswith("job_templates/?page=2"):
            return Resp(200, {"count": 3, "next": None, "results": [{"id": 3, "name": "C"}]})
        if r == "job_templates/9/" or r == "job_templates/9":
            return Resp(200, {"id": 9, "name": "Sistema · Reiniciar", "summary_fields": {"organization": {"id": 1}}})
        if r == "job_templates/9/launch/":
            return Resp(201, {"job": 501, "id": 501})
        if r.startswith("workflow_job_templates/") and r.endswith("/launch/"):
            return Resp(201, {"workflow_job": 601, "id": 601})
        if r == "workflow_job_templates/" and metodo == "GET":
            return Resp(200, {"results": []})
        if r == "workflow_job_templates/" and metodo == "POST":
            return Resp(201, {"id": 77, "name": kw["json"]["name"]})
        if r.startswith("workflow_job_templates/77/workflow_nodes/") and metodo == "GET":
            return Resp(200, {"results": [], "next": None})
        if r.startswith("workflow_job_templates/77/workflow_nodes/") and metodo == "POST":
            return Resp(201, {"id": next(self.ids)})
        if "create_approval_template" in r or r.endswith("/success_nodes/") or r.endswith("/credentials/") and metodo == "POST":
            return Resp(204, None, "")
        if r.startswith("job_templates/9/credentials/"):
            return Resp(200, {"results": [], "next": None})
        if r == "workflow_approvals/" or r.startswith("workflow_approvals/?"):
            return Resp(200, {"results": [{"id": 44, "name": "¿Ejecutar?", "created": "2099-01-01T00:00:00Z",
                                           "summary_fields": {"source_workflow_job": {"id": 601, "name": "FaceOps · Aprobar"}}}]})
        if r == "workflow_approvals/44/approve/":
            return Resp(204, None, "")
        if r == "unified_jobs/":
            return Resp(200, {"results": [{"id": 501, "type": "job", "name": "Sistema · Reiniciar", "status": "failed", "finished": "2099-01-01T00:00:00Z"}]})
        if r == "jobs/501/stdout/":
            return Resp(200, None, "PLAY [x]\nok: [nodo]\nPLAY RECAP")
        if r == "hosts/":
            return Resp(200, {"count": 3, "results": [{"id": 1}]})
        return Resp(404, {"detail": f"sin simular: {metodo} {r}"})


def instalar(srv):
    requests.Session.request = lambda self_, metodo, url, **kw: srv(metodo, url, **{k: v for k, v in kw.items() if k != "timeout"})
    requests.request = lambda metodo, url, **kw: srv(metodo, url, **{k: v for k, v in kw.items() if k != "timeout"})
    requests.get = lambda url, **kw: srv("GET", url, **{k: v for k, v in kw.items() if k != "timeout"})


def probar(modo):
    print(f"\n== modo {modo}")
    if modo == "awx":
        os.environ.update(FACEOPS_AAP_MODO="awx", FACEOPS_EDA_URL="http://eda.local:30081", FACEOPS_EDA_PUBLICA="https://eda.demo",
                          FACEOPS_EDA_USUARIO="admin", FACEOPS_EDA_CLAVE="clave-eda", FACEOPS_AAP_PUBLICA="https://awx.demo")
        url = "http://awx.local:30080"
    else:
        os.environ["FACEOPS_AAP_PUBLICA"] = "https://aap.demo"
        url = "https://aap.local"
    srv = Servidor(modo)
    instalar(srv)
    cli = {"id": 1, "nombre": "Prueba", "aap_url": url, "aap_token": "tok"}
    a = M.AAP.de_cliente(cli)
    chk(a.modo == modo, f"modo detectado: {a.modo}")
    chk(a.base == url + M.API[modo], f"prefijo del controller {M.API[modo]}")
    chk(a.ping()["version"], "ping")
    chk(a.yo()["username"] == "faceops", "usuario del token (me/)")
    chk([p["name"] for p in a.plantillas()] == ["A", "B", "C"], "paginación con «next» relativo")
    # lanzar: la autorización de FaceOps Admin se simula
    from app import confianza
    confianza.autorizar = lambda *x, **k: ("token-firmado", {})
    jid = a.lanzar("job", 9, {"x": 1}, limite="nodo1")
    post = [x for x in srv.log if x[0] == "POST" and x[1].endswith("job_templates/9/launch/")][-1]
    chk(jid == 501 and post[2]["json"]["limit"] == "nodo1" and post[2]["json"]["extra_vars"]["faceops_autorizacion"] == "token-firmado",
        "lanzar plantilla con límite y token de autorización")
    chk(a.lanzar("workflow", 12) == 601, "lanzar workflow")
    wf = a.asegurar_flujo_aprobacion({"nombre": "Reiniciar", "nombre_aap": "Sistema · Reiniciar", "aap_tipo": "job", "aap_id": 9},
                                     etapas=[["Operaciones"], ["Seguridad"]])
    aprob = [x for x in srv.log if "create_approval_template" in x[1]]
    chk(wf == 77 and len(aprob) == 2 and all(M.API[modo] in x[1] for x in aprob), "workflow de aprobación con 2 etapas (create_approval_template)")
    chk(a.get("workflow_approvals/", status="pending")["results"][0]["id"] == 44, "aprobación pendiente")
    a.responder_aprobacion(44, True)
    chk(any(x[1].endswith(M.API[modo] + "workflow_approvals/44/approve/") for x in srv.log), "aprobar desde FaceOps")
    chk("PLAY RECAP" in a.salida(501), "salida del trabajo")
    pub = "https://aap.demo" if modo == "aap" else "https://awx.demo"
    esp = {"aap": "/execution/jobs/playbook/501/output", "awx": "/#/jobs/playbook/501/output"}[modo]
    chk(a.url_trabajo("job", 501) == pub + esp, f"enlace al trabajo {esp}")
    chk(a.url_trabajo("workflow_job", 601).endswith({"aap": "/execution/jobs/workflow/601/output", "awx": "/#/jobs/workflow/601/output"}[modo]),
        "enlace al workflow")
    chk(a.url_ui("aprobacion", 44).endswith({"aap": "/execution/workflow-approvals/44/details", "awx": "/#/workflow_approvals/44/details"}[modo]),
        "enlace a la aprobación")
    # EDA
    act = a.eda("GET", "activations/")["results"]
    chk(act and act[0]["name"] == "faceops-alertas", "EDA en su URL base" + (" con credencial propia" if modo == "awx" else " (gateway)"))
    chk(a.url_eda("eda_regla", 7) == ("https://eda.demo" if modo == "awx" else "https://aap.demo") + "/eda/rule-audits/7/details", "enlace a la regla de EDA")
    # licencia
    lic = a.licencia()
    chk(lic.get("comunitario") if modo == "awx" else lic.get("instance_count") == 100, "licencia: " + ("sin suscripción" if modo == "awx" else "suscripción de AAP"))
    from app import licencias, replicar_maquina, bloqueo_admin
    licencias._aap_lic.clear()
    la = licencias.licencia_aap(cli)
    chk(la and (la.get("comunitario") and not la["vencida"] if modo == "awx" else la["nodos"] == 100), f"Licencias › {la and la['producto']}")
    cupo = replicar_maquina._cupo_aap(cli)
    chk(cupo is None if modo == "awx" else cupo == 60, f"cupo de nodos: {cupo} (" + ("no bloquea" if modo == "awx" else "de la suscripción") + ")")
    bd.ej("DELETE FROM clientes")
    bd.ej("INSERT INTO clientes(id, codigo, nombre, aap_url, aap_token) VALUES (1,'PRB','Prueba',?,?)", url, "tok")
    n = bloqueo_admin._nodos()
    chk(n and n["nodos"] == (3 if modo == "awx" else 40), f"nodos informados a FaceOps Admin: {n and n['fuente']}")
    # prueba de conexión y validación de usuario
    from app import conexiones, fuentes_usuarios
    from app import boveda
    boveda.leer = lambda *x, **k: {}
    conexiones._tabla()
    bd.ej("DELETE FROM conexiones")
    bd.ej("INSERT INTO conexiones(cliente_id, tipo, nombre, url, params) VALUES (1,'aap','AAP',?,?)", url, json.dumps({"url": url}))
    srv.log.clear()
    try:
        conexiones.probar(cli, "aap")
    except Exception:  # noqa: BLE001 - lo que importa es la URL que se probó
        pass
    chk(any(x[1].endswith(M.API[modo] + "ping/") for x in srv.log), f"Conexiones › probar AAP usa {M.API[modo]}ping/")
    srv.log.clear()
    fuentes_usuarios._gateway_valida({"cliente_id": 1}, "faceops", "x")
    chk(srv.log and srv.log[-1][1] == url + M.YO[modo], f"validar usuario en {M.YO[modo]}")
    # alertas de FaceOps
    from app import alertas
    alertas._cache.clear()
    r = alertas.leer(dict(cli, codigo="PRB"))
    por = {x["origen"]: x for x in r["alertas"]}
    chk(not r["errores"] and {"eda", "eda-activacion", "trabajo", "aprobacion"} <= set(por), f"alertas: {sorted(por)} sin errores")
    chk(por["trabajo"]["url"] == pub + esp and por["aprobacion"]["url"] == a.url_ui("aprobacion", 44), "alertas enlazan a la interfaz correcta")


def sql_inicial():
    c = bd.con()
    c.executescript(bd.ESQUEMA)
    for t in ("CREATE TABLE IF NOT EXISTS creditos_mov (id INTEGER PRIMARY KEY, fecha TEXT, creditos REAL, detalle TEXT, tipo TEXT)",):
        c.execute(t)


if __name__ == "__main__":
    sql_inicial()
    probar("aap")
    probar("awx")
    # sin configuración, el modo por defecto sigue siendo aap
    for k in ("FACEOPS_AAP_MODO",):
        os.environ.pop(k, None)
    chk(M.modo() == "aap" and M.modo({"aap_modo": "AWX"}) == "awx" and M.modo({"aap_modo": "otro"}) == "aap", "modo por defecto: aap")
    print(f"\n{sum(OK)}/{len(OK)} verificaciones OK")
