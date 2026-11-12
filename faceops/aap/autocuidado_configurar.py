#!/usr/bin/python3
"""Autocuidado de la plataforma · deja listo el anfitrión, el monitoreo, AAP y EDA (idempotente; lo llama configurar.py al
final, en el anfitrión, como el usuario de AAP con sudo). No imprime claves ni tokens.

  1. Anfitrión: herramienta /usr/local/sbin/faceops-autocuidado (files/autocuidado.py), su configuración, el exportador de
     Prometheus (127.0.0.1:9469), el vigía externo (timer cada 2 min, independiente de AAP), sudo de aap-kvm SOLO para la
     herramienta, el directorio de registro que lee FaceOps y el token de eventos (FaceOps ⇄ AAP).
  2. Monitoreo: reglas de Prometheus generadas desde datos/autocuidado.yml (grupo faceops-autocuidado), el trabajo de
     scraping y la ruta de Alertmanager → EDA :5023. Valida con promtool y vuelve atrás si algo falla.
  3. AAP: plantillas «Autocuidado · …» y la credencial de eventos.
  4. EDA: rulebook «FaceOps · autocuidado» activo (se recrea solo si cambió)."""
import base64, glob, hashlib, json, os, re, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

import yaml

AQUI = os.path.dirname(os.path.abspath(__file__))
POLITICA = os.path.join(os.path.dirname(AQUI), "datos", "autocuidado.yml")
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, EDA = "https://localhost/api/controller/v2", "https://localhost/api/eda/v1"
CTX = ssl._create_unverified_context()
ADMIN = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
M = "/opt/summit-monitoreo"
DIR = "/var/lib/faceops/autocuidado"
PUERTO_EDA = 5023
ACTIVACION = "FaceOps · autocuidado"
paso = lambda t: print(f"   · autocuidado: {t}", flush=True)


def http(m, url, d=None):
    req = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": ADMIN})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{m} {url.split('?')[0]} -> {e.code}: {e.read()[:300].decode(errors='replace')}") from None


aap = lambda m, p, d=None: http(m, f"{C}/{p}", d)
eda = lambda m, p, d=None: http(m, f"{EDA}/{p}", d)


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


def uno_eda(rec, **f):
    r = eda("GET", f"{rec}/?" + urllib.parse.urlencode(f)).get("results", [])
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


def sudo(script, entrada=None):
    return subprocess.run(["sudo", "-n", "bash", "-c", script], input=entrada, capture_output=True, text=True, timeout=600)


# ── reglas de Prometheus desde la política ─────────────────────────────────────────────────────────────────────────────
def reglas_prometheus(pol):
    sev = {"aviso": "warning", "critico": "critical"}
    out = []
    for r in pol["reglas"]:
        obj = r.get("objeto") or ""
        out.append({"alert": f"FaceOpsAutocuidado{r['id']}", "expr": r["expr"], "for": r.get("para", "5m"),
                    "labels": {"severity": sev.get(r["severidad"], "warning"), "faceops_autocuidado": "si", "faceops_ac_regla": r["id"],
                               "faceops_ac_accion": r["accion"], "faceops_ac_objeto": ("{{ $labels.%s }}" % obj) if obj else "anfitrion"},
                    "annotations": {"summary": r.get("resumen") or r["nombre"], "description": f"{r['id']} · {r['nombre']} ({r['grupo']})"}})
    return {"groups": [{"name": "faceops-autocuidado", "interval": "30s", "rules": out}]}


