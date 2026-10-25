#!/usr/bin/python3
# Demo 1 · Registro de estado en Git y propagación entre servidores. Idempotente.
#  - Gitea: repo invoke/estado-servidores y usuario técnico "registro-estado" (token guardado en cicd-secretos.txt)
#  - AAP: tipo de credencial y credencial "Gitea · registro de estado", plantillas
#    "Estado · Registrar servidor" y "Estado · Aplicar base desde Git" y "Estado · Proponer base", y permiso de ejecución para panel-drift.
# Nunca imprime claves ni tokens.
import base64, glob, json, os, re, secrets, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

SECRETOS = os.path.expanduser("~/demo-drift/cicd-secretos.txt")
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE_AAP = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
AAP, GW, GITEA = "https://localhost/api/controller/v2", "https://localhost/api/gateway/v1", "http://127.0.0.1:3000/api/v1"
CTX = ssl._create_unverified_context()
paso = lambda t: print(f"· {t}", flush=True)
q = urllib.parse.quote


def secreto(nombre):
    for l in open(SECRETOS):
        if l.startswith(nombre + "="):
            return l.split("=", 1)[1].strip()
    return None


def guardar_secreto(nombre, valor):
    lineas = [l for l in open(SECRETOS) if not l.startswith(nombre + "=")]
    lineas.append(f"{nombre}={valor}\n")
    open(SECRETOS, "w").writelines(lineas)


def http(m, url, d=None, auth=None, tolerar=(), ctx=None):
    h = {"Content-Type": "application/json"}
    if auth:
        h["Authorization"] = auth
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            b = r.read()
            return r.status, (json.loads(b) if b else {})
    except urllib.error.HTTPError as e:
        if e.code in tolerar:
            return e.code, None
        sys.exit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


basic = lambda u, p: "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()

# ---------------- Gitea ----------------
ADMIN = basic(secreto("gitea_usuario"), secreto("gitea_clave"))
st, _ = http("GET", f"{GITEA}/repos/invoke/estado-servidores", auth=ADMIN, tolerar=(404,))
if st == 404:
    # "invoke" es un usuario (dueño de los repos de la demo), no una organización
    http("POST", f"{GITEA}/admin/users/invoke/repos", {
        "name": "estado-servidores", "auto_init": True, "default_branch": "main", "private": False,
        "description": "Demo 1 · Historia del estado real de cada servidor: cada cambio queda como commit"}, auth=ADMIN)
paso("Gitea: repositorio invoke/estado-servidores")

clave_u = secreto("registro_estado_clave")
if not clave_u:
    clave_u = secrets.token_urlsafe(18)
    guardar_secreto("registro_estado_clave", clave_u)
st, _ = http("GET", f"{GITEA}/users/registro-estado", auth=ADMIN, tolerar=(404,))
if st == 404:
    http("POST", f"{GITEA}/admin/users", {"username": "registro-estado", "email": "registro-estado@invoke.local",
                                          "password": clave_u, "must_change_password": False,
                                          "full_name": "Registro de estado (AAP)"}, auth=ADMIN)
else:
    http("PATCH", f"{GITEA}/admin/users/registro-estado", {"login_name": "registro-estado", "source_id": 0,
                                                            "password": clave_u, "must_change_password": False}, auth=ADMIN)
http("PUT", f"{GITEA}/repos/invoke/estado-servidores/collaborators/registro-estado", {"permission": "write"}, auth=ADMIN)

token = secreto("registro_estado_token")
if token:
    st, _ = http("GET", f"{GITEA}/user", auth=f"token {token}", tolerar=(401, 403))
    token = token if st == 200 else None
if not token:
    st, t = http("POST", f"{GITEA}/users/registro-estado/tokens",
                 {"name": f"aap-{int(time.time())}", "scopes": ["write:repository", "read:user"]},
                 auth=basic("registro-estado", clave_u))
    token = t["sha1"]
    guardar_secreto("registro_estado_token", token)
paso("Gitea: usuario técnico 'registro-estado' con permiso de escritura (token en cicd-secretos.txt)")

