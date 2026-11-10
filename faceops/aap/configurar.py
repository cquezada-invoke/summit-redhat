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
shutil.copy(f"{AQUI}/rendimiento-canal.yml", f"{tmp}/playbooks/rendimiento-canal.yml")
shutil.copy(f"{AQUI}/segmento-aprobado.yml", f"{tmp}/playbooks/segmento-aprobado.yml")
shutil.copy(f"{AQUI}/replicar-ha.yml", f"{tmp}/playbooks/replicar-ha.yml")
shutil.copy(f"{AQUI}/aplicar-politica.yml", f"{tmp}/playbooks/aplicar-politica.yml")
shutil.copy(f"{AQUI}/vault-servidor.yml", f"{tmp}/playbooks/vault-servidor.yml")
shutil.copy(f"{AQUI}/redis-servidor.yml", f"{tmp}/playbooks/redis-servidor.yml")
shutil.copy(f"{AQUI}/certificados.yml", f"{tmp}/playbooks/certificados.yml")
shutil.copy(f"{AQUI}/cambio-hora.yml", f"{tmp}/playbooks/cambio-hora.yml")
shutil.copy(f"{AQUI}/incidentes-verificar.yml", f"{tmp}/playbooks/incidentes-verificar.yml")
shutil.copy(f"{AQUI}/postgresql-soporte.yml", f"{tmp}/playbooks/postgresql-soporte.yml")
for _f in os.listdir(AQUI):  # Migraciones · sistema operativo (playbook, sus tareas y el planificador)
    if _f.startswith("migrar-so"):
        shutil.copy(f"{AQUI}/{_f}", f"{tmp}/playbooks/{_f}")
shutil.copy(f"{AQUI}/certificados-inventario.yml", f"{tmp}/playbooks/certificados-inventario.yml")
os.makedirs(f"{tmp}/rulebooks", exist_ok=True)
shutil.copy(f"{AQUI}/rulebooks/certificados.yml", f"{tmp}/rulebooks/certificados.yml")
shutil.copy(f"{AQUI}/rulebooks/openshift.yml", f"{tmp}/rulebooks/openshift.yml")
shutil.copy(f"{AQUI}/rulebooks/alertas-objetos.yml", f"{tmp}/rulebooks/alertas-objetos.yml")
shutil.copy(f"{AQUI}/monitoreo-responder.yml", f"{tmp}/playbooks/monitoreo-responder.yml")
shutil.copy(f"{AQUI}/openshift-recolectar.yml", f"{tmp}/playbooks/openshift-recolectar.yml")
shutil.copy(f"{AQUI}/openshift-segmento.yml", f"{tmp}/playbooks/openshift-segmento.yml")
shutil.copy(f"{AQUI}/retirar-balanceo.yml", f"{tmp}/playbooks/retirar-balanceo.yml")
shutil.copy(f"{AQUI}/eliminar-vm.yml", f"{tmp}/playbooks/eliminar-vm.yml")
shutil.copy(f"{AQUI}/energia.yml", f"{tmp}/playbooks/energia.yml")
shutil.copy(f"{AQUI}/disco-anfitrion.yml", f"{tmp}/playbooks/disco-anfitrion.yml")
shutil.copy(f"{AQUI}/disco-preparar.yml", f"{tmp}/playbooks/disco-preparar.yml")
shutil.copy(f"{AQUI}/disco-ampliar-anfitrion.yml", f"{tmp}/playbooks/disco-ampliar-anfitrion.yml")
shutil.copy(f"{AQUI}/recursos-anfitrion.yml", f"{tmp}/playbooks/recursos-anfitrion.yml")
shutil.copy(f"{AQUI}/autoinicio-anfitrion.yml", f"{tmp}/playbooks/autoinicio-anfitrion.yml")
shutil.copy(f"{AQUI}/satellite-instalar.yml", f"{tmp}/playbooks/satellite-instalar.yml")
shutil.copy(f"{AQUI}/satellite-incorporar-rhel.yml", f"{tmp}/playbooks/satellite-incorporar-rhel.yml")
shutil.copy(f"{AQUI}/files/alertamiento-ocp.yml", f"{tmp}/playbooks/files/alertamiento-ocp.yml")
for f_ in ("alertas-objetos.yml", "alertas_objeto.py"):   # Monitoreo · Configurar alertas del equipo (catálogo compartido con FaceOps)
    shutil.copy(f"{AQUI}/files/{f_}", f"{tmp}/playbooks/files/{f_}")
shutil.copy(f"{AQUI}/monitoreo-alertas.yml", f"{tmp}/playbooks/monitoreo-alertas.yml")
shutil.copy(f"{AQUI}/identidad-fuente.yml", f"{tmp}/playbooks/identidad-fuente.yml")   # Identidad · Fuente de usuarios de FaceOps
shutil.copy(f"{AQUI}/files/identidad_fuente.py", f"{tmp}/playbooks/files/identidad_fuente.py")
shutil.copy(f"{AQUI}/inventario-archivos.yml", f"{tmp}/playbooks/inventario-archivos.yml")
shutil.copy(f"{AQUI}/quitar-agente.yml", f"{tmp}/playbooks/quitar-agente.yml")
shutil.copy(f"{AQUI}/postgresql-servidor.yml", f"{tmp}/playbooks/postgresql-servidor.yml")
shutil.copy(f"{AQUI}/mysql-servidor.yml", f"{tmp}/playbooks/mysql-servidor.yml")
shutil.copy(f"{AQUI}/mongodb-servidor.yml", f"{tmp}/playbooks/mongodb-servidor.yml")   # Sistema · Inventariar máquina · parte 3
shutil.copy(f"{AQUI}/files/inventario-a-git.py", f"{tmp}/playbooks/files/inventario-a-git.py")
shutil.copytree(f"{AQUI}/files/lib", f"{tmp}/playbooks/files/lib", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
for f_ in ("satellite-convertir-rhel.yml", "satellite-actualizar.yml", "vpn-servidor.yml", "vpn-gestion.yml", "openshift-virtualizacion.yml", "migracion-iis-ocp.yml",
           "openshift-alertamiento.yml", "openshift-remediar.yml", "openshift-imagenes.yml", "homologacion-relevar.yml", "infra-dc-emulado.yml", "infra-mover-maquina.yml",
           "fiscalia-aviso-complemento.yml", "storage-lake-s3.yml"):
    shutil.copy(f"{AQUI}/{f_}", f"{tmp}/playbooks/{f_}")
shutil.copy(f"{AQUI}/devsecops-pipeline.yml", f"{tmp}/playbooks/devsecops-pipeline.yml")
shutil.copy(f"{AQUI}/disco-ampliar.yml", f"{tmp}/playbooks/disco-ampliar.yml")
shutil.copy(f"{AQUI}/servicio-plataforma.yml", f"{tmp}/playbooks/servicio-plataforma.yml")
shutil.copy(f"{AQUI}/desinstalar-plataforma.yml", f"{tmp}/playbooks/desinstalar-plataforma.yml")
shutil.copy(f"{AQUI}/windows-escritorio-remoto.yml", f"{tmp}/playbooks/windows-escritorio-remoto.yml")
shutil.copy(f"{AQUI}/paquetes-registro.yml", f"{tmp}/playbooks/paquetes-registro.yml")
shutil.copy(f"{AQUI}/inventariar-maquina.yml", f"{tmp}/playbooks/inventariar-maquina.yml")
shutil.copy(f"{AQUI}/origen-maquinas.yml", f"{tmp}/playbooks/origen-maquinas.yml")
shutil.copy(f"{AQUI}/registro-imagenes.yml", f"{tmp}/playbooks/registro-imagenes.yml")
shutil.copy(f"{AQUI}/vincular-maquina.yml", f"{tmp}/playbooks/vincular-maquina.yml")
shutil.copy(f"{AQUI}/evaluar-stack.yml", f"{tmp}/playbooks/evaluar-stack.yml")
shutil.copy(f"{AQUI}/licencias-levantar.yml", f"{tmp}/playbooks/licencias-levantar.yml")
shutil.copy(f"{AQUI}/politicas-dev-evaluar.yml", f"{tmp}/playbooks/politicas-dev-evaluar.yml")
shutil.copy(f"{AQUI}/datalake-derivar.yml", f"{tmp}/playbooks/datalake-derivar.yml")
shutil.copy(f"{AQUI}/uso-segmentos.yml", f"{tmp}/playbooks/uso-segmentos.yml")
shutil.copy(f"{AQUI}/redis-insight.yml", f"{tmp}/playbooks/redis-insight.yml")
shutil.copy(f"{AQUI}/jenkins-servidor.yml", f"{tmp}/playbooks/jenkins-servidor.yml")
shutil.copy(f"{AQUI}/sqlserver-servidor.yml", f"{tmp}/playbooks/sqlserver-servidor.yml")
shutil.copy(f"{AQUI}/sqlserver-medicion.yml", f"{tmp}/playbooks/sqlserver-medicion.yml")
shutil.copy(f"{AQUI}/sqlserver-ha.yml", f"{tmp}/playbooks/sqlserver-ha.yml")
shutil.copy(f"{AQUI}/sqlserver-conmutar.yml", f"{tmp}/playbooks/sqlserver-conmutar.yml")
shutil.copy(f"{AQUI}/despliegue-trafico.yml", f"{tmp}/playbooks/despliegue-trafico.yml")
shutil.copy(f"{AQUI}/k8s-ocp-evaluar.yml", f"{tmp}/playbooks/k8s-ocp-evaluar.yml")
shutil.copy(f"{AQUI}/k8s-ocp-preparar-destino.yml", f"{tmp}/playbooks/k8s-ocp-preparar-destino.yml")
shutil.copy(f"{AQUI}/migracion-gcp-evaluar.yml", f"{tmp}/playbooks/migracion-gcp-evaluar.yml")
shutil.copy(f"{AQUI}/migracion-gcp-servidor.yml", f"{tmp}/playbooks/migracion-gcp-servidor.yml")
shutil.copy(f"{AQUI}/k8s-ocp-convertir.yml", f"{tmp}/playbooks/k8s-ocp-convertir.yml")
shutil.copy(f"{AQUI}/k8s-ocp-migrar.yml", f"{tmp}/playbooks/k8s-ocp-migrar.yml")
shutil.copy(f"{AQUI}/k8s-ocp-conmutar.yml", f"{tmp}/playbooks/k8s-ocp-conmutar.yml")
shutil.copy(f"{AQUI}/registrar-rhel.yml", f"{tmp}/playbooks/registrar-rhel.yml")
os.makedirs(f"{tmp}/playbooks/files", exist_ok=True)
shutil.copy(f"{AQUI}/red-inventariar.yml", f"{tmp}/playbooks/red-inventariar.yml")   # Red · Inventariar equipo de red
for f_ in ("red-lectura.sh", "red_normalizar.py"):
    shutil.copy(f"{AQUI}/files/{f_}", f"{tmp}/playbooks/files/{f_}")
shutil.copy(f"{AQUI}/files/uso-medir.py", f"{tmp}/playbooks/files/uso-medir.py")
shutil.copy(f"{AQUI}/revisar-cves.yml", f"{tmp}/playbooks/revisar-cves.yml")
shutil.copy(f"{AQUI}/escaneo-servidores.yml", f"{tmp}/playbooks/escaneo-servidores.yml")
shutil.copy(f"{AQUI}/aplicar-parches-seguridad.yml", f"{tmp}/playbooks/aplicar-parches-seguridad.yml")
shutil.copy(f"{AQUI}/storage-emulador.yml", f"{tmp}/playbooks/storage-emulador.yml")
shutil.copy(f"{AQUI}/storage-operar.yml", f"{tmp}/playbooks/storage-operar.yml")
os.makedirs(f"{tmp}/playbooks/files", exist_ok=True)
shutil.copy(os.path.join(os.path.dirname(AQUI), "emuladores", "storage.py"), f"{tmp}/playbooks/files/storage.py")
shutil.copy(f"{AQUI}/openshift-operador.yml", f"{tmp}/playbooks/openshift-operador.yml")
shutil.copy(f"{AQUI}/imagenes-rhel.yml", f"{tmp}/playbooks/imagenes-rhel.yml")
for f_ in ("openshift-regularizacion-diagnostico.yml", "openshift-etcd-respaldo.yml", "migracion-jboss-eap8.yml",
           "openshift-olm-regularizar.yml", "openshift-olm-aprobar-uno.yml", "openshift-version.yml", "openshift-limites.yml", "openshift-informe.yml", "openshift-acs.yml", "openshift-certificado.yml",
           "ciclo-vida-evaluar-destino.yml"):   # guía de regularización de OpenShift
    shutil.copy(f"{AQUI}/{f_}", f"{tmp}/playbooks/{f_}")
shutil.copy(f"{AQUI}/openshift-ipi-vmware-preparar.yml", f"{tmp}/playbooks/openshift-ipi-vmware-preparar.yml")
shutil.copy(f"{AQUI}/openshift-ipi-vmware-instalar.yml", f"{tmp}/playbooks/openshift-ipi-vmware-instalar.yml")
shutil.copy(f"{AQUI}/nube-simulada.yml", f"{tmp}/playbooks/nube-simulada.yml")
shutil.copy(f"{AQUI}/incorporar-maquinas.yml", f"{tmp}/playbooks/incorporar-maquinas.yml")
for f_ in ("ipi_vmware_vcenter.py",):
    shutil.copy(f"{AQUI}/openshift/{f_}", f"{tmp}/playbooks/files/{f_}")
for f_ in ("jboss_eap8_analizar.py", "jboss_eap8_ajustar.py"):
    shutil.copy(f"{AQUI}/files/{f_}", f"{tmp}/playbooks/files/{f_}")
for f_ in ("olm_clasificar.py", "ocp_version_evaluar.py", "ocp_limites_medir.py", "ocp_informe.py", "ocp_diagnostico.py", "ocp_alertamiento.py", "ocp_imagenes.py"):
    shutil.copy(f"{AQUI}/files/{f_}", f"{tmp}/playbooks/files/{f_}")
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
    "description": "Crea servidores (RHEL 5 a 10, Ubuntu 24.04, Windows 2022) on-premise o en Cloud GCP. FaceOps la lanza con aprobación"})
