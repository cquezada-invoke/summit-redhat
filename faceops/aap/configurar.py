#!/usr/bin/python3
# FaceOps · deja AAP listo para FaceOps (idempotente, corre en la VM del stand):
#  · Usuario de servicio "faceops" en AAP y su token (queda en ~/faceops/faceops.env, 0600; no se imprime)
#  · Gitea invoke/faceops-automatizaciones con el recolector; organización y proyecto "FaceOps"
#  · Plantilla "FaceOps · Recolectar inventario" (caché de facts; inventario, credencial y límite se eligen al lanzar)
#  · "Preguntar límite al lanzar" en las plantillas que actúan sobre equipos (FaceOps las aplica por segmento)
import base64, glob, json, os, re, secrets, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, G, GITEA = "https://localhost/api/controller/v2", "https://localhost/api/gateway/v1", "http://127.0.0.1:3000/api/v1"
CTX = ssl._create_unverified_context()
ADMIN = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
ENV = os.path.expanduser("~/faceops/faceops.env")
INVENTARIOS_DE_EQUIPOS = ["Nodos demo", "Servidores de aplicación", "Drift · Servidores", "Servidores Windows", "Data center 1"]
paso = lambda t: print(f"   · {t}", flush=True)


def http(m, url, d=None, auth=ADMIN, tolerar=False):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": auth})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:500].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http(m, f"{C}/{p}", d, tolerar=t)
gw = lambda m, p, d=None, t=False, auth=ADMIN: http(m, f"{G}/{p}", d, auth=auth, tolerar=t)
gitea = lambda m, p, d=None, t=False: http(m, f"{GITEA}/{p}", d, auth="token " + SEC["gitea_token"], tolerar=t)


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


def esperar(url):
    for _ in range(100):
        st = aap("GET", url)["status"]
        if st in ("successful", "failed", "error", "canceled"):
            return st
        time.sleep(3)


# 1. Usuario de servicio y token
os.makedirs(os.path.dirname(ENV), mode=0o700, exist_ok=True)
env = dict(l.strip().split("=", 1) for l in open(ENV) if "=" in l) if os.path.exists(ENV) else {}
token_ok = env.get("FACEOPS_AAP_TOKEN") and http("GET", f"{C}/me/", auth="Bearer " + env["FACEOPS_AAP_TOKEN"], tolerar=True)
if not token_ok:
    clave = secrets.token_urlsafe(18)
    u = gw("GET", "users/?username=faceops")["results"]
    datos = {"username": "faceops", "password": clave, "first_name": "FaceOps", "last_name": "(servicio)", "is_superuser": True,
             "email": "faceops@invoke.cl"}
    u = gw("PATCH", f"users/{u[0]['id']}/", datos) if u else gw("POST", "users/", datos)
    basic = "Basic " + base64.b64encode(f"faceops:{clave}".encode()).decode()
    tok = gw("POST", "tokens/", {"description": "FaceOps · consola del Managed Service", "scope": "write"}, auth=basic)
    env.update({"FACEOPS_AAP_TOKEN": tok["token"], "FACEOPS_AAP_URL": "https://127.0.0.1"})
    fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, "".join(f"{k}={v}\n" for k, v in env.items()).encode())
    os.close(fd)
    paso("Usuario de servicio «faceops» en AAP y token nuevo (en ~/faceops/faceops.env)")
else:
    paso("Usuario de servicio «faceops» y token vigentes")

# 2. Gitea y proyecto
REPO = "faceops-automatizaciones"
if not gitea("GET", f"repos/invoke/{REPO}", t=True):
    gitea("POST", "user/repos", {"name": REPO, "private": False, "default_branch": "main", "auto_init": True,
                                 "description": "FaceOps: automatizaciones propias (recolector del inventario inteligente)"})
