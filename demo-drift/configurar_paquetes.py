#!/usr/bin/python3
# Demo 1 · Control de paquetes. Idempotente. No imprime claves.
# 1) Base inicial de paquetes en master (paquetes/<servidor>.txt, versión exacta) para los nodos que aún no la tienen,
#    tomada del estado actual de cada nodo, por Pull Request aprobado (merge).
# 2) AAP: plantilla "Estado · Volver a un commit" (encuesta: servidor, commit, elementos) con las mismas credenciales,
#    proyecto e inventario que "Estado · Registrar servidor"; el panel puede lanzarla.
import base64, glob, json, os, re, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
NODOS = [n["nombre"] for n in json.load(open(os.path.expanduser("~/demo-drift/panel/config.json")))["nodos"]]
JT = "Estado · Volver a un commit"
paso = lambda t: print(f"   · {t}", flush=True)
q = urllib.parse.quote


def http(m, url, d=None, cab=None, ok=()):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers=dict({"Content-Type": "application/json"}, **(cab or {})))
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return None
        sys.exit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


aap = lambda m, p, d=None, ok=(): http(m, f"https://localhost/api/controller/v2/{p}", d, {"Authorization": BASIC}, ok)
gitea = lambda m, p, d=None, ok=(): http(m, f"http://127.0.0.1:3000/api/v1/repos/invoke/estado-servidores/{p}", d,
                                         {"Authorization": "token " + SEC["gitea_token"]}, ok)

# 1. Base inicial de paquetes
faltan = [n for n in NODOS if not gitea("GET", f"contents/paquetes/{n}.txt?ref=master", ok=(404,))]
if faltan:
    rama = f"paquetes/base-inicial-{int(time.time())}"
    gitea("POST", "branches", {"new_branch_name": rama, "old_branch_name": "master"})
    for n in faltan:
        lista = subprocess.run(["ssh", "-o", "BatchMode=yes", n, "rpm -qa | sort"], capture_output=True, text=True, timeout=60).stdout
        if not lista.strip():
            print(f"   aviso: no se pudo leer {n}"); continue
        gitea("POST", f"contents/paquetes/{n}.txt", {"branch": rama, "message": f"Base de paquetes de {n}",
              "content": base64.b64encode(lista.encode()).decode(),
              "author": {"name": "Cristian Quezada", "email": "cquezada@invoke.cl"}})
    pr = gitea("POST", "pulls", {"base": "master", "head": rama, "title": "Base inicial de paquetes (versión exacta) · " + ", ".join(faltan),
                                 "body": "Lista de paquetes aprobados por servidor. Desde aquí, cada instalación, desinstalación o cambio de versión se compara con esta base."})
    gitea("POST", f"pulls/{pr['number']}/merge", {"Do": "merge"})
    paso(f"Git: base de paquetes en master para {', '.join(faltan)} (PR #{pr['number']} aprobado)")
else:
    paso("Git: la base de paquetes ya existe para todos los nodos")

# 2. Plantilla en AAP
ref = aap("GET", "job_templates/?name=" + q("Estado · Registrar servidor"))["results"][0]
datos = {"organization": ref["organization"], "project": ref["project"], "inventory": ref["inventory"],
         "playbook": "estado/volver_a_commit.yml", "ask_variables_on_launch": True, "ask_job_type_on_launch": True,
         "diff_mode": True, "allow_simultaneous": True, "survey_enabled": True,
         "description": "Deja un servidor igual a un commit de Git (paquetes con versión exacta y archivos controlados)"}
o = aap("GET", "job_templates/?name=" + q(JT))["results"]
jt = (aap("PATCH", f"job_templates/{o[0]['id']}/", datos) if o else aap("POST", "job_templates/", dict(datos, name=JT)))["id"]
ya = {c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]}
for c in aap("GET", f"job_templates/{ref['id']}/credentials/")["results"]:
    if c["id"] not in ya:
        aap("POST", f"job_templates/{jt}/credentials/", {"id": c["id"]})
aap("POST", f"job_templates/{jt}/survey_spec/", {"name": "Volver a un commit", "description": "", "spec": [
    {"question_name": "Servidor", "variable": "servidor", "type": "multiplechoice", "choices": NODOS, "required": True,
     "default": NODOS[0], "question_description": ""},
    {"question_name": "Commit o tag", "variable": "commit", "type": "text", "required": False, "default": "base", "min": 0, "max": 80,
     "question_description": "Hash o tag de invoke/estado-servidores. «base» = base aprobada (rama master)"},
    {"question_name": "Qué restablecer", "variable": "elementos", "type": "multiselect", "choices": ["paquetes", "archivos"],
     "required": True, "default": "paquetes", "question_description": "«archivos» (sshd_config, sudo, /opt/apps) solo con un commit de foto"}]})
lbl = [l["name"] for l in aap("GET", f"job_templates/{jt}/labels/")["results"]]
if "cumplimiento" not in lbl:
    aap("POST", f"job_templates/{jt}/labels/", {"name": "cumplimiento", "organization": ref["organization"]}, ok=(400,))
u = http("GET", "https://localhost/api/gateway/v1/users/?username=panel-drift", cab={"Authorization": BASIC})["results"]
defs = {d["name"]: d["id"] for d in aap("GET", "role_definitions/?page_size=200")["results"]}
if u:
    aap("POST", "role_user_assignments/", {"role_definition": defs["JobTemplate Execute"], "object_id": str(jt),
                                           "user_ansible_id": u[0]["summary_fields"]["resource"]["ansible_id"]}, ok=(400,))
paso(f"AAP: plantilla '{JT}' (id {jt}) con encuesta; el panel puede lanzarla")
print("   LISTO")
