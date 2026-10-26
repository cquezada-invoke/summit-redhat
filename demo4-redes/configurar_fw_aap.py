#!/usr/bin/python3
# Demo 4 · caso 1 con firewall VyOS: en AAP (organización "Redes") agrega el host fw al inventario del laboratorio,
# la credencial del firewall desde Vault (secret/red/firewall), la de ServiceNow, y deja lista la plantilla
# "Red · Solicitar acceso" con su encuesta. Sube a Gitea (invoke/red-laboratorio) el playbook y sus archivos.
# El host fgt (FortiGate sin licencia) queda deshabilitado. Idempotente. No imprime claves.
import base64, glob, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, GITEA = "https://localhost/api/controller/v2", "http://127.0.0.1:3000/api/v1"
VAULT = "https://127.0.0.1:8200/v1"
ROOT = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"]
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
REPO = "red-laboratorio"
paso = lambda t: print(f"   · {t}", flush=True)


def http(m, url, d=None, cab=None, tolerar=False):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers=dict({"Content-Type": "application/json"}, **(cab or {})))
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {url} -> {e.code}: {e.read()[:400].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http(m, f"{C}/{p}", d, {"Authorization": BASIC}, t)
gitea = lambda m, p, d=None, t=False: http(m, f"{GITEA}/{p}", d, {"Authorization": "token " + SEC["gitea_token"]}, t)
vault = lambda m, p, d=None: http(m, f"{VAULT}/{p}", d, {"X-Vault-Token": ROOT})


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


# 1. Playbook, tareas de ServiceNow, tiempos manuales y colecciones al día en Gitea
for ruta, msg in (("playbooks/solicitar-acceso.yml", "Solicitar acceso: firewall VyOS, prueba desde pc-usuario y registro en ServiceNow"),
                  ("playbooks/tareas/snow.yml", "Registro en ServiceNow (mismo patrón de la Demo 3)"),
                  ("playbooks/tiempos_manuales.yml", "Tiempo manual estimado de una regla de firewall"),
                  ("collections/requirements.yml", "Colecciones vyos.vyos y ansible.netcommon para el caso 1")):
    nuevo = open(os.path.join(AQUI, REPO, ruta), "rb").read()
    act = gitea("GET", f"repos/invoke/{REPO}/contents/{ruta}?ref=main", t=True)
    if not act or base64.b64decode(act["content"]) != nuevo:
        datos = {"content": base64.b64encode(nuevo).decode(), "branch": "main", "message": msg,
                 "author": {"name": "Cristian Quezada", "email": "cquezada@invoke.cl"}}
        if act:
            gitea("PUT", f"repos/invoke/{REPO}/contents/{ruta}", dict(datos, sha=act["sha"]))
        else:
            gitea("POST", f"repos/invoke/{REPO}/contents/{ruta}", datos)
    paso(f"Gitea: {REPO}/{ruta} al día")

# 2. Vault: AAP puede leer secret/red/firewall
pol = vault("GET", "sys/policy/aap-lectura")["rules"]
if "secret/data/red/firewall" not in pol and "secret/data/red/*" not in pol:
    vault("PUT", "sys/policy/aap-lectura", {"policy": pol + '\npath "secret/data/red/firewall" { capabilities = ["read"] }\n'})
paso("Vault: política aap-lectura permite secret/red/firewall")

org = uno("organizations", name="Redes")["id"]
inv = uno("inventories", name="Red · laboratorio")["id"]


# 3. Hosts: fw (grupo firewall) y fgt deshabilitado
def host(nombre, vars_, grupo, desc, habilitado=True):
    h = aap("GET", f"hosts/?name={nombre}&inventory={inv}")["results"]
    datos = {"variables": vars_, "enabled": habilitado}
    hid = aap("PATCH", f"hosts/{h[0]['id']}/", datos)["id"] if h else \
        aap("POST", f"inventories/{inv}/hosts/", dict(datos, name=nombre))["id"]
    g = aap("GET", f"groups/?name={grupo}&inventory={inv}")["results"]
    gid = g[0]["id"] if g else aap("POST", f"inventories/{inv}/groups/", {"name": grupo, "description": desc})["id"]
    aap("POST", f"groups/{gid}/hosts/", {"id": hid}, t=True)

host("fw", "ansible_host: 172.20.20.12\nansible_connection: ansible.netcommon.network_cli\n"
     "ansible_network_os: vyos.vyos.vyos\nansible_user: '{{ fw_usuario }}'\nansible_password: '{{ fw_clave }}'\n"
     "descripcion: Firewall VyOS (eth1 WAN · eth2 LAN usuarios)", "firewall", "Firewall del laboratorio")
f = aap("GET", f"hosts/?name=fgt&inventory={inv}")["results"]
if f:
    aap("PATCH", f"hosts/{f[0]['id']}/", {"enabled": False,
        "description": "FortiGate sin licencia de evaluación: reemplazado por el firewall VyOS (host fw)"})
paso("Inventario: fw en 'firewall'; fgt deshabilitado")

# 4. Tipo de credencial y credencial del firewall (desde Vault)
tipo = asegurar("credential_types", "Firewall · usuario y clave", {
    "kind": "cloud", "description": "Usuario y clave del firewall del laboratorio (VyOS)",
    "inputs": {"fields": [{"id": "usuario", "label": "Usuario", "type": "string"},
                          {"id": "clave", "label": "Clave", "type": "string", "secret": True}],
               "required": ["usuario", "clave"]},
    "injectors": {"extra_vars": {"fw_usuario": "{{ usuario }}", "fw_clave": "{{ clave }}"}}})["id"]
cred = asegurar("credentials", "Red · firewall VyOS", {
    "organization": org, "credential_type": tipo, "inputs": {},
    "description": "Usuario y clave desde Vault (secret/red/firewall)"})["id"]
vcred = uno("credentials", name="HashiCorp Vault · secretos")["id"]
for s in aap("GET", f"credentials/{cred}/input_sources/")["results"]:
    aap("DELETE", f"credential_input_sources/{s['id']}/")
for campo, clave in (("usuario", "usuario"), ("clave", "clave")):
    aap("POST", "credential_input_sources/", {"target_credential": cred, "input_field_name": campo,
                                              "source_credential": vcred,
                                              "metadata": {"secret_backend": "secret", "secret_path": "red/firewall",
                                                           "secret_key": clave}})
paso("Credencial 'Red · firewall VyOS' enlazada a Vault")

# 5. Plantilla: credenciales (firewall, ssh del laboratorio, ServiceNow) y encuesta
jt = uno("job_templates", name="Red · Solicitar acceso")["id"]
aap("PATCH", f"job_templates/{jt}/", {"description": "Crea en el firewall la regla pedida, la prueba desde pc-usuario y la registra en ServiceNow",
                                      "survey_enabled": True, "ask_variables_on_launch": True})
ya = {c["name"]: c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]}
if "Red · FortiGate (API)" in ya:
    aap("POST", f"job_templates/{jt}/credentials/", {"id": ya["Red · FortiGate (API)"], "disassociate": True})
