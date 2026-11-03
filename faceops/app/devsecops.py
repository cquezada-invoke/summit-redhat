"""DevSecOps: pipelines Tekton como código. Los pipelines viven en Git (repositorio «pipelines»); FaceOps los lee,
dibuja sus etapas, permite editarlos o crear nuevos (cada cambio es un commit) y pide a AAP aplicarlos o ejecutarlos con
aprobación. FaceOps no se conecta al clúster: lo hace AAP («DevSecOps · Pipeline Tekton»)."""
import base64
import os
import re

import requests
import yaml

GITEA_API = "http://192.168.122.1:3000/api/v1"
DUENO, REPO = "repositorio", "pipelines"
CARPETA = "tekton"
AUTOMATIZACION = "DevSecOps · Pipeline Tekton"

# Etapas listas para sumar a un pipeline (DevSecOps): nombre, qué hace, categoría y su definición Tekton (taskSpec).
CATALOGO = [
    ("secretos", "Busca secretos filtrados en el código (gitleaks)", "seguridad",
     {"steps": [{"name": "gitleaks", "image": "docker.io/zricethezav/gitleaks:v8.18.4", "workingDir": "$(workspaces.fuente.path)",
                 "script": "gitleaks detect --no-git --source . --redact --verbose"}]}),
    ("sast-sonarqube", "Análisis estático del código (SonarQube)", "seguridad",
     {"steps": [{"name": "sonar", "image": "docker.io/sonarsource/sonar-scanner-cli:5", "workingDir": "$(workspaces.fuente.path)",
                 "script": "sonar-scanner -Dsonar.projectKey=$(context.pipelineRun.name) -Dsonar.host.url=$SONAR_URL -Dsonar.token=$SONAR_TOKEN"}]}),
    ("dependencias-owasp", "Revisa librerías con vulnerabilidades conocidas (OWASP Dependency-Check)", "seguridad",
     {"steps": [{"name": "dependency-check", "image": "docker.io/owasp/dependency-check:9.2.0", "workingDir": "$(workspaces.fuente.path)",
                 "script": "/usr/share/dependency-check/bin/dependency-check.sh --scan . --format HTML --failOnCVSS 9"}]}),
    ("compilar-maven", "Compila y prueba (Maven)", "construcción",
     {"steps": [{"name": "maven", "image": "docker.io/library/maven:3.9-eclipse-temurin-17", "workingDir": "$(workspaces.fuente.path)",
                 "script": "mvn -B -q package"}]}),
    ("escaneo-trivy", "Escanea vulnerabilidades del artefacto o la imagen (Trivy)", "seguridad",
     {"steps": [{"name": "trivy", "image": "docker.io/aquasec/trivy:0.53.0", "workingDir": "$(workspaces.fuente.path)",
                 "script": "trivy fs --exit-code 1 --severity CRITICAL ."}]}),
    ("politica-acs", "Valida la imagen contra las políticas de Red Hat ACS (roxctl)", "seguridad",
     {"steps": [{"name": "roxctl", "image": "quay.io/stackrox-io/roxctl:4.5.0",
                 "script": "roxctl image check --image $IMAGEN -e $ACS_CENTRAL --insecure-skip-tls-verify"}]}),
    ("firmar-cosign", "Firma la imagen (Sigstore cosign)", "cadena de suministro",
     {"steps": [{"name": "cosign", "image": "gcr.io/projectsigstore/cosign:v2.2.4", "script": "cosign sign --yes --key env://COSIGN_KEY $IMAGEN"}]}),
    ("aap-automatizacion", "Lanza una automatización en Ansible Automation Platform (con su aprobación)", "automatización",
     {"params": [{"name": "plantilla", "default": "FaceOps · ..."}],
      "steps": [{"name": "pedir-a-aap", "image": "registry.access.redhat.com/ubi9/ubi-minimal",
                 "script": "curl -sk -X POST -H \"Authorization: Bearer $AAP_TOKEN\" \"$AAP_URL/api/controller/v2/job_templates/?name=$(params.plantilla)\""}]}),
]
CAT_IMG = [("gitleaks", "seguridad", "Secretos"), ("trivy", "seguridad", "Vulnerabilidades"), ("sonar", "seguridad", "SAST"),
           ("dependency", "seguridad", "Dependencias"), ("roxctl", "seguridad", "Política ACS"), ("cosign", "cadena de suministro", "Firma"),
           ("maven", "construcción", "Compilación"), ("gradle", "construcción", "Compilación"), ("git", "fuente", "Código"),
           ("aap", "automatización", "AAP"), ("ansible", "automatización", "AAP"), ("curl", "automatización", "Integración")]


def _tok():
    return os.environ.get("FACEOPS_RESPALDO_TOKEN", "")


