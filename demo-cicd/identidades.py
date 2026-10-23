#!/usr/bin/python3
# Personas reales en la demo: usuarios de Gitea con permiso de escritura en la app y etiquetas
# "solicitado:<usuario>" en AAP para ver y filtrar quién pidió cada despliegue. Idempotente.
import base64, glob, json, os, re, secrets, ssl, subprocess, sys, urllib.error, urllib.parse, urllib.request

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
SECF = os.path.expanduser("~/demo-drift/cicd-secretos.txt")
SEC = dict(l.strip().split("=", 1) for l in open(SECF) if "=" in l)
CTX = ssl._create_unverified_context()
PERSONAS = [("cquezada", "Cristian Quezada", "cquezada@invoke.cl"), ("jperez", "Juan Pérez", "jperez@invoke.local")]
ORG_N = "Despliegues CI/CD"


def http(metodo, url, datos=None, auth=None, tolerar=False):
    req = urllib.request.Request(url, method=metodo, data=json.dumps(datos).encode() if datos is not None else None,
                                 headers={"Authorization": auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            cuerpo = r.read()
            return json.loads(cuerpo) if cuerpo else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {metodo} {url} -> {e.code}: {e.read()[:400].decode(errors='replace')}")


GITEA = "http://127.0.0.1:3000/api/v1"
GT = "token " + os.environ.get("GITEA_ADMIN_TOKEN", SEC["gitea_token"])
AAP = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
C = "https://localhost/api/controller/v2"
paso = lambda t: print(f"· {t}", flush=True)

nuevas = []
for usuario, nombre, correo in PERSONAS:
    if not http("GET", f"{GITEA}/users/{usuario}", auth=GT, tolerar=True):
        clave = secrets.token_hex(8)
        http("POST", f"{GITEA}/admin/users", {"username": usuario, "password": clave, "email": correo,
                                              "full_name": nombre, "must_change_password": False}, auth=GT)
        nuevas.append(f"gitea_{usuario}_clave={clave}")
    http("PUT", f"{GITEA}/repos/invoke/app-jboss/collaborators/{usuario}", {"permission": "write"}, auth=GT)
if nuevas:
    with open(SECF, "a") as f:
        f.write("\n".join(nuevas) + "\n")
paso("Usuarios de Gitea con permiso de escritura en invoke/app-jboss")

org = http("GET", f"{C}/organizations/?name=" + urllib.parse.quote(ORG_N), auth=AAP)["results"][0]["id"]
for usuario in [p[0] for p in PERSONAS] + ["invoke"]:
    nombre = f"solicitado:{usuario}"
    if not http("GET", f"{C}/labels/?name=" + urllib.parse.quote(nombre), auth=AAP)["results"]:
        http("POST", f"{C}/labels/", {"name": nombre, "organization": org}, auth=AAP)
jt = http("GET", f"{C}/job_templates/?name=" + urllib.parse.quote("JBoss · Desplegar app"), auth=AAP)["results"][0]
http("PATCH", f"{C}/job_templates/{jt['id']}/", {"ask_labels_on_launch": True}, auth=AAP)
paso("Etiquetas solicitado:<usuario> y plantilla que las acepta al lanzar")

# tekton-ci necesita leer las etiquetas de la organización para usarlas al lanzar
gu = http("GET", "https://localhost/api/gateway/v1/users/?username=tekton-ci", auth=AAP)["results"][0]
defs = {d["name"]: d["id"] for d in http("GET", f"{C}/role_definitions/?page_size=200", auth=AAP)["results"]}
http("POST", f"{C}/role_user_assignments/", {"role_definition": defs["Organization Member"], "object_id": str(org),
                                             "user_ansible_id": gu["summary_fields"]["resource"]["ansible_id"]},
     auth=AAP, tolerar=True)
paso("tekton-ci puede leer las etiquetas del espacio de despliegues")
print("LISTO")