tmp = tempfile.mkdtemp()
url = f"http://invoke:{SEC['gitea_token']}@127.0.0.1:3000/invoke/{REPO}.git"
subprocess.run(["git", "clone", "-q", url, tmp], check=True, capture_output=True)
os.makedirs(f"{tmp}/playbooks", exist_ok=True)
shutil.copy(f"{AQUI}/recolectar.yml", f"{tmp}/playbooks/recolectar.yml")
shutil.copy(f"{AQUI}/apagar.yml", f"{tmp}/playbooks/apagar.yml")
shutil.copy(f"{AQUI}/habilitar-servidor.yml", f"{tmp}/playbooks/habilitar-servidor.yml")
shutil.copy(f"{AQUI}/agente-monitoreo.yml", f"{tmp}/playbooks/agente-monitoreo.yml")
shutil.copy(f"{AQUI}/verificar-flujos.yml", f"{tmp}/playbooks/verificar-flujos.yml")
shutil.copy(f"{AQUI}/segmento-aprobado.yml", f"{tmp}/playbooks/segmento-aprobado.yml")
shutil.copy(f"{AQUI}/replicar-ha.yml", f"{tmp}/playbooks/replicar-ha.yml")
shutil.copy(f"{AQUI}/aplicar-politica.yml", f"{tmp}/playbooks/aplicar-politica.yml")
shutil.copy(f"{AQUI}/vault-servidor.yml", f"{tmp}/playbooks/vault-servidor.yml")
shutil.copy(f"{AQUI}/redis-servidor.yml", f"{tmp}/playbooks/redis-servidor.yml")
shutil.copy(f"{AQUI}/certificados.yml", f"{tmp}/playbooks/certificados.yml")
shutil.copy(f"{AQUI}/cambio-hora.yml", f"{tmp}/playbooks/cambio-hora.yml")
shutil.copy(f"{AQUI}/certificados-inventario.yml", f"{tmp}/playbooks/certificados-inventario.yml")
os.makedirs(f"{tmp}/rulebooks", exist_ok=True)
shutil.copy(f"{AQUI}/rulebooks/certificados.yml", f"{tmp}/rulebooks/certificados.yml")
shutil.copy(f"{AQUI}/rulebooks/openshift.yml", f"{tmp}/rulebooks/openshift.yml")
shutil.copy(f"{AQUI}/openshift-recolectar.yml", f"{tmp}/playbooks/openshift-recolectar.yml")
shutil.copy(f"{AQUI}/openshift-segmento.yml", f"{tmp}/playbooks/openshift-segmento.yml")
shutil.copy(f"{AQUI}/retirar-balanceo.yml", f"{tmp}/playbooks/retirar-balanceo.yml")
shutil.copy(f"{AQUI}/eliminar-vm.yml", f"{tmp}/playbooks/eliminar-vm.yml")
shutil.copy(f"{AQUI}/revisar-cves.yml", f"{tmp}/playbooks/revisar-cves.yml")
shutil.copy(f"{AQUI}/aplicar-parches-seguridad.yml", f"{tmp}/playbooks/aplicar-parches-seguridad.yml")
shutil.copy(f"{AQUI}/storage-emulador.yml", f"{tmp}/playbooks/storage-emulador.yml")
shutil.copy(f"{AQUI}/storage-operar.yml", f"{tmp}/playbooks/storage-operar.yml")
os.makedirs(f"{tmp}/playbooks/files", exist_ok=True)
shutil.copy(os.path.join(os.path.dirname(AQUI), "emuladores", "storage.py"), f"{tmp}/playbooks/files/storage.py")
shutil.copy(f"{AQUI}/openshift-habilitar.yml", f"{tmp}/playbooks/openshift-habilitar.yml")
shutil.copy(f"{AQUI}/nube-simulada.yml", f"{tmp}/playbooks/nube-simulada.yml")
shutil.copy(f"{AQUI}/incorporar-maquinas.yml", f"{tmp}/playbooks/incorporar-maquinas.yml")
for f_ in ("ocp_manifiesto.py", "ocp_assisted.py"):
    shutil.copy(f"{AQUI}/openshift/{f_}", f"{tmp}/playbooks/files/{f_}")
shutil.copy(f"{AQUI}/respaldo-continuo.yml", f"{tmp}/playbooks/respaldo-continuo.yml")
shutil.copy(f"{AQUI}/tareas-respaldo-agente.yml", f"{tmp}/playbooks/tareas-respaldo-agente.yml")
shutil.copy(f"{AQUI}/recuperar-servidor.yml", f"{tmp}/playbooks/recuperar-servidor.yml")
os.makedirs(f"{tmp}/datos", exist_ok=True)
os.makedirs(f"{tmp}/collections", exist_ok=True)
open(f"{tmp}/collections/requirements.yml", "w").write(
    "# Colecciones que AAP descarga al sincronizar el proyecto «FaceOps · automatizaciones»\n"
    "collections:\n  - name: google.cloud\n  - name: ansible.windows\n  - name: ansible.posix\n")
pub_nube = gitea("GET", "repos/invoke/nube/contents/datos/ssh.pub", t=True)
if pub_nube:
    open(f"{tmp}/datos/ssh-nube.pub", "w").write(base64.b64decode(pub_nube["content"]).decode())
g = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True)
g("add", "-A")
if g("status", "--porcelain").stdout.strip():
    g("-c", "user.name=Cristian Quezada", "-c", "user.email=cquezada@invoke.cl", "commit", "-qm", "FaceOps: recolector del inventario y apagado de equipos vacíos")
    g("push", "-q", url, "HEAD:main")
