#!/usr/bin/python3
"""Portal de automatización: deja en AAP todo lo que el portal muestra. Idempotente.
  1. Gitea: repositorio invoke/portal-automatizaciones (playbooks + rulebooks) y el playbook de red telco en invoke/red-laboratorio.
  2. OpenShift (SNO): ServiceAccount aap-portal con su token en Vault (secret/portal/ocp); nunca se imprime.
  3. AAP: organización, proyecto, inventario local, credencial de OpenShift (desde Vault), una plantilla por playbook y, por cada
     entrada de catalogo.yml, un workflow «Portal · …» = aprobación → plantilla(s), con su encuesta y la etiqueta portal-<sección>.
  4. EDA: activación «Amdocs · Órdenes de servicio» (webhook :5011) que lanza el workflow de activación de órdenes.
Uso: python3 configurar_aap.py [--sin-eda]"""
import base64, glob, json, os, re, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
import yaml

AQUI = os.path.dirname(os.path.abspath(__file__))
INIT = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
VA, C, G, E = "http://192.168.122.1:8202/v1", "https://localhost/api/controller/v2", "https://localhost/api/gateway/v1", "https://localhost/api/eda/v1"
GITEA = "http://127.0.0.1:3000/api/v1"
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
ORG_N, REPO = "Portal de automatización", "portal-automatizaciones"
OC = os.path.expanduser("~/openshift/sno/bin/oc")
KCFG = os.path.expanduser("~/openshift/sno/instalar/auth/kubeconfig")
API_OCP = "https://192.168.122.50:6443"
paso = lambda t: print(f"   · {t}", flush=True)
q = urllib.parse.quote


def http(m, url, d=None, cab=None, tolerar=False):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers=dict({"Content-Type": "application/json"}, **(cab or {})))
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=90) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:500].decode(errors='replace')}")


vault = lambda m, p, d=None: http(m, f"{VA}/{p}", d, {"X-Vault-Token": INIT["root_token"]})
aap = lambda m, p, d=None, t=False: http(m, f"{C}/{p}", d, {"Authorization": BASIC}, t)
gw = lambda m, p, d=None, t=False: http(m, f"{G}/{p}", d, {"Authorization": BASIC}, t)
eda = lambda m, p, d=None, t=False: http(m, f"{E}/{p}", d, {"Authorization": BASIC}, t)
gitea = lambda m, p, d=None, t=False: http(m, f"{GITEA}/{p}", d, {"Authorization": "token " + SEC["gitea_token"]}, t)


def uno(rec, f=aap, **filtro):
    r = f("GET", f"{rec}/?" + urllib.parse.urlencode(filtro))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


def oc(*a, entrada=None):
    r = subprocess.run([OC, "--kubeconfig", KCFG, *a], input=entrada, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ERROR oc {' '.join(a[:3])}: {r.stderr[:300]}")
    return r.stdout


# 1. Gitea ---------------------------------------------------------------------------------------------------------------
if not gitea("GET", f"repos/invoke/{REPO}", t=True):
    gitea("POST", "user/repos", {"name": REPO, "private": False, "default_branch": "main",
                                 "description": "Automatizaciones del portal (OpenShift, telco Claro PR y Amdocs)"})
tmp = tempfile.mkdtemp()
url = f"http://invoke:{SEC['gitea_token']}@127.0.0.1:3000/invoke/{REPO}.git"
git = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True)
if subprocess.run(["git", "clone", "-q", url, tmp], capture_output=True).returncode:
    sys.exit("ERROR al clonar el repositorio del portal")
if not git("rev-parse", "HEAD").stdout.strip():
    git("checkout", "-q", "-b", "main")
for d in ("playbooks", "rulebooks"):
    shutil.rmtree(os.path.join(tmp, d), ignore_errors=True)
    shutil.copytree(os.path.join(AQUI, d), os.path.join(tmp, d))
os.makedirs(os.path.join(tmp, "collections"), exist_ok=True)
open(os.path.join(tmp, "collections", "requirements.yml"), "w").write(
    "collections:\n  - name: kubernetes.core\n  - name: ansible.utils\n")
git("add", "-A")
if git("status", "--porcelain").stdout.strip():
    git("-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl", "commit", "-qm", "Automatizaciones del portal")
    if git("push", "-q", url, "HEAD:main").returncode:
        sys.exit("ERROR al subir el repositorio del portal")
shutil.rmtree(tmp)
red_pb = "playbooks/telco-red-servicio.yml"
cont = open(os.path.join(AQUI, "..", "demo4-redes", "red-laboratorio", red_pb)).read()
act = gitea("GET", f"repos/invoke/red-laboratorio/contents/{red_pb}?ref=main", t=True)
if not act or base64.b64decode(act["content"]).decode() != cont:
    gitea("PUT" if act else "POST", f"repos/invoke/red-laboratorio/contents/{red_pb}",
          dict({"branch": "main", "message": "Telco: red del servicio (VLAN y BGP) con vuelta atrás",
                "content": base64.b64encode(cont.encode()).decode()}, **({"sha": act["sha"]} if act else {})))