# ---------------- Base aprobada: rama master con base/ ----------------
SUDO_PROPIOS = {"visitantes", "grupo-admin", "90-cloud-init-users"}  # propios de cada servidor, no van en la base
st, _ = http("GET", f"{GITEA}/repos/invoke/estado-servidores/branches/master", auth=ADMIN, tolerar=(404,))
if st == 404:
    w = tempfile.mkdtemp()
    url = f"http://registro-estado:{token}@127.0.0.1:3000/invoke/estado-servidores.git"
    git = lambda *a: subprocess.run(["git", "-C", w, *a], check=True, capture_output=True, text=True)
    subprocess.run(["git", "clone", "-q", url, w], check=True, capture_output=True)
    src = os.path.join(w, "nodo-so")
    if not os.path.isdir(src):
        sys.exit("ERROR: no hay estado registrado de nodo-so en la rama main (regístralo antes)")
    sshd = open(os.path.join(src, "etc/ssh/sshd_config")).read()
    sshd = re.sub(r"\n*# BEGIN VISITANTES DEMO[\s\S]*# END VISITANTES DEMO\n?", "\n", sshd).rstrip("\n") + "\n"
    sudo = {}
    d_sudo = os.path.join(src, "etc/sudoers.d")
    for f in sorted(os.listdir(d_sudo)) if os.path.isdir(d_sudo) else []:
        if f not in SUDO_PROPIOS:
            sudo[f] = open(os.path.join(d_sudo, f)).read()
    apps = [m.group(1) for m in re.finditer(r"^d ([^/\n]+)$", open(os.path.join(src, "opt/apps.txt")).read(), re.M)]
    git("checkout", "-q", "--orphan", "master")
    git("rm", "-rqf", ".")
    for x in os.listdir(w):
        if x != ".git":
            shutil.rmtree(os.path.join(w, x), ignore_errors=True)
    os.makedirs(os.path.join(w, "base/sudoers.d"))
    open(os.path.join(w, "base/sshd_config"), "w").write(sshd)
    for f, c in sudo.items():
        open(os.path.join(w, "base/sudoers.d", f), "w").write(c)
    open(os.path.join(w, "base/opt_apps.txt"), "w").write("# Carpetas permitidas en /opt/apps (una por línea)\n" + "\n".join(apps) + "\n")
    open(os.path.join(w, "README.md"), "w").write(
        "# Base aprobada (Demo 1)\n\nEsta rama guarda el estado BASE que se propaga a los servidores RHEL similares.\n"
        "Solo cambia por Pull Request revisado (botón \"Proponer como base\" en el panel). Cada commit es una versión que\n"
        "se puede aplicar desde el panel con \"Propagar base\".\n\n"
        "- `base/sshd_config`: archivo completo (el bloque de visitantes es propio de cada servidor y no va aquí)\n"
        "- `base/sudoers.d/`: permisos sudo comunes\n- `base/opt_apps.txt`: carpetas permitidas en /opt/apps\n\n"
        "La historia del estado real de cada servidor está en la rama `main`.\n")
    git("add", "-A")
    git("-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl", "commit", "-qm", "Base inicial desde nodo-so")
    git("push", "-q", "origin", "master")
    shutil.rmtree(w, ignore_errors=True)
    paso("Gitea: rama master creada con la base inicial (estado de nodo-so)")
# master protegida: nadie hace push directo; cambia solo por Pull Request con merge
http("POST", f"{GITEA}/repos/invoke/estado-servidores/branch_protections",
     {"branch_name": "master", "rule_name": "master", "enable_push": False}, auth=ADMIN, tolerar=(403, 409, 422))
http("PUT", f"{GITEA}/repos/invoke/estado-servidores/collaborators/cquezada", {"permission": "write"}, auth=ADMIN, tolerar=(404, 422))
paso("Gitea: master protegida (solo cambia por Pull Request) y cquezada puede aprobar")

