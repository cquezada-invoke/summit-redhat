"""Portal de automatización (demo): catálogo de automatizaciones de Ansible Automation Platform listo para lanzar y probar.
- Ingreso con el usuario de AAP (Gateway): el portal crea un token personal del usuario y lanza con SUS permisos; AAP audita
  quién fue. El token vive solo en la memoria del servidor y se revoca al salir.
- El catálogo sale de AAP: workflows con etiqueta portal-<sección>; la descripción y los parámetros vienen de la plantilla y su
  encuesta (una sola fuente de verdad). Cada workflow lleva un nodo de aprobación: se aprueba aquí o en AAP.
Variables: AAP_URL (https://192.168.122.1), AAP_VERIFICAR (0), PORTAL_SECRETO, AMDOCS_URL (API de órdenes emulada)."""
import json
import os
import re
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

AAP = os.environ.get("AAP_URL", "https://192.168.122.1").rstrip("/")
CTX = ssl.create_default_context() if os.environ.get("AAP_VERIFICAR") == "1" else ssl._create_unverified_context()
AAP_PUBLICO = os.environ.get("AAP_PUBLICO", "https://aap.34.176.42.56.sslip.io").rstrip("/")   # consola de AAP para los enlaces
GIT_INTERNO = os.environ.get("GIT_INTERNO", "http://192.168.122.1:3000").rstrip("/")
GIT_PUBLICO = os.environ.get("GIT_PUBLICO", "https://faceops.34.176.42.56.sslip.io:8843/git").rstrip("/")
TENANT = os.environ.get("PORTAL_ORGANIZACION", "Claro Puerto Rico")   # organización de AAP que muestra el portal ("" = todas)
_ORG = f"&organization__name={urllib.parse.quote(TENANT)}" if TENANT else ""
AMDOCS = os.environ.get("AMDOCS_URL", "https://amdocs-ordering.sno.34.176.42.56.sslip.io").rstrip("/")
SECCIONES = [
    ("infra", "Infraestructura", "Servidores, sistemas operativos y servicios base del data center"),
    ("ocp", "OpenShift", "Lo que OpenShift y ACM no hacen solos: actualizaciones guiadas, incidentes, certificados de punta a punta, accesos e informes"),
    ("telco", "Telco · Claro Puerto Rico", "Clúster de cargas para funciones de red (CNF) de Nabstract y la red del servicio"),
    ("amdocs", "Telco · Amdocs", "Órdenes de servicio, conciliación con la red y OpenShift, y despliegue de releases"),
    ("linux", "Linux", "Análisis de seguridad de servidores Linux con informe en PDF"),
    ("windows", "Windows", "Análisis de seguridad de servidores Windows con informe en PDF"),
]
SESIONES = {}   # id → {usuario, token, token_id, nombre, creada}; solo en memoria
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = os.environ.get("PORTAL_SECRETO") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=8 * 3600)


class ErrorAAP(Exception):
    def __init__(self, codigo, texto):
        super().__init__(texto)
        self.codigo, self.texto = codigo, texto


def _http(metodo, url, datos=None, cab=None, timeout=30):
    req = urllib.request.Request(url, method=metodo, data=json.dumps(datos).encode() if datos is not None else None,
                                 headers=dict({"Content-Type": "application/json", "Accept": "application/json"}, **(cab or {})))
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        raise ErrorAAP(e.code, e.read()[:600].decode(errors="replace")) from None
    except urllib.error.URLError as e:
        raise ErrorAAP(502, f"AAP no responde: {e.reason}") from None


def sesion():
    s = SESIONES.get(session.get("sid", ""))
    if not s:
        return None
    s["visto"] = time.time()
    return s


def aap(metodo, ruta, datos=None):
    s = sesion()
    if not s:
        abort(401)
    url = ruta if ruta.startswith("http") else f"{AAP}/api/controller/v2/{ruta.lstrip('/')}"
    try:
        return _http(metodo, url, datos, {"Authorization": f"Bearer {s['token']}"})
    except ErrorAAP as e:
        if e.codigo == 401:
            SESIONES.pop(session.get("sid", ""), None)
            session.clear()
            abort(401)
        raise


@app.errorhandler(401)
def _no_autorizado(_e):
    if request.path.startswith("/api/"):
        return jsonify(error="sesión vencida"), 401
    return redirect(url_for("ingresar", siguiente=request.full_path))


