#!/usr/bin/python3
"""Renovación automática de certificados sin FaceOps en el medio (todo en AAP + Event-Driven Ansible):
  · «Certificados · Inventario» (solo lectura, cada hora): cruza lo emitido por la CA con lo que está en uso en las máquinas
    y avisa a EDA de lo que vence pronto
  · «Certificados · Renovar (EDA)»: la renovación que lanza EDA, limitada al equipo del aviso
  · activación de EDA «FaceOps · Certificados» (rulebooks/certificados.yml, webhook :5020)
Ambas plantillas usan el inventario de equipos del laboratorio y la credencial de máquinas del plan de recolección
de FaceOps, más la credencial «FaceOps · CA interna». Idempotente; corre en la VM del stand; no imprime secretos."""
import base64, glob, json, os, re, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

INV_EQUIPOS = "Nodos demo"
CL = re.search(r"^gateway_admin_password=(.*)$", open(glob.glob(os.path.expanduser("~/aap/*/inventory-demo"))[0]).read(), re.M).group(1).strip()
AU = "Basic " + base64.b64encode(f"admin:{CL}".encode()).decode()
CTX = ssl._create_unverified_context()
paso = lambda t: print(f"   · {t}", flush=True)


def http(api, m, p, d=None, tolerar=False):
    q = urllib.request.Request(f"https://127.0.0.1/api/{api}/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Authorization": AU, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(q, context=CTX, timeout=30) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {p} → {e.code}: {e.read()[:400].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http("controller/v2", m, p, d, t)
eda = lambda m, p, d=None, t=False: http("eda/v1", m, p, d, t)


def uno(f, rec, **k):
    r = f("GET", f"{rec}/?" + urllib.parse.urlencode(k))["results"]
    return r[0] if r else None


org = uno(aap, "organizations", name="FaceOps")
proy = uno(aap, "projects", name="FaceOps · automatizaciones") or uno(aap, "projects", organization=org["id"])
ee = uno(aap, "execution_environments", name="Default execution environment")
inv = uno(aap, "inventories", name=INV_EQUIPOS)
plan = json.loads(subprocess.run(["sudo", "podman", "exec", "faceops", "python3", "-c",
                                  "import sqlite3,json;c=sqlite3.connect('/var/lib/faceops/faceops.db');"
                                  "print(c.execute(\"select recoleccion from clientes where aap_token is not null and aap_token!='' order by id limit 1\").fetchone()[0])"],
                                 capture_output=True, text=True, check=True).stdout)
cred_maq = uno(aap, "credentials", name=next(p["credencial"] for p in plan[INV_EQUIPOS] if p.get("credencial")))
cred_ca = uno(aap, "credentials", name="FaceOps · CA interna")
if not (inv and cred_maq and cred_ca):
    sys.exit("Falta el inventario, la credencial de máquinas o «FaceOps · CA interna» (correr agente-faceops.py)")

for nombre, pb, desc, extra in (
        ("Certificados · Inventario", "certificados-inventario.yml",
         "Solo lectura: certificados emitidos por la CA y en uso en las máquinas; avisa a EDA de los que vencen pronto", {"cert_dias": 30}),
        ("Certificados · Renovar (EDA)", "certificados.yml",
         "Renovación lanzada por Event-Driven Ansible para el equipo del aviso", {"cert_accion": "renovar"})):
    datos = {"organization": org["id"], "project": proy["id"], "playbook": f"playbooks/{pb}", "inventory": inv["id"],
             "execution_environment": ee["id"] if ee else None, "ask_limit_on_launch": True, "ask_variables_on_launch": True,
             "become_enabled": True, "extra_vars": json.dumps(extra), "description": desc}
    t = uno(aap, "job_templates", name=nombre)
    t = aap("PATCH", f"job_templates/{t['id']}/", datos) if t else aap("POST", "job_templates/", dict(datos, name=nombre))
    ya = {c["id"] for c in aap("GET", f"job_templates/{t['id']}/credentials/")["results"]}
    for c in (cred_maq, cred_ca):
        if c["id"] not in ya:
            aap("POST", f"job_templates/{t['id']}/credentials/", {"id": c["id"]})
    paso(f"Plantilla «{nombre}» (inventario {INV_EQUIPOS}, credenciales de máquinas y de la CA)")
    if nombre == "Certificados · Inventario":
        jinv = t

s = uno(aap, "schedules", name="Certificados · revisión cada hora")
sd = {"rrule": "DTSTART;TZID=America/Santiago:20261030T000500 RRULE:FREQ=HOURLY;INTERVAL=1", "enabled": True,
      "description": "Lee la CA y las máquinas; si algo vence en < 30 días avisa a EDA, que renueva"}
aap("PATCH", f"schedules/{s['id']}/", sd) if s else aap("POST", f"job_templates/{jinv['id']}/schedules/", dict(sd, name="Certificados · revisión cada hora"))
paso("Programación: revisión cada hora")

# Event-Driven Ansible
eorg = (uno(eda, "organizations", name="FaceOps") or uno(eda, "organizations", name="Default"))["id"]
tipo = uno(eda, "credential-types", name="Red Hat Ansible Automation Platform")["id"]
cd = {"name": "AAP · FaceOps (certificados)", "credential_type_id": tipo, "organization_id": eorg,
      "inputs": {"host": "https://192.168.122.1/api/controller/", "username": "admin", "password": CL, "verify_ssl": False}}
c = uno(eda, "eda-credentials", name=cd["name"])
ecred = eda("PATCH", f"eda-credentials/{c['id']}/", cd)["id"] if c else eda("POST", "eda-credentials/", cd)["id"]
p = uno(eda, "projects", name="FaceOps · automatizaciones")
if p:
    eda("POST", f"projects/{p['id']}/sync/", {}, t=True)
    ep = p["id"]
else:
    ep = eda("POST", "projects/", {"name": "FaceOps · automatizaciones", "organization_id": eorg, "verify_ssl": False,
                                   "url": "http://192.168.122.1:3000/invoke/faceops-automatizaciones.git",
                                   "description": "Rulebooks de FaceOps (certificados)"})["id"]
for _ in range(60):
    if eda("GET", f"projects/{ep}/")["import_state"] in ("completed", "failed"):
        break
    time.sleep(3)
des = eda("GET", "decision-environments/?page_size=50")["results"]
de = next((d for d in des if d["name"] == "Hub Default Decision Environment"), des[0])["id"]


def activar(archivo, nombre, desc, puerto):
    rb = None
    for _ in range(20):
        rb = uno(eda, "rulebooks", project_id=ep, name=archivo)
        if rb:
            break
        time.sleep(3)
    if not rb:
        sys.exit(f"ERROR: el proyecto EDA no trae rulebooks/{archivo}")
    a = uno(eda, "activations", name=nombre)
    if a:
        eda("POST", f"activations/{a['id']}/disable/", {}, t=True)
        time.sleep(5)
        eda("DELETE", f"activations/{a['id']}/", t=True)
        for _ in range(20):
            if not uno(eda, "activations", name=nombre):
                break
            time.sleep(3)
    act = eda("POST", "activations/", {
        "name": nombre, "description": desc, "is_enabled": True, "decision_environment_id": de, "rulebook_id": rb["id"],
        "eda_credentials": [ecred], "organization_id": eorg, "restart_policy": "on-failure", "log_level": "info"})["id"]
    for _ in range(60):
        st = eda("GET", f"activations/{act}/")["status"]
        if st in ("running", "failed", "error"):
            break
        time.sleep(5)
    paso(f"EDA: activación «{nombre}» {st} (webhook :{puerto})")


activar("certificados.yml", "FaceOps · Certificados", "Certificados por vencer → renovación en AAP; renovado → actualizar inventario", 5020)
if "--openshift" in sys.argv:
    activar("openshift.yml", "FaceOps · OpenShift", "Alertas de Alertmanager de OpenShift → recolectar el clúster", 5021)
print("LISTO")