# ---------------- AAP ----------------
AUTH = basic("admin", CLAVE_AAP)
aap = lambda m, p, d=None, t=(): http(m, f"{AAP}/{p}", d, AUTH, t, CTX)[1]


def asegurar(rec, nombre, datos, filtro=""):
    o = aap("GET", f"{rec}/?name=" + q(nombre) + filtro)["results"]
    return aap("PATCH", f"{rec}/{o[0]['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


org = aap("GET", "organizations/?name=Default")["results"][0]["id"]
ct = asegurar("credential_types", "Gitea · registro de estado", {
    "kind": "cloud", "description": "Token del usuario técnico que guarda el estado de los servidores en Git",
    "inputs": {"fields": [{"id": "token", "label": "Token de Gitea", "type": "string", "secret": True}], "required": ["token"]},
    "injectors": {"env": {"GITEA_TOKEN_ESTADO": "{{ token }}"}}})["id"]
cred = asegurar("credentials", "Gitea · registro de estado", {"organization": org, "credential_type": ct,
                "inputs": {"token": token}, "description": "Escribe en invoke/estado-servidores"})["id"]
paso("AAP: credencial 'Gitea · registro de estado'")

proy = aap("GET", "projects/?name=" + q("Cumplimiento de servidores"))["results"][0]["id"]
upd = aap("POST", f"projects/{proy}/update/", {})
while aap("GET", f"project_updates/{upd['id']}/")["status"] not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
inv = aap("GET", "inventories/?name=" + q("Drift · Servidores"))["results"][0]["id"]
maq = aap("GET", "credentials/?name=" + q("Servidores de aplicación (ansible)"))["results"][0]["id"]
viejo = aap("GET", "job_templates/?name=" + q("Estado · Propagar referencia"))["results"]
if viejo and not aap("GET", "job_templates/?name=" + q("Estado · Aplicar base desde Git"))["results"]:
    aap("PATCH", f"job_templates/{viejo[0]['id']}/", {"name": "Estado · Aplicar base desde Git"})
jts = {}
for nombre, libro, desc in (
        ("Estado · Registrar servidor", "estado/registrar.yml", "Copia en solo lectura el estado del servidor y lo guarda como commit en Git"),
        ("Estado · Aplicar base desde Git", "estado/aplicar_base.yml", "Aplica la base aprobada (rama master, commit elegido) a servidores similares; vista previa en modo check"),
        ("Estado · Proponer base", "estado/proponer_base.yml", "Abre un Pull Request en Gitea para que el estado de un servidor pase a ser la base aprobada")):
    jt = asegurar("job_templates", nombre, {"organization": org, "project": proy, "inventory": inv, "playbook": libro,
                  "ask_variables_on_launch": True, "ask_job_type_on_launch": True, "diff_mode": True,
                  "allow_simultaneous": True, "description": desc})["id"]
    actuales = [c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]]
    for c in (maq, cred):
        if c not in actuales:
            aap("POST", f"job_templates/{jt}/credentials/", {"id": c})
    if "cumplimiento" not in [l["name"] for l in aap("GET", f"job_templates/{jt}/labels/")["results"]]:
        aap("POST", f"job_templates/{jt}/labels/", {"name": "cumplimiento", "organization": org})
    jts[nombre] = jt
paso("AAP: plantillas 'Estado · Registrar servidor', 'Estado · Aplicar base desde Git' y 'Estado · Proponer base'")

u = http("GET", f"{GW}/users/?username=panel-drift", auth=AUTH, ctx=CTX)[1]["results"]
aid = u[0]["summary_fields"]["resource"]["ansible_id"]
defs = {d["name"]: d["id"] for d in aap("GET", "role_definitions/?page_size=200")["results"]}
for jt in jts.values():
    aap("POST", "role_user_assignments/", {"role_definition": defs["JobTemplate Execute"], "object_id": str(jt),
                                           "user_ansible_id": aid}, t=(400,))
paso("AAP: panel-drift puede lanzar las plantillas de estado")
print(json.dumps({"plantillas": jts}))
print("LISTO")