jom = asegurar("job_templates", "Infra · Origen de las máquinas", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/origen-maquinas.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"],
    "description": "Solo lectura: de qué imagen base o ISO nació el sistema de cada máquina virtual del anfitrión KVM (con su huella)"})
if c_kvm["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jom['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jom['id']}/credentials/", {"id": c_kvm["id"]})
paso(f"Plantilla «Infra · Origen de las máquinas» (id {jom['id']})")
jri = asegurar("job_templates", "Infra · Cargar imágenes base al registro", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/registro-imagenes.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Sube al registro propio las imágenes base e ISO que usan las máquinas y que no estén con la misma huella"})
_ya_ri = {c["id"] for c in aap("GET", f"job_templates/{jri['id']}/credentials/")["results"]}
for c_ in (c_kvm, uno("credentials", name="FaceOps · Gitea respaldo")):
    if c_ and c_["id"] not in _ya_ri:
        aap("POST", f"job_templates/{jri['id']}/credentials/", {"id": c_["id"]})
paso(f"Plantilla «Infra · Cargar imágenes base al registro» (id {jri['id']})")
# Imágenes base de RHEL 5 a 10 desde el portal de Red Hat (offline token de la cuenta, en ~/faceops/rh-offline-token)
ct_rh = uno("credential_types", name="Red Hat · Offline token") or aap("POST", "credential_types/", {
    "name": "Red Hat · Offline token", "kind": "cloud", "description": "Offline token de access.redhat.com/management/api (descarga de imágenes y RPM)",
    "inputs": {"fields": [{"id": "token", "label": "Offline token", "type": "string", "secret": True, "multiline": True}], "required": ["token"]},
    "injectors": {"extra_vars": {"rh_offline_token": "{{ token }}"}}})
TK_RH = os.path.expanduser("~/faceops/rh-offline-token")
c_rh = asegurar("credentials", "Red Hat · Offline token", {"organization": org["id"], "credential_type": ct_rh["id"],
                "inputs": {"token": open(TK_RH).read().strip()}}) if os.path.exists(TK_RH) else None
jri = asegurar("job_templates", "FaceOps · Preparar imágenes RHEL", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/imagenes-rhel.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True, "timeout": 3 * 3600,
    "description": "Descarga del portal de Red Hat las imágenes base de RHEL 5 a 10 para crear servidores (RHEL 5 se instala desde su ISO)"})
ya_ri = {c["id"] for c in aap("GET", f"job_templates/{jri['id']}/credentials/")["results"]}
for c_ in (c_kvm, c_rh):
    if c_ and c_["id"] not in ya_ri:
        aap("POST", f"job_templates/{jri['id']}/credentials/", {"id": c_["id"]})
paso(f"Plantilla «FaceOps · Preparar imágenes RHEL» (id {jri['id']})")
ya = {c["id"] for c in aap("GET", f"job_templates/{jh['id']}/credentials/")["results"]}
c_vmw = next(iter(aap("GET", "credentials/?credential_type__kind=vmware")["results"]), None)   # el vCenter de la infraestructura VMware
for cid in [c_kvm["id"]] + [c["id"] for c in [uno("credentials", name="GCP · invoke-summit-cloud"), c_vmw] if c]:
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
jrc = asegurar("job_templates", "FaceOps · Rendimiento del canal", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/rendimiento-canal.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True,
    "description": "Prueba de rendimiento no invasiva de los tramos de un segmento (TCP corto, varias rondas, sin cambios)"})
paso(f"Plantilla «FaceOps · Rendimiento del canal» (id {jrc['id']})")

# Segmento de negocio nuevo: doble aprobación en AAP (Arquitectura → Infraestructura) y cierre
js = asegurar("job_templates", "FaceOps · Segmento aprobado", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/segmento-aprobado.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Cierre de una solicitud de segmento de negocio aprobada (FaceOps publica el manifiesto en Git)"})
wfs = asegurar("workflow_job_templates", "FaceOps · Nuevo segmento de negocio", {
    "organization": org["id"], "ask_variables_on_launch": True,
    "description": "Solicitud de segmento de negocio desde FaceOps: una aprobación (Arquitectura e Infraestructura, que decide parque nuevo o máquinas existentes)"})
# Una sola aprobación por ticket: Arquitectura e Infraestructura en el mismo paso (quien aprueba también asigna los recursos)
nodos_s = aap("GET", f"workflow_job_templates/{wfs['id']}/workflow_nodes/")["results"]
if len(nodos_s) != 2:
    for n_ in nodos_s:
        aap("DELETE", f"workflow_job_template_nodes/{n_['id']}/")
    n1 = aap("POST", f"workflow_job_templates/{wfs['id']}/workflow_nodes/", {})
    aap("POST", f"workflow_job_template_nodes/{n1['id']}/create_approval_template/",
        {"name": "Arquitectura e Infraestructura: ¿aprueba la solución y asigna los recursos?", "timeout": 0,
         "description": "Revisa el manifiesto en el ticket de FaceOps y decide por componente: parque nuevo o máquinas existentes con el producto"})
    n3 = aap("POST", f"workflow_job_templates/{wfs['id']}/workflow_nodes/", {"unified_job_template": js["id"]})
    aap("POST", f"workflow_job_template_nodes/{n1['id']}/success_nodes/", {"id": n3["id"]})
paso(f"Workflow «FaceOps · Nuevo segmento de negocio» (id {wfs['id']}): una aprobación (Arquitectura e Infraestructura) → cierre")

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

# Satellite: aprobación → Habilitar servidor (RHEL 9 con la clave de activación del cliente) → instalar Satellite y cargar el manifiesto
jsi = asegurar("job_templates", "Satellite · Instalar", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/satellite-instalar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True, "timeout": 10800,
    "description": "Instala Red Hat Satellite en la máquina creada, carga el manifiesto y deja el ciclo de vida de contenido"})