paso("Gitea: invoke/portal-automatizaciones y telco-red-servicio.yml en invoke/red-laboratorio")

# 2. OpenShift: cuenta de servicio de AAP y su token en Vault ---------------------------------------------------------------
oc("apply", "-f", "-", entrada="""
apiVersion: v1
kind: Namespace
metadata: {name: aap-portal, labels: {invoke.cl/uso: automatizacion}}
---
apiVersion: v1
kind: ServiceAccount
metadata: {name: aap-portal, namespace: aap-portal}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: {name: aap-portal-admin}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: cluster-admin}
subjects: [{kind: ServiceAccount, name: aap-portal, namespace: aap-portal}]
---
apiVersion: v1
kind: Secret
metadata: {name: aap-portal-token, namespace: aap-portal, annotations: {kubernetes.io/service-account.name: aap-portal}}
type: kubernetes.io/service-account-token
""")
tok = ""
for _ in range(20):
    tok = oc("-n", "aap-portal", "get", "secret", "aap-portal-token", "-o", "jsonpath={.data.token}")
    if tok:
        break
    time.sleep(2)
vault("POST", "secret/data/portal/ocp", {"data": {"host": API_OCP, "token": base64.b64decode(tok).decode()}})
tok = None
pol = vault("GET", "sys/policies/acl/aap-lectura")["data"]["policy"]
if "secret/data/portal/" not in pol:
    vault("PUT", "sys/policies/acl/aap-lectura", {"policy": pol.rstrip() + '\npath "secret/data/portal/*" { capabilities = ["read"] }\n'})
paso("OpenShift: ServiceAccount aap-portal (token en Vault secret/portal/ocp)")

# 3. AAP -----------------------------------------------------------------------------------------------------------------
if not gw("GET", "organizations/?name=" + q(ORG_N))["results"]:
    gw("POST", "organizations/", {"name": ORG_N, "description": "Catálogo del portal de automatización (demos con aprobación)"})
org = None
for _ in range(20):
    org = uno("organizations", name=ORG_N)
    if org:
        break
    time.sleep(3)
org = org["id"]

vcred = uno("credentials", name="HashiCorp Vault · secretos")["id"]
tipo_k8s = aap("GET", "credential_types/?namespace=kubernetes_bearer_token")["results"][0]["id"]
cred_ocp = asegurar("credentials", "OpenShift · clúster (token)", {
    "organization": org, "credential_type": tipo_k8s, "inputs": {"verify_ssl": False},
    "description": "API y token del ServiceAccount aap-portal desde Vault (secret/portal/ocp)"})["id"]
for f in aap("GET", f"credentials/{cred_ocp}/input_sources/")["results"]:
    aap("DELETE", f"credential_input_sources/{f['id']}/")
for campo, clave in (("host", "host"), ("bearer_token", "token")):
    aap("POST", "credential_input_sources/", {"target_credential": cred_ocp, "input_field_name": campo, "source_credential": vcred,
                                              "metadata": {"secret_backend": "secret", "secret_path": "portal/ocp", "secret_key": clave}})
paso("Credencial 'OpenShift · clúster (token)' enlazada a Vault")

proy = asegurar("projects", "Portal · automatizaciones", {
    "organization": org, "scm_type": "git", "scm_url": f"http://192.168.122.1:3000/invoke/{REPO}.git", "scm_branch": "main",
    "scm_update_on_launch": True, "scm_update_cache_timeout": 30, "description": "Playbooks del portal de automatización"})["id"]
for _ in range(60):
    st = aap("GET", f"projects/{proy}/")["status"]
    if st in ("successful", "failed", "error", "canceled"):
        break
    time.sleep(3)
if st != "successful":
    aap("POST", f"projects/{proy}/update/", {})
    for _ in range(60):
        time.sleep(3)
        st = aap("GET", f"projects/{proy}/")["status"]
        if st in ("successful", "failed", "error"):
            break
if st != "successful":
    sys.exit(f"ERROR al sincronizar el proyecto del portal: {st}")
proy_red = uno("projects", name="Red · laboratorio")
aap("POST", f"projects/{proy_red['id']}/update/", {})
paso("Proyecto 'Portal · automatizaciones' sincronizado")

inv = asegurar("inventories", "Portal · local", {"organization": org, "description": "Ejecución local: las automatizaciones hablan con APIs"})["id"]
if not aap("GET", f"inventories/{inv}/hosts/?name=localhost")["results"]:
    aap("POST", f"inventories/{inv}/hosts/", {"name": "localhost", "variables": "ansible_connection: local\nansible_python_interpreter: '{{ ansible_playbook_python }}'\n"})