@app.errorhandler(ErrorAAP)
def _error_aap(e):
    if request.path.startswith("/api/"):
        return jsonify(error=e.texto, codigo=e.codigo), 400 if e.codigo < 500 else 502
    return render_template("error.html", u=sesion(), codigo=e.codigo, texto=e.texto), 400 if e.codigo < 500 else 502


@app.before_request
def _csrf():
    if request.method == "POST" and request.endpoint not in ("ingresar", "api_informe"):
        if not secrets.compare_digest(request.headers.get("X-CSRF", request.form.get("csrf", "")), session.get("csrf", "-")):
            abort(400)


def en_aap(tipo, i):
    """Enlace a la consola de AAP (el portal no reemplaza a AAP: todo se ve y se audita allá)."""
    rutas = {"wf": "templates/workflow-job-template/{}/details", "jt": "templates/job-template/{}/details", "wj": "jobs/workflow/{}/output",
             "job": "jobs/playbook/{}/output", "aprob": "administration/workflow-approvals/{}/details", "aprobs": "administration/workflow-approvals",
             "tpls": "templates", "inv": "infrastructure/inventories/inventory/{}/details", "invs": "infrastructure/inventories", "host": "infrastructure/hosts/{}/details",
             "cred": "infrastructure/credentials/{}/details", "proy": "projects/{}/details", "eda": "../decisions/rulebook-activations"}
    return f"{AAP_PUBLICO}/execution/" + rutas[tipo].format(i)


def repo_publico(url, rama="main", archivo=""):
    if not url:
        return ""
    u = url.replace(GIT_INTERNO, GIT_PUBLICO).replace("http://127.0.0.1:3000", GIT_PUBLICO)
    u = u[:-4] if u.endswith(".git") else u
    if "github.com" in u:   # AAP con el proyecto en GitHub (instalación fuera del laboratorio)
        return f"{u}/blob/{rama}/{archivo}" if archivo else u
    return f"{u}/src/branch/{rama}/{archivo}" if archivo else u


@app.route("/api/informe", methods=["POST"])
def api_informe():
    """Los playbooks de análisis envían aquí sus hallazgos; el portal arma el PDF y lo envía por correo. Solo desde la red interna."""
    ip = request.remote_addr or ""
    if not (ip.startswith("127.") or ip.startswith("192.168.122.") or ip.startswith("10.") or ip == "::1"):
        return jsonify({"error": "solo desde la red interna de AAP"}), 403
    from informe import procesar
    try:
        return jsonify(procesar(request.get_json(force=True)))
    except (ValueError, KeyError, TypeError) as x:
        return jsonify({"error": f"informe inválido ({type(x).__name__})"}), 400


@app.route("/informes/<nombre>")
def informe_descarga(nombre):
    if not sesion():
        return redirect(url_for("ingresar"))
    from informe import DIR
    if not re.fullmatch(r"[a-z0-9-]+\.pdf", nombre):
        abort(404)
    return send_from_directory(DIR, nombre, as_attachment=True)


@app.context_processor
def _contexto():
    return {"secciones": SECCIONES, "csrf": session.get("csrf", ""), "en_aap": en_aap, "tenant": TENANT}


@app.route("/ingresar", methods=["GET", "POST"])
def ingresar():
    error = ""
    if request.method == "POST":
        u, c = request.form.get("usuario", "").strip(), request.form.get("clave", "")
        basic = "Basic " + __import__("base64").b64encode(f"{u}:{c}".encode()).decode()
        try:
            t = _http("POST", f"{AAP}/api/gateway/v1/tokens/", {"description": "Portal de automatización", "scope": "write"},
                      {"Authorization": basic})
            yo = _http("GET", f"{AAP}/api/gateway/v1/me/", cab={"Authorization": f"Bearer {t['token']}"})["results"][0]
            sid = secrets.token_urlsafe(32)
            SESIONES[sid] = {"usuario": yo["username"], "nombre": (f"{yo.get('first_name', '')} {yo.get('last_name', '')}".strip() or yo["username"]),
                             "token": t["token"], "token_id": t["id"], "creada": time.time(), "visto": time.time(),
                             "admin": yo.get("is_superuser", False)}
            session.clear()
            session.permanent = True
            session.update(sid=sid, csrf=secrets.token_urlsafe(24))
            sig = request.args.get("siguiente", "/")
            return redirect(sig if sig.startswith("/") and not sig.startswith("//") else "/")
        except ErrorAAP as e:
            error = "Usuario o clave incorrectos" if e.codigo in (401, 403) else f"No se pudo validar con AAP ({e.codigo})"
    return render_template("ingresar.html", error=error, aap=AAP)