ya = {c["id"] for c in aap("GET", f"job_templates/{jsi['id']}/credentials/")["results"]}
if cred and cred["id"] not in ya:
    aap("POST", f"job_templates/{jsi['id']}/credentials/", {"id": cred["id"]})
wsi = asegurar("workflow_job_templates", "Satellite · Instalar servidor", {
    "organization": org["id"], "ask_variables_on_launch": True,
    "extra_vars": json.dumps({"hab_so": "rhel9", "hab_destino": "onpremise", "hab_replicas": "1", "hab_cpu": "4", "hab_ram_gb": "20", "hab_disco_gb": "150", "hab_nombre": "satellite", "hab_ambiente": "produccion", "hab_ubicacion": "Centro de datos Santiago", "sat_org": "Invoke", "sat_version": "6.17", "sat_rhel": [8, 9]}, ensure_ascii=False),
    "description": "Crea la máquina de Red Hat Satellite (RHEL 9, 4 vCPU, 20 GB) con la suscripción del cliente, instala Satellite y carga el manifiesto"})
if not aap("GET", f"workflow_job_templates/{wsi['id']}/workflow_nodes/")["results"]:
    s1 = aap("POST", f"workflow_job_templates/{wsi['id']}/workflow_nodes/", {})
    aap("POST", f"workflow_job_template_nodes/{s1['id']}/create_approval_template/",
        {"name": "¿Aprueba crear el servidor de Satellite e instalarlo?", "timeout": 0,
         "description": "Crea una máquina de 4 vCPU y 20 GB y consume la suscripción de Satellite del manifiesto"})
    s2 = aap("POST", f"workflow_job_templates/{wsi['id']}/workflow_nodes/", {"unified_job_template": jh["id"]})
    s3 = aap("POST", f"workflow_job_templates/{wsi['id']}/workflow_nodes/", {"unified_job_template": jsi["id"]})
    aap("POST", f"workflow_job_template_nodes/{s1['id']}/success_nodes/", {"id": s2["id"]})
    aap("POST", f"workflow_job_template_nodes/{s2['id']}/success_nodes/", {"id": s3["id"]})
paso(f"Workflow «Satellite · Instalar servidor» (id {wsi['id']}): aprobación → Habilitar servidor → Instalar Satellite")
jsr = asegurar("job_templates", "Satellite · Incorporar RHEL", {   # acción de la plataforma Satellite: inventario y credencial del equipo al lanzar
    "organization": org["id"], "project": proy, "playbook": "playbooks/satellite-incorporar-rhel.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "timeout": 3600,
    "description": "Registra servidores RHEL en un Satellite (contenido, parches y vulnerabilidades)"})
paso(f"Plantilla «Satellite · Incorporar RHEL» (id {jsr['id']})")

jho = asegurar("job_templates", "Homologación · Relevar entorno", {   # solo lectura; inventario, credencial y límite al lanzar
    "organization": org["id"], "project": proy, "playbook": "playbooks/homologacion-relevar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "timeout": 1800,
    "description": "Huella del entorno (sistema, recursos, condiciones, JBoss/Java) para homologar ambientes. Solo lectura"})
paso(f"Plantilla «Homologación · Relevar entorno» (id {jho['id']})")

jls = asegurar("job_templates", "Storage · Servicio S3 del data lake", {   # acción sobre un equipo de almacenamiento: inventario y credencial al lanzar
    "organization": org["id"], "project": proy, "playbook": "playbooks/storage-lake-s3.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "timeout": 1800,
    "description": "Servicio S3 (SeaweedFS) del data lake sobre un disco dedicado del equipo de almacenamiento; puertos solo a los clientes indicados"})
paso(f"Plantilla «Storage · Servicio S3 del data lake» (id {jls['id']})")

# VPN (WireGuard): aprobación → Habilitar servidor (RHEL 9 con la llave de la organización) → instalar la VPN · gestión de accesos
jvi = asegurar("job_templates", "VPN · Instalar", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/vpn-servidor.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True, "timeout": 3600,
    "description": "Instala el servidor VPN WireGuard en la máquina creada (paso del workflow «VPN · Preparar servidor»)"})
wvp = asegurar("workflow_job_templates", "VPN · Preparar servidor", {
    "organization": org["id"], "ask_variables_on_launch": True,
    "extra_vars": json.dumps({"hab_so": "rhel9", "hab_destino": "onpremise", "hab_replicas": "1", "hab_cpu": "1", "hab_ram_gb": "2", "hab_disco_gb": "20", "hab_nombre": "vpn", "hab_ambiente": "produccion", "hab_ubicacion": "Centro de datos Santiago", "hab_interno": True}, ensure_ascii=False),
    "description": "Crea el servidor VPN (RHEL 9, 1 vCPU, 2 GB) e instala WireGuard administrado por FaceOps"})
if not aap("GET", f"workflow_job_templates/{wvp['id']}/workflow_nodes/")["results"]:
    v1 = aap("POST", f"workflow_job_templates/{wvp['id']}/workflow_nodes/", {})
    aap("POST", f"workflow_job_template_nodes/{v1['id']}/create_approval_template/",
        {"name": "¿Aprueba crear el servidor VPN?", "timeout": 0, "description": "Crea una máquina de 1 vCPU y 2 GB con WireGuard"})
    v2 = aap("POST", f"workflow_job_templates/{wvp['id']}/workflow_nodes/", {"unified_job_template": jh["id"]})
    v3 = aap("POST", f"workflow_job_templates/{wvp['id']}/workflow_nodes/", {"unified_job_template": jvi["id"]})
    aap("POST", f"workflow_job_template_nodes/{v1['id']}/success_nodes/", {"id": v2["id"]})
    aap("POST", f"workflow_job_template_nodes/{v2['id']}/success_nodes/", {"id": v3["id"]})
if cred and cred["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jvi['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jvi['id']}/credentials/", {"id": cred["id"]})
paso(f"Workflow «VPN · Preparar servidor» (id {wvp['id']}): aprobación → Habilitar servidor → Instalar VPN")
jvg = asegurar("job_templates", "VPN · Gestionar acceso", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/vpn-gestion.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "timeout": 900,
    "description": "Clientes y usuarios de la VPN: crear, extender vigencia y revocar (la configuración del usuario va a la bóveda)"})
