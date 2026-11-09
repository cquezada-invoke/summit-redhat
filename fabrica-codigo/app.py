"""Fábrica de código: chat donde se pide un requerimiento funcional y un agente (Claude) genera la aplicación cumpliendo las
políticas de desarrollo de FaceOps, crea el repositorio y la rama feature/… en Git y pide a FaceOps ejecutar el pipeline de Tekton
(revisión y despliegue en OpenShift, con aprobación). Se entra desde FaceOps (enlace firmado); corre en OpenShift sin root."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import base64
import threading

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, session

app = Flask(__name__)
app.secret_key = os.environ.get("FABRICA_SESION") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="None")   # se usa embebida en FaceOps (otro sitio)

FACEOPS = os.environ.get("FACEOPS_URL", "").rstrip("/")                      # API (red interna)
FACEOPS_PUBLICA = os.environ.get("FACEOPS_URL_PUBLICA", FACEOPS).rstrip("/")  # enlaces para el navegador
TOKEN = os.environ.get("FACEOPS_FABRICA_TOKEN", "")
GITEA = os.environ.get("GITEA_URL", "").rstrip("/")
GITEA_PIPELINE = os.environ.get("GITEA_URL_PIPELINE", GITEA).rstrip("/")   # cómo ve Git el pipeline
GITEA_PUBLICA = os.environ.get("GITEA_URL_PUBLICA", GITEA).rstrip("/")      # enlaces para el navegador
GITEA_TOKEN = os.environ.get("GITEA_TOKEN", "")
ORG = os.environ.get("GITEA_ORG", "fabrica")
DOMINIO_APPS = os.environ.get("FABRICA_DOMINIO_APPS", "")   # las apps quedan en https://<nombre>.<dominio>
MODELO = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
VERIFY = os.environ.get("FACEOPS_VERIFY", "false") == "true"
TRABAJOS = {}    # id de trabajo → avance de una generación (pasos que ve el usuario)
PROYECTOS = {}   # id de sesión → último proyecto generado (en memoria: el repositorio es la fuente de verdad)

# ------------------------------------------------------------------ acceso: enlace firmado desde FaceOps (60 s)


@app.route("/entrar")
def entrar():
    u, t, f = request.args.get("u", ""), request.args.get("t", ""), request.args.get("f", "")
    if not (TOKEN and u and t.isdigit() and abs(time.time() - int(t)) < 60):
        abort(403)
    esperada = hmac.new(TOKEN.encode(), f"{u}|{t}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperada, f):
        abort(403)
    session.clear()
    session["usuario"], session["sid"] = u[:60], secrets.token_hex(8)
    return redirect("/")


def _sesion():
    if not session.get("usuario"):
        abort(401)
    return session["usuario"], session["sid"]


@app.route("/salud")
def salud():
    return {"ok": True}


@app.route("/")
def inicio():
    if not session.get("usuario"):
        return render_template("fuera.html", faceops=FACEOPS_PUBLICA), 401
    return render_template("chat.html", usuario=session["usuario"], faceops=FACEOPS_PUBLICA, modelo=MODELO)

# ------------------------------------------------------------------ FaceOps y políticas


def _fo(metodo, ruta, **kw):
    return requests.request(metodo, FACEOPS + ruta, headers={"X-Fabrica-Token": TOKEN}, timeout=30, verify=VERIFY, **kw)


def politicas():
    r = _fo("GET", "/api/fabrica/politicas")
    r.raise_for_status()
    return r.json()

# ------------------------------------------------------------------ agente


SISTEMA = """Eres el agente de desarrollo de la Fábrica de código de Invoke. Construyes aplicaciones completas y pequeñas a partir de un
requerimiento funcional, listas para correr en Red Hat OpenShift, y SIEMPRE cumpliendo TODAS las políticas de desarrollo que siguen
(las obligatorias sin excepción). Si el requerimiento choca con una política, lo dices y propones la alternativa que la cumple.

{politicas}

Reglas de entrega:
- Stack: Spring Boot 3 con Java 21 y Maven (pom.xml y mvnw), como exigen las políticas.
- Estructura mínima: Containerfile multi-stage (construye con registry.access.redhat.com/ubi9/openjdk-21:<versión fija> y ejecuta con
  ubi9/openjdk-21-runtime:<versión fija>, USER 185, EXPOSE 8080), código en src/main, pruebas en src/test, README.md y en deploy/:
  deployment.yaml, service.yaml, route.yaml, configmap.yaml, hpa.yaml y pdb.yaml.