@app.route("/salir", methods=["POST"])
def salir():
    s = SESIONES.pop(session.get("sid", ""), None)
    if s:
        try:
            _http("DELETE", f"{AAP}/api/gateway/v1/tokens/{s['token_id']}/", cab={"Authorization": f"Bearer {s['token']}"})
        except ErrorAAP:
            pass
    session.clear()
    return redirect(url_for("ingresar"))


ETQ_PRUEBA = {"probada": "probada", "repetir": "falta volver a probar", "sin_probar": "sin probar"}


def estado_pruebas():
    try:
        import yaml
        base = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "estado_pruebas.yml"))) or {}
        vivo = os.path.join(os.environ.get("PORTAL_INFORMES", "/tmp/informes"), "estado_pruebas.yml")   # lo actualiza cada prueba
        if os.path.exists(vivo):
            base.update(yaml.safe_load(open(vivo)) or {})
        return base
    except (ImportError, OSError, ValueError):
        return {}


def catalogo():
    ep = estado_pruebas()
    wf = aap("GET", f"workflow_job_templates/?labels__name__startswith=portal-&page_size=200&order_by=name{_ORG}")["results"]
    items = []
    for w in wf:
        etiquetas = [x["name"] for x in w["summary_fields"].get("labels", {}).get("results", [])]
        sec = next((e.split("-", 1)[1] for e in etiquetas if e.startswith("portal-")), "infra")
        items.append({"id": w["id"], "nombre": w["name"].split("·", 1)[-1].strip() if w["name"].startswith("Portal") else w["name"],
                      "completo": w["name"], "descripcion": w["description"], "seccion": sec,
                      "puede": w["summary_fields"].get("user_capabilities", {}).get("start", False),
                      "ultima": (w["summary_fields"].get("last_job") or {}).get("status", ""),
                      "ultima_fecha": (w.get("last_job_run") or "")[:16].replace("T", " ")})
        pr = ep.get(items[-1]["nombre"]) or {}
        items[-1]["prueba"] = ETQ_PRUEBA.get(pr.get("estado"), "sin probar")
        items[-1]["prueba_nota"] = pr.get("nota", "")
        lineas = {l.split(":", 1)[0].strip(): l.split(":", 1)[1].strip() for l in (w["description"] or "").split("\n") if ":" in l}
        tipo = lineas.get("Tipo", "")
        items[-1]["tipo"] = "Informe de análisis" if "análisis" in tipo else ("Hace cambios" if "cambios" in tipo else ("Solo lectura" if tipo else ""))
        items[-1]["para"] = lineas.get("Para qué", w["description"])
        items[-1]["origen"] = lineas.get("Se lanza desde", "")
    return items


@app.route("/")
def inicio():
    u = sesion()
    if not u:
        return redirect(url_for("ingresar"))
    items = catalogo()
    sec = request.args.get("seccion", "")
    pend = aap("GET", "workflow_approvals/?status=pending&page_size=20&order_by=-id")["results"]
    return render_template("inicio.html", u=u, items=items, seccion=sec, pendientes=pend)


@app.route("/automatizacion/<int:wid>")
def ficha(wid):
    u = sesion() or abort(401)
    w = aap("GET", f"workflow_job_templates/{wid}/")
    enc = aap("GET", f"workflow_job_templates/{wid}/survey_spec/") if w.get("survey_enabled") else {}
    nodos = aap("GET", f"workflow_job_templates/{wid}/workflow_nodes/?page_size=50")["results"]
    pasos = []
    for n in nodos:
        u = dict(n["summary_fields"].get("unified_job_template", {}))
        if u.get("unified_job_type") == "job" and u.get("id"):
            try:
                jt = aap("GET", f"job_templates/{u['id']}/")
                pr = aap("GET", f"projects/{jt['project']}/") if jt.get("project") else {}
                u["aap"] = en_aap("jt", u["id"])
                u["repo"] = repo_publico(pr.get("scm_url", ""), pr.get("scm_branch") or "main", jt.get("playbook", ""))
                u["playbook"] = jt.get("playbook", "")
            except ErrorAAP:
                pass
        if u.get("id") not in [p.get("id") for p in pasos] or u.get("unified_job_type") != "job":
            pasos.append(u)
    recientes = aap("GET", f"workflow_jobs/?workflow_job_template={wid}&order_by=-id&page_size=6")["results"]
    etiquetas = [x["name"] for x in w["summary_fields"].get("labels", {}).get("results", [])]
    sec = next((e.split("-", 1)[1] for e in etiquetas if e.startswith("portal-")), "infra")
    hosts = []
    if w.get("ask_limit_on_launch") and w.get("inventory"):
        hosts = [h["name"] for h in aap("GET", f"inventories/{w['inventory']}/hosts/?page_size=200&order_by=name")["results"]]
    return render_template("ficha.html", servidor_prueba=os.environ.get("PORTAL_SERVIDOR_PRUEBA", "nodo-jboss"), u=u, w=w, preguntas=enc.get("spec", []), pasos=pasos, recientes=recientes, sec=sec, hosts=hosts)