paso(f"Plantilla «VPN · Gestionar acceso» (id {jvg['id']})")

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
    jpq = asegurar("job_templates", "Sistema · Paquetes · inventario y respaldo", {
        "organization": org["id"], "project": proy, "playbook": "playbooks/paquetes-registro.yml", "inventory": inv["id"],
        "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
        "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "timeout": 3600,
        "description": "Lista los paquetes de la máquina con su fuente y respalda en el registro propio (repositorio/rpm) los RPM que falten"})
    if c_r["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jpq['id']}/credentials/")["results"]}:
        aap("POST", f"job_templates/{jpq['id']}/credentials/", {"id": c_r["id"]})
    paso(f"Plantilla «Sistema · Paquetes · inventario y respaldo» (id {jpq['id']})")
    jim = asegurar("job_templates", "Sistema · Inventariar máquina", {
        "organization": org["id"], "project": proy, "playbook": "playbooks/inventariar-maquina.yml", "inventory": inv["id"],
        "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
        "ask_limit_on_launch": True, "ask_variables_on_launch": True, "use_fact_cache": True, "timeout": 3600,
        "description": "Trae toda la información de la máquina (sistema, servicios, paquetes con su fuente) y respalda los RPM que falten"})
    if c_r["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jim['id']}/credentials/")["results"]}:
        aap("POST", f"job_templates/{jim['id']}/credentials/", {"id": c_r["id"]})
    _cb = uno("credentials", name="FaceOps · Bóveda (escritura)")   # secretos de la instantánea → bóveda (maquinas/<nombre>/archivos)
    if _cb and _cb["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jim['id']}/credentials/")["results"]}:
        aap("POST", f"job_templates/{jim['id']}/credentials/", {"id": _cb["id"]})
    paso(f"Plantilla «Sistema · Inventariar máquina» (id {jim['id']})")

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
jri = asegurar("job_templates", "Redis · Instalar Redis Insight", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/redis-insight.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Consola web oficial de Redis (Redis Insight) en la misma máquina, publicada a través de FaceOps"})
paso(f"Plantilla «Redis · Instalar Redis Insight» (id {jri['id']})")
jjk = asegurar("job_templates", "Jenkins · Preparar servidor", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/jenkins-servidor.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Skill «jenkins»: Jenkins LTS en contenedor con Pipeline y Git, publicado a través de FaceOps"})
paso(f"Plantilla «Jenkins · Preparar servidor» (id {jjk['id']})")
jsq1 = asegurar("job_templates", "SQL Server · Preparar servidor", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/sqlserver-servidor.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Skill «sqlserver»: Microsoft SQL Server 2022 con sqlcmd, base de ejemplo en modelo completo y clave de sa en la bóveda"})
paso(f"Plantilla «SQL Server · Preparar servidor» (id {jsq1['id']})")
j_postgresql = asegurar("job_templates", "PostgreSQL · Preparar servidor", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/postgresql-servidor.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "description": "Skill «postgres»: PostgreSQL 16 con acceso por red y clave en la bóveda"})
_cbs = uno("credentials", name="FaceOps · Bóveda (servicios)")
if _cbs and _cbs["id"] not in {c["id"] for c in aap("GET", f"job_templates/{j_postgresql['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{j_postgresql['id']}/credentials/", {"id": _cbs["id"]})
paso(f"Plantilla «PostgreSQL · Preparar servidor» (id {j_postgresql['id']})")
j_mysql = asegurar("job_templates", "MySQL · Preparar servidor", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/mysql-servidor.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "description": "Skill «mysql»: MySQL 8.4 con usuario de administración y clave en la bóveda"})
_cbs = uno("credentials", name="FaceOps · Bóveda (servicios)")
if _cbs and _cbs["id"] not in {c["id"] for c in aap("GET", f"job_templates/{j_mysql['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{j_mysql['id']}/credentials/", {"id": _cbs["id"]})
paso(f"Plantilla «MySQL · Preparar servidor» (id {j_mysql['id']})")
j_mongodb = asegurar("job_templates", "MongoDB · Preparar servidor", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/mongodb-servidor.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "description": "Skill «mongodb»: MongoDB 8.0 con autenticación y clave en la bóveda"})
_cbs = uno("credentials", name="FaceOps · Bóveda (servicios)")
if _cbs and _cbs["id"] not in {c["id"] for c in aap("GET", f"job_templates/{j_mongodb['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{j_mongodb['id']}/credentials/", {"id": _cbs["id"]})
paso(f"Plantilla «MongoDB · Preparar servidor» (id {j_mongodb['id']})")
jsq2 = asegurar("job_templates", "SQL Server · Levantamiento y medición", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/sqlserver-medicion.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Solo lectura: versión, edición y lo que permite, bases, volumen de cambio del log y latencia entre centros de datos"})
paso(f"Plantilla «SQL Server · Levantamiento y medición» (id {jsq2['id']})")
jsq3 = asegurar("job_templates", "SQL Server · Configurar alta disponibilidad", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/sqlserver-ha.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Availability Group entre centros de datos (sin clúster): certificado, replicación cifrada, siembra automática y prueba de replicación"})
paso(f"Plantilla «SQL Server · Configurar alta disponibilidad» (id {jsq3['id']})")
jsq4 = asegurar("job_templates", "SQL Server · Conmutar réplica primaria", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/sqlserver-conmutar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Conmutación planificada sin pérdida de datos al servidor elegido, con verificación de escritura y lectura; la reversa es la misma con el primario original"})
paso(f"Plantilla «SQL Server · Conmutar réplica primaria» (id {jsq4['id']})")
jdt = asegurar("job_templates", "Despliegue · Cambiar tráfico", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/despliegue-trafico.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Estrategias de despliegue en el balanceador: blue-green, canary y A/B, con medición y vuelta atrás"})
paso(f"Plantilla «Despliegue · Cambiar tráfico» (id {jdt['id']})")
jko0 = asegurar("job_templates", "Migración K8s → OpenShift · Evaluar origen", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/k8s-ocp-evaluar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_credential_on_launch": True, "ask_variables_on_launch": True,
    "description": "Esqueleto: inventario del clúster Kubernetes de origen y semáforo de compatibilidad con OpenShift (solo lectura)"})
paso(f"Plantilla «Migración K8s → OpenShift · Evaluar origen» (id {jko0['id']})")
jko1 = asegurar("job_templates", "Migración K8s → OpenShift · Preparar destino", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/k8s-ocp-preparar-destino.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_credential_on_launch": True, "ask_variables_on_launch": True,
    "description": "Esqueleto: proyectos, cuotas, SCC, StorageClass GCE PD CSI y OADP con bucket GCS en OpenShift sobre GCP"})
paso(f"Plantilla «Migración K8s → OpenShift · Preparar destino» (id {jko1['id']})")
jko2 = asegurar("job_templates", "Migración K8s → OpenShift · Convertir manifiestos", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/k8s-ocp-convertir.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_credential_on_launch": True, "ask_variables_on_launch": True,
    "description": "Esqueleto: manifiestos del origen convertidos a OpenShift (Route, securityContext, StorageClass) como Pull Request GitOps"})
paso(f"Plantilla «Migración K8s → OpenShift · Convertir manifiestos» (id {jko2['id']})")
jko3 = asegurar("job_templates", "Migración K8s → OpenShift · Migrar aplicación", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/k8s-ocp-migrar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_credential_on_launch": True, "ask_variables_on_launch": True,
    "description": "Esqueleto: respaldo Velero en el origen y restauración OADP en OpenShift (o GitOps + datos)"})
paso(f"Plantilla «Migración K8s → OpenShift · Migrar aplicación» (id {jko3['id']})")
jko4 = asegurar("job_templates", "Migración K8s → OpenShift · Validar y conmutar", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/k8s-ocp-conmutar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_credential_on_launch": True, "ask_variables_on_launch": True,
    "description": "Esqueleto: pruebas de humo, DNS hacia OpenShift y origen a cero; reversa al origen"})
paso(f"Plantilla «Migración K8s → OpenShift · Validar y conmutar» (id {jko4['id']})")
jsp = asegurar("job_templates", "Plataforma · Controlar servicio", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/servicio-plataforma.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Enciende, apaga o reinicia el servicio de una plataforma (JBoss, nginx, PostgreSQL, Redis…) sin tocar la máquina"})
paso(f"Plantilla «Plataforma · Controlar servicio» (id {jsp['id']})")
jqa = asegurar("job_templates", "Sistema · Quitar agente", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/quitar-agente.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True, "ask_variables_on_launch": True,
    "ask_limit_on_launch": True, "description": "Retira un agente de FaceOps de la máquina (respaldo continuo o monitoreo)"})
paso(f"Plantilla «Sistema · Quitar agente» (id {jqa['id']})")
jdp = asegurar("job_templates", "Plataforma · Desinstalar", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/desinstalar-plataforma.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Quita una plataforma de la máquina (detiene su servicio y quita sus paquetes) sin tocar el sistema ni las otras plataformas"})
paso(f"Plantilla «Plataforma · Desinstalar» (id {jdp['id']})")
jrdp = asegurar("job_templates", "Windows · Habilitar escritorio remoto", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/windows-escritorio-remoto.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True,
    "description": "Activa RDP (con NLA), abre el 3389 en el firewall de Windows y deja el servicio en automático"})
paso(f"Plantilla «Windows · Habilitar escritorio remoto» (id {jrdp['id']})")
# Vincular máquinas a la bóveda: tipo de credencial que inyecta la dirección y un token de escritura acotado a maquinas/*
ct_bov = asegurar("credential_types", "FaceOps · Bóveda (escritura)", {
    "kind": "cloud", "description": "Dirección y token de HashiCorp Vault con escritura solo en faceops/data/maquinas/*",
    "inputs": {"fields": [{"id": "addr", "label": "Dirección de la bóveda", "type": "string"},
                          {"id": "token", "label": "Token de escritura", "type": "string", "secret": True}], "required": ["addr", "token"]},
    "injectors": {"env": {"FACEOPS_VAULT_ADDR": "{{ addr }}", "FACEOPS_VAULT_TOKEN": "{{ token }}"}}})
jvm = asegurar("job_templates", "FaceOps · Vincular máquina a la bóveda", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/vincular-maquina.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Crea el usuario de servicio con llave propia (o toma la clave entregada) y guarda la credencial en la bóveda"})
cr_bov = uno("credentials", name="FaceOps · Bóveda (escritura)")
if cr_bov and cr_bov["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jvm['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jvm['id']}/credentials/", {"id": cr_bov["id"]})
jst = asegurar("job_templates", "Arquitectura · Evaluar stack tecnológico", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/evaluar-stack.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "forks": 10,
    "description": "Solo lectura: barre las máquinas y reporta versiones de sistema, plataformas y lenguajes para medir su obsolescencia"})
if cred and cred["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jst['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jst['id']}/credentials/", {"id": cred["id"]})
paso(f"Plantilla «Arquitectura · Evaluar stack tecnológico» (id {jst['id']})")
jlic = asegurar("job_templates", "Licencias · Levantar desde las plataformas", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/licencias-levantar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "forks": 10,
    "description": "Solo lectura: lee en cada máquina las suscripciones y licencias que consume y su fecha de término"})
if cred and cred["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jlic['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jlic['id']}/credentials/", {"id": cred["id"]})
paso(f"Plantilla «Licencias · Levantar desde las plataformas» (id {jlic['id']})")
jpev = asegurar("job_templates", "Políticas de desarrollo · Evaluar aplicaciones", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/politicas-dev-evaluar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "forks": 10,
    "description": "Solo lectura: encuentra las aplicaciones de IIS, JBoss, Spring Boot y Quarkus y revisa su configuración contra las políticas de desarrollo"})
if cred and cred["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jpev['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jpev['id']}/credentials/", {"id": cred["id"]})
paso(f"Plantilla «Políticas de desarrollo · Evaluar aplicaciones» (id {jpev['id']})")
jdl = asegurar("job_templates", "Data lake · Derivar segmento", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/datalake-derivar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_limit_on_launch": True, "ask_variables_on_launch": True, "ask_inventory_on_launch": True, "become_enabled": True,
    "description": "Pone o quita el espejo de tráfico del segmento hacia el colector del data lake (nginx mirror) y arranca flujos funcionales"})
