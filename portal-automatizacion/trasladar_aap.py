#!/usr/bin/python3
"""Copia el tenant «Claro Puerto Rico» del AAP del laboratorio a otro AAP (por ejemplo el de una máquina de cliente).
Copia: organización, proyecto (desde GitHub), inventario local, plantillas y workflows del portal (con aprobaciones,
encuestas y etiquetas) y el usuario claro-demo como administrador del tenant (clave aleatoria que no se muestra).
No copia: credencial de OpenShift ni la activación de EDA (dependen del clúster del laboratorio).
Uso (en el laboratorio): DESTINO=https://10.194.0.12 DESTINO_CLAVE=... python3 trasladar_aap.py
Las claves se leen del entorno y nunca se imprimen."""
import base64, glob, json, os, re, secrets, ssl, sys, time, urllib.error, urllib.parse, urllib.request

CTX = ssl._create_unverified_context()
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE_O = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
DEST, CLAVE_D = os.environ["DESTINO"].rstrip("/"), os.environ["DESTINO_CLAVE"]
ORG, USUARIO = "Claro Puerto Rico", "claro-demo"
REPO = "https://github.com/cquezada-invoke/summit-redhat.git"
RUTAS = {"portal-automatizaciones": "portal-automatizacion/", "red-laboratorio": "demo4-redes/red-laboratorio/"}
q = urllib.parse.quote
paso = lambda t: print(f"   · {t}", flush=True)


def http(base, clave, m, p, d=None, tolerar=False):
    req = urllib.request.Request(f"{base}{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Basic " + base64.b64encode(f"admin:{clave}".encode()).decode()})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=120) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {p.split('?')[0]} -> {e.code}: {e.read()[:400].decode(errors='replace')}")


o = lambda m, p, d=None, t=False: http("https://localhost/api/controller/v2/", CLAVE_O, m, p, d, t)
c = lambda m, p, d=None, t=False: http(f"{DEST}/api/controller/v2/", CLAVE_D, m, p, d, t)
g = lambda m, p, d=None, t=False: http(f"{DEST}/api/gateway/v1/", CLAVE_D, m, p, d, t)


def todos(f, p):
    out, url = [], p
    while url:
        r = f("GET", url)
        out += r["results"]
        url = (r.get("next") or "").split("/v2/", 1)[-1].split("/v1/", 1)[-1] or None
    return out


def uno(f, recurso, **filtro):
    r = f("GET", f"{recurso}/?" + "&".join(f"{k}={q(str(v))}" for k, v in filtro.items()))["results"]
    return r[0] if r else None


def asegurar(f, recurso, nombre, datos, **filtro):
    x = uno(f, recurso, name=nombre, **filtro)
    datos = dict(datos, name=nombre)
    return f("PATCH", f"{recurso}/{x['id']}/", datos) if x else f("POST", f"{recurso}/", datos)


# --- origen -------------------------------------------------------------------------------------------------------------
org_o = uno(o, "organizations", name=ORG)
wfs = todos(o, f"workflow_job_templates/?organization={org_o['id']}&labels__name__startswith=portal-&page_size=100")
paso(f"Origen: {len(wfs)} workflows del tenant {ORG}")

# --- destino: organización, proyecto, inventario --------------------------------------------------------------------------
if not uno(g, "organizations", name=ORG):
    g("POST", "organizations/", {"name": ORG, "description": "Tenant de Claro Puerto Rico (portal de automatización)"})
for _ in range(30):
    org = uno(c, "organizations", name=ORG)
    if org:
        break
    time.sleep(3)
paso(f"Organización {ORG}")
proy = asegurar(c, "projects", "Claro PR · automatizaciones", {"organization": org["id"], "scm_type": "git", "scm_url": REPO,
                                                               "scm_branch": "main", "scm_update_on_launch": False,
                                                               "description": "Playbooks del portal desde GitHub"})
c("POST", f"projects/{proy['id']}/update/", {}, t=True)
for _ in range(120):
    if c("GET", f"projects/{proy['id']}/")["status"] in ("successful", "failed", "error", "canceled"):
        break
    time.sleep(5)
paso("Proyecto sincronizado: " + c("GET", f"projects/{proy['id']}/")["status"])
inv = asegurar(c, "inventories", "Claro PR · local", {"organization": org["id"], "description": "Sin hosts: localhost implícito"})


def etiqueta(n):
    x = uno(c, "labels", name=n, organization=org["id"])
    return x or c("POST", "labels/", {"name": n, "organization": org["id"]})


# --- plantillas --------------------------------------------------------------------------------------------------------
mapa_jt = {}