@app.route("/automatizacion/<int:wid>/lanzar", methods=["POST"])
def lanzar(wid):
    sesion() or abort(401)
    enc = aap("GET", f"workflow_job_templates/{wid}/survey_spec/").get("spec", [])
    ev = {}
    for p in enc:
        v = request.form.getlist(p["variable"]) if p["type"] == "multiselect" else request.form.get(p["variable"], "")
        if p["type"] == "integer" and v != "":
            v = int(v)
        if v in ("", []) and not p.get("required"):
            continue
        ev[p["variable"]] = v
    cuerpo = {"extra_vars": ev}
    lim = [h for h in request.form.getlist("_limite") if h]
    if lim:
        cuerpo["limit"] = ",".join(lim)
    r = aap("POST", f"workflow_job_templates/{wid}/launch/", cuerpo)
    return redirect(en_aap("wj", r["workflow_job"]))   # se sigue y se aprueba en AAP


@app.route("/ejecucion/<int:jid>")
def ejecucion(jid):
    u = sesion() or abort(401)
    j = aap("GET", f"workflow_jobs/{jid}/")
    return render_template("ejecucion.html", u=u, j=j)


def _eventos(job_id):
    ev = aap("GET", f"jobs/{job_id}/job_events/?event__in=runner_on_ok,runner_on_failed,runner_on_skipped,runner_on_unreachable"
                    f"&order_by=counter&page_size=200")["results"]
    tareas = []
    for e in ev:
        if e["task"].startswith(("Gathering", "Resultado para el portal")):
            continue
        estado = {"runner_on_ok": "changed" if e.get("changed") else "ok", "runner_on_failed": "fallo",
                  "runner_on_skipped": "omitido", "runner_on_unreachable": "inalcanzable"}[e["event"]]
        if e["event"] == "runner_on_failed" and e.get("event_data", {}).get("ignore_errors"):
            estado = "ok"
        msg = ""
        if estado in ("fallo", "inalcanzable"):
            res = e.get("event_data", {}).get("res", {})
            msg = (res.get("msg") or res.get("stderr") or "")[:400] if isinstance(res, dict) else ""
        if tareas and tareas[-1]["tarea"] == e["task"] and tareas[-1]["host"] == e.get("host_name"):
            if estado in ("fallo", "changed"):
                tareas[-1].update(estado=estado, msg=msg or tareas[-1]["msg"])
            continue
        tareas.append({"tarea": e["task"], "host": e.get("host_name", ""), "estado": estado, "msg": msg})
    return tareas


@app.route("/api/ejecucion/<int:jid>")
def api_ejecucion(jid):
    sesion() or abort(401)
    j = aap("GET", f"workflow_jobs/{jid}/")
    nodos = aap("GET", f"workflow_jobs/{jid}/workflow_nodes/?page_size=50&order_by=id")["results"]
    pasos = []
    for n in nodos:
        sj = n["summary_fields"].get("job")
        ujt = n["summary_fields"].get("unified_job_template", {})
        p = {"nodo": n["id"], "nombre": ujt.get("name", ""), "tipo": ujt.get("unified_job_type", ""), "estado": "pendiente" if not sj else sj["status"],
             "job": sj["id"] if sj else None, "do_not_run": n.get("do_not_run")}
        if sj and ujt.get("unified_job_type") == "workflow_approval":
            p["aprobacion"] = sj["id"]
            if sj["status"] != "pending":
                a = aap("GET", f"workflow_approvals/{sj['id']}/")
                p["aprobado_por"] = (a["summary_fields"].get("approved_or_denied_by") or {}).get("username", "")
        elif sj and ujt.get("unified_job_type") == "job":
            jj = aap("GET", f"jobs/{sj['id']}/")
            p["tareas"] = _eventos(sj["id"])
            p["resultado"] = (jj.get("artifacts") or {}).get("portal_resultado")
            p["duracion"] = round(jj.get("elapsed") or 0)
        if not n.get("do_not_run") or sj:
            pasos.append(p)
    return jsonify(id=j["id"], nombre=j["name"], estado=j["status"], lanzado_por=j["summary_fields"].get("created_by", {}).get("username", "EDA"),
                   inicio=j.get("started"), fin=j.get("finished"), duracion=round(j.get("elapsed") or 0), pasos=pasos,
                   variables=json.loads(j.get("extra_vars") or "{}"))