if cred and cred["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jdl['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jdl['id']}/credentials/", {"id": cred["id"]})
paso(f"Plantilla «Data lake · Derivar segmento» (id {jdl['id']})")
paso(f"Plantilla «FaceOps · Vincular máquina a la bóveda» (id {jvm['id']})" + ("" if cr_bov else " · falta crear la credencial «FaceOps · Bóveda (escritura)»"))
# Red · Inventariar equipo de red: ficha de red (interfaces, VLAN, LLDP, rutas, BGP, balanceo) en la caché de facts
inv_red = uno("inventories", name="Red · laboratorio")
if inv_red:
    jrd = asegurar("job_templates", "Red · Inventariar equipo de red", {
        "organization": org["id"], "project": proy, "playbook": "playbooks/red-inventariar.yml", "inventory": inv_red["id"],
        "execution_environment": ee["id"], "use_fact_cache": True, "ask_limit_on_launch": True, "ask_variables_on_launch": True, "forks": 10,
        "description": "Solo lectura: plataforma, versión, interfaces con estado/IP/VLAN, vecinos LLDP, rutas, BGP y balanceo de routers, switches, firewalls y balanceadores"})
    ya_r = {c["id"] for c in aap("GET", f"job_templates/{jrd['id']}/credentials/")["results"]}
    for n_ in ("Red · laboratorio (ssh)", "Red · firewall VyOS"):
        c_ = uno("credentials", name=n_)
        if c_ and c_["id"] not in ya_r:
            aap("POST", f"job_templates/{jrd['id']}/credentials/", {"id": c_["id"]})
    paso(f"Plantilla «Red · Inventariar equipo de red» (id {jrd['id']})")

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
                               ("Seguridad · Aplicar parches de seguridad", "aplicar-parches-seguridad.yml", "Instala las erratas de seguridad pendientes (todas o por CVE)"),
                               ("Seguridad · Escaneo de servidores", "escaneo-servidores.yml", "Escaneo de seguridad de servidores (solo lectura): parches, SSH, puertos, SELinux, firewall, cuentas, permisos, fin de vida")):
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
# Instalación de OpenShift por método y plataforma: una automatización prepara los prerrequisitos y genera el manifiesto
# (en Git) y otra lo valida e instala. Por ahora IPI sobre VMware vSphere; la credencial del vCenter la agrega FaceOps
# según la infraestructura elegida. La automatización anterior (todas las formas en una) se retira.
for vieja_wf in uno("workflow_job_templates", name="Red · Preparar red OpenShift con aprobación"), :   # retirada a pedido (nov-2026)
    if vieja_wf:
        aap("DELETE", f"workflow_job_templates/{vieja_wf['id']}/")
        paso("Retirado el workflow «Red · Preparar red OpenShift con aprobación»")
for vieja_jt in uno("job_templates", name="Red · Preparar red OpenShift"), :   # retirada a pedido (nov-2026)
    if vieja_jt:
        aap("DELETE", f"job_templates/{vieja_jt['id']}/")
        paso("Retirada la plantilla «Red · Preparar red OpenShift»")
for vieja in uno("job_templates", name="OpenShift · Habilitar clúster"), :
    if vieja:
        aap("DELETE", f"job_templates/{vieja['id']}/")
        paso("Retirada la plantilla «OpenShift · Habilitar clúster» (reemplazada por preparar + instalar por método)")
cr_gitea = uno("credentials", name="FaceOps · Gitea respaldo")
jop = asegurar("job_templates", "OpenShift · Operador", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/openshift-operador.yml",
    "inventory": (uno("inventories", name="OpenShift · clústeres") or inv_inf)["id"], "execution_environment": ee["id"],
    "ask_variables_on_launch": True, "ask_limit_on_launch": True, "ask_credential_on_launch": True,
    "description": "Reinicia o desinstala un operador de OpenShift instalado por OLM"})
c_ocp_w = uno("credentials", name="OpenShift · SNO (despliegue)")
if c_ocp_w and c_ocp_w["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jop['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jop['id']}/credentials/", {"id": c_ocp_w["id"]})
paso(f"Plantilla «OpenShift · Operador» (id {jop['id']})")
jocpv = asegurar("job_templates", "OpenShift · Habilitar OpenShift Virtualization", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/openshift-virtualizacion.yml",
    "inventory": (uno("inventories", name="OpenShift · clústeres") or inv_inf)["id"], "execution_environment": ee["id"],
    "ask_variables_on_launch": True, "ask_limit_on_launch": True, "ask_credential_on_launch": True, "timeout": 7200,
    "description": "Evalúa los prerrequisitos y habilita OpenShift Virtualization (sin probar)"})
if c_ocp_w and c_ocp_w["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jocpv['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jocpv['id']}/credentials/", {"id": c_ocp_w["id"]})
paso(f"Plantilla «OpenShift · Habilitar OpenShift Virtualization» (id {jocpv['id']})")
# Modelo de alertamiento (disponibilidad, seguridad, rendimiento) y su remediación desde Event-Driven Ansible
inv_ocp = (uno("inventories", name="OpenShift · clústeres") or inv_inf)["id"]
for nombre_o, pb_o, desc_o in (("OpenShift · Modelo de alertamiento", "openshift-alertamiento.yml", "Aplica, evalúa o retira las reglas de alertamiento FaceOps (disponibilidad, seguridad, rendimiento) y su envío a EDA"),
                               ("OpenShift · Remediar alerta", "openshift-remediar.yml", "Diagnóstico o remediación de una alerta del modelo (reiniciar pod, escalar, expandir volumen, cordonar nodo)"),
                               ("OpenShift · Relevamiento de imágenes", "openshift-imagenes.yml", "Informe de imágenes: en uso, sin uso, caché de nodos, antiguas y replicadas (solo lectura)")):
    jo_ = asegurar("job_templates", nombre_o, {
        "organization": org["id"], "project": proy, "playbook": f"playbooks/{pb_o}", "inventory": inv_ocp, "execution_environment": ee["id"],
        "ask_variables_on_launch": True, "ask_limit_on_launch": True, "ask_credential_on_launch": True, "timeout": 1800, "description": desc_o})
    if c_ocp_w and c_ocp_w["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jo_['id']}/credentials/")["results"]}:
        aap("POST", f"job_templates/{jo_['id']}/credentials/", {"id": c_ocp_w["id"]})
    paso(f"Plantilla «{nombre_o}» (id {jo_['id']})")
    if nombre_o == "OpenShift · Remediar alerta":
        jrem = jo_
wrem = asegurar("workflow_job_templates", "OpenShift · Remediar alerta (con aprobación)", {
    "organization": org["id"], "ask_variables_on_launch": True, "ask_limit_on_launch": True,
    "description": "Umbral crítico del modelo de alertamiento: aprobación y luego la remediación"})
if not aap("GET", f"workflow_job_templates/{wrem['id']}/workflow_nodes/")["results"]:
    r1 = aap("POST", f"workflow_job_templates/{wrem['id']}/workflow_nodes/", {})
    aap("POST", f"workflow_job_template_nodes/{r1['id']}/create_approval_template/",
        {"name": "¿Aprueba la remediación de la alerta crítica?", "timeout": 0, "description": "Acción de remediación del modelo de alertamiento de OpenShift"})
    r2 = aap("POST", f"workflow_job_templates/{wrem['id']}/workflow_nodes/", {"unified_job_template": jrem["id"]})
    aap("POST", f"workflow_job_template_nodes/{r1['id']}/success_nodes/", {"id": r2["id"]})
paso(f"Workflow «OpenShift · Remediar alerta (con aprobación)» (id {wrem['id']})")
# ACS Central (Red Hat Advanced Cluster Security): URL y token de API, desde ~/faceops/acs-central.json {"url", "token"}
ct_acs = uno("credential_types", name="ACS · Central") or aap("POST", "credential_types/", {
    "name": "ACS · Central", "kind": "cloud", "description": "URL de ACS Central y token de API (rol con permiso para registrar clústeres)",
    "inputs": {"fields": [{"id": "url", "label": "URL de Central (https://…)", "type": "string"},
                          {"id": "token", "label": "Token de API", "type": "string", "secret": True}], "required": ["url", "token"]},
    "injectors": {"extra_vars": {"acs_central_url": "{{ url }}", "acs_token": "{{ token }}"}}})
ARCH_ACS = os.path.expanduser("~/faceops/acs-central.json")
c_acs = asegurar("credentials", "ACS · Central", {"organization": org["id"], "credential_type": ct_acs["id"],
                 "inputs": json.load(open(ARCH_ACS))}) if os.path.exists(ARCH_ACS) else None
# Guía de regularización de OpenShift: una plantilla por tipo de tarea, sobre el inventario de clústeres
c_ocp_r = uno("credentials", name="OpenShift · SNO (lectura)")
for nombre_r, pb_r, cred_r, desc_r in (
        ("OpenShift · Diagnóstico de regularización", "openshift-regularizacion-diagnostico.yml", c_ocp_r,
         "Anexo A de la guía: estado del clúster frente a respaldo de etcd, ACS, Compliance, planes en espera, suscripciones, versión, límites y respaldo de datos (solo lectura)"),
        ("OpenShift · Respaldo de etcd", "openshift-etcd-respaldo.yml", c_ocp_w,
         "T-01 de la guía: CronJob de respaldo de etcd con copia fuera del clúster, prueba de ejecución y retiro"),
        ("OpenShift · Operadores OLM", "openshift-olm-regularizar.yml", c_ocp_w,
         "T-03/T-04/T-05 de la guía: estrategia de aprobación Manual, planes en espera clasificados (parches de Red Hat uno a uno) y Compliance Operator con escaneo de línea base"),
        ("OpenShift · Versión del clúster", "openshift-version.yml", c_ocp_w,
         "T-06 de la guía: evalúa si el clúster puede actualizarse (APIs retiradas, operadores, etcd), prepara el canal y actualiza solo con confirmación"),
        ("OpenShift · Límites y cuotas", "openshift-limites.yml", c_ocp_w,
         "T-07 de la guía: mide reserva frente a consumo (guardado en Git), propone LimitRange y ResourceQuota por namespace y los aplica o retira"),
        ("OpenShift · Agente de ACS", "openshift-acs.yml", c_ocp_w,
         "T-02 de la guía: conecta el clúster a ACS Central (operador con aprobación Manual, paquete de inicialización y SecuredCluster) o lo retira"),
        ("OpenShift · Informe y ficha", "openshift-informe.yml", c_ocp_r,
         "T-09/T-12/T-13 de la guía: informe diario (respaldo de etcd, volúmenes, planes en espera, OADP) y ficha operativa del clúster, en Git")):
    jr = asegurar("job_templates", nombre_r, {
        "organization": org["id"], "project": proy, "playbook": f"playbooks/{pb_r}",
        "inventory": (uno("inventories", name="OpenShift · clústeres") or inv_inf)["id"], "execution_environment": ee["id"],
        "ask_variables_on_launch": True, "ask_limit_on_launch": True, "ask_credential_on_launch": True, "description": desc_r})
    ya = {c["id"] for c in aap("GET", f"job_templates/{jr['id']}/credentials/")["results"]}
    for cr_ in (cred_r, cr_gitea if pb_r in ("openshift-etcd-respaldo.yml", "openshift-limites.yml", "openshift-informe.yml", "openshift-olm-regularizar.yml") else None,
                c_acs if pb_r == "openshift-acs.yml" else None):
        if cr_ and cr_["id"] not in ya:
            aap("POST", f"job_templates/{jr['id']}/credentials/", {"id": cr_["id"]})
    paso(f"Plantilla «{nombre_r}» (id {jr['id']})")
