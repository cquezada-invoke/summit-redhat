#!/usr/bin/python3
# Demo 4 · caso 1: en AAP (organización "Redes") agrega el FortiGate y pc-usuario al inventario del laboratorio,
# la credencial del token de API desde Vault y deja lista la plantilla "Red · Solicitar acceso" con su encuesta.
# También actualiza el playbook en Gitea (invoke/red-laboratorio). Idempotente. No imprime claves.
import base64, glob, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, GITEA = "https://localhost/api/controller/v2", "http://127.0.0.1:3000/api/v1"
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


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


# 1. Playbook actualizado en Gitea
ruta = "playbooks/solicitar-acceso.yml"
nuevo = open(os.path.join(AQUI, REPO, ruta), "rb").read()
act = gitea("GET", f"repos/invoke/{REPO}/contents/{ruta}?ref=main", t=True)
if not act or base64.b64decode(act["content"]) != nuevo:
    datos = {"content": base64.b64encode(nuevo).decode(), "branch": "main",
             "message": "Solicitar acceso: FortiGate del laboratorio (LAN port3 → WAN port2) y prueba desde pc-usuario",
             "author": {"name": "Cristian Quezada", "email": "cquezada@invoke.cl"}}
    if act:
        gitea("PUT", f"repos/invoke/{REPO}/contents/{ruta}", dict(datos, sha=act["sha"]))
    else:
        gitea("POST", f"repos/invoke/{REPO}/contents/{ruta}", datos)
paso(f"Gitea: {REPO}/{ruta} al día")

org = uno("organizations", name="Redes")["id"]
inv = uno("inventories", name="Red · laboratorio")["id"]

# 2. Hosts: fgt (grupo fortigate) y pc-usuario (grupo usuarios, para la prueba)
def host(nombre, vars_, grupo, desc):
    h = aap("GET", f"hosts/?name={nombre}&inventory={inv}")["results"]
    hid = aap("PATCH", f"hosts/{h[0]['id']}/", {"variables": vars_})["id"] if h else \
        aap("POST", f"inventories/{inv}/hosts/", {"name": nombre, "variables": vars_})["id"]
    g = aap("GET", f"groups/?name={grupo}&inventory={inv}")["results"]
    gid = g[0]["id"] if g else aap("POST", f"inventories/{inv}/groups/", {"name": grupo, "description": desc})["id"]
    aap("POST", f"groups/{gid}/hosts/", {"id": hid}, t=True)

host("fgt", "ansible_host: 172.20.20.12\ndescripcion: FortiGate VM 7.6 (port2 WAN · port3 LAN usuarios)",
     "fortigate", "Firewall del laboratorio")
host("pc-usuario", "ansible_host: 172.20.20.22\nansible_python_interpreter: /usr/bin/python3\n"
     "descripcion: Equipo de usuario detrás del FortiGate", "usuarios", "Equipos de prueba detrás del FortiGate")
paso("Inventario: fgt en 'fortigate' y pc-usuario en 'usuarios'")

# 3. Tipo de credencial y credencial del token (desde Vault)
tipo = asegurar("credential_types", "FortiGate · token de API", {
    "kind": "cloud", "description": "Token REST del FortiGate para la colección fortinet.fortios",
    "inputs": {"fields": [{"id": "token", "label": "Token de API", "type": "string", "secret": True}],
               "required": ["token"]},
    "injectors": {"extra_vars": {"fortios_access_token": "{{ token }}"}}})["id"]
cred = asegurar("credentials", "Red · FortiGate (API)", {
    "organization": org, "credential_type": tipo, "inputs": {},
    "description": "Token desde Vault (secret/red/fortigate · api_token)"})["id"]
vcred = uno("credentials", name="HashiCorp Vault · secretos")["id"]
for f in aap("GET", f"credentials/{cred}/input_sources/")["results"]:
    aap("DELETE", f"credential_input_sources/{f['id']}/")
aap("POST", "credential_input_sources/", {"target_credential": cred, "input_field_name": "token",
                                          "source_credential": vcred,
                                          "metadata": {"secret_backend": "secret", "secret_path": "red/fortigate",
                                                       "secret_key": "api_token"}})
paso("Credencial 'Red · FortiGate (API)' enlazada a Vault")

# 4. Plantilla con encuesta
jt = uno("job_templates", name="Red · Solicitar acceso")["id"]
aap("PATCH", f"job_templates/{jt}/", {"description": "Crea en el FortiGate la regla pedida y la prueba desde pc-usuario",
                                      "survey_enabled": True, "ask_variables_on_launch": True})
ya = [c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]]
for cid in (cred, uno("credentials", name="Red · laboratorio (ssh)")["id"]):
    if cid not in ya:
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
paso(f"Plantilla 'Red · Solicitar acceso' con encuesta y credenciales · proyecto {st}")