@app.route("/api/aprobacion/<int:aid>/<accion>", methods=["POST"])
def api_aprobar(aid, accion):
    sesion() or abort(401)
    if accion not in ("approve", "deny"):
        abort(400)
    aap("POST", f"workflow_approvals/{aid}/{accion}/", {})
    return jsonify(ok=True)


@app.route("/inventario")
def inventario():
    """Servidores: los inventarios y hosts de AAP que el usuario puede ver (todo enlazado a AAP)."""
    u = sesion() or abort(401)
    invs = aap("GET", f"inventories/?page_size=100&order_by=name{_ORG}")["results"]
    sel = request.args.get("inv", type=int)
    hosts = aap("GET", f"inventories/{sel}/hosts/?page_size=200&order_by=name")["results"] if sel else []
    return render_template("inventario.html", u=u, invs=invs, sel=sel, hosts=hosts)


@app.route("/aprobaciones")
def aprobaciones():
    u = sesion() or abort(401)
    pend = aap("GET", "workflow_approvals/?status=pending&page_size=50&order_by=-id")["results"]
    return render_template("aprobaciones.html", u=u, pendientes=pend)


@app.route("/historial")
def historial():
    u = sesion() or abort(401)
    jobs = aap("GET", "workflow_jobs/?workflow_job_template__labels__name__startswith=portal-&order_by=-id&page_size=40"
                      + (f"&workflow_job_template__organization__name={urllib.parse.quote(TENANT)}" if TENANT else ""))["results"]
    return render_template("historial.html", u=u, jobs=jobs)


@app.route("/amdocs/orden", methods=["POST"])
def amdocs_orden():
    sesion() or abort(401)
    d = request.get_json(silent=True) or {}
    cuerpo = {k: d.get(k) for k in ("cliente", "oferta", "cnf", "nombre", "vlan", "prefijo", "ip_publica") if d.get(k) not in (None, "")}
    try:
        o = _http("POST", f"{AMDOCS}/orders", cuerpo)
    except ErrorAAP as e:
        return jsonify(error=f"Amdocs (emulado) no respondió: {e.texto[:200]}"), 502
    # La activación arranca de inmediato (no depende de que EDA esté escuchando); AAP pide la aprobación.
    wf = aap("GET", "workflow_job_templates/?name=" + urllib.parse.quote("Portal · Amdocs · Activar orden de servicio"))["results"]
    if wf and o.get("id"):
        r = aap("POST", f"workflow_job_templates/{wf[0]['id']}/launch/", {"extra_vars": {"orden_id": o["id"]}})
        o["ejecucion"] = r.get("workflow_job")
        o["ejecucion_url"] = en_aap("wj", r.get("workflow_job"))
    return jsonify(o)


@app.route("/api/amdocs/ordenes")
def amdocs_ordenes():
    sesion() or abort(401)
    try:
        ords = _http("GET", f"{AMDOCS}/orders")
    except ErrorAAP:
        ords = []
    eda = aap("GET", "workflow_jobs/?name__icontains=Amdocs&order_by=-id&page_size=10")["results"]
    return jsonify(ordenes=sorted(ords, key=lambda o: o["creada"], reverse=True)[:10],
                   ejecuciones=[{"id": j["id"], "estado": j["status"], "inicio": j.get("started"),
                                 "orden": json.loads(j.get("extra_vars") or "{}").get("orden_id", "")} for j in eda])


@app.route("/integracion")
def integracion():
    return render_template("integracion.html", u=sesion())


@app.route("/salud")
def salud():
    return {"ok": True, "sesiones": len(SESIONES)}


if __name__ == "__main__":
    app.run("0.0.0.0", 8120)