# Certificado de la CA interna para las rutas de OpenShift (comodín *.apps del IngressController)
j_occ = asegurar("job_templates", "Certificados · OpenShift", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/openshift-certificado.yml",
    "inventory": (uno("inventories", name="OpenShift · clústeres") or inv_inf)["id"], "execution_environment": ee["id"],
    "ask_variables_on_launch": True, "ask_limit_on_launch": True, "ask_credential_on_launch": True,
    "description": "Certificado de la CA interna para las rutas del clúster (*.apps: consola, OAuth y aplicaciones): emitir y aplicar, consultar o retirar"})
ya_occ = {c["id"] for c in aap("GET", f"job_templates/{j_occ['id']}/credentials/")["results"]}
for cr_ in (c_ocp_w, uno("credentials", name="FaceOps · CA interna")):
    if cr_ and cr_["id"] not in ya_occ:
        aap("POST", f"job_templates/{j_occ['id']}/credentials/", {"id": cr_["id"]})
paso(f"Plantilla «Certificados · OpenShift» (id {j_occ['id']})")
for nombre_o, pb_o, desc_o in (
        ("OpenShift · Preparar instalación IPI VMware", "openshift-ipi-vmware-preparar.yml",
         "Prerrequisitos de una instalación IPI sobre vSphere (vCenter, red, DNS, instalador, pull secret) y el manifiesto en Git"),
        ("OpenShift · Validar e instalar IPI VMware", "openshift-ipi-vmware-instalar.yml",
         "Toma el manifiesto preparado, revalida el vCenter, valida con openshift-install e instala el clúster")):
    jo = asegurar("job_templates", nombre_o, {
        "organization": org["id"], "project": proy, "playbook": f"playbooks/{pb_o}", "inventory": inv_inf["id"],
        "execution_environment": ee["id"], "ask_variables_on_launch": True, "ask_credential_on_launch": True, "timeout": 0, "description": desc_o})
    ya = {c["id"] for c in aap("GET", f"job_templates/{jo['id']}/credentials/")["results"]}
    for cid in creds_ocp + ([cr_gitea["id"]] if cr_gitea else []):
        if cid not in ya:
            aap("POST", f"job_templates/{jo['id']}/credentials/", {"id": cid})
    paso(f"Plantilla «{nombre_o}» (id {jo['id']})")

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

# Migración a GCP: evaluar (solo lectura) y rehost de servidores Linux a Compute Engine (credencial de GCP fija; la de la máquina de origen la pone FaceOps)
jmg1 = asegurar("job_templates", "Migración a GCP · Evaluar servidor", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/migracion-gcp-evaluar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True, "ask_limit_on_launch": True,
    "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Solo lectura: estrategia, tipo de máquina, disco, servicios administrados, puertos y riesgos para llevar el servidor a GCP"})
jmg2 = asegurar("job_templates", "Migración a GCP · Servidor Linux", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/migracion-gcp-servidor.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True, "ask_limit_on_launch": True,
    "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Rehost a Compute Engine: VM destino, datos, paquetes y servicios iguales; corte solo si se pide y el destino respondió igual"})
c_gcp = uno("credentials", name="GCP · invoke-summit-cloud")
if c_gcp and c_gcp["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jmg2['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jmg2['id']}/credentials/", {"id": c_gcp["id"]})
paso(f"Plantillas «Migración a GCP · Evaluar servidor» (id {jmg1['id']}) y «Servidor Linux» (id {jmg2['id']})")

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