INSTALAR = r"""set -e
D=__DIR__; M=__M__; F=$1
install -d -m 0755 /var/lib/faceops $D
DUENO=$(stat -c %u /var/lib/faceops/faceops.db 2>/dev/null || echo 1001)
install -m 0755 -o root -g root "$F/autocuidado.py" /usr/local/sbin/faceops-autocuidado
[ -f /etc/faceops-autocuidado.conf ] || echo '{}' > /etc/faceops-autocuidado.conf
chmod 0644 /etc/faceops-autocuidado.conf
# token de eventos (AAP → FaceOps): lo lee FaceOps (su usuario) y lo pone AAP en su credencial; nunca se imprime
[ -s $D/token ] || (umask 077; openssl rand -hex 32 > $D/token)
chown "$DUENO" $D/token; chmod 0600 $D/token
install -d -m 0700 /var/backups/faceops-autocuidado
cat > /etc/systemd/system/faceops-autocuidado-exportador.service <<'U'
[Unit]
Description=FaceOps · exportador de autocuidado para Prometheus (127.0.0.1:9469)
After=network-online.target libvirtd.service
[Service]
ExecStart=/usr/bin/python3 /usr/local/sbin/faceops-autocuidado exportador
Restart=always
RestartSec=10
Nice=10
[Install]
WantedBy=multi-user.target
U
cat > /etc/systemd/system/faceops-autocuidado-vigia.service <<'U'
[Unit]
Description=FaceOps · vigía externo de autocuidado (actúa solo si AAP no responde)
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/faceops-autocuidado vigia --origen vigia
TimeoutStartSec=1500
U
cat > /etc/systemd/system/faceops-autocuidado-vigia.timer <<'U'
[Unit]
Description=FaceOps · vigía externo de autocuidado cada 2 minutos
[Timer]
OnBootSec=10min
OnUnitInactiveSec=2min
AccuracySec=20s
[Install]
WantedBy=timers.target
U
printf '%s\n' 'aap-kvm ALL=(root) NOPASSWD: /usr/local/sbin/faceops-autocuidado *' > /etc/sudoers.d/aap-kvm-autocuidado
chmod 0440 /etc/sudoers.d/aap-kvm-autocuidado
visudo -cq
systemctl daemon-reload
systemctl enable -q faceops-autocuidado-exportador.service faceops-autocuidado-vigia.timer
systemctl restart faceops-autocuidado-exportador.service
systemctl start faceops-autocuidado-vigia.timer
# Prometheus: reglas propias, scraping y ruta de Alertmanager (con respaldo y vuelta atrás)
install -d -m 0755 $M/prometheus/autocuidado $M/prometheus/autocuidado/respaldo
cp -p $M/prometheus/prometheus.yml $M/prometheus/autocuidado/respaldo/prometheus.yml
cp -p $M/alertmanager/alertmanager.yml $M/prometheus/autocuidado/respaldo/alertmanager.yml
[ -f $M/prometheus/autocuidado/reglas.yml ] && cp -p $M/prometheus/autocuidado/reglas.yml $M/prometheus/autocuidado/respaldo/reglas.yml || true
install -m 0644 "$F/reglas.yml" $M/prometheus/autocuidado/reglas.yml
python3 - $M <<'PY'
import sys, yaml
m = sys.argv[1]
p = f"{m}/prometheus/prometheus.yml"; d = yaml.safe_load(open(p)); cambio = False
rf = d.setdefault("rule_files", [])
if "/etc/prometheus/autocuidado/reglas.yml" not in rf:
    rf.append("/etc/prometheus/autocuidado/reglas.yml"); cambio = True
sc = d.setdefault("scrape_configs", [])
if not any(x.get("job_name") == "faceops-autocuidado" for x in sc):
    sc.append({"job_name": "faceops-autocuidado", "scrape_interval": "30s", "scrape_timeout": "25s",
               "static_configs": [{"targets": ["127.0.0.1:9469"], "labels": {"nombre": "vm-anfitriona"}}]}); cambio = True
if cambio:
    open(p, "w").write(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
p = f"{m}/alertmanager/alertmanager.yml"; d = yaml.safe_load(open(p))
r = d.setdefault("route", {}); rs = r.setdefault("routes", [])
if not any(x.get("receiver") == "faceops-autocuidado" for x in rs):
    rs.insert(0, {"receiver": "faceops-autocuidado", "matchers": ['faceops_autocuidado="si"'], "continue": False,
                  "group_by": ["faceops_ac_regla", "faceops_ac_objeto"], "group_wait": "10s", "group_interval": "1m", "repeat_interval": "30m"})
    d.setdefault("receivers", []).append({"name": "faceops-autocuidado", "webhook_configs": [{"url": "http://127.0.0.1:__PUERTO__/endpoint", "send_resolved": True}]})
    open(p, "w").write(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
PY
if ! podman exec summit-prometheus promtool check config /etc/prometheus/prometheus.yml >/dev/null 2>&1; then
  cp -p $M/prometheus/autocuidado/respaldo/prometheus.yml $M/prometheus/prometheus.yml
  cp -p $M/prometheus/autocuidado/respaldo/alertmanager.yml $M/alertmanager/alertmanager.yml
  [ -f $M/prometheus/autocuidado/respaldo/reglas.yml ] && cp -p $M/prometheus/autocuidado/respaldo/reglas.yml $M/prometheus/autocuidado/reglas.yml || rm -f $M/prometheus/autocuidado/reglas.yml
  echo "PROMTOOL_FALLO"; exit 3
fi
curl -s -X POST http://127.0.0.1:9090/-/reload >/dev/null
curl -s -X POST http://127.0.0.1:9093/-/reload >/dev/null
echo INSTALADO
"""