# ------------------------------------------------------------------ Odoo Helpdesk → Event-Driven Ansible
ODOO = {k: os.environ.get(f"ODOO_{k.upper()}", "") for k in ("url", "db", "usuario", "clave")}
ODOO_ETIQUETA = os.environ.get("ODOO_ETIQUETA", "Claro PR")
EDA_ODOO = os.environ.get("EDA_ODOO", "http://127.0.0.1:5012/endpoint")


def _odoo(modelo, metodo, args, kw=None):
    def rpc(srv, met, *a):
        r = urllib.request.Request(ODOO["url"].rstrip("/") + "/jsonrpc", headers={"Content-Type": "application/json"},
                                   data=json.dumps({"jsonrpc": "2.0", "method": "call", "id": 1, "params": {"service": srv, "method": met, "args": list(a)}}).encode())
        x = json.load(urllib.request.urlopen(r, timeout=30))
        if x.get("error"):
            raise ValueError(str(x["error"])[:200])
        return x["result"]
    uid = rpc("common", "authenticate", ODOO["db"], ODOO["usuario"], ODOO["clave"], {})
    return rpc("object", "execute_kw", ODOO["db"], uid, ODOO["clave"], modelo, metodo, args, kw or {})


def _entender(texto):
    """Qué pide el ticket: acción, servidor y servicio (reglas simples y explicables)."""
    t = texto.lower()
    srv = re.search(r"\b(nodo-[a-z0-9-]+)\b", t)
    serv = re.search(r"servicio\s+([a-z0-9@._-]+)", t)
    if any(k in t for k in ("disco", "espacio", "lleno", "filesystem")):
        acc = "liberar_disco"
    elif "reinici" in t and serv:
        acc = "reiniciar_servicio"
    else:
        acc = "diagnostico"
    return acc, (srv.group(1) if srv else ""), (serv.group(1) if serv else "")


def _vigilar_odoo():
    """Cada 30 s: tickets abiertos con la etiqueta «Claro PR» que aún no se enviaron → evento a EDA y nota en el ticket."""
    import threading
    enviados_f = os.path.join(os.environ.get("PORTAL_INFORMES", "/tmp/informes"), "odoo_enviados.json")

    def ciclo():
        while True:
            try:
                env = set(json.load(open(enviados_f))) if os.path.exists(enviados_f) else set()
                tag = _odoo("helpdesk.tag", "search", [[["name", "=ilike", ODOO_ETIQUETA]]], {"limit": 1})
                if tag:
                    cerr = _odoo("helpdesk.stage", "search", [[["fold", "=", True]]])
                    for tk in _odoo("helpdesk.ticket", "search_read", [[["tag_ids", "in", tag], ["stage_id", "not in", cerr]]],
                                    {"fields": ["id", "name", "description"], "limit": 20, "order": "id"}):
                        if tk["id"] in env:
                            continue
                        acc, srv, serv = _entender(f"{tk['name']} {re.sub('<[^>]+>', ' ', tk.get('description') or '')}")
                        if not srv:
                            _odoo("helpdesk.ticket", "message_post", [[tk["id"]]], {"body": "Ansible: indica el servidor (por ejemplo nodo-jboss) para atender el ticket.",
                                                                                    "message_type": "comment", "subtype_xmlid": "mail.mt_note"})
                        else:
                            urllib.request.urlopen(urllib.request.Request(EDA_ODOO, headers={"Content-Type": "application/json"}, data=json.dumps(
                                {"origen": "odoo", "ticket": tk["id"], "accion": acc, "servidor": srv, "servicio": serv}).encode()), timeout=10)
                            _odoo("helpdesk.ticket", "message_post", [[tk["id"]]], {
                                "body": f"Recibido por Ansible (Event-Driven Ansible): <b>{acc.replace('_', ' ')}</b> en <b>{srv}</b>. Espera aprobación en AAP.",
                                "message_type": "comment", "subtype_xmlid": "mail.mt_note"})
                        env.add(tk["id"])
                        os.makedirs(os.path.dirname(enviados_f), exist_ok=True)
                        json.dump(sorted(env), open(enviados_f, "w"))
            except Exception as x:  # noqa: BLE001 - se reintenta en el próximo ciclo
                print("odoo:", type(x).__name__, str(x)[:150], flush=True)
            time.sleep(30)
    threading.Thread(target=ciclo, daemon=True).start()


if ODOO["url"] and ODOO["clave"]:
    _vigilar_odoo()