ee_nom = os.environ.get("PORTAL_EE", "Default execution environment")
ee = uno("execution_environments", name=ee_nom)
ee = ee["id"] if ee else None
jt_red = uno("job_templates", name="Red · Preparar red OpenShift") or uno("job_templates", name="Red · Aplicar configuración")
cred_red = [c["id"] for c in aap("GET", f"job_templates/{jt_red['id']}/credentials/")["results"]]

etiqueta_ids = {}


def etiqueta(nombre):
    if nombre not in etiqueta_ids:
        e = uno("labels", name=nombre, organization=org) or aap("POST", "labels/", {"name": nombre, "organization": org})
        etiqueta_ids[nombre] = e["id"]
    return etiqueta_ids[nombre]


def plantilla(nombre, playbook, red=False, descripcion=""):
    jt = asegurar("job_templates", nombre, {
        "organization": org if not red else jt_red["organization"], "inventory": inv if not red else jt_red["inventory"],
        "project": proy if not red else jt_red["project"], "playbook": playbook, "ask_variables_on_launch": True,
        "execution_environment": ee if not red else jt_red.get("execution_environment"), "description": descripcion,
        "allow_simultaneous": False})["id"]
    ya = {c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]}
    for c in (cred_red if red else [cred_ocp]):
        if c not in ya:
            aap("POST", f"job_templates/{jt}/credentials/", {"id": c})
    if red and jt_red["organization"] != org:   # la organización del portal puede usarla
        pass
    return jt


cat = yaml.safe_load(open(os.path.join(AQUI, "catalogo.yml")))
jts = {}
for it in cat:
    if it.get("playbook"):
        jts[it["nombre"]] = plantilla(f"Portal · {it['nombre']} (plantilla)", it["playbook"], it.get("red", False), it["descripcion"])
    elif it.get("existente"):
        jts[it["nombre"]] = uno("job_templates", name=it["existente"])["id"]
jts["_orden"] = plantilla("Portal · Amdocs · Orden de servicio (plantilla)", "playbooks/amdocs-orden.yml",
                          descripcion="Lee y actualiza la orden en Amdocs (fase recibir o cerrar)")
paso(f"{len(jts)} plantillas")


def workflow(it):
    nombre = f"Portal · {it['nombre']}"
    wf = aap("GET", "workflow_job_templates/?name=" + q(nombre))["results"]
    datos = {"organization": org, "description": it["descripcion"], "survey_enabled": bool(it.get("encuesta")), "ask_variables_on_launch": False,
             "allow_simultaneous": False}
    wf = aap("PATCH", f"workflow_job_templates/{wf[0]['id']}/", datos)["id"] if wf else aap("POST", "workflow_job_templates/", dict(datos, name=nombre))["id"]
    for n in aap("GET", f"workflow_job_templates/{wf}/workflow_nodes/?page_size=50")["results"]:
        aap("DELETE", f"workflow_job_template_nodes/{n['id']}/")
    nodo = lambda ident, d: aap("POST", f"workflow_job_templates/{wf}/workflow_nodes/", dict(d, identifier=ident))["id"]
    enlace = lambda a, b, tipo="success": aap("POST", f"workflow_job_template_nodes/{a}/{tipo}_nodes/", {"id": b})

    def aprobacion(titulo, detalle):
        n = nodo("aprobacion", {})
        aap("POST", f"workflow_job_template_nodes/{n}/create_approval_template/", {"name": titulo, "description": detalle, "timeout": 0})
        return n

    if it.get("orden"):
        n1 = nodo("recibir", {"unified_job_template": jts["_orden"], "extra_data": {"fase": "recibir"}})
        n2 = aprobacion("¿Activar la orden de Amdocs?", "Revisa la orden en el paso «recibir»: cliente, servicio, VLAN y prefijo.")
        n3 = nodo("red", {"unified_job_template": jts["Telco · Red del servicio (VLAN y BGP)"]})
        n4 = nodo("cnf", {"unified_job_template": jts["Telco · Onboarding de CNF"]})
        n5 = nodo("cerrar_ok", {"unified_job_template": jts["_orden"], "extra_data": {"fase": "cerrar", "resultado": "ok"}})
        n6 = nodo("cerrar_falla", {"unified_job_template": jts["_orden"], "extra_data": {"fase": "cerrar", "resultado": "falla"}})
        n7 = nodo("cerrar_rechazo", {"unified_job_template": jts["_orden"], "extra_data": {"fase": "cerrar", "resultado": "falla"}})
        enlace(n1, n2); enlace(n2, n3); enlace(n3, n4); enlace(n4, n5)
        enlace(n2, n7, "failure"); enlace(n3, n6, "failure"); enlace(n4, n6, "failure")
    else:
        pasos = [jts[p] for p in it.get("pasos", [it["nombre"]])]
        n = aprobacion(f"¿Ejecutar «{it['nombre']}»?", "Revisa los parámetros de la ejecución antes de aprobar.")
        for i, jt in enumerate(pasos):
            m = nodo(f"paso{i + 1}", {"unified_job_template": jt})
            enlace(n, m)
            n = m
    if it.get("encuesta"):
        aap("POST", f"workflow_job_templates/{wf}/survey_spec/", {"name": it["nombre"], "description": it["descripcion"], "spec": it["encuesta"]})
    else:
        aap("DELETE", f"workflow_job_templates/{wf}/survey_spec/", t=True)
    ya = {x["name"] for x in aap("GET", f"workflow_job_templates/{wf}/labels/")["results"]}
    if f"portal-{it['seccion']}" not in ya:
        aap("POST", f"workflow_job_templates/{wf}/labels/", {"id": etiqueta(f"portal-{it['seccion']}")})
    return wf