def anfitrion(pol):
    tmp = tempfile.mkdtemp()
    os.chmod(tmp, 0o755)
    import shutil
    shutil.copy(f"{AQUI}/files/autocuidado.py", f"{tmp}/autocuidado.py")
    open(f"{tmp}/reglas.yml", "w").write("# Generado por aap/autocuidado_configurar.py desde datos/autocuidado.yml: no editar a mano\n"
                                         + yaml.safe_dump(reglas_prometheus(pol), sort_keys=False, allow_unicode=True))
    for f in os.listdir(tmp):
        os.chmod(f"{tmp}/{f}", 0o644)
    sc = INSTALAR.replace("__DIR__", DIR).replace("__M__", M).replace("__PUERTO__", str(PUERTO_EDA))
    r = subprocess.run(["sudo", "-n", "bash", "-s", tmp], input=sc, capture_output=True, text=True, timeout=600)
    shutil.rmtree(tmp, ignore_errors=True)
    if "INSTALADO" in r.stdout:
        paso(f"anfitrión listo: herramienta, exportador :9469, vigía cada 2 min, sudo acotado de aap-kvm, {len(pol['reglas'])} reglas en Prometheus y ruta a EDA :{PUERTO_EDA}")
        return True
    motivo = "promtool rechazó la configuración (se volvió atrás)" if "PROMTOOL_FALLO" in r.stdout else (r.stderr.strip().splitlines() or ["?"])[-1][:200]
    paso(f"ERROR en el anfitrión: {motivo}")
    return False


# ── AAP ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
PLANTILLAS = [   # nombre, playbook, credenciales, tiempo máx (s), descripción
    ("Autocuidado · Revisar estado", "autocuidado-estado.yml", ["kvm"], 600,
     "Solo lectura: disco, inodos, memoria, carga, máquinas virtuales, contenedores y API de AAP, FaceOps, despliegue, respaldos y certificados"),
    ("Autocuidado · Liberar disco", "autocuidado-liberar-disco.yml", ["kvm"], 1800,
     "Libera disco del anfitrión de forma segura: journal, imágenes colgantes, contenedores detenidos no protegidos, logs rotados, artefactos viejos (nunca volúmenes ni datos)"),
    ("Autocuidado · Recuperar máquinas virtuales", "autocuidado-recuperar-vms.yml", ["kvm"], 900,
     "Reanuda las máquinas pausadas por E/S (disco lleno) y enciende las caídas o de encendido automático que quedaron abajo"),
    ("Autocuidado · Reiniciar componentes de AAP", "autocuidado-reiniciar-aap.yml", ["kvm"], 1200,
     "Reinicia en orden los contenedores de AAP no saludables (sin tocar lo que sostiene al propio trabajo) y espera la API"),
    ("Autocuidado · Reiniciar despachador de AAP", "autocuidado-reiniciar-despachador.yml", ["kvm"], 600,
     "Instancia sin latido o trabajos pendientes con capacidad libre: reinicia receptor y controller-task (diferido 45 s para que este trabajo termine)"),
    ("Autocuidado · Reiniciar FaceOps y despliegue", "autocuidado-reiniciar-faceops.yml", ["kvm"], 900,
     "Reinicia FaceOps si no responde, reactiva el timer del despliegue y lanza el despliegue atrasado"),
    ("Autocuidado · Respaldar bases de AAP y FaceOps", "autocuidado-respaldar.yml", ["kvm"], 3600,
     "Respaldo comprimido de PostgreSQL de AAP y de la base de FaceOps (7 de cada una), solo con espacio seguro"),
    ("Autocuidado · Renovar certificados", "autocuidado-certificados.yml", ["kvm"], 900,
     "Renueva el certificado público de FaceOps (Let's Encrypt) y revisa los de AAP"),
    ("Autocuidado · Purgar historial de AAP", "autocuidado-purgar-aap.yml", ["gw"], 3600,
     "Lanza la limpieza de trabajos (y del flujo de actividad) de AAP conservando los últimos días (con aprobación humana)"),
    ("Autocuidado · Ampliar disco del anfitrión (GCP)", "autocuidado-ampliar-disco.yml", ["kvm", "gcp"], 1800,
     "Agranda el disco de arranque de la VM del laboratorio en GCP y crece el sistema de archivos raíz (con aprobación humana; nunca achica)"),
    ("Autocuidado · Atender alerta", "autocuidado-atender.yml", ["gw", "eventos"], 300,
     "Lo lanza EDA: entrega la alerta de autocuidado a FaceOps (política, ticket y aprobación) o, sin FaceOps, lanza la acción de bajo riesgo"),
]