def _api(m, ruta, **kw):
    t = _tok()
    r = requests.request(m, GITEA_API + ruta, timeout=15, headers={"Authorization": f"token {t}"} if t else {}, **kw)
    return r


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS devsecops_corridas (
      id INTEGER PRIMARY KEY, cliente_id INTEGER, pipeline TEXT, nombre TEXT, inicio TEXT, fin TEXT, estado TEXT, ticket_id INTEGER, visto TEXT,
      UNIQUE(cliente_id, nombre));""")


def registrar_corridas(cliente_id, d, ticket_id):
    """Guarda (o actualiza) las ejecuciones que AAP leyó del clúster; la ejecución recién lanzada queda «en curso»."""
    from .bd import ahora, ej
    _tabla()
    pipe = d.get("pipeline") or ""
    for r in d.get("ejecuciones") or []:
        ej("INSERT INTO devsecops_corridas(cliente_id, pipeline, nombre, inicio, fin, estado, ticket_id, visto) VALUES (?,?,?,?,?,?,?,?) "
           "ON CONFLICT(cliente_id, nombre) DO UPDATE SET fin=excluded.fin, estado=excluded.estado, visto=excluded.visto",
           cliente_id, pipe, r.get("nombre"), r.get("inicio") or "", r.get("fin") or "", r.get("estado") or "", ticket_id, ahora())
    if d.get("ejecucion"):
        ej("INSERT OR IGNORE INTO devsecops_corridas(cliente_id, pipeline, nombre, inicio, estado, ticket_id, visto) VALUES (?,?,?,?,?,?,?)",
           cliente_id, pipe, d["ejecucion"], ahora(), "Running", ticket_id, ahora())


def corridas(cliente_id, pipeline):
    from .bd import q
    _tabla()
    return q("SELECT * FROM devsecops_corridas WHERE cliente_id=? AND pipeline=? ORDER BY COALESCE(NULLIF(inicio,''), visto) DESC LIMIT 50", cliente_id, pipeline)


def asegurar_repo(semilla=None):
    """El repositorio de pipelines existe (público para que AAP baje los YAML); si está vacío, se siembra con el pipeline de la demo."""
    if _api("GET", f"/repos/{DUENO}/{REPO}").status_code == 404:
        r = _api("POST", "/user/repos", json={"name": REPO, "auto_init": True, "default_branch": "main", "private": False,
                                              "description": "Pipelines Tekton como código (FaceOps · DevSecOps)"})
        if not r.ok:
            raise ValueError(f"No se pudo crear el repositorio de pipelines en Git: {r.status_code}")
    if semilla:
        ya = set(listar())
        for nombre, texto in semilla.items():
            if nombre not in ya:   # los pipelines de ejemplo que falten (no pisa los que ya existen)
                guardar(nombre, texto, "FaceOps", "pipeline de ejemplo")


def listar():
    r = _api("GET", f"/repos/{DUENO}/{REPO}/contents/{CARPETA}")
    if not r.ok:
        return []
    return sorted(x["name"].rsplit(".", 1)[0] for x in r.json() if x["name"].endswith((".yaml", ".yml")))


def leer(nombre):
    r = _api("GET", f"/repos/{DUENO}/{REPO}/contents/{CARPETA}/{nombre}.yaml")
    if not r.ok:
        return None, None
    d = r.json()
    return base64.b64decode(d["content"]).decode(), d.get("sha")


def historial(nombre, n=8):
    r = _api("GET", f"/repos/{DUENO}/{REPO}/commits", params={"path": f"{CARPETA}/{nombre}.yaml", "limit": n})
    return [{"sha": c["sha"][:8], "msg": c["commit"]["message"].splitlines()[0], "autor": c["commit"]["author"]["name"],
             "cuando": c["commit"]["author"]["date"], "url": c.get("html_url", "")} for c in (r.json() if r.ok else [])]


def url_raw(nombre):
    return f"http://192.168.122.1:3000/{DUENO}/{REPO}/raw/branch/main/{CARPETA}/{nombre}.yaml"


def validar(texto):
    """→ (documento, errores). Un Pipeline de Tekton con etapas de nombre único y runAfter que apunte a etapas existentes."""
    try:
        docs = [d for d in yaml.safe_load_all(texto) if d]
    except yaml.YAMLError as e:
        return None, [f"YAML inválido: {str(e)[:200]}"]
    p = next((d for d in docs if isinstance(d, dict) and d.get("kind") == "Pipeline"), None)
    if not p:
        return None, ["Falta un objeto «kind: Pipeline»"]
    err = []
    nombre = (p.get("metadata") or {}).get("name", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", nombre or ""):
        err.append("metadata.name: minúsculas, números y guiones")
    tareas = (p.get("spec") or {}).get("tasks") or []
    if not tareas:
        err.append("El pipeline no tiene etapas (spec.tasks)")
    nombres = [t.get("name") for t in tareas]
    if len(set(nombres)) != len(nombres):
        err.append("Hay etapas con el mismo nombre")
    for t in tareas:
        for ra in t.get("runAfter") or []:
            if ra not in nombres:
                err.append(f"«{t.get('name')}» corre después de «{ra}», que no existe")
        if not t.get("taskRef") and not t.get("taskSpec"):
            err.append(f"«{t.get('name')}» no tiene taskRef ni taskSpec")
    return p, err


def etapas(p):
    """Columnas del diagrama: cada etapa en la columna siguiente a la última de la que depende (runAfter)."""
    tareas = (p.get("spec") or {}).get("tasks") or []
    nivel = {}

    def niv(t, visto=()):
        if t["name"] in nivel:
            return nivel[t["name"]]
        dep = [x for x in tareas if x["name"] in (t.get("runAfter") or []) and x["name"] not in visto]
        nivel[t["name"]] = 0 if not dep else 1 + max(niv(x, visto + (t["name"],)) for x in dep)
        return nivel[t["name"]]
    for t in tareas:
        niv(t)
    cols = [[] for _ in range(max(nivel.values(), default=-1) + 1)]
    for t in tareas:
        ts = t.get("taskSpec") or {}
        pasos = [{"nombre": s.get("name", ""), "imagen": (s.get("image") or "").split("/")[-1]} for s in ts.get("steps") or []]
        txt = " ".join([t["name"]] + [s["nombre"] + " " + s["imagen"] for s in pasos]).lower()
        cat, et = next(((c, e) for k, c, e in CAT_IMG if k in txt), ("otra", "Tarea"))
        cols[nivel[t["name"]]].append({"nombre": t["name"], "despues": t.get("runAfter") or [], "pasos": pasos, "categoria": cat,
                                       "etiqueta": et, "ref": (t.get("taskRef") or {}).get("name", ""),
                                       "params": [x.get("name") for x in t.get("params") or []]})
    return cols


def guardar(nombre, texto, autor, motivo):
    """Commit del pipeline en Git. → (sha, url)"""
    ruta = f"/repos/{DUENO}/{REPO}/contents/{CARPETA}/{nombre}.yaml"
    _, sha = leer(nombre)
    datos = {"content": base64.b64encode(texto.encode()).decode(), "branch": "main",
             "message": f"{nombre}: {motivo or 'actualización'} ({autor})", "author": {"name": autor or "FaceOps", "email": "devsecops@faceops.invoke.cl"}}
    if sha:
        datos["sha"] = sha
    r = _api("PUT" if sha else "POST", ruta, json=datos)
    if not r.ok:
        raise ValueError(f"Git no aceptó el cambio ({r.status_code}): {r.text[:160]}")
    c = (r.json() or {}).get("commit") or {}
    return c.get("sha", ""), c.get("html_url", "")


def agregar_etapa(texto, clave, despues):
    """Suma una etapa del catálogo al final (o después de la indicada) y devuelve el YAML nuevo."""
    p, err = validar(texto)
    if err:
        raise ValueError("; ".join(err))
    cat = next((c for c in CATALOGO if c[0] == clave), None)
    if not cat:
        raise ValueError("Etapa desconocida")
    tareas = p["spec"]["tasks"]
    nombre, i = cat[0], 2
    while any(t["name"] == nombre for t in tareas):
        nombre, i = f"{cat[0]}-{i}", i + 1
    previa = despues if any(t["name"] == despues for t in tareas) else (tareas[-1]["name"] if tareas else None)
    nueva = {"name": nombre, "taskSpec": dict(cat[3])}
    if previa:
        nueva["runAfter"] = [previa]
    if "workspaces.fuente" in str(cat[3]):
        nueva["workspaces"] = [{"name": "fuente", "workspace": "fuente"}]
        nueva["taskSpec"] = dict(cat[3], workspaces=[{"name": "fuente"}])
    tareas.append(nueva)
    return yaml.safe_dump(p, allow_unicode=True, sort_keys=False, width=140)


PLANTILLA = """apiVersion: tekton.dev/v1
kind: Pipeline
metadata:
  name: {nombre}
spec:
  description: {desc}
  workspaces:
    - name: fuente
  tasks:
    - name: secretos
      workspaces: [{{name: fuente, workspace: fuente}}]
      taskSpec:
        workspaces: [{{name: fuente}}]
        steps:
          - name: gitleaks
            image: docker.io/zricethezav/gitleaks:v8.18.4
            workingDir: $(workspaces.fuente.path)
            script: gitleaks detect --no-git --source . --redact --verbose
"""