- deploy/deployment.yaml usa la imagen IMAGEN_APP (el pipeline la reemplaza), nombre de la app en metadata.name y en las etiquetas
  app.kubernetes.io/name, part-of: fabrica-codigo y version; sondas /actuator/health/liveness y /actuator/health/readiness en el puerto 8080; requests y limits.
- deploy/route.yaml: tls termination edge con insecureEdgeTerminationPolicy Redirect.
- Sin secretos en el código; configuración por variables de entorno.
- Entrega el proyecto completo SOLO con la herramienta entregar_proyecto (cada archivo con su ruta y su contenido completo)."""

HERRAMIENTA = {"name": "entregar_proyecto", "description": "Entrega el proyecto generado, completo.",
               "input_schema": {"type": "object", "required": ["nombre", "resumen", "stack", "politicas_aplicadas", "archivos"], "properties": {
                   "nombre": {"type": "string", "description": "kebab-case, máximo 30 caracteres"},
                   "resumen": {"type": "string", "description": "qué hace, 2-3 frases"},
                   "stack": {"type": "string"},
                   "politicas_aplicadas": {"type": "array", "items": {"type": "string"}},
                   "archivos": {"type": "array", "items": {"type": "object", "required": ["ruta", "contenido"],
                                "properties": {"ruta": {"type": "string"}, "contenido": {"type": "string"}}}}}}}


def generar(requerimiento, previo=None, correccion="", paso=lambda t: None):
    """Claude escribe el proyecto (streaming): a medida que llegan los archivos se informa cuál está escribiendo."""
    pol = politicas()
    paso(f"Leí las {len(pol['politicas'])} políticas de software vigentes (versión {pol['huella']})")
    texto_pol = "\n".join(f"- {p['codigo']} [{p['area']} · {p['nivel']}] {p['titulo']}: {p['regla']}" for p in pol["politicas"])
    msgs = [{"role": "user", "content": f"Requerimiento funcional:\n{requerimiento}"}]
    if previo:
        msgs = [{"role": "user", "content": f"Requerimiento funcional:\n{previo['requerimiento']}"},
                {"role": "assistant", "content": json.dumps({k: previo[k] for k in ("nombre", "resumen", "stack", "archivos")}, ensure_ascii=False)},
                {"role": "user", "content": correccion or requerimiento}]
    paso(f"Claude ({MODELO}) está diseñando la aplicación")
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=300, stream=True,
                      headers={"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": MODELO, "max_tokens": 24000, "system": SISTEMA.format(politicas=texto_pol), "messages": msgs,
                            "tools": [HERRAMIENTA], "tool_choice": {"type": "auto"}, "stream": True})
    r.raise_for_status()
    js, parada, vistos = "", "", 0
    for linea in r.iter_lines(decode_unicode=True):
        if not linea or not linea.startswith("data:"):
            continue
        try:
            ev = json.loads(linea[5:])
        except ValueError:
            continue
        if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "input_json_delta":
            js += ev["delta"].get("partial_json", "")
            rutas = re.findall(r'"ruta"\s*:\s*"([^"]+)"', js)
            if len(rutas) > vistos:
                vistos = len(rutas)
                paso(f"Escribiendo {rutas[-1]} ({vistos} archivo{'s' if vistos != 1 else ''})")
        elif ev.get("type") == "message_delta":
            parada = (ev.get("delta") or {}).get("stop_reason") or parada
        elif ev.get("type") == "error":
            raise ValueError((ev.get("error") or {}).get("message", "error del agente"))
    if not js or parada == "max_tokens":
        raise ValueError("respuesta incompleta del agente")
    d = json.loads(js)
    arch = d.get("archivos") or []
    d["archivos"] = {a["ruta"].lstrip("/"): a["contenido"] for a in arch if a.get("ruta")} if isinstance(arch, list) else dict(arch)
    if not d["archivos"]:
        raise ValueError("el agente no entregó archivos")
    d["nombre"] = re.sub(r"[^a-z0-9-]", "-", (d.get("nombre") or "app").lower()).strip("-")[:30] or "app"
    d["huella_politicas"] = pol["huella"]
    return d


def revisar(archivos):
    """Revisión previa de las políticas automáticas (la misma idea que el pipeline): lista de incumplimientos."""
    malos = []
    cf = archivos.get("Containerfile") or archivos.get("Dockerfile") or ""
    if not cf:
        malos.append("PS-21: falta el Containerfile")
    froms = re.findall(r"^FROM\s+(\S+)", cf, re.M)
    if not froms or not froms[-1].startswith("registry.access.redhat.com/ubi9/openjdk-") or ":" not in froms[-1].split("/")[-1] or froms[-1].endswith(":latest"):
        malos.append("PS-01: la imagen de ejecución debe ser ubi9/openjdk-21-runtime con versión fija")
    usr = re.findall(r"^USER\s+(\S+)", cf, re.M)
    if not usr or usr[-1] in ("0", "root"):
        malos.append("PS-08: debe terminar con USER no root")
    if not re.search(r"^EXPOSE\s+8080", cf, re.M):
        malos.append("PS-21: debe exponer el puerto 8080")
    pom = archivos.get("pom.xml", "")
    if "spring-boot-starter-parent" not in pom:
        malos.append("PS-02: falta pom.xml con spring-boot-starter-parent")
    if "SNAPSHOT" in pom:
        malos.append("PS-02: dependencias SNAPSHOT")
    if "spring-boot-starter-security" not in pom:
        malos.append("PS-06: falta spring-boot-starter-security")
    if "micrometer-registry-prometheus" not in pom:
        malos.append("PS-23: falta micrometer-registry-prometheus")
    dep = "\n".join(v for k, v in archivos.items() if k.startswith("deploy/"))
    for clave, pol in (("livenessProbe", "PS-15"), ("readinessProbe", "PS-15"), ("limits", "PS-18"), ("requests", "PS-18"),
                       ("kind: Route", "PS-21"), ("termination: edge", "PS-12"), ("IMAGEN_APP", "PS-21"),
                       ("kind: HorizontalPodAutoscaler", "PS-13"), ("kind: PodDisruptionBudget", "PS-14"), ("runAsNonRoot", "PS-08")):
        if clave not in dep:
            malos.append(f"{pol}: deploy/ no tiene {clave}")
    for ruta, cont in archivos.items():
        if re.search(r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*['\"]?[^'\"\s$]{6,}", cont) and not ruta.startswith("src/test/"):
            malos.append(f"PS-07: posible secreto en {ruta}")
    if not any(k.startswith("src/test/") for k in archivos):
        malos.append("PS-25: faltan pruebas (src/test)")
    return sorted(set(malos))


def _trabajo(tid, sid, req_, previo):
    t = TRABAJOS[tid]
    paso = lambda txt: t["pasos"].append({"t": round(time.time() - t["inicio"]), "texto": txt})   # noqa: E731
    try:
        p = generar(req_, previo, paso=paso)
        paso("Revisión previa de las políticas que verifica el pipeline")
        malos = revisar(p["archivos"])
        if malos:   # una corrección automática antes de mostrarlo
            paso(f"{len(malos)} incumplimiento{'s' if len(malos) != 1 else ''}: Claude los está corrigiendo")
            p2 = generar(req_, dict(p, requerimiento=req_), "Corrige estos incumplimientos de las políticas y entrega el proyecto completo:\n" + "\n".join(malos), paso=paso)
            p, malos = p2, revisar(p2["archivos"])
        p["requerimiento"] = (previo or {}).get("requerimiento", req_) if previo else req_
        p["revision"] = malos
        PROYECTOS[sid] = p
        paso("Listo" if not malos else f"Listo, con {len(malos)} pendiente{'s' if len(malos) != 1 else ''}")
        t["resultado"] = {"nombre": p["nombre"], "resumen": p.get("resumen", ""), "stack": p.get("stack", ""), "politicas": p.get("politicas_aplicadas", []),
                          "archivos": dict(p["archivos"]), "revision": malos, "huella": p["huella_politicas"]}
    except requests.RequestException as x:
        rs = getattr(x, "response", None)
        det = ""
        if rs is not None:
            try:
                det = f" {rs.status_code} · {str((rs.json().get('error') or {}).get('message', ''))[:200]}"
            except Exception:  # noqa: BLE001
                det = f" {rs.status_code}"
        app.logger.warning("generar: %s%s", type(x).__name__, det)
        t["error"] = f"No se pudo generar ({type(x).__name__}{det})"
    except (ValueError, KeyError) as x:
        app.logger.warning("generar: %s", x)
        t["error"] = "El agente no devolvió un proyecto válido; intenta de nuevo o precisa el requerimiento"
    t["listo"] = True


@app.route("/api/generar", methods=["POST"])
def api_generar():
    usuario, sid = _sesion()
    d = request.get_json(silent=True) or {}
    req_ = (d.get("mensaje") or "").strip()[:4000]
    if len(req_) < 10:
        return jsonify({"error": "Describe el requerimiento funcional"}), 400
    previo = PROYECTOS.get(sid) if d.get("ajustar") else None
    tid = secrets.token_hex(8)
    TRABAJOS[tid] = {"sid": sid, "inicio": time.time(), "pasos": [], "listo": False}
    for k in [k for k, v in TRABAJOS.items() if time.time() - v["inicio"] > 3600]:
        TRABAJOS.pop(k, None)
    threading.Thread(target=_trabajo, args=(tid, sid, req_, previo), daemon=True).start()
    return jsonify({"trabajo": tid})


@app.route("/api/avance/<tid>")
def api_avance(tid):
    _, sid = _sesion()
    t = TRABAJOS.get(tid)
    if not t or t["sid"] != sid:
        return jsonify({"error": "Trabajo no encontrado"}), 404
    return jsonify({"pasos": t["pasos"], "listo": t["listo"], "error": t.get("error"), "resultado": t.get("resultado"),
                    "segundos": round(time.time() - t["inicio"])})

# ------------------------------------------------------------------ Git y pipeline


def _git(metodo, ruta, **kw):
    return requests.request(metodo, f"{GITEA}/api/v1{ruta}", headers={"Authorization": f"token {GITEA_TOKEN}"}, timeout=30, **kw)


def publicar(p, usuario):
    if not _git("GET", f"/orgs/{ORG}").ok:
        _git("POST", "/orgs", json={"username": ORG, "visibility": "private"}).raise_for_status()
    repo = p["nombre"]
    if not _git("GET", f"/repos/{ORG}/{repo}").ok:
        _git("POST", f"/orgs/{ORG}/repos", json={"name": repo, "private": True, "auto_init": True, "default_branch": "main",
                                                 "description": p.get("resumen", "")[:250]}).raise_for_status()
        _git("POST", f"/repos/{ORG}/{repo}/branches", json={"new_branch_name": "develop", "old_branch_name": "main"})
    rama = "feature/" + re.sub(r"[^a-z0-9-]", "-", (p["nombre"] + "-" + time.strftime("%m%d%H%M")).lower())[:60]
    _git("POST", f"/repos/{ORG}/{repo}/branches", json={"new_branch_name": rama, "old_branch_name": "develop"}).raise_for_status()
    existentes = {}
    r = _git("GET", f"/repos/{ORG}/{repo}/git/trees/{rama}", params={"recursive": "true"})
    if r.ok:
        existentes = {x["path"]: x["sha"] for x in r.json().get("tree", []) if x.get("type") == "blob"}
    files = [{"operation": "update" if ruta in existentes else "create", "path": ruta, "sha": existentes.get(ruta),
              "content": base64.b64encode(cont.encode()).decode()} for ruta, cont in p["archivos"].items()]
    msg = f"feat: {p['nombre']} desde la Fábrica de código\n\n{p.get('requerimiento', '')[:900]}\n\nPolíticas de desarrollo: versión {p['huella_politicas']}\nPedido por: {usuario}"
    _git("POST", f"/repos/{ORG}/{repo}/contents", json={"branch": rama, "message": msg, "files": files}).raise_for_status()
    return repo, rama


@app.route("/api/publicar", methods=["POST"])
def api_publicar():
    usuario, sid = _sesion()
    p = PROYECTOS.get(sid)
    if not p:
        return jsonify({"error": "Primero genera un proyecto"}), 400
    if p["revision"]:
        return jsonify({"error": "El proyecto aún no cumple las políticas obligatorias: " + "; ".join(p["revision"])}), 409
    try:
        repo, rama = publicar(p, usuario)
    except requests.RequestException as x:
        return jsonify({"error": f"Git no aceptó el proyecto ({type(x).__name__})"}), 502
    url_repo = f"{GITEA_PIPELINE}/{ORG}/{repo}.git"
    r = _fo("POST", "/api/fabrica/ejecutar", json={"repo": url_repo, "rama": rama, "nombre": repo, "solicitante": usuario, "requerimiento": p.get("requerimiento", "")})
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": f"FaceOps respondió {r.status_code}"}
    p.update(repo=repo, rama=rama, ticket=d.get("ticket"))
    return jsonify({"repo": f"{GITEA_PUBLICA}/{ORG}/{repo}/src/branch/{rama}", "rama": rama, "ticket": d.get("ticket"), "error": d.get("error"),
                    "app_url": f"https://{repo}.{DOMINIO_APPS}" if DOMINIO_APPS else ""}), (200 if r.ok else 502)


@app.route("/api/ticket/<numero>")
def api_ticket(numero):
    _sesion()
    r = _fo("GET", f"/api/fabrica/ticket/{numero}")
    return (r.json(), r.status_code) if r.ok else ({"error": f"FaceOps respondió {r.status_code}"}, 502)
