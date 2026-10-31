#!/bin/bash
# OpenShift → AAP (sin FaceOps en el medio). Idempotente; corre en la VM del stand; no imprime el token.
#  · cuenta de servicio faceops-lector (cluster-reader + cluster-monitoring-view) con token de larga duración
#  · credencial AAP «OpenShift · SNO (lectura)» (OpenShift or Kubernetes API Bearer Token)
#  · plantilla «OpenShift · Recolectar clúster» (inventario «OpenShift · clústeres», caché de facts) cada 15 min
#  · infraestructura «OpenShift · SNO» de FaceOps queda con esa credencial
#  · Alertmanager del clúster envía sus alertas a EDA (webhook 192.168.122.1:5021) → recolectar al instante
set -euo pipefail
D=$HOME/openshift/sno; export KUBECONFIG=$D/instalar/auth/kubeconfig; OC=$D/bin/oc
API=https://192.168.122.50:6443
paso() { echo "== $*"; }
paso "Cuenta de servicio de solo lectura"
$OC get ns faceops >/dev/null 2>&1 || $OC create ns faceops >/dev/null
$OC -n faceops get sa faceops-lector >/dev/null 2>&1 || $OC -n faceops create sa faceops-lector >/dev/null
for r in cluster-reader cluster-monitoring-view; do $OC adm policy add-cluster-role-to-user $r -z faceops-lector -n faceops >/dev/null; done
$OC apply -f - >/dev/null <<Y
apiVersion: v1
kind: Secret
metadata: {name: faceops-lector-token, namespace: faceops, annotations: {kubernetes.io/service-account.name: faceops-lector}}
type: kubernetes.io/service-account-token
Y
for i in $(seq 1 20); do T=$($OC -n faceops get secret faceops-lector-token -o jsonpath='{.data.token}' 2>/dev/null | base64 -d); [ -n "$T" ] && break; sleep 2; done
[ -n "$T" ] || { echo "sin token"; exit 1; }

paso "AAP: credencial, plantilla y programación"
TOKEN="$T" API="$API" python3 - <<'P'
import base64, glob, json, os, re, ssl, subprocess, urllib.parse, urllib.request
CL = re.search(r"^gateway_admin_password=(.*)$", open(glob.glob(os.path.expanduser("~/aap/*/inventory-demo"))[0]).read(), re.M).group(1).strip()
AU = "Basic " + base64.b64encode(f"admin:{CL}".encode()).decode(); CTX = ssl._create_unverified_context()
def aap(m, p, d=None):
    q = urllib.request.Request(f"https://127.0.0.1/api/controller/v2/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Authorization": AU, "Content-Type": "application/json"})
    with urllib.request.urlopen(q, context=CTX, timeout=30) as r:
        b = r.read(); return json.loads(b) if b else {}
uno = lambda rec, **k: (aap("GET", f"{rec}/?" + urllib.parse.urlencode(k))["results"] or [None])[0]
org = uno("organizations", name="FaceOps")
ct = uno("credential_types", name="OpenShift or Kubernetes API Bearer Token")
cd = {"name": "OpenShift · SNO (lectura)", "organization": org["id"], "credential_type": ct["id"],
      "description": "Cuenta de servicio faceops-lector (cluster-reader) del SNO",
      "inputs": {"host": os.environ["API"], "bearer_token": os.environ["TOKEN"], "verify_ssl": False}}
c = uno("credentials", name=cd["name"]); c = aap("PATCH", f"credentials/{c['id']}/", cd) if c else aap("POST", "credentials/", cd)
inv = uno("inventories", name="OpenShift · clústeres")
proy = uno("projects", name="FaceOps · automatizaciones") or uno("projects", organization=org["id"])
ee = uno("execution_environments", name="Default execution environment")
d = {"organization": org["id"], "project": proy["id"], "playbook": "playbooks/openshift-recolectar.yml", "inventory": inv["id"],
     "execution_environment": ee["id"] if ee else None, "use_fact_cache": True, "ask_limit_on_launch": True, "ask_variables_on_launch": True,
     "description": "Solo lectura: Ansible lee la API de OpenShift (versión, nodos, arquitectura, recursos, operadores, storage) y lo deja en AAP"}
t = uno("job_templates", name="OpenShift · Recolectar clúster")
t = aap("PATCH", f"job_templates/{t['id']}/", d) if t else aap("POST", "job_templates/", dict(d, name="OpenShift · Recolectar clúster"))
if c["id"] not in {x["id"] for x in aap("GET", f"job_templates/{t['id']}/credentials/")["results"]}:
    aap("POST", f"job_templates/{t['id']}/credentials/", {"id": c["id"]})
s = uno("schedules", name="OpenShift · recolectar cada 15 min")
sd = {"rrule": "DTSTART;TZID=America/Santiago:20261030T000000 RRULE:FREQ=MINUTELY;INTERVAL=15", "enabled": True,
      "description": "Estado del clúster para FaceOps (además, cada alerta de Alertmanager lo dispara por EDA)"}
aap("PATCH", f"schedules/{s['id']}/", sd) if s else aap("POST", f"job_templates/{t['id']}/schedules/", dict(sd, name="OpenShift · recolectar cada 15 min"))
print(f"   · credencial «{cd['name']}», plantilla «OpenShift · Recolectar clúster» (id {t['id']}) y programación cada 15 min")
subprocess.run(["sudo", "podman", "exec", "faceops", "python3", "-c",
                "import sqlite3;c=sqlite3.connect('/var/lib/faceops/faceops.db');"
                "c.execute(\"update infraestructuras set credencial='OpenShift · SNO (lectura)', inventario='OpenShift · clústeres', estado='conectada', "
                "mensaje='Ansible lee su API con la cuenta faceops-lector (cluster-reader)' where nombre='OpenShift · SNO'\");c.commit()"], check=True)
print("   · infraestructura «OpenShift · SNO» de FaceOps con su credencial")
P

paso "Alertmanager → Event-Driven Ansible (192.168.122.1:5021)"
sudo firewall-cmd -q --zone=libvirt --query-port=5021/tcp || { sudo firewall-cmd -q --zone=libvirt --add-port=5021/tcp; sudo firewall-cmd -q --permanent --zone=libvirt --add-port=5021/tcp; }
$OC -n openshift-monitoring get secret alertmanager-main -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d > /tmp/am.yaml
python3 - /tmp/am.yaml <<'P'
import sys, yaml
c = yaml.safe_load(open(sys.argv[1])) or {}
rx = [r for r in c.get("receivers", []) if r.get("name") != "faceops-eda"]
rx.append({"name": "faceops-eda", "webhook_configs": [{"url": "http://192.168.122.1:5021/", "send_resolved": True}]})
c["receivers"] = rx
ruta = c.setdefault("route", {})
hijas = [r for r in ruta.get("routes", []) if r.get("receiver") != "faceops-eda"]
hijas.insert(0, {"receiver": "faceops-eda", "matchers": ['alertname!~"Watchdog|InfoInhibitor"'], "continue": True})
ruta["routes"] = hijas
yaml.safe_dump(c, open(sys.argv[1], "w"), sort_keys=False)
P
$OC -n openshift-monitoring create secret generic alertmanager-main --from-file=alertmanager.yaml=/tmp/am.yaml --dry-run=client -o yaml | $OC -n openshift-monitoring replace -f - >/dev/null
rm -f /tmp/am.yaml
echo "LISTO"
