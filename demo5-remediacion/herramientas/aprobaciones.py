#!/usr/bin/python3
# Demo 5: aprobaciones de AAP desde la terminal (la forma normal es la UI de AAP: Automation Execution → Workflow approvals).
#   aprobaciones.py               → lista las pendientes y los últimos workflows de remediación
#   aprobaciones.py aprobar|rechazar  → aprueba o rechaza la pendiente más reciente
import base64, glob, json, os, re, ssl, sys, urllib.request
inv = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
c = re.search(r"^gateway_admin_password=(.*)$", open(inv).read(), re.M).group(1).strip()
H = {"Authorization": "Basic " + base64.b64encode(f"admin:{c}".encode()).decode(), "Content-Type": "application/json"}
CTX = ssl._create_unverified_context()


def api(m, p, d=None):
    r = urllib.request.Request("https://localhost/api/controller/v2/" + p, method=m, headers=H,
                               data=json.dumps(d).encode() if d is not None else None)
    with urllib.request.urlopen(r, context=CTX) as x:
        b = x.read()
        return json.loads(b) if b else {}


pend = api("GET", "workflow_approvals/?status=pending&order_by=-id")["results"]
if len(sys.argv) > 1 and pend:
    a = pend[0]
    api("POST", f"workflow_approvals/{a['id']}/{'approve' if sys.argv[1] == 'aprobar' else 'deny'}/", {})
    print(f"Aprobación {a['id']} ({a['name']}): {'APROBADA' if sys.argv[1] == 'aprobar' else 'RECHAZADA'}")
else:
    print("Pendientes:", [(a["id"], a["name"]) for a in pend] or "ninguna")
for w in api("GET", "workflow_jobs/?name__startswith=Remediaci&order_by=-id&page_size=4")["results"]:
    nodos = api("GET", f"workflow_jobs/{w['id']}/workflow_nodes/")["results"]
    pasos = ", ".join(f"{n['identifier']}:{(n.get('summary_fields', {}).get('job') or {}).get('status', '-')}" for n in nodos)
    print(f"workflow {w['id']} {w['status']} · {pasos}")