# Agregar disco: aprobación → crear y conectar el volumen en el anfitrión KVM → registrarlo en la SAN (si viene del storage) y prepararlo en la máquina
jda = asegurar("job_templates", "Infra · Agregar disco (anfitrión)", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/disco-anfitrion.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Crea un volumen qcow2 en un pool del anfitrión KVM y lo conecta a la máquina virtual (en caliente)"})
if c_kvm["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jda['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jda['id']}/credentials/", {"id": c_kvm["id"]})
# Inventario construido con todos los servidores (nodos base + habilitados por FaceOps): los pasos que entran a la máquina
# en un workflow (preparar o ampliar un disco) deben encontrarla esté en el inventario que esté.
inv_todos = uno("inventories", name="FaceOps · Todos los servidores")
if not inv_todos:
    inv_todos = aap("POST", "inventories/", {"name": "FaceOps · Todos los servidores", "organization": org["id"], "kind": "constructed",
                                           "description": "Nodos demo + Servidores habilitados (construido; se actualiza al lanzar)"})
ya_in = {x["id"] for x in aap("GET", f"inventories/{inv_todos['id']}/input_inventories/")["results"]}
for i_ in (inv, inv_hab):
    if i_["id"] not in ya_in:
        aap("POST", f"inventories/{inv_todos['id']}/input_inventories/", {"id": i_["id"]})
for src_ in aap("GET", f"inventories/{inv_todos['id']}/inventory_sources/")["results"]:
    aap("PATCH", f"inventory_sources/{src_['id']}/", {"update_on_launch": True, "update_cache_timeout": 0, "overwrite": True})
    upd_ = aap("POST", f"inventory_sources/{src_['id']}/update/", {})
    st_ = esperar(f"inventory_updates/{upd_['id']}/")
    paso(f"Inventario construido «FaceOps · Todos los servidores»: {st_}")
jdp = asegurar("job_templates", "Sistema · Preparar disco nuevo", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/disco-preparar.yml", "inventory": inv_todos["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Registra el volumen en la SAN si viene del storage; particiona, da formato y monta el disco nuevo en la máquina"})
if cred and cred["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jdp['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jdp['id']}/credentials/", {"id": cred["id"]})
wfd = asegurar("workflow_job_templates", "FaceOps · Agregar disco", {"organization": org["id"], "ask_variables_on_launch": True,
               "description": "Agrega un disco a una máquina: del hipervisor o del storage (con aprobación)"})
if not aap("GET", f"workflow_job_templates/{wfd['id']}/workflow_nodes/")["results"]:
    d1 = aap("POST", f"workflow_job_templates/{wfd['id']}/workflow_nodes/", {})
    aap("POST", f"workflow_job_template_nodes/{d1['id']}/create_approval_template/", {"name": "¿Aprueba agregar el disco?", "timeout": 72 * 3600})
    d2 = aap("POST", f"workflow_job_templates/{wfd['id']}/workflow_nodes/", {"unified_job_template": jda["id"]})
    d3 = aap("POST", f"workflow_job_templates/{wfd['id']}/workflow_nodes/", {"unified_job_template": jdp["id"]})
    aap("POST", f"workflow_job_template_nodes/{d1['id']}/success_nodes/", {"id": d2["id"]})
    aap("POST", f"workflow_job_template_nodes/{d2['id']}/success_nodes/", {"id": d3["id"]})
paso(f"Workflow «FaceOps · Agregar disco» (id {wfd['id']}): aprobación → disco en el anfitrión → prepararlo en la máquina")

# Ampliar disco: aprobación → agrandar el disco virtual en el anfitrión → crecer partición, LVM y sistema de archivos
jxa = asegurar("job_templates", "Infra · Ampliar disco (anfitrión)", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/disco-ampliar-anfitrion.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Agranda el disco virtual de una máquina del anfitrión KVM (en caliente si está encendida); nunca achica"})
if c_kvm["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jxa['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jxa['id']}/credentials/", {"id": c_kvm["id"]})
jra = asegurar("job_templates", "Infra · Ajustar recursos de máquinas", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/recursos-anfitrion.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Sube o baja vCPU y memoria de máquinas del anfitrión KVM (apagado ordenado, ajuste y encendido)"})
if c_kvm["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jra['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jra['id']}/credentials/", {"id": c_kvm["id"]})
for n_, pb_, de_ in (("Infra · Preparar centro de datos emulado", "infra-dc-emulado.yml",
                      "Segundo centro de datos dentro del anfitrión KVM (demostraciones): red NAT propia y pool de discos propio"),
                     ("Infra · Mover máquina entre centros de datos", "infra-mover-maquina.yml",
                      "Traslada una máquina KVM a otro centro de datos (copia de discos, red del destino, IP nueva) con vuelta atrás")):
    jx_ = asegurar("job_templates", n_, {"organization": org["id"], "project": proy, "playbook": f"playbooks/{pb_}", "inventory": inv_inf["id"],
                                        "execution_environment": ee["id"], "ask_variables_on_launch": True, "description": de_})
    if c_kvm["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jx_['id']}/credentials/")["results"]}:
        aap("POST", f"job_templates/{jx_['id']}/credentials/", {"id": c_kvm["id"]})
jai = asegurar("job_templates", "Infra · Encendido automático de máquinas", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/autoinicio-anfitrion.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Qué máquinas se encienden solas al reiniciar el anfitrión KVM (por defecto solo la bóveda)"})
if c_kvm["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jai['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jai['id']}/credentials/", {"id": c_kvm["id"]})
jma = asegurar("job_templates", "Monitoreo · Configurar alertas del equipo", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/monitoreo-alertas.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Alertas de un equipo (botón «Alertas» de FaceOps): reglas y objetivos en el Prometheus del anfitrión, promtool, recarga y vuelta atrás"})
if c_kvm["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jma['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jma['id']}/credentials/", {"id": c_kvm["id"]})
# Identidad · Fuente de usuarios de FaceOps: la credencial de cada fuente (AD, LDAP, midPoint) vive en AAP; la plantilla lee la
# fuente y deja el autenticador LDAP en el Gateway (con la credencial de AAP del administrador). FaceOps nunca habla con la fuente.
asegurar("credential_types", "FaceOps · Fuente de identidad", {
    "kind": "cloud", "description": "Fuente de usuarios de FaceOps (Active Directory, LDAP o midPoint): dirección, cuenta de servicio y su clave",
    "inputs": {"fields": [{"id": "url", "label": "URL (ldaps://… o https://…)", "type": "string"},
                          {"id": "usuario", "label": "Cuenta de servicio (DN o usuario)", "type": "string"},
                          {"id": "clave", "label": "Clave", "type": "string", "secret": True},
                          {"id": "verificar_tls", "label": "Verificar TLS", "type": "boolean"}], "required": ["url", "usuario", "clave"]},
    "injectors": {"env": {"FO_ID_URL": "{{ url }}", "FO_ID_BIND": "{{ usuario }}", "FO_ID_CLAVE": "{{ clave }}", "FO_ID_TLS": "{{ verificar_tls }}"}}})
t_aap = uno("credential_types", name="Red Hat Ansible Automation Platform", managed=True)
cr_gw = asegurar("credentials", "AAP · FaceOps (gateway)", {
    "organization": org["id"], "credential_type": t_aap["id"], "description": "Administrador del Gateway de AAP: autenticadores de las fuentes de usuarios",
    "inputs": {"host": "https://192.168.122.1", "username": "admin", "password": CLAVE, "verify_ssl": False}})
jid = asegurar("job_templates", "Identidad · Fuente de usuarios de FaceOps", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/identidad-fuente.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True, "ask_credential_on_launch": True,
    "description": "Lee una fuente de usuarios (AD, LDAP o midPoint) con su credencial y deja su autenticador en el Gateway de AAP"})
if cr_gw["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jid['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jid['id']}/credentials/", {"id": cr_gw["id"]})
jrs = asegurar("job_templates", "Monitoreo · Responder alerta", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/monitoreo-responder.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Lo lanza EDA cuando se dispara una alerta de objeto con acción: pide la automatización con aprobación"})
if cr_gw["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jrs['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jrs['id']}/credentials/", {"id": cr_gw["id"]})
jdp = asegurar("job_templates", "DevSecOps · Pipeline Tekton", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/devsecops-pipeline.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Aplica un pipeline Tekton desde Git, lo ejecuta o lee sus últimas ejecuciones (FaceOps lo pide con aprobación)"})
if c_kvm["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jdp['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jdp['id']}/credentials/", {"id": c_kvm["id"]})
jxp = asegurar("job_templates", "Sistema · Ampliar disco en la máquina", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/disco-ampliar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True, "ask_limit_on_launch": True,
    "description": "La máquina ve el tamaño nuevo: crece la partición, el LVM y el sistema de archivos del montaje"})
if cred and cred["id"] not in {c["id"] for c in aap("GET", f"job_templates/{jxp['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{jxp['id']}/credentials/", {"id": cred["id"]})
wfx = asegurar("workflow_job_templates", "FaceOps · Ampliar disco", {"organization": org["id"], "ask_variables_on_launch": True,
               "ask_limit_on_launch": True, "description": "Amplía un disco de una máquina y su sistema de archivos (con aprobación)"})
if not aap("GET", f"workflow_job_templates/{wfx['id']}/workflow_nodes/")["results"]:
    x1 = aap("POST", f"workflow_job_templates/{wfx['id']}/workflow_nodes/", {})
    aap("POST", f"workflow_job_template_nodes/{x1['id']}/create_approval_template/", {"name": "¿Aprueba ampliar el disco?", "timeout": 72 * 3600})
    x2 = aap("POST", f"workflow_job_templates/{wfx['id']}/workflow_nodes/", {"unified_job_template": jxa["id"]})
    x3 = aap("POST", f"workflow_job_templates/{wfx['id']}/workflow_nodes/", {"unified_job_template": jxp["id"]})
    aap("POST", f"workflow_job_template_nodes/{x1['id']}/success_nodes/", {"id": x2["id"]})
    aap("POST", f"workflow_job_template_nodes/{x2['id']}/success_nodes/", {"id": x3["id"]})
paso(f"Workflow «FaceOps · Ampliar disco» (id {wfx['id']}): aprobación → agrandar en el anfitrión → crecer en la máquina")

# Encender o apagar equipo (anfitrión KVM): desde el inventario o el detalle del equipo
jen = asegurar("job_templates", "Infra · Encender o apagar equipo", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/energia.yml", "inventory": inv_inf["id"],
    "execution_environment": ee["id"], "ask_variables_on_launch": True,
    "description": "Enciende, apaga (ordenado) o reinicia una máquina virtual del anfitrión KVM"})
ya = {c["id"] for c in aap("GET", f"job_templates/{jen['id']}/credentials/")["results"]}
if c_kvm["id"] not in ya:
    aap("POST", f"job_templates/{jen['id']}/credentials/", {"id": c_kvm["id"]})
paso(f"Plantilla «Infra · Encender o apagar equipo» (id {jen['id']})")

# Uso por segmento (solo lectura, cada hora desde FaceOps): mismos inventarios y credenciales que la recolección
jus = asegurar("job_templates", "FaceOps · Uso por segmento", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/uso-segmentos.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "forks": 10, "become_enabled": True,
    "description": "Mide CPU, memoria, peticiones y bytes por aplicación para repartir el uso de cada máquina entre sus segmentos"})
paso(f"Plantilla «FaceOps · Uso por segmento» (id {jus['id']})")

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

# Migraciones · sistema operativo: evaluar (lectura) y migrar RHEL / convertir CentOS (con aprobación, snapshot y vuelta atrás)
t_kvm = asegurar("credential_types", "Infra · Anfitrión KVM (snapshots)", {
    "kind": "cloud", "description": "Acceso al anfitrión de virtualización para tomar y revertir snapshots de VM",
    "inputs": {"fields": [{"id": "anfitrion", "label": "Anfitrión (IP o nombre)", "type": "string"},
                          {"id": "usuario", "label": "Usuario SSH (con sudo para virsh)", "type": "string"},
                          {"id": "llave", "label": "Llave privada SSH", "type": "string", "secret": True, "multiline": True}],
               "required": ["anfitrion", "usuario", "llave"]},
    "injectors": {"file": {"template.llave": "{{ llave }}"},
                  "extra_vars": {"kvm_anfitrion": "{{ anfitrion }}", "kvm_usuario": "{{ usuario }}", "kvm_llave": "{{ tower.filename.llave }}"}}})
c_snap = asegurar("credentials", "Infra · KVM Santiago (snapshots)", {
    "organization": org["id"], "credential_type": t_kvm["id"], "description": "Usuario aap-kvm del anfitrión KVM del Centro de datos Santiago",
    "inputs": {"anfitrion": "192.168.122.1", "usuario": "aap-kvm", "llave": llave}})
t_rhsm = asegurar("credential_types", "Red Hat · Llave de activación", {
    "kind": "cloud", "description": "Organización y llave de activación de Red Hat (registro en CDN o Satellite; la usa Convert2RHEL)",
    "inputs": {"fields": [{"id": "org", "label": "ID de organización", "type": "string"},
                          {"id": "llave", "label": "Llave de activación", "type": "string", "secret": True}], "required": ["org", "llave"]},
    "injectors": {"extra_vars": {"rhsm_org": "{{ org }}", "rhsm_llave": "{{ llave }}"}}})
ARCH_RHSM = os.path.expanduser("~/faceops/rhsm-activacion.json")   # {"org": "...", "llave": "..."} (no está en el repositorio)
c_rhsm = asegurar("credentials", "Red Hat · Llave de activación", {
    "organization": org["id"], "credential_type": t_rhsm["id"], "inputs": json.load(open(ARCH_RHSM))}) if os.path.exists(ARCH_RHSM) else None
def uno_jt(n):
    r = aap("GET", "job_templates/?name=" + urllib.parse.quote(n)).get("results", [])
    return r[0] if r else None


jrr = asegurar("job_templates", "Sistema · Registrar en Red Hat", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/registrar-rhel.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Registra la máquina RHEL en Red Hat con una clave de activación (queda regularizada)"})
for jt_ in (jrr, uno_jt("FaceOps · Habilitar servidor")):   # flujos internos: la llave guardada de la organización
    if jt_ and c_rhsm and c_rhsm["id"] not in [x["id"] for x in aap("GET", f"job_templates/{jt_['id']}/credentials/").get("results", [])]:
        aap("POST", f"job_templates/{jt_['id']}/credentials/", {"id": c_rhsm["id"]})
paso(f"Plantilla «Sistema · Registrar en Red Hat» (id {jrr['id']})")
if c_rhsm:   # parches: si el equipo no está registrado, se registra con la llave de activación
    jps_ = uno("job_templates", name="Seguridad · Aplicar parches de seguridad")
    if jps_ and c_rhsm["id"] not in [x["id"] for x in aap("GET", f"job_templates/{jps_['id']}/credentials/").get("results", [])]:
        aap("POST", f"job_templates/{jps_['id']}/credentials/", {"id": c_rhsm["id"]})
for nombre_m, desc_m, creds_m in (
        ("Migración · Evaluar sistema operativo", "Composición del sistema por categoría, versión de destino con parches, camino y semáforo (no cambia nada)", []),
        ("Migración · RHEL a versión soportada", "Lleva RHEL a la versión con parches según la política: última menor y saltos in-place (Leapp / redhat-upgrade-tool) con snapshot y vuelta atrás", [c_snap]),
        ("Migración · CentOS a RHEL", "Convierte CentOS, Rocky, AlmaLinux u Oracle Linux a RHEL con Convert2RHEL (y sigue a la versión con parches si la política lo pide)", [c_snap, c_rhsm])):
    jm_ = asegurar("job_templates", nombre_m, {
        "organization": org["id"], "project": proy, "playbook": "playbooks/migrar-so.yml", "inventory": inv["id"],
        "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
        "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "timeout": 4 * 3600,
        "extra_vars": json.dumps({"mig_modo": "evaluar" if "Evaluar" in nombre_m else "migrar",
                                  "mig_tipo": "centos" if "CentOS" in nombre_m else "rhel" if "RHEL a" in nombre_m else "auto"}),
        "description": desc_m})
    ya = {c["id"] for c in aap("GET", f"job_templates/{jm_['id']}/credentials/")["results"]}
    for c_ in creds_m:
        if c_ and c_["id"] not in ya:
            aap("POST", f"job_templates/{jm_['id']}/credentials/", {"id": c_["id"]})
    paso(f"Plantilla «{nombre_m}» (id {jm_['id']})")
# Acciones de la plataforma Satellite: convertir a RHEL y dejarla en Satellite · actualizar Satellite (con snapshot)
for nombre_s, pb_s, desc_s, ev_s, creds_s in (
        ("Satellite · Convertir a RHEL", "satellite-convertir-rhel.yml", "Convierte CentOS, Rocky, AlmaLinux u Oracle Linux a RHEL (Convert2RHEL, snapshot previo) y la registra en Satellite",
         {"mig_modo": "migrar", "mig_tipo": "centos"}, [c_snap, c_rhsm]),
        ("Satellite · Actualizar", "satellite-actualizar.yml", "Actualiza Red Hat Satellite con satellite-maintain (chequeos previos, snapshot y vuelta atrás si falla)",
         {}, [c_snap])):
    js_ = asegurar("job_templates", nombre_s, {
        "organization": org["id"], "project": proy, "playbook": f"playbooks/{pb_s}", "inventory": inv["id"],
        "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
        "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "timeout": 4 * 3600,
        "extra_vars": json.dumps(ev_s), "description": desc_s})
    ya = {c["id"] for c in aap("GET", f"job_templates/{js_['id']}/credentials/")["results"]}
    for c_ in creds_s:
        if c_ and c_["id"] not in ya:
            aap("POST", f"job_templates/{js_['id']}/credentials/", {"id": c_["id"]})
    paso(f"Plantilla «{nombre_s}» (id {js_['id']})")
# Migración de aplicaciones JBoss AS 5/6 o EAP 6/7 a JBoss EAP 8 (evaluar con informe en Git, o migrar a un destino EAP 8)
jiis = asegurar("job_templates", "Migración · IIS a OpenShift", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/migracion-iis-ocp.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "timeout": 7200,
    "description": "Evalúa sitios y aplicaciones de IIS para llevarlos a OpenShift (migración en construcción)"})
paso(f"Plantilla «Migración · IIS a OpenShift» (id {jiis['id']})")
jmj = asegurar("job_templates", "Migración · JBoss a EAP 8", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/migracion-jboss-eap8.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True, "timeout": 3600,
    "description": "Evalúa aplicaciones de JBoss 5/6/7 frente a EAP 8 (informe en Git) y las migra: Jakarta EE 10, datasources, despliegue y verificación"})
ya = {c["id"] for c in aap("GET", f"job_templates/{jmj['id']}/credentials/")["results"]}
for c_ in (uno("credentials", name="FaceOps · Gitea respaldo"),):
    if c_ and c_["id"] not in ya:
        aap("POST", f"job_templates/{jmj['id']}/credentials/", {"id": c_["id"]})
paso(f"Plantilla «Migración · JBoss a EAP 8» (id {jmj['id']})")

# Ciclo de vida: antes de saltar de versión, ¿la máquina puede ir al destino? (solo lectura)
jcv = asegurar("job_templates", "Ciclo de vida · Evaluar destino", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/ciclo-vida-evaluar-destino.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Antes de saltar de versión: versión instalada, destino en los repositorios, disco, memoria y servicio, con semáforo (no cambia nada)"})
paso(f"Plantilla «Ciclo de vida · Evaluar destino» (id {jcv['id']})")

# Incidentes: verificar en el equipo el error conocido antes de remediar (solo lectura)
jiv = asegurar("job_templates", "Incidentes · Verificar error conocido", {
    "organization": org["id"], "project": proy, "playbook": "playbooks/incidentes-verificar.yml", "inventory": inv["id"],
    "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
    "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
    "description": "Antes de remediar: Ansible mira el equipo y confirma que el incidente es el error conocido (solo lectura)"})
paso(f"Plantilla «Incidentes · Verificar error conocido» (id {jiv['id']})")
# Soporte de productos: integración y diagnóstico (solo lectura) · PostgreSQL primero
for nombre_p, modo_p, desc_p in (("Integración · PostgreSQL", "integrar", "Integra PostgreSQL al soporte de incidentes: versión, puerto, directorios de datos y logs, conexiones máximas y bases (solo lectura)"),
                                 ("Incidentes · Diagnosticar PostgreSQL", "diagnosticar", "Recolecta conexiones, bloqueos, transacciones abiertas, disco y errores del log (24 h) para reconocer errores conocidos (solo lectura)")):
    jp_ = asegurar("job_templates", nombre_p, {
        "organization": org["id"], "project": proy, "playbook": "playbooks/postgresql-soporte.yml", "inventory": inv["id"],
        "execution_environment": ee["id"], "ask_inventory_on_launch": True, "ask_credential_on_launch": True,
        "ask_limit_on_launch": True, "ask_variables_on_launch": True, "become_enabled": True,
        "extra_vars": json.dumps({"pg_modo": modo_p}), "description": desc_p})
    paso(f"Plantilla «{nombre_p}» (id {jp_['id']})")

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
# Sistemas legados (RHEL 5 y 6): su sshd solo acepta llaves RSA y firmas ssh-rsa con SHA-1 (ni ed25519 ni rsa-sha2), que
# el entorno de ejecución (RHEL 9) rechaza por política. Esta credencial lo permite SOLO en las plantillas que leen o
# evalúan máquinas: agrega la llave RSA de los legados (~/faceops/legado_rsa, su pública la reciben al nacer) y los
# algoritmos antiguos al final de la lista (con los equipos modernos se sigue negociando lo moderno).
ARCH_LEG = os.path.expanduser("~/faceops/legado_rsa")
N_LEG = "FaceOps · SSH para sistemas legados"
ct_leg = uno("credential_types", name=N_LEG)
if ct_leg and "llave" not in json.dumps(ct_leg.get("inputs")):   # versión anterior (sin llave RSA): se reemplaza
    for c_ in aap("GET", f"credentials/?credential_type={ct_leg['id']}")["results"]:
        aap("DELETE", f"credentials/{c_['id']}/")
    aap("DELETE", f"credential_types/{ct_leg['id']}/")
    ct_leg = None
ct_leg = ct_leg or aap("POST", "credential_types/", {
    "name": N_LEG, "kind": "cloud",
    "description": "Conexión SSH a RHEL 5/6: llave RSA propia, ssh-rsa con SHA-1, kex diffie-hellman-group14-sha1 y cifrado CBC",
    "inputs": {"fields": [{"id": "llave", "label": "Llave privada RSA", "type": "string", "secret": True, "multiline": True}], "required": ["llave"]},
    "injectors": {"file": {"template.cnf": "openssl_conf = default_modules\n[default_modules]\nalg_section = evp_properties\n[evp_properties]\nrh-allow-sha1-signatures = yes\n",
                           "template.llave": "{{ llave }}"},
                  "env": {"OPENSSL_CONF": "{{ tower.filename.cnf }}"},
                  "extra_vars": {"ansible_ssh_common_args": "-o IdentityFile={{ tower.filename.llave }} -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1 -o Ciphers=+aes128-cbc,aes256-cbc"}}})
if os.path.exists(ARCH_LEG):
    c_leg = asegurar("credentials", N_LEG, {"organization": org["id"], "credential_type": ct_leg["id"], "inputs": {"llave": open(ARCH_LEG).read()}})
    for n_leg in ("Sistema · Inventariar máquina", "FaceOps · Recolectar inventario", "Migración · Evaluar sistema operativo"):
        j_leg = uno("job_templates", name=n_leg)
        if j_leg and c_leg["id"] not in {c["id"] for c in aap("GET", f"job_templates/{j_leg['id']}/credentials/")["results"]}:
            aap("POST", f"job_templates/{j_leg['id']}/credentials/", {"id": c_leg["id"]})
    paso("Credencial «FaceOps · SSH para sistemas legados» en inventariar, recolectar y evaluar sistema operativo")

# Una aprobación pendiente no debe frenar a los demás pedidos de la misma automatización (las aprobaciones no vencen):
# todos los workflows de FaceOps aceptan ejecuciones simultáneas.
n_sim = 0
for w_ in aap("GET", f"workflow_job_templates/?organization={org['id']}&page_size=200")["results"]:
    if not w_.get("allow_simultaneous"):
        aap("PATCH", f"workflow_job_templates/{w_['id']}/", {"allow_simultaneous": True})
        n_sim += 1
paso(f"Ejecución simultánea activada en {n_sim} workflows")
print("LISTO")