shutil.rmtree(tmp)
paso(f"Gitea: invoke/{REPO}")

if not gw("GET", "organizations/?name=FaceOps")["results"]:
    gw("POST", "organizations/", {"name": "FaceOps", "description": "Automatizaciones propias de FaceOps"})
org = None
for _ in range(20):
    org = uno("organizations", name="FaceOps")
    if org:
        break
    time.sleep(3)
# Galaxy para que el proyecto descargue sus colecciones (las mismas credenciales de la organización Default)
ref = uno("organizations", name="Default")
ya_g = {c["id"] for c in aap("GET", f"organizations/{org['id']}/galaxy_credentials/")["results"]}
for c in aap("GET", f"organizations/{ref['id']}/galaxy_credentials/")["results"] if ref else []:
    if c["id"] not in ya_g:
        aap("POST", f"organizations/{org['id']}/galaxy_credentials/", {"id": c["id"]})
proy = asegurar("projects", "FaceOps · automatizaciones", {
    "organization": org["id"], "scm_type": "git", "scm_url": f"http://192.168.122.1:3000/invoke/{REPO}.git",
    "scm_branch": "main", "scm_update_on_launch": True})["id"]
st = esperar(f"project_updates/{aap('POST', f'projects/{proy}/update/', {})['id']}/")
paso(f"Proyecto «FaceOps · automatizaciones»: {st}")

# 3. Plantilla del recolector
inv = uno("inventories", name="Nodos demo")
ee = uno("execution_environments", name="Default execution environment")
jt = asegurar("job_templates", "FaceOps · Recolectar inventario", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/recolectar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "use_fact_cache": True, "ask_inventory_on_launch": True,
    "ask_credential_on_launch": True, "ask_limit_on_launch": True, "forks": 10,
    "description": "Inventario inteligente de FaceOps: sistema, recursos, servicios, registro y parches de cada equipo"})
cred = uno("credentials", name="Nodos demo (ansible)")
ya = {c["id"] for c in aap("GET", f"job_templates/{jt['id']}/credentials/")["results"]}
if cred and cred["id"] not in ya:
    aap("POST", f"job_templates/{jt['id']}/credentials/", {"id": cred["id"]})
paso(f"Plantilla «FaceOps · Recolectar inventario» (id {jt['id']})")

# 3b. Apagar equipo (FaceOps la lanza siempre dentro de un workflow con aprobación)
ja = asegurar("job_templates", "FaceOps · Apagar equipo", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/apagar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True,
    "description": "Apaga un equipo sin skills (máquina vacía). FaceOps la lanza con aprobación en AAP"})
paso(f"Plantilla «FaceOps · Apagar equipo» (id {ja['id']})")

# 3c. Habilitar servidor (categoría «Sistemas operativos»): anfitrión KVM y Cloud GCP
llave = open(os.path.expanduser("~/faceops/aap-kvm")).read()
t_machine = uno("credential_types", name="Machine", managed=True)
c_kvm = asegurar("credentials", "FaceOps · anfitrión KVM (ssh)", {
    "organization": org["id"], "credential_type": t_machine["id"], "description": "Usuario aap-kvm: solo virsh y virt-install",
    "inputs": {"username": "aap-kvm", "ssh_key_data": llave}})
inv_inf = asegurar("inventories", "FaceOps · Infraestructura", {"organization": org["id"],
                   "description": "Anfitriones donde FaceOps crea servidores (KVM del Centro de datos Santiago)"})
grp = uno("groups", name="anfitrion_kvm", inventory=inv_inf["id"]) or aap("POST", "groups/", {"name": "anfitrion_kvm", "inventory": inv_inf["id"]})
hst = uno("hosts", name="anfitrion-kvm", inventory=inv_inf["id"]) or aap("POST", "hosts/", {
    "name": "anfitrion-kvm", "inventory": inv_inf["id"], "variables": "ansible_host: 192.168.122.1\nansible_python_interpreter: /usr/bin/python3"})
aap("POST", f"groups/{grp['id']}/hosts/", {"id": hst["id"]})
inv_hab = asegurar("inventories", "Servidores habilitados", {"organization": org["id"],
                   "description": "Servidores creados por FaceOps · Habilitar servidor (on-premise y Cloud GCP)"})
for gname in ("linux_onpremise", "linux_gcp", "windows"):
    uno("groups", name=gname, inventory=inv_hab["id"]) or aap("POST", "groups/", {"name": gname, "inventory": inv_hab["id"]})
