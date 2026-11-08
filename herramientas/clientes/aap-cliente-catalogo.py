#!/usr/bin/python3
"""Deja en el AAP de un cliente todas las acciones de FaceOps (idempotente). Se corre desde Invoke Lab:
  · lee del AAP del laboratorio las plantillas de FaceOps (nombre, playbook, descripción, encuesta y qué se pregunta al lanzar)
  · en el AAP del cliente crea la organización «FaceOps» y el proyecto «FaceOps · Acciones» desde el repositorio central (GitHub)
  · crea o actualiza cada plantilla apuntando al mismo playbook en faceops/aap/ (las credenciales e inventarios del cliente se
    agregan cuando el cliente incorpora sus máquinas; no se copian los del laboratorio)
Uso: aap-cliente-catalogo.py <ip-privada> <codigo>"""
import base64, glob, json, os, re, ssl, sys, time, urllib.error, urllib.request

IP, COD = sys.argv[1], sys.argv[2]
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AAP_DIR = os.path.join(RAIZ, "faceops", "aap")
REPO = os.environ.get("FACEOPS_REPO", "https://github.com/cquezada-invoke/summit-redhat.git")
CTX = ssl._create_unverified_context()
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
LAB = "Basic " + base64.b64encode(("admin:" + re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()).encode()).decode()
ENV = dict(l.strip().split("=", 1) for l in open(os.path.expanduser(f"~/faceops/clientes/{COD}.env")) if "=" in l)
CLI = "Basic " + base64.b64encode(("admin:" + ENV["ADMIN"]).encode()).decode()
ORGS = {"FaceOps", "Invoke", "Default"}


def http(m, url, d=None, auth=LAB, tolerar=False):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": auth})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        raise SystemExit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


def todos(base, path, auth):
    out, url = [], f"{base}/{path}"
    while url:
        r = http("GET", url, auth=auth)
        out += r["results"]
        url = (base.split("/api/")[0] + r["next"]) if r.get("next") else None
    return out


L, C = "https://localhost/api/controller/v2", f"https://{IP}/api/controller/v2"
cli = lambda m, p, d=None, t=False: http(m, f"{C}/{p}", d, auth=CLI, tolerar=t)


def asegurar(rec, nombre, datos):
    r = cli("GET", f"{rec}/?name={urllib.request.quote(nombre)}")["results"]
    return cli("PATCH", f"{rec}/{r[0]['id']}/", datos) if r else cli("POST", f"{rec}/", dict(datos, name=nombre))


org = asegurar("organizations", "FaceOps", {"description": "Acciones de FaceOps"})
proy = asegurar("projects", "FaceOps · Acciones", {"organization": org["id"], "scm_type": "git", "scm_url": REPO, "scm_branch": "main",
                                                   "scm_update_on_launch": False, "description": "Repositorio central de FaceOps (faceops/aap)"})
cli("POST", f"projects/{proy['id']}/update/", {}, t=True)
for _ in range(60):   # esperar la primera sincronización del proyecto
    p = cli("GET", f"projects/{proy['id']}/")
    if p.get("status") in ("successful", "failed", "error", "canceled"):
        break
    time.sleep(5)
print(f"· proyecto: {p.get('status')}")
inv = asegurar("inventories", "FaceOps · Máquinas del cliente", {"organization": org["id"], "description": "Máquinas que el cliente incorpora a FaceOps"})
disponibles = set(cli("GET", f"projects/{proy['id']}/playbooks/") or [])
CAMPOS = ("description", "job_type", "verbosity", "forks", "timeout", "ask_limit_on_launch", "ask_variables_on_launch", "ask_inventory_on_launch",
          "ask_credential_on_launch", "ask_job_type_on_launch", "ask_tags_on_launch", "ask_skip_tags_on_launch", "ask_verbosity_on_launch",
          "ask_execution_environment_on_launch", "extra_vars", "become_enabled", "allow_simultaneous", "survey_enabled", "use_fact_cache")
n, sin = 0, []
for t in todos(L, "job_templates/?page_size=200", LAB):
    o = ((t.get("summary_fields") or {}).get("organization") or {}).get("name", "")
    if o not in ORGS:
        continue
    pb = "faceops/aap/" + os.path.basename(t.get("playbook") or "")
    if pb not in disponibles:
        sin.append(t["name"])
        continue
    datos = {k: t.get(k) for k in CAMPOS if k in t}
    datos.update(organization=org["id"], project=proy["id"], playbook=pb, inventory=inv["id"], ask_inventory_on_launch=True, ask_credential_on_launch=True)
    nj = asegurar("job_templates", t["name"], datos)
    if t.get("survey_enabled"):
        sv = http("GET", f"{L}/job_templates/{t['id']}/survey_spec/", tolerar=True)
        if sv:
            cli("POST", f"job_templates/{nj['id']}/survey_spec/", sv, t=True)
    n += 1
print(f"· {COD}: {n} acciones en su AAP · sin playbook en el repositorio central: {len(sin)}")
for x in sin[:40]:
    print("   -", x)