for it in cat:
    paso(f"workflow 'Portal · {it['nombre']}' (id {workflow(it)})")

# Equipo «Portal · operadores»: lanzan y aprueban (el admin ya puede todo)
equipo = gw("GET", "teams/?name=" + q("Portal · operadores"))["results"]
if not equipo:
    gw("POST", "teams/", {"name": "Portal · operadores", "organization": gw("GET", "organizations/?name=" + q(ORG_N))["results"][0]["id"],
                          "description": "Pueden lanzar y aprobar las automatizaciones del portal"})
paso("Equipo 'Portal · operadores' (asignar usuarios para que vean el portal)")

# 4. EDA -----------------------------------------------------------------------------------------------------------------
if "--sin-eda" not in sys.argv:
    IP = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
    eorg = uno("organizations", eda, name=ORG_N) or uno("organizations", eda, name="Default")
    tipo = uno("credential-types", eda, name="Red Hat Ansible Automation Platform")["id"]
    datos = {"name": "AAP · portal (lanzar workflows)", "credential_type_id": tipo, "organization_id": eorg["id"],
             "inputs": {"host": f"https://{IP}/api/controller/", "username": "admin", "password": CLAVE, "verify_ssl": False}}
    c = uno("eda-credentials", eda, name=datos["name"])
    ecred = eda("PATCH", f"eda-credentials/{c['id']}/", datos)["id"] if c else eda("POST", "eda-credentials/", datos)["id"]
    p = uno("projects", eda, name="Portal · automatizaciones")
    if p:
        eda("POST", f"projects/{p['id']}/sync/", {}, t=True)
        ep = p["id"]
    else:
        ep = eda("POST", "projects/", {"name": "Portal · automatizaciones", "organization_id": eorg["id"], "verify_ssl": False,
                                       "url": f"http://192.168.122.1:3000/invoke/{REPO}.git"})["id"]
    for _ in range(60):
        st = eda("GET", f"projects/{ep}/")["import_state"]
        if st in ("completed", "failed"):
            break
        time.sleep(3)
    rb = None
    for _ in range(10):
        rb = uno("rulebooks", eda, project_id=ep, name="amdocs-ordenes.yml")
        if rb:
            break
        time.sleep(3)
    if not rb:
        sys.exit("ERROR: el proyecto EDA no trae rulebooks/amdocs-ordenes.yml")
    des = eda("GET", "decision-environments/?page_size=50")["results"]
    de = next((d for d in des if d["name"] == "Hub Default Decision Environment"), des[0])["id"]
    ACT = "Amdocs · Órdenes de servicio"
    a = uno("activations", eda, name=ACT)
    if a:
        eda("POST", f"activations/{a['id']}/disable/", {}, t=True)
        time.sleep(5)
        eda("DELETE", f"activations/{a['id']}/", t=True)
        for _ in range(20):
            if not uno("activations", eda, name=ACT):
                break
            time.sleep(3)
    act = eda("POST", "activations/", {"name": ACT, "description": "Recibe las órdenes de Amdocs (emulado) y lanza la activación con aprobación",
                                        "is_enabled": True, "decision_environment_id": de, "rulebook_id": rb["id"], "eda_credentials": [ecred],
                                        "organization_id": eorg["id"], "restart_policy": "on-failure", "log_level": "info"})["id"]
    for _ in range(60):
        st = eda("GET", f"activations/{act}/")["status"]
        if st in ("running", "failed", "error"):
            break
        time.sleep(5)
    paso(f"EDA: activación '{ACT}' ({st}) escuchando en :5011")
print("   LISTO")