def plantillas():
    org = uno("organizations", name="FaceOps")
    proy = uno("projects", name="FaceOps · automatizaciones")
    ee = uno("execution_environments", name="Default execution environment")
    inv = uno("inventories", name="FaceOps · Infraestructura")
    creds = {"kvm": uno("credentials", name="FaceOps · anfitrión KVM (ssh)"), "gw": uno("credentials", name="AAP · FaceOps (gateway)"),
             "gcp": uno("credentials", name="GCP · invoke-summit-cloud")}
    if not all((org, proy, ee, inv, creds["kvm"], creds["gw"])):
        paso("ERROR: falta la organización, el proyecto, el entorno, el inventario o las credenciales base de FaceOps (corre configurar.py completo)")
        return False
    # el proyecto trae los playbooks «autocuidado-*.yml» recién subidos a Gitea: se actualiza antes de crear las plantillas
    upd = aap("POST", f"projects/{proy['id']}/update/", {})
    st_ = "?"
    for _ in range(300):   # hasta 15 min: con la cola de AAP cargada la actualización puede esperar su turno
        st_ = aap("GET", f"project_updates/{upd['id']}/")["status"]
        if st_ in ("successful", "failed", "error", "canceled"):
            break
        time.sleep(3)
    if st_ != "successful":
        paso(f"ERROR: la actualización del proyecto quedó «{st_}»: sin ella AAP no ve los playbooks de autocuidado")
        return False
    # credencial de eventos: FaceOps la valida con el token del anfitrión (sha256, como los demás webhooks)
    tok = sudo(f"cat {DIR}/token").stdout.strip()
    ct = uno("credential_types", name="Autocuidado · FaceOps (eventos)") or aap("POST", "credential_types/", {
        "name": "Autocuidado · FaceOps (eventos)", "kind": "cloud",
        "description": "Dirección de FaceOps y token para entregarle los eventos de autocuidado",
        "inputs": {"fields": [{"id": "url", "label": "URL de FaceOps", "type": "string"},
                              {"id": "token", "label": "Token de eventos", "type": "string", "secret": True}], "required": ["url", "token"]},
        "injectors": {"env": {"FO_AC_URL": "{{ url }}", "FO_AC_TOKEN": "{{ token }}"}}})
    if tok:
        creds["eventos"] = asegurar("credentials", "Autocuidado · FaceOps (eventos)", {
            "organization": org["id"], "credential_type": ct["id"], "description": "Token de eventos de autocuidado (AAP → FaceOps)",
            "inputs": {"url": "http://192.168.122.1:8110", "token": hashlib.sha256(tok.encode()).hexdigest()}})
    n = 0
    for nombre, pb, cr, to, desc in PLANTILLAS:
        local = pb in ("autocuidado-purgar-aap.yml", "autocuidado-atender.yml")
        jt = asegurar("job_templates", nombre, {
            "organization": org["id"], "project": proy["id"], "playbook": f"playbooks/{pb}", "inventory": inv["id"],
            "execution_environment": ee["id"], "ask_variables_on_launch": True, "timeout": to, "allow_simultaneous": local,
            "description": desc})
        ya = {c["id"] for c in aap("GET", f"job_templates/{jt['id']}/credentials/")["results"]}
        for k in cr:
            if creds.get(k) and creds[k]["id"] not in ya:
                aap("POST", f"job_templates/{jt['id']}/credentials/", {"id": creds[k]["id"]})
        n += 1
    paso(f"{n} plantillas «Autocuidado · …» en AAP" + ("" if creds.get("gcp") else " · sin credencial de GCP: «Ampliar disco del anfitrión» fallará al validar"))
    return True