jh = asegurar("job_templates", "FaceOps · Habilitar servidor", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/habilitar-servidor.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Crea servidores (RHEL 9, Ubuntu 24.04, Windows 2022) on-premise o en Cloud GCP. FaceOps la lanza con aprobación"})
ya = {c["id"] for c in aap("GET", f"job_templates/{jh['id']}/credentials/")["results"]}
for cid in [c_kvm["id"]] + [c["id"] for c in [uno("credentials", name="GCP · invoke-summit-cloud")] if c]:
    if cid not in ya:
        aap("POST", f"job_templates/{jh['id']}/credentials/", {"id": cid})
paso(f"Plantilla «FaceOps · Habilitar servidor» (id {jh['id']}) e inventarios «FaceOps · Infraestructura» y «Servidores habilitados»")

# 3d. Skills propias de FaceOps (se lanzan con el inventario y la credencial de cada equipo)
jm = asegurar("job_templates", "FaceOps · Agente de monitoreo (Linux)", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/agente-monitoreo.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Skill «monitoreo»: instala node_exporter para Prometheus y Grafana"})
paso(f"Plantilla «FaceOps · Agente de monitoreo (Linux)» (id {jm['id']})")

jv = asegurar("job_templates", "FaceOps · Verificar flujos de red", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/verificar-flujos.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True,
    "description": "Prueba los flujos de red del mapa de FaceOps (TCP, sin cambios)"})
paso(f"Plantilla «FaceOps · Verificar flujos de red» (id {jv['id']})")

# Segmento de negocio nuevo: doble aprobación en AAP (Arquitectura → Infraestructura) y cierre
js = asegurar("job_templates", "FaceOps · Segmento aprobado", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/segmento-aprobado.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Cierre de una solicitud de segmento de negocio aprobada (FaceOps publica el manifiesto en Git)"})
wfs = asegurar("workflow_job_templates", "FaceOps · Nuevo segmento de negocio", {
    "organization": org["id"], "ask_variables_on_launch": True,
    "description": "Solicitud de segmento de negocio desde FaceOps: aprueba Arquitectura, luego Infraestructura (que decide parque nuevo o máquinas existentes)"})
if not aap("GET", f"workflow_job_templates/{wfs['id']}/workflow_nodes/")["results"]:
    n1 = aap("POST", f"workflow_job_templates/{wfs['id']}/workflow_nodes/", {})
    aap("POST", f"workflow_job_template_nodes/{n1['id']}/create_approval_template/",
        {"name": "Arquitectura: ¿aprueba la solución del segmento?", "timeout": 72 * 3600,
         "description": "Revisa el manifiesto adjunto en el ticket de FaceOps: componentes, servicios de red, flujos y ficha"})
    n2 = aap("POST", f"workflow_job_templates/{wfs['id']}/workflow_nodes/", {})
    aap("POST", f"workflow_job_template_nodes/{n2['id']}/create_approval_template/",
        {"name": "Infraestructura: ¿aprueba y asigna los recursos?", "timeout": 72 * 3600,
         "description": "En FaceOps decide por componente: parque nuevo o máquinas existentes con el producto"})
    n3 = aap("POST", f"workflow_job_templates/{wfs['id']}/workflow_nodes/", {"unified_job_template": js["id"]})
    aap("POST", f"workflow_job_template_nodes/{n1['id']}/success_nodes/", {"id": n2["id"]})
    aap("POST", f"workflow_job_template_nodes/{n2['id']}/success_nodes/", {"id": n3["id"]})
paso(f"Workflow «FaceOps · Nuevo segmento de negocio» (id {wfs['id']}): Arquitectura → Infraestructura → cierre")

# Replicar con alta disponibilidad: aprobación → Habilitar servidor (réplicas) → réplica del producto y balanceo round robin
jr = asegurar("job_templates", "FaceOps · Replicar y balancear", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/replicar-ha.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Deja las réplicas iguales al origen (paquetes, configuración, despliegues) y las suma a un balanceador Nginx en round robin"})
ya = {c["id"] for c in aap("GET", f"job_templates/{jr['id']}/credentials/")["results"]}
if cred and cred["id"] not in ya:
    aap("POST", f"job_templates/{jr['id']}/credentials/", {"id": cred["id"]})
wfr = asegurar("workflow_job_templates", "FaceOps · Replicar con alta disponibilidad", {
    "organization": org["id"], "ask_variables_on_launch": True,
    "description": "Desde el mapa de FaceOps: crea réplicas de un servidor y las pone en alta disponibilidad detrás de un balanceador (round robin)"})
if not aap("GET", f"workflow_job_templates/{wfr['id']}/workflow_nodes/")["results"]:
    r1 = aap("POST", f"workflow_job_templates/{wfr['id']}/workflow_nodes/", {})
    aap("POST", f"workflow_job_template_nodes/{r1['id']}/create_approval_template/",
        {"name": "¿Aprueba replicar el servidor y balancearlo?", "timeout": 72 * 3600,
         "description": "Crea servidores nuevos (con costo) y cambia el balanceador del segmento"})
    r2 = aap("POST", f"workflow_job_templates/{wfr['id']}/workflow_nodes/", {"unified_job_template": jh["id"]})
    r3 = aap("POST", f"workflow_job_templates/{wfr['id']}/workflow_nodes/", {"unified_job_template": jr["id"]})
    aap("POST", f"workflow_job_template_nodes/{r1['id']}/success_nodes/", {"id": r2["id"]})
    aap("POST", f"workflow_job_template_nodes/{r2['id']}/success_nodes/", {"id": r3["id"]})
paso(f"Workflow «FaceOps · Replicar con alta disponibilidad» (id {wfr['id']}): aprobación → Habilitar servidor → Replicar y balancear")

# Respaldo continuo en Git: la credencial del usuario de servicio de Gitea viaja cifrada en AAP (nunca en el playbook)
TOK_R = os.path.expanduser("~/faceops/respaldo-token")
if os.path.exists(TOK_R):
    ct = uno("credential_types", name="FaceOps · Gitea respaldo") or aap("POST", "credential_types/", {
        "name": "FaceOps · Gitea respaldo", "kind": "cloud", "description": "Token del usuario de servicio faceops-respaldo (repositorio de respaldos y registro de paquetes)",
        "inputs": {"fields": [{"id": "token", "label": "Token", "type": "string", "secret": True}], "required": ["token"]},
        "injectors": {"extra_vars": {"faceops_respaldo_token": "{{ token }}"}}})
    c_r = asegurar("credentials", "FaceOps · Gitea respaldo", {"organization": org["id"], "credential_type": ct["id"],
                   "inputs": {"token": open(TOK_R).read().strip()}})
    jrc = asegurar("job_templates", "FaceOps · Respaldo continuo en Git", {
        "organization": org["id"], "project": proy, "playbook": "playbooks/respaldo-continuo.yml", "inventory": inv["id"],
        "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
        "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
        "description": "Skill «respaldo-git»: cada minuto guarda en Git la configuración, paquetes, servicios y comandos del equipo"})
    ya = {c["id"] for c in aap("GET", f"job_templates/{jrc['id']}/credentials/")["results"]}
    if c_r["id"] not in ya:
        aap("POST", f"job_templates/{jrc['id']}/credentials/", {"id": c_r["id"]})
    paso(f"Plantilla «FaceOps · Respaldo continuo en Git» (id {jrc['id']}) con la credencial cifrada de Gitea")

    # Recuperar servidor (máquina nueva con el mismo nombre) y Volver a una versión (misma máquina): siempre con aprobación
    jre = asegurar("job_templates", "FaceOps · Restaurar desde respaldo", {
        "organization": org["id"], "project": proy, "playbook": "playbooks/recuperar-servidor.yml", "inventory": inv["id"],
        "execution_environment": ee["id"], "ask_variables_on_launch": True,
        "description": "Reconstruye un equipo desde su respaldo en Git y el registro de paquetes (paquetes, usuarios, configuración, SELinux, servicios)"})
    ya = {c["id"] for c in aap("GET", f"job_templates/{jre['id']}/credentials/")["results"]}
    for cid in [x["id"] for x in (cred, c_r) if x]:
        if cid not in ya:
            aap("POST", f"job_templates/{jre['id']}/credentials/", {"id": cid})
    for nombre, desc, pasos in (
            ("FaceOps · Recuperar servidor", "Crea de nuevo un servidor perdido con su mismo nombre y lo reconstruye desde su respaldo en Git",
             ["¿Aprueba recuperar el servidor (se recrea la máquina)?", jh["id"], jre["id"]]),
            ("FaceOps · Volver a una versión", "Deja un servidor como estaba en una versión de su historial (respaldo continuo en Git)",
             ["¿Aprueba volver el servidor a esa versión?", jre["id"]])):
        wf = asegurar("workflow_job_templates", nombre, {"organization": org["id"], "ask_variables_on_launch": True, "description": desc})
        if not aap("GET", f"workflow_job_templates/{wf['id']}/workflow_nodes/")["results"]:
            ant = None
            for p_ in pasos:
                n_ = aap("POST", f"workflow_job_templates/{wf['id']}/workflow_nodes/", {} if isinstance(p_, str) else {"unified_job_template": p_})
                if isinstance(p_, str):
                    aap("POST", f"workflow_job_template_nodes/{n_['id']}/create_approval_template/", {"name": p_, "timeout": 72 * 3600})
                if ant:
                    aap("POST", f"workflow_job_template_nodes/{ant['id']}/success_nodes/", {"id": n_["id"]})
                ant = n_
        paso(f"Workflow «{nombre}» (id {wf['id']})")

# Políticas de seguridad: motor genérico (FaceOps manda la política versionada; aplica o solo verifica)
jpol = asegurar("job_templates", "FaceOps · Aplicar política de seguridad", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/aplicar-politica.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "forks": 10,
    "description": "Aplica (o verifica sin cambiar nada) una política de seguridad versionada de FaceOps en los equipos del límite"})
paso(f"Plantilla «FaceOps · Aplicar política de seguridad» (id {jpol['id']})")

# Vault · Preparar servidor (skill «vault»): bóveda de contraseñas de FaceOps
jva = asegurar("job_templates", "Vault · Preparar servidor", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/vault-servidor.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Skill «vault»: HashiCorp Vault (bóveda de contraseñas y secretos) con interfaz web y desellado automático"})
paso(f"Plantilla «Vault · Preparar servidor» (id {jva['id']})")

# Redis · Preparar servidor (skill «redis»): caché en memoria para las aplicaciones
jrd = asegurar("job_templates", "Redis · Preparar servidor", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/redis-servidor.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Skill «redis»: Redis de Red Hat con contraseña, memoria máxima, persistencia y firewall"})
paso(f"Plantilla «Redis · Preparar servidor» (id {jrd['id']})")

# Cambio de hora del país: tzdata, zona, NTP (Linux y Windows)
jch = asegurar("job_templates", "Sistema · Cambio de hora del país", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/cambio-hora.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Verifica o aplica el horario del país: actualiza tzdata (o carga la regla del decreto), fija la zona, sincroniza NTP"})
paso(f"Plantilla «Sistema · Cambio de hora del país» (id {jch['id']})")

# Certificados (skill «certificados»): emitir y renovar certificados TLS de servicios con la CA interna
for nombre_c, acc_c, desc_c in (
        ("Certificados · Emitir para servicio", "emitir", "Skill «certificados»: registra un servicio (Kafka, nginx, …) y le emite su certificado de la CA interna"),
        ("Certificados · Renovar por vencer", "renovar", "Renueva los certificados registrados que vencen pronto: llave nueva en el equipo, CA interna, recarga y verificación")):
    jc = asegurar("job_templates", nombre_c, {
        "organization": org["id"], "project": proy, "playbook": "playbooks/certificados.yml", "inventory": inv["id"],
        "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
        "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
        "extra_vars": json.dumps({"cert_accion": acc_c}), "description": desc_c})
    paso(f"Plantilla «{nombre_c}» (id {jc['id']})")

# Storage emulado: preparar el emulador (skill) y operarlo (una plantilla por acción)
for nombre_st, pb_st, desc_st in (
        ("Storage · Preparar emulador", "storage-emulador.yml", "Skill «storage-emulado»: Invoke SAN emulada (pools, volúmenes, API y consola web)"),
        ("Storage · Crear volumen", "storage-operar.yml", "Crea un volumen en el arreglo de storage"),
        ("Storage · Ampliar volumen", "storage-operar.yml", "Amplía un volumen del arreglo de storage"),
        ("Storage · Asignar volumen a servidor", "storage-operar.yml", "Asigna (mapea) un volumen a un servidor por iSCSI o FC"),
        ("Storage · Snapshot de volumen", "storage-operar.yml", "Toma un snapshot de un volumen")):
    accion_st = {"Crear": "crear", "Ampliar": "ampliar", "Asignar": "asignar", "Snapshot": "snapshot"}.get(nombre_st.split("· ")[1].split()[0], "")
    j_st = asegurar("job_templates", nombre_st, {
        "organization": org["id"], "project": proy, "playbook": f"playbooks/{pb_st}", "inventory": inv["id"],
        "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
        "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "description": desc_st,
        "extra_vars": json.dumps({"st_accion": accion_st}) if accion_st else ""})
paso("Plantillas de storage (preparar emulador, crear, ampliar, asignar, snapshot)")

# Seguridad: CVE por equipo (solo lectura) y parches de seguridad (con aprobación, la pone FaceOps)
for nombre_s, pb_s, desc_s in (("Seguridad · Revisar CVEs", "revisar-cves.yml", "CVE cubiertos y pendientes de cada equipo (erratas de seguridad de Red Hat)"),
                               ("Seguridad · Aplicar parches de seguridad", "aplicar-parches-seguridad.yml", "Instala las erratas de seguridad pendientes (todas o por CVE)")):
    asegurar("job_templates", nombre_s, {
        "organization": org["id"], "project": proy, "playbook": f"playbooks/{pb_s}", "inventory": inv["id"],
        "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
        "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "forks": 10, "description": desc_s})
paso("Plantillas de seguridad (revisar CVEs, aplicar parches de seguridad)")

# OpenShift · Habilitar clúster: corre en el anfitrión KVM; el pull secret (y el token de OCM, si existe) viajan como
# credenciales cifradas de AAP. La credencial de la nube la agrega FaceOps según la infraestructura elegida.
ct_ps = uno("credential_types", name="OpenShift · Pull secret") or aap("POST", "credential_types/", {
    "name": "OpenShift · Pull secret", "kind": "cloud", "description": "Pull secret de Red Hat para instalar OpenShift (console.redhat.com/openshift/install/pull-secret)",
    "inputs": {"fields": [{"id": "pull_secret", "label": "Pull secret (JSON)", "type": "string", "secret": True, "multiline": True}], "required": ["pull_secret"]},
    "injectors": {"extra_vars": {"ocp_pull_secret": "{{ pull_secret }}"}}})
ct_ocm = uno("credential_types", name="OpenShift · Red Hat OCM") or aap("POST", "credential_types/", {
    "name": "OpenShift · Red Hat OCM", "kind": "cloud", "description": "Offline token de console.redhat.com para el Assisted Installer",
    "inputs": {"fields": [{"id": "token", "label": "Offline token", "type": "string", "secret": True}], "required": ["token"]},
    "injectors": {"extra_vars": {"ocp_ocm_token": "{{ token }}"}}})
creds_ocp = [c_kvm["id"]]
PS = os.path.expanduser("~/faceops/pull-secret.txt")
if os.path.exists(PS):
    c_ps = asegurar("credentials", "OpenShift · Pull secret", {"organization": org["id"], "credential_type": ct_ps["id"],
                    "inputs": {"pull_secret": open(PS).read().strip()}})
    creds_ocp.append(c_ps["id"])
OCM = os.path.expanduser("~/faceops/ocm-token")
if os.path.exists(OCM):
    creds_ocp.append(asegurar("credentials", "OpenShift · Red Hat OCM", {"organization": org["id"], "credential_type": ct_ocm["id"],
                                                                         "inputs": {"token": open(OCM).read().strip()}})["id"])
jocp = asegurar("job_templates", "OpenShift · Habilitar clúster", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/openshift-habilitar.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True, "ask_credential_on_launch": True, "timeout": 0,
    "description": "Instala OpenShift: agent, assisted, IPI o UPI · SNO, compacto o HA · KVM, baremetal, vSphere, GCP, AWS o Azure, desde un manifiesto"})
ya = {c["id"] for c in aap("GET", f"job_templates/{jocp['id']}/credentials/")["results"]}
for cid in creds_ocp:
    if cid not in ya:
        aap("POST", f"job_templates/{jocp['id']}/credentials/", {"id": cid})
paso(f"Plantilla «OpenShift · Habilitar clúster» (id {jocp['id']}) con {len(creds_ocp)} credenciales")

# Incorporar máquinas: usuario de servicio de FaceOps en las máquinas de una nube, por la API de la plataforma
asegurar("job_templates", "FaceOps · Incorporar máquinas", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/incorporar-maquinas.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True, "ask_credential_on_launch": True,
    "description": "Deja el usuario de servicio de FaceOps (llave en AAP) en las máquinas de una nube: GCP, Azure o AWS"})
paso("Plantilla «FaceOps · Incorporar máquinas»")

# Nube GCP simulada: VM en GCP con KVM anidado (otra infraestructura donde FaceOps crea servidores)
jns = asegurar("job_templates", "FaceOps · Nube GCP simulada", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/nube-simulada.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Crea en GCP (invoke-summit-cloud, etiqueta demo=summit-cloud) una VM con KVM anidado que hace de nube adicional"})
ya = {c["id"] for c in aap("GET", f"job_templates/{jns['id']}/credentials/")["results"]}
for c_ in (uno("credentials", name="GCP · invoke-summit-cloud"), uno("credentials", name="Nube · ssh (ansible)")):
    if c_ and c_["id"] not in ya:
        aap("POST", f"job_templates/{jns['id']}/credentials/", {"id": c_["id"]})
paso(f"Plantilla «FaceOps · Nube GCP simulada» (id {jns['id']})")

# Eliminar servidor: aprobación → retirar del balanceo (credencial de los nodos) → eliminar la VM (anfitrión KVM)
jrb = asegurar("job_templates", "FaceOps · Retirar del balanceo", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/retirar-balanceo.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Saca un servidor de los upstream de Nginx de sus balanceadores"})
ya = {c["id"] for c in aap("GET", f"job_templates/{jrb['id']}/credentials/")["results"]}
if cred and cred["id"] not in ya:
    aap("POST", f"job_templates/{jrb['id']}/credentials/", {"id": cred["id"]})
jev = asegurar("job_templates", "FaceOps · Eliminar VM", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/eliminar-vm.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Apaga y retira de KVM un servidor creado por FaceOps (el disco se conserva aparte)"})
ya = {c["id"] for c in aap("GET", f"job_templates/{jev['id']}/credentials/")["results"]}
if c_kvm["id"] not in ya:
    aap("POST", f"job_templates/{jev['id']}/credentials/", {"id": c_kvm["id"]})
wfe = asegurar("workflow_job_templates", "FaceOps · Eliminar servidor", {"organization": org["id"], "ask_variables_on_launch": True,
               "description": "Desde FaceOps: saca el servidor del balanceo, lo apaga y lo retira de KVM (disco y respaldo en Git se conservan)"})
if not aap("GET", f"workflow_job_templates/{wfe['id']}/workflow_nodes/")["results"]:
    e1 = aap("POST", f"workflow_job_templates/{wfe['id']}/workflow_nodes/", {})
    aap("POST", f"workflow_job_template_nodes/{e1['id']}/create_approval_template/", {"name": "¿Aprueba eliminar el servidor?", "timeout": 72 * 3600})
    e2 = aap("POST", f"workflow_job_templates/{wfe['id']}/workflow_nodes/", {"unified_job_template": jrb["id"]})
    e3 = aap("POST", f"workflow_job_templates/{wfe['id']}/workflow_nodes/", {"unified_job_template": jev["id"]})
    aap("POST", f"workflow_job_template_nodes/{e1['id']}/success_nodes/", {"id": e2["id"]})
    aap("POST", f"workflow_job_template_nodes/{e2['id']}/success_nodes/", {"id": e3["id"]})
paso(f"Workflow «FaceOps · Eliminar servidor» (id {wfe['id']}): aprobación → retirar del balanceo → eliminar la VM")

# Plataforma del stand: el servidor donde viven Git (Gitea), el registro (Automation Hub), AAP y la CA interna
inv_pl = asegurar("inventories", "Plataforma del stand", {"organization": org["id"],
                  "description": "Servidores de la plataforma: Git y registro de paquetes, registry de contenedores, AAP, CA interna, KVM"})
vars_pl = {"ansible_host": "192.168.122.1", "faceops_so": "RedHat 9.8", "faceops_categoria": "servidor", "faceops_forma": "virtual",
           "faceops_proveedor": "gcp", "faceops_ubicacion": "Centro de datos Santiago", "faceops_ambiente": "produccion",
           "faceops_skills": ["gitea", "registro-paquetes", "automation-hub", "aap", "certificate-system", "kvm"]}
h_pl = uno("hosts", name="anfitrion-stand", inventory=inv_pl["id"])
if h_pl:
    aap("PATCH", f"hosts/{h_pl['id']}/", {"variables": json.dumps(vars_pl)})
else:
    aap("POST", "hosts/", {"name": "anfitrion-stand", "inventory": inv_pl["id"], "variables": json.dumps(vars_pl)})
paso("Inventario «Plataforma del stand» (anfitrion-stand: Git, registros, AAP, CA interna, KVM)")

# 4. Límite al lanzar en las plantillas que actúan sobre equipos
n = 0
for t in aap("GET", "job_templates/?page_size=200")["results"]:
    inv_n = (t.get("summary_fields", {}).get("inventory") or {}).get("name")
    if inv_n in INVENTARIOS_DE_EQUIPOS and not t["ask_limit_on_launch"]:
        aap("PATCH", f"job_templates/{t['id']}/", {"ask_limit_on_launch": True})
        n += 1
paso(f"«Preguntar límite al lanzar» activado en {n} plantillas más")

# 5. Variables al lanzar: lo que pide el formulario de FaceOps debe llegar a AAP (sin encuesta, AAP las descarta)
n = 0
for r in ("job_templates", "workflow_job_templates"):
    for t in aap("GET", f"{r}/?page_size=200")["results"]:
        if not t["survey_enabled"] and not t["ask_variables_on_launch"] and t["name"] != "Demo Job Template":
            aap("PATCH", f"{r}/{t['id']}/", {"ask_variables_on_launch": True})
            n += 1
paso(f"«Preguntar variables al lanzar» activado en {n} plantillas sin encuesta")
print("LISTO")