for nombre in ("Red · firewall VyOS", "Red · laboratorio (ssh)", "ServiceNow · ansible.automatizacion"):
    cid = uno("credentials", name=nombre)["id"]
    if cid not in ya.values():
        aap("POST", f"job_templates/{jt}/credentials/", {"id": cid})
campo = lambda v, q, d, t="text", req=True: {"variable": v, "question_name": q, "default": d, "type": t,
                                             "required": req, "question_description": ""}
aap("POST", f"job_templates/{jt}/survey_spec/", {"name": "Solicitud de acceso", "description": "", "spec": [
    campo("acceso_nombre", "Nombre de la solicitud", "usuarios-a-srv-app"),
    campo("acceso_origen", "Red de origen", "10.40.40.0/24"),
    campo("acceso_destino", "Destino", "10.10.10.10/32"),
    campo("acceso_puerto", "Puerto TCP", 8080, "integer"),
    campo("solicitado_por", "Solicitado por", "cquezada")]})
proy = uno("projects", name="Red · laboratorio")["id"]
u = aap("POST", f"projects/{proy}/update/", {})
while (st := aap("GET", f"project_updates/{u['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
paso(f"Plantilla 'Red · Solicitar acceso' (id {jt}) con credenciales y encuesta · proyecto {st}")
