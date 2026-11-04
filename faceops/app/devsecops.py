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


# Herramientas de CI/CD que puede tener un cliente (puede tener varias, cada una para operaciones distintas)
TIPOS = {
    "tekton": {"nombre": "Tekton", "detalle": "OpenShift Pipelines · pipelines como código en Git", "uso": "CI/CD", "color": "#e00"},
    "jenkins": {"nombre": "Jenkins", "detalle": "Pipelines (Jenkinsfile) y jobs", "uso": "CI/CD", "color": "#d33833"},
    "azure-devops": {"nombre": "Azure DevOps", "detalle": "Azure Pipelines (YAML) y releases", "uso": "CI/CD", "color": "#0078d4"},
    "gitlab-ci": {"nombre": "GitLab CI/CD", "detalle": ".gitlab-ci.yml y runners", "uso": "CI/CD", "color": "#fc6d26"},
    "github-actions": {"nombre": "GitHub Actions", "detalle": "Workflows en .github/workflows", "uso": "CI/CD", "color": "#24292f"},
    "argocd": {"nombre": "Argo CD", "detalle": "OpenShift GitOps · despliegue continuo desde Git", "uso": "CD", "color": "#ef7b4d"},
}
USOS = {"CI": "Integración continua", "CD": "Despliegue continuo", "CI/CD": "Integración y despliegue continuo"}


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS devsecops_ajustes (
      id INTEGER PRIMARY KEY, cliente_id INTEGER, pipeline TEXT, titulo TEXT, automatizacion TEXT, orden INTEGER);
    CREATE TABLE IF NOT EXISTS devsecops_ajustes_sembrados (cliente_id INTEGER, pipeline TEXT, UNIQUE(cliente_id, pipeline));
    CREATE TABLE IF NOT EXISTS devsecops_herramientas (
      id INTEGER PRIMARY KEY, cliente_id INTEGER, tipo TEXT, nombre TEXT, url TEXT, uso TEXT, equipo TEXT, descripcion TEXT,
      creado TEXT, UNIQUE(cliente_id, nombre));
    CREATE TABLE IF NOT EXISTS devsecops_corridas (
      id INTEGER PRIMARY KEY, cliente_id INTEGER, pipeline TEXT, nombre TEXT, inicio TEXT, fin TEXT, estado TEXT, ticket_id INTEGER, visto TEXT,
      UNIQUE(cliente_id, nombre));
    CREATE TABLE IF NOT EXISTS devsecops_etapas (
      id INTEGER PRIMARY KEY, cliente_id INTEGER, pipeline TEXT, corrida TEXT, tarea TEXT, inicio TEXT, fin TEXT, estado TEXT,
      UNIQUE(cliente_id, corrida, tarea));""")


def _col_ambiente():
    from .bd import con
    for col in ("ambiente", "origen", "rama"):
        try:
            con().execute(f"ALTER TABLE devsecops_corridas ADD COLUMN {col} TEXT DEFAULT ''")
        except Exception:  # noqa: BLE001 - ya existe
            pass


def registrar_corridas(cliente_id, d, ticket_id):
    """Guarda (o actualiza) las ejecuciones que AAP leyó del clúster; la ejecución recién lanzada queda «en curso».
    Cada ejecución guarda el ambiente al que desplegó (parámetro «ambiente» del PipelineRun o, si no, el del ticket)."""
    from .bd import ahora, ej, j, q1
    _tabla()
    _col_ambiente()
    pipe = d.get("pipeline") or ""
    t = q1("SELECT parametros FROM tickets WHERE id=?", ticket_id) if ticket_id else None
    amb_t = ((j((t or {}).get("parametros"), {}) or {}).get("dp_params") or {}).get("ambiente", "")
    for r in d.get("ejecuciones") or []:
        # el ticket solo es de la ejecución si ella misma lo trae (etiqueta faceops/ticket): las demás las leyó un ticket de lectura
        tk = ticket_id if r.get("nombre") == d.get("ejecucion") else None
        ej("INSERT INTO devsecops_corridas(cliente_id, pipeline, nombre, inicio, fin, estado, ticket_id, visto, ambiente, origen, rama) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
           "ON CONFLICT(cliente_id, nombre) DO UPDATE SET fin=excluded.fin, estado=excluded.estado, visto=excluded.visto, "
           "ambiente=CASE WHEN excluded.ambiente!='' THEN excluded.ambiente ELSE devsecops_corridas.ambiente END, "
           "origen=CASE WHEN excluded.origen!='' THEN excluded.origen ELSE devsecops_corridas.origen END, "
           "rama=CASE WHEN excluded.rama!='' THEN excluded.rama ELSE devsecops_corridas.rama END",
           cliente_id, pipe, r.get("nombre"), r.get("inicio") or "", r.get("fin") or "", r.get("estado") or "", tk, ahora(),
           r.get("ambiente") or "", r.get("origen") or "", r.get("rama") or "")
    for x in d.get("etapas") or []:   # cada stage (TaskRun) de cada ejecución, con su inicio y fin
        if x.get("corrida") and x.get("tarea"):
            ej("INSERT INTO devsecops_etapas(cliente_id, pipeline, corrida, tarea, inicio, fin, estado) VALUES (?,?,?,?,?,?,?) "
               "ON CONFLICT(cliente_id, corrida, tarea) DO UPDATE SET inicio=excluded.inicio, fin=excluded.fin, estado=excluded.estado",
               cliente_id, pipe, x["corrida"], x["tarea"], x.get("inicio") or "", x.get("fin") or "", x.get("estado") or "")
    if d.get("ejecucion"):
        ej("INSERT OR IGNORE INTO devsecops_corridas(cliente_id, pipeline, nombre, inicio, estado, ticket_id, visto, ambiente, origen) VALUES (?,?,?,?,?,?,?,?,?)",
           cliente_id, pipe, d["ejecucion"], ahora(), "Running", ticket_id, ahora(), amb_t, "faceops")


def corridas(cliente_id, pipeline):
    from .bd import q
    _tabla()
    _col_ambiente()
    filas = q("SELECT c.*, t.numero ticket_numero FROM devsecops_corridas c LEFT JOIN tickets t ON t.id=c.ticket_id "
              "WHERE c.cliente_id=? AND c.pipeline=? ORDER BY COALESCE(NULLIF(c.inicio,''), c.visto) DESC LIMIT 50", cliente_id, pipeline)
    suma = {}
    for x in q("SELECT corrida, inicio, fin FROM devsecops_etapas WHERE cliente_id=? AND pipeline=? AND fin!=''", cliente_id, pipeline):
        sg = _seg(x["inicio"], x["fin"])
        if sg is not None:
            suma[x["corrida"]] = suma.get(x["corrida"], 0) + sg
    for f in filas:   # duración: la suma de lo que tomó cada etapa (caja) del pipeline en esa ejecución
        f["dur"] = texto_dur(suma[f["nombre"]]) if f["nombre"] in suma else ""
        if "-faceops-" not in (f["nombre"] or "") and f.get("origen") != "faceops":   # no la lanzó FaceOps: no es de ese ticket
            f["ticket_id"], f["ticket_numero"], f["origen"] = None, None, f.get("origen") or "manual"
    return filas


def herramientas(cliente_id, equipos=()):
    """Herramientas de CI/CD del cliente. Se siembra Tekton (la del laboratorio) y cada servidor con Jenkins aparece solo."""
    from .bd import ahora, ej, q
    _tabla()
    if not q("SELECT id FROM devsecops_herramientas WHERE cliente_id=? AND tipo='tekton'", cliente_id):
        ej("INSERT OR IGNORE INTO devsecops_herramientas(cliente_id, tipo, nombre, url, uso, equipo, descripcion, creado) VALUES (?,?,?,?,?,?,?,?)",
           cliente_id, "tekton", "Tekton · Centro de datos Santiago", "", "CI/CD", "anfitrion-kvm",
           "Pipelines de las aplicaciones (JBoss, contenedores, Python) y de la calidad de los playbooks", ahora())
    for e in equipos:
        if "jenkins" in (e.get("skills") or []) or "jenkins" in (e.get("nombre") or ""):
            ej("INSERT OR IGNORE INTO devsecops_herramientas(cliente_id, tipo, nombre, url, uso, equipo, descripcion, creado) VALUES (?,?,?,?,?,?,?,?)",
               cliente_id, "jenkins", f"Jenkins · {e['nombre']}", "", "CI/CD", e["nombre"], "Jenkins LTS con Pipeline y Git", ahora())
    return q("SELECT * FROM devsecops_herramientas WHERE cliente_id=? ORDER BY CASE tipo WHEN 'tekton' THEN 0 WHEN 'jenkins' THEN 1 ELSE 2 END, nombre", cliente_id)


def agregar_herramienta(cliente_id, f):
    from .bd import ahora, ej
    _tabla()
    tipo, nombre, url, uso = f.get("tipo", ""), (f.get("nombre") or "").strip()[:80], (f.get("url") or "").strip()[:200], f.get("uso", "")
    if tipo not in TIPOS or not nombre or uso not in USOS:
        raise ValueError("Elige el tipo de herramienta, su nombre y para qué se usa")
    if url and not re.fullmatch(r"https?://[^\s\"'<>]{3,190}", url):
        raise ValueError("URL no válida")
    ej("INSERT INTO devsecops_herramientas(cliente_id, tipo, nombre, url, uso, equipo, descripcion, creado) VALUES (?,?,?,?,?,?,?,?)",
       cliente_id, tipo, nombre, url, uso, (f.get("equipo") or "").strip()[:80], (f.get("descripcion") or "").strip()[:200], ahora())


# Ajuste operativo: lo que FaceOps lanza (con aprobación) durante el despliegue continuo de cada pipeline.
# (título, automatización de AAP o "" si aún no existe en el catálogo)
AJUSTES_BASE = {
    "app-jboss": [("Preparar la plataforma (dependencias y base de datos)", "Plataforma · Preparar dependencias"),
                  ("Desplegar la app en JBoss", "JBoss · Desplegar app"),
                  ("Abrir regla de firewall", "Red · Solicitar acceso con aprobación"), ("Modificar base de datos", ""),
                  ("Crear cliente en Keycloak", ""), ("Emitir certificado del servicio", "Certificados · Emitir para servicio"),
                  ("Publicar en Nginx", "Autoservicio · 2 Publicar en Nginx")],
    "app-node-contenedor": [("Desplegar el segmento en OpenShift", "OpenShift · Desplegar segmento"),
                            ("Abrir regla de firewall", "Red · Solicitar acceso con aprobación"), ("Crear cliente en Keycloak", ""),
                            ("Emitir certificado del servicio", "Certificados · Emitir para servicio")],
    "app-python-sast": [("Habilitar los servidores de la API", "FaceOps · Habilitar servidor"),
                        ("Abrir regla de firewall", "Red · Solicitar acceso con aprobación"), ("Modificar base de datos", ""),
                        ("Publicar en Nginx", "Autoservicio · 2 Publicar en Nginx")],
    "infra-ansible-calidad": [("Sincronizar el proyecto en AAP", ""), ("Aplicar política de seguridad", "FaceOps · Aplicar política de seguridad")],
}


# Reverso de cada ajuste si el despliegue falla (compensación, en orden inverso): (qué hace, automatización, modo)
# modo «auto»: corre solo al fallar el despliegue · «aprobacion»: pide aprobación (puede borrar datos o servicios)
REVERSOS = {
    "Red · Solicitar acceso con aprobación": ("Revocar el acceso de red", "Red · Revocar acceso con aprobación", "auto"),
    "Certificados · Emitir para servicio": ("Revocar el certificado emitido", "", "auto"),
    "Autoservicio · 2 Publicar en Nginx": ("Retirar la publicación en Nginx", "Autoservicio · Retirar app", "auto"),
    "JBoss · Desplegar app": ("Volver a la versión anterior", "JBoss · Restablecer a versión", "auto"),
    "Plataforma · Preparar dependencias": ("Desinstalar lo que se preparó", "Plataforma · Desinstalar", "aprobacion"),
    "OpenShift · Desplegar segmento": ("Retirar el segmento desplegado", "", "aprobacion"),
    "FaceOps · Habilitar servidor": ("Eliminar los servidores creados", "FaceOps · Eliminar servidor", "aprobacion"),
    "FaceOps · Aplicar política de seguridad": ("Volver a la política anterior", "", "aprobacion"),
}
REVERSOS_TITULO = {
    "Modificar base de datos": ("Restaurar el snapshot previo de la base de datos", "", "aprobacion"),
    "Crear cliente en Keycloak": ("Eliminar el cliente creado en Keycloak", "", "auto"),
    "Sincronizar el proyecto en AAP": ("Sincronizar la versión anterior del proyecto", "", "auto"),
}


def ajustes(cliente_id, pipeline):
    """Automatizaciones del ajuste operativo del pipeline (se siembran una vez con las de referencia)."""
    from .bd import ej, q
    _tabla()
    if not q("SELECT 1 FROM devsecops_ajustes_sembrados WHERE cliente_id=? AND pipeline=?", cliente_id, pipeline):
        for i, (t, a) in enumerate(AJUSTES_BASE.get(pipeline, [])):
            ej("INSERT INTO devsecops_ajustes(cliente_id, pipeline, titulo, automatizacion, orden) VALUES (?,?,?,?,?)", cliente_id, pipeline, t, a, i)
        ej("INSERT OR IGNORE INTO devsecops_ajustes_sembrados(cliente_id, pipeline) VALUES (?,?)", cliente_id, pipeline)
    filas = q("SELECT * FROM devsecops_ajustes WHERE cliente_id=? AND pipeline=? ORDER BY orden, id", cliente_id, pipeline)
    autos = {a["nombre_aap"]: a for a in q("SELECT id, nombre, nombre_aap, descripcion, requiere_aprobacion FROM automatizaciones WHERE cliente_id=?", cliente_id)}
    probadas = {r["automatizacion_id"] for r in q("SELECT DISTINCT automatizacion_id FROM tickets WHERE cliente_id=? AND estado='exitoso'", cliente_id)}
    out = []
    est = lambda a: "por crear" if not a else ("probada" if a["id"] in probadas else "sin probar")  # noqa: E731
    for f in filas:
        a = autos.get(f["automatizacion"] or "")
        r = REVERSOS.get(f["automatizacion"] or "") or REVERSOS_TITULO.get(f["titulo"])
        rv = None
        if r:
            ra = autos.get(r[1]) if r[1] else None
            rv = {"titulo": r[0], "auto": ra, "modo": r[2], "estado": est(ra)}
        out.append(dict(f, auto=a, estado=est(a), reverso=rv))
    return out


def ajuste_agregar(cliente_id, pipeline, titulo, automatizacion):
    from .bd import ej, q1
    _tabla()
    titulo = (titulo or automatizacion or "").strip()[:80]
    if not titulo:
        raise ValueError("Indica qué ajuste se hace")
    n = (q1("SELECT MAX(orden) m FROM devsecops_ajustes WHERE cliente_id=? AND pipeline=?", cliente_id, pipeline) or {}).get("m")
    ej("INSERT INTO devsecops_ajustes(cliente_id, pipeline, titulo, automatizacion, orden) VALUES (?,?,?,?,?)",
       cliente_id, pipeline, titulo, (automatizacion or "")[:120], (n or 0) + 1)


def ajuste_quitar(cliente_id, pipeline, aid):
    from .bd import ej
    ej("DELETE FROM devsecops_ajustes WHERE id=? AND cliente_id=? AND pipeline=?", aid, cliente_id, pipeline)


def _seg(a, b):
    from datetime import datetime
    try:
        return (datetime.fromisoformat(b.replace("Z", "+00:00")) - datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds()
    except (ValueError, AttributeError):
        return None


def texto_dur(seg):
    if seg is None:
        return ""
    seg = int(round(seg))
    return f"{seg} s" if seg < 60 else (f"{seg // 60} min {seg % 60:02d} s" if seg < 3600 else f"{seg // 3600} h {seg % 3600 // 60:02d} min")


def ultimo_ambiente(cliente_id, pipeline):
    """Ambiente de la última ejecución (lo que de verdad se desplegó); '' si nunca se ejecutó."""
    from .bd import q1
    _tabla()
    _col_ambiente()
    r = q1("SELECT ambiente FROM devsecops_corridas WHERE cliente_id=? AND pipeline=? AND COALESCE(ambiente,'')!='' "
           "ORDER BY COALESCE(NULLIF(inicio,''), visto) DESC LIMIT 1", cliente_id, pipeline)
    return (r or {}).get("ambiente") or ""


def etapas_por_corrida(cliente_id, pipeline):
    """{corrida: {tarea: {'estado', 'dur', 'inicio', 'fin'}}}: cómo le fue a cada etapa en cada ejecución."""
    from .bd import q
    _tabla()
    out = {}
    for x in q("SELECT corrida, tarea, inicio, fin, estado FROM devsecops_etapas WHERE cliente_id=? AND pipeline=?", cliente_id, pipeline):
        out.setdefault(x["corrida"], {})[x["tarea"]] = {"estado": x["estado"] or "", "inicio": x["inicio"], "fin": x["fin"],
                                                       "dur": texto_dur(_seg(x["inicio"], x["fin"])) if x["fin"] else ""}
    return out


def duraciones(cliente_id, pipeline, n=5):
    """Tiempo de cada stage en las últimas ejecuciones exitosas: {tarea: {'ultima', 'prom', 'n'}} (textos listos para mostrar)."""
    from .bd import q
    _tabla()
    por = {}
    for x in q("SELECT tarea, inicio, fin FROM devsecops_etapas WHERE cliente_id=? AND pipeline=? AND estado='Succeeded' AND fin!='' "
               "ORDER BY fin DESC", cliente_id, pipeline):
        sg = _seg(x["inicio"], x["fin"])
        if sg is not None and len(por.setdefault(x["tarea"], [])) < n:
            por[x["tarea"]].append(sg)
    return {k: {"ultima": texto_dur(v[0]), "prom": texto_dur(sum(v) / len(v)), "n": len(v)} for k, v in por.items() if v}


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
                                       "params": [x.get("name") for x in t.get("params") or []],
                                       "pasos_todos": [{"nombre": st.get("name", ""), "imagen": st.get("image") or "",
                                                        "script": (st.get("script") or " ".join(st.get("args") or []) or "")[:1500]} for st in ts.get("steps") or []],
                                       "yaml": yaml.safe_dump(t, allow_unicode=True, sort_keys=False, width=120)[:6000]})
    return cols


RE_REPO = r"http://[0-9.]+:3000/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git"
RE_RAMA = r"[A-Za-z0-9._/-]{1,80}"


def origen(p):
    """Repositorio y rama desde los que se lanza el pipeline (valores por defecto de sus parámetros repo-url y revision)."""
    ps = {x.get("name"): x.get("default") for x in ((p or {}).get("spec") or {}).get("params") or []}
    repo = str(ps.get("repo-url") or "")
    return {"repo": repo, "ruta": re.sub(r"^https?://[^/]+/|\.git$", "", repo), "rama": str(ps.get("revision") or "main"),
            "tiene_rama": "revision" in ps, "tiene_ambiente": "ambiente" in ps,
            # Gitflow: la rama por defecto define el ambiente (si la rama no es de Gitflow, el parámetro del pipeline)
            "ambiente": ambiente_de_rama(str(ps.get("revision") or "main")) or str(ps.get("ambiente") or "")}


AMBIENTES = {"desarrollo": "Desarrollo", "qa": "QA", "produccion": "Producción"}
# Gitflow: la rama define el ambiente al que se despliega
GITFLOW = [("main", "produccion"), ("master", "produccion"), ("release/", "qa"), ("hotfix/", "qa"), ("develop", "desarrollo"), ("feature/", "desarrollo")]


def ambiente_de_rama(rama):
    for pref, amb in GITFLOW:
        if rama == pref or (pref.endswith("/") and rama.startswith(pref)):
            return amb
    return None


def ramas_gitflow(o):
    """→ (ramas [{'rama', 'ambiente'}] desplegables según Gitflow, motivo si no se pudieron leer)."""
    ruta = o.get("ruta") or ""
    try:
        r = _api("GET", f"/repos/{ruta}/branches", params={"limit": 100}) if ruta else None
    except requests.RequestException:
        return [], "Git no responde"
    if r is None or not r.ok:
        return [], "el repositorio no existe en Git"
    orden = {"produccion": 0, "qa": 1, "desarrollo": 2}
    out = [{"rama": b["name"], "ambiente": ambiente_de_rama(b["name"])} for b in r.json()]
    out = [x for x in out if x["ambiente"]]
    out.sort(key=lambda x: (orden[x["ambiente"]], x["rama"]))
    return out, ("" if out else "el repositorio no tiene ramas de Gitflow (main, release/*, hotfix/*, develop, feature/*)")


# Dónde despliega cada pipeline: qué máquinas del inventario reciben la app en el ambiente elegido
DESTINO_REGLA = {"app-jboss": ("skill", "jboss", "servidores JBoss EAP"), "app-python-sast": ("skill", "python", "servidores de la API"),
                 "app-node-contenedor": ("categoria", "clúster openshift", "clúster OpenShift"),
                 "infra-ansible-calidad": ("aap", "", "proyecto «Políticas de seguridad» en AAP (no despliega en máquinas)")}


def _amb(x):
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFD", str(x or "").lower()) if unicodedata.category(c) != "Mn")
    return {"prod": "produccion", "produccion": "produccion", "dev": "desarrollo", "desarrollo": "desarrollo", "qa": "qa", "test": "qa", "pruebas": "qa"}.get(t, t)


def destino(nombre, ambiente, equipos, p=None):
    """→ {'tipo': 'maquinas'|'otro', 'maquinas': [...], 'texto'}: las máquinas del ambiente donde despliega el pipeline.
    Un pipeline puede fijarlas con el parámetro «maquinas» (nombres separados por coma) o «destino-skill»."""
    ps = {x.get("name"): x.get("default") for x in ((p or {}).get("spec") or {}).get("params") or []}
    regla = DESTINO_REGLA.get(nombre)
    if ps.get("maquinas"):
        nom = {x.strip() for x in str(ps["maquinas"]).split(",") if x.strip()}
        sel = [e for e in equipos if e["nombre"] in nom]
    elif ps.get("destino-skill") or (regla and regla[0] in ("skill", "categoria")):
        tipo, val = ("skill", ps["destino-skill"]) if ps.get("destino-skill") else regla[:2]
        sel = [e for e in equipos if (val in (e.get("skills") or []) if tipo == "skill" else (e.get("categoria") or "") == val)]
        sel = [e for e in sel if _amb(e.get("ambiente")) == _amb(ambiente)] if ambiente else sel
    elif regla:
        return {"tipo": "otro", "maquinas": [], "texto": regla[2]}
    else:
        return {"tipo": "otro", "maquinas": [], "texto": "sin regla de destino (define el parámetro «maquinas» o «destino-skill»)"}
    return {"tipo": "maquinas", "texto": regla[2] if regla else "", "maquinas": [{"id": e["id"], "nombre": e["nombre"], "ip": e.get("ip") or "",
                                                                                  "energia": e.get("energia") or ""} for e in sorted(sel, key=lambda e: e["nombre"])]}


CD_CLAVES = ("desplegar", "despliegue", "deploy", "preparar-plataforma", "publicar-en-aap", "promover", "promocion", "release", "rollout", "argocd", "helm")


def fase(e):
    """Integración continua (código, compilación, pruebas, seguridad, artefacto) o despliegue continuo (entrega en la plataforma)."""
    n = e["nombre"].lower()
    return "cd" if any(k in n for k in CD_CLAVES) or e.get("categoria") == "automatización" and "aap" in n else "ci"


def fases(cols):
    """Separa las columnas del diagrama en dos grupos: CI y CD (cada uno sin columnas vacías)."""
    out = {"ci": [], "cd": []}
    for col in cols:
        for f in ("ci", "cd"):
            c = [e for e in col if fase(e) == f]
            if c:
                out[f].append(c)
    return out


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
