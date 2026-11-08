#!/usr/bin/python3
"""Carga en el AAP local del cliente el catálogo de acciones de FaceOps (idempotente): organización «FaceOps», proyecto
«FaceOps · Acciones» desde el repositorio central, inventario de máquinas del cliente y una plantilla por acción con su encuesta.
Lo usa el instalador de FaceOps. Entorno: AAP_URL (https://localhost), AAP_ADMIN_CLAVE, CATALOGO (json), FACEOPS_REPO."""
import base64, json, os, ssl, sys, time, urllib.error, urllib.parse, urllib.request

URL = os.environ.get("AAP_URL", "https://localhost").rstrip("/") + "/api/controller/v2"
AUTH = "Basic " + base64.b64encode(("admin:" + os.environ["AAP_ADMIN_CLAVE"]).encode()).decode()
REPO = os.environ.get("FACEOPS_REPO", "https://github.com/cquezada-invoke/summit-redhat.git")
CTX = ssl._create_unverified_context()


def http(m, p, d=None, tolerar=False):
    req = urllib.request.Request(f"{URL}/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": AUTH})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=90) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        raise SystemExit(f"ERROR {m} {p.split('?')[0]} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


def asegurar(rec, nombre, datos):
    r = http("GET", f"{rec}/?name={urllib.parse.quote(nombre)}")["results"]
    return http("PATCH", f"{rec}/{r[0]['id']}/", datos) if r else http("POST", f"{rec}/", dict(datos, name=nombre))


cat = json.load(open(os.environ["CATALOGO"], encoding="utf-8")).get("plantillas") or []
org = asegurar("organizations", "FaceOps", {"description": "Acciones de FaceOps"})
proy = asegurar("projects", "FaceOps · Acciones", {"organization": org["id"], "scm_type": "git", "scm_url": REPO, "scm_branch": "main",
                                                   "scm_update_on_launch": False, "description": "Repositorio central de FaceOps (faceops/aap)"})
http("POST", f"projects/{proy['id']}/update/", {}, tolerar=True)
for _ in range(90):
    p = http("GET", f"projects/{proy['id']}/")
    if p.get("status") in ("successful", "failed", "error", "canceled"):
        break
    time.sleep(5)
print(f"   proyecto «FaceOps · Acciones»: {p.get('status')}")
inv = asegurar("inventories", "FaceOps · Máquinas del cliente", {"organization": org["id"], "description": "Máquinas que el cliente incorpora a FaceOps"})
disponibles = set(http("GET", f"projects/{proy['id']}/playbooks/", tolerar=True) or [])
n, sin = 0, []
for t in cat:
    if disponibles and t["playbook"] not in disponibles:
        sin.append(t["nombre"])
        continue
    d = dict(t["datos"], organization=org["id"], project=proy["id"], playbook=t["playbook"], inventory=inv["id"],
             ask_inventory_on_launch=True, ask_credential_on_launch=True)
    nj = asegurar("job_templates", t["nombre"], d)
    if t.get("encuesta"):
        http("POST", f"job_templates/{nj['id']}/survey_spec/", t["encuesta"], tolerar=True)
    n += 1
print(f"   {n} acciones cargadas en AAP" + (f" · {len(sin)} sin playbook en esta versión" if sin else ""))
