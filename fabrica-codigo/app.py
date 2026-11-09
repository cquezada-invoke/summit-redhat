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

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, session

app = Flask(__name__)
app.secret_key = os.environ.get("FABRICA_SESION") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

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
    return render_template("chat.html", usuario=session["usuario"], faceops=FACEOPS_PUBLICA)

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
- Elige el stack aceptado más simple para el requerimiento (preferencia: Python 3.11 + Flask).
- Estructura mínima: Containerfile (FROM registry.access.redhat.com/ubi9/python-311:<versión fija> o ubi9/nodejs-20:<versión fija>, USER 1001,
  EXPOSE 8080), código, pruebas unitarias, README.md, deploy/deployment.yaml, deploy/service.yaml, deploy/route.yaml.
- deploy/deployment.yaml usa la imagen IMAGEN_APP (el pipeline la reemplaza), nombre de la app en metadata.name y en las etiquetas
  app.kubernetes.io/name, part-of: fabrica-codigo y version; sondas /health/live y /health/ready en el puerto 8080; requests y limits.
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


def generar(requerimiento, previo=None, correccion=""):
    pol = politicas()
    texto_pol = "\n".join(f"- {p['codigo']} [{p['area']} · {p['nivel']}] {p['titulo']}: {p['regla']}" for p in pol["politicas"])
    msgs = [{"role": "user", "content": f"Requerimiento funcional:\n{requerimiento}"}]
    if previo:
        msgs = [{"role": "user", "content": f"Requerimiento funcional:\n{previo['requerimiento']}"},
                {"role": "assistant", "content": json.dumps({k: previo[k] for k in ("nombre", "resumen", "stack", "archivos")}, ensure_ascii=False)},
                {"role": "user", "content": correccion or requerimiento}]
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=300,
                      headers={"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": MODELO, "max_tokens": 24000, "system": SISTEMA.format(politicas=texto_pol), "messages": msgs,
                            "tools": [HERRAMIENTA], "tool_choice": {"type": "tool", "name": "entregar_proyecto"}})
    r.raise_for_status()
    rj = r.json()
    uso = next((b for b in rj.get("content", []) if b.get("type") == "tool_use"), None)
    if not uso or rj.get("stop_reason") == "max_tokens":
        raise ValueError("respuesta incompleta del agente")
    d = dict(uso["input"])
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
        malos.append("PD-01: falta el Containerfile")
    m = re.search(r"^FROM\s+(\S+)", cf, re.M)
    if not m or not m.group(1).startswith("registry.access.redhat.com/ubi9/") or ":" not in m.group(1).split("/")[-1] or m.group(1).endswith(":latest"):
        malos.append("PD-02: la imagen base debe ser UBI 9 con versión fija")
    usr = re.findall(r"^USER\s+(\S+)", cf, re.M)
    if not usr or usr[-1] in ("0", "root"):
        malos.append("PD-03: debe terminar con USER no root (1001)")
    if not re.search(r"^EXPOSE\s+8080", cf, re.M):
        malos.append("PD-04: debe exponer el puerto 8080")
    dep = "\n".join(v for k, v in archivos.items() if k.startswith("deploy/"))
    for clave, pol in (("livenessProbe", "PD-06"), ("readinessProbe", "PD-06"), ("limits", "PD-07"), ("requests", "PD-07"),
                       ("kind: Route", "PD-08"), ("termination: edge", "PD-08"), ("IMAGEN_APP", "PD-08")):
        if clave not in dep:
            malos.append(f"{pol}: deploy/ no tiene {clave}")
    for ruta, cont in archivos.items():
        if re.search(r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*['\"][^'\"\s]{6,}", cont):
            malos.append(f"PD-05: posible secreto en {ruta}")
    if not any(re.search(r"(^|/)(test_|tests?/)|\.test\.|_test\.", k) for k in archivos):
        malos.append("PD-16: faltan pruebas unitarias")
    return sorted(set(malos))


@app.route("/api/generar", methods=["POST"])
def api_generar():
    usuario, sid = _sesion()
    d = request.get_json(silent=True) or {}
    req_ = (d.get("mensaje") or "").strip()[:4000]
    if len(req_) < 10:
        return jsonify({"error": "Describe el requerimiento funcional"}), 400
    previo = PROYECTOS.get(sid) if d.get("ajustar") else None
    try:
        p = generar(req_, previo)
        malos = revisar(p["archivos"])
        if malos:   # una corrección automática antes de mostrarlo
            p2 = generar(req_, dict(p, requerimiento=req_), "Corrige estos incumplimientos de las políticas y entrega el JSON completo:\n" + "\n".join(malos))
            p, malos = p2, revisar(p2["archivos"])
    except requests.RequestException as x:
        return jsonify({"error": f"No se pudo generar ({type(x).__name__})"}), 502
    except (ValueError, KeyError):
        return jsonify({"error": "El agente no devolvió un proyecto válido; intenta de nuevo o precisa el requerimiento"}), 502
    p["requerimiento"] = (previo or {}).get("requerimiento", req_) if previo else req_
    p["revision"] = malos
    PROYECTOS[sid] = p
    return jsonify({"nombre": p["nombre"], "resumen": p.get("resumen", ""), "stack": p.get("stack", ""), "politicas": p.get("politicas_aplicadas", []),
                    "archivos": {k: v for k, v in p["archivos"].items()}, "revision": malos, "huella": p["huella_politicas"]})

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