def copiar_jt(jid):
    if jid in mapa_jt:
        return mapa_jt[jid]
    jt = o("GET", f"job_templates/{jid}/")
    src = o("GET", f"projects/{jt['project']}/")["scm_url"] if jt.get("project") else ""
    pref = next((v for k, v in RUTAS.items() if k in src), "")
    datos = {k: jt[k] for k in ("description", "extra_vars", "ask_variables_on_launch", "ask_limit_on_launch", "ask_inventory_on_launch",
                                 "survey_enabled", "limit", "verbosity", "allow_simultaneous") if k in jt}
    datos.update(job_type="run", project=proy["id"], inventory=inv["id"], playbook=pref + jt["playbook"], organization=org["id"])
    n = c("GET", f"job_templates/?name={q(jt['name'])}")["results"]
    d = c("PATCH", f"job_templates/{n[0]['id']}/", dict(datos, name=jt["name"])) if n else c("POST", "job_templates/", dict(datos, name=jt["name"]))
    sv = o("GET", f"job_templates/{jid}/survey_spec/", t=True)
    if sv and sv.get("spec"):
        c("POST", f"job_templates/{d['id']}/survey_spec/", sv)
    mapa_jt[jid] = d["id"]
    return d["id"]


# --- workflows ---------------------------------------------------------------------------------------------------------
for w in wfs:
    datos = {k: w[k] for k in ("description", "extra_vars", "survey_enabled", "ask_variables_on_launch", "allow_simultaneous") if k in w}
    datos.update(organization=org["id"], inventory=inv["id"])
    x = uno(c, "workflow_job_templates", name=w["name"])
    if x:   # se rehace el grafo desde cero
        for n in todos(c, f"workflow_job_templates/{x['id']}/workflow_nodes/?page_size=100"):
            c("DELETE", f"workflow_job_template_nodes/{n['id']}/", t=True)
        nw = c("PATCH", f"workflow_job_templates/{x['id']}/", dict(datos, name=w["name"]))
    else:
        nw = c("POST", "workflow_job_templates/", dict(datos, name=w["name"]))
    sv = o("GET", f"workflow_job_templates/{w['id']}/survey_spec/", t=True)
    if sv and sv.get("spec"):
        c("POST", f"workflow_job_templates/{nw['id']}/survey_spec/", sv)
    for lb in w["summary_fields"].get("labels", {}).get("results", []):
        c("POST", f"workflow_job_templates/{nw['id']}/labels/", {"id": etiqueta(lb["name"])["id"]}, t=True)
    nodos = todos(o, f"workflow_job_templates/{w['id']}/workflow_nodes/?page_size=100")
    nuevo = {}
    for n in nodos:
        ujt = n["summary_fields"].get("unified_job_template") or {}
        base = {"identifier": n.get("identifier"), "all_parents_must_converge": n.get("all_parents_must_converge", False),
                "extra_data": n.get("extra_data") or {}}
        if ujt.get("unified_job_type") == "workflow_approval":
            nn = c("POST", f"workflow_job_templates/{nw['id']}/workflow_nodes/", base)
            ap = o("GET", f"workflow_approval_templates/{n['unified_job_template']}/")
            c("POST", f"workflow_job_template_nodes/{nn['id']}/create_approval_template/",
              {"name": ap["name"], "description": ap.get("description", ""), "timeout": ap.get("timeout", 0)})
        else:
            nn = c("POST", f"workflow_job_templates/{nw['id']}/workflow_nodes/", dict(base, unified_job_template=copiar_jt(n["unified_job_template"])))
        nuevo[n["id"]] = nn["id"]
    for n in nodos:
        for tipo in ("success_nodes", "failure_nodes", "always_nodes"):
            for h in n[tipo]:
                c("POST", f"workflow_job_template_nodes/{nuevo[n['id']]}/{tipo}/", {"id": nuevo[h]})
    paso(f"{w['name']}: {len(nodos)} pasos")

# --- usuario del tenant --------------------------------------------------------------------------------------------------
u = uno(g, "users", username=USUARIO)
if not u:
    u = g("POST", "users/", {"username": USUARIO, "password": secrets.token_urlsafe(24), "first_name": "Claro", "last_name": "PR (demo)"})
gorg = uno(g, "organizations", name=ORG)
rd = uno(g, "role_definitions", name="Organization Admin")
if rd and gorg:
    g("POST", "role_user_assignments/", {"role_definition": rd["id"], "user": u["id"], "object_id": gorg["id"]}, t=True)
for _ in range(20):
    cu = uno(c, "users", username=USUARIO)
    if cu:
        break
    time.sleep(3)
if cu:
    ro = c("GET", f"organizations/{org['id']}/")["summary_fields"]["object_roles"]
    for k in ("admin_role", "execute_role", "read_role"):
        c("POST", f"users/{cu['id']}/roles/", {"id": ro[k]["id"]}, t=True)
paso(f"Usuario {USUARIO}: administrador del tenant (clave aleatoria no guardada; se fija en AAP › Users)")
print(f"LISTO · {len(wfs)} workflows · {len(mapa_jt)} plantillas")
