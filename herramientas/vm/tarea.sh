# Trabajo de AAP del caso C3 de Atender alerta (sin secretos)
J=$(sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -1
from app import create_app
create_app()
from app.bd import q1
t = q1("SELECT aap_job_id, aap_job_tipo FROM tickets WHERE numero='FO-LAB-02416'"); print(f"{t['aap_job_id']} {t['aap_job_tipo']}")
Q
); echo "trabajo $J"
python3 - $J <<'PY'
import base64, glob, json, re, ssl, sys, urllib.request
inv = glob.glob("/home/cquezada/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo")[0]
cl = re.search(r"^gateway_admin_password=(.*)$", open(inv).read(), re.M).group(1).strip()
A = "Basic " + base64.b64encode(f"admin:{cl}".encode()).decode(); ctx = ssl._create_unverified_context()
def g(u):
    with urllib.request.urlopen(urllib.request.Request("https://localhost/api/controller/v2/" + u, headers={"Authorization": A}), context=ctx, timeout=60) as r: return json.loads(r.read())
jid, tipo = sys.argv[1], sys.argv[2]
jobs = [jid]
if tipo == "workflow":
    ns = g(f"workflow_jobs/{jid}/workflow_nodes/")["results"]
    print("nodos:", [((n.get("summary_fields") or {}).get("job") or {}).get("name", "")[:50] + ":" + str(((n.get("summary_fields") or {}).get("job") or {}).get("status")) for n in ns])
    jobs = [n["summary_fields"]["job"]["id"] for n in ns if (n.get("summary_fields") or {}).get("job", {}).get("type") == "job"]
for j in jobs:
    d = g(f"jobs/{j}/"); print("job", j, d["status"], (d.get("job_explanation") or "")[:200])
    for e in g(f"jobs/{j}/job_events/?event=runner_on_failed&page_size=3")["results"]:
        print("falla:", e.get("task"), str((e.get("event_data") or {}).get("res", {}).get("msg", ""))[:300])
PY