# ── EDA ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def activacion():
    rb_txt = open(f"{AQUI}/rulebooks/autocuidado.yml").read()
    huella = hashlib.sha256(rb_txt.encode()).hexdigest()[:10]
    p = next((x for x in eda("GET", "projects/?page_size=100").get("results", []) if "faceops-automatizaciones" in (x.get("url") or "")), None)
    if not p:
        org = uno_eda("organizations", name="FaceOps") or uno_eda("organizations", name="Default")
        p = eda("POST", "projects/", {"name": "FaceOps · automatizaciones (EDA)", "organization_id": org["id"],
                                      "url": "http://192.168.122.1:3000/invoke/faceops-automatizaciones.git"})
    eda("POST", f"projects/{p['id']}/sync/", {})
    rb = None
    for _ in range(40):
        time.sleep(3)
        rb = uno_eda("rulebooks", project_id=p["id"], name="autocuidado.yml")
        if rb and eda("GET", f"projects/{p['id']}/").get("import_state") in ("completed", "failed"):
            break
    if not rb:
        paso("ERROR: el proyecto de EDA no trae rulebooks/autocuidado.yml")
        return False
    a = uno_eda("activations", name=ACTIVACION)
    if a and huella in (a.get("description") or "") and a.get("status") == "running":
        paso(f"EDA: activación «{ACTIVACION}» vigente (running)")
        return True
    if a:
        try:
            eda("POST", f"activations/{a['id']}/disable/", {})
        except RuntimeError:
            pass
        time.sleep(5)
        eda("DELETE", f"activations/{a['id']}/")
        for _ in range(20):
            if not uno_eda("activations", name=ACTIVACION):
                break
            time.sleep(3)
    cred = next((c for c in eda("GET", "eda-credentials/?page_size=100").get("results", [])
                 if (c.get("credential_type") or {}).get("name") == "Red Hat Ansible Automation Platform"), None)
    des = eda("GET", "decision-environments/?page_size=50").get("results", [])
    de = next((d for d in des if d["name"] == "Hub Default Decision Environment"), des[0] if des else None)
    if not cred or not de:
        paso("ERROR: EDA sin credencial de AAP o sin decision environment")
        return False
    org_id = p.get("organization_id") or (p.get("organization") or {}).get("id")
    act = eda("POST", "activations/", {
        "name": ACTIVACION, "description": f"Autocuidado: Alertmanager (:{PUERTO_EDA}) → Autocuidado · Atender alerta → FaceOps · huella {huella}",
        "is_enabled": True, "decision_environment_id": de["id"], "rulebook_id": rb["id"], "eda_credentials": [cred["id"]],
        "organization_id": org_id, "restart_policy": "always", "log_level": "info"})
    st = "?"
    for _ in range(60):
        st = eda("GET", f"activations/{act['id']}/").get("status")
        if st in ("running", "failed", "error"):
            break
        time.sleep(5)
    paso(f"EDA: activación «{ACTIVACION}» {st} (escucha :{PUERTO_EDA})")
    return st == "running"


if __name__ == "__main__":
    pol = yaml.safe_load(open(POLITICA, encoding="utf-8"))
    ok = True
    for nombre, fn in (("anfitrión", lambda: anfitrion(pol)), ("AAP", plantillas), ("EDA", activacion)):
        try:
            ok = fn() and ok
        except Exception as x:  # noqa: BLE001  un tramo que falla no frena los otros; el error no lleva secretos
            ok = False
            paso(f"ERROR en {nombre}: {type(x).__name__}: {str(x)[:200]}")
    print("AUTOCUIDADO LISTO" if ok else "AUTOCUIDADO CON ERRORES")
