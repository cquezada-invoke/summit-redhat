# Diagnóstico de la cadena Alertmanager → EDA → Atender alerta → FaceOps (solo lectura, sin secretos)
grep -n "faceops-autocuidado\|5023" /opt/summit-monitoreo/alertmanager/alertmanager.yml | head -5
curl -s http://127.0.0.1:9093/api/v2/alerts?filter=faceops_autocuidado%3D%22si%22 | python3 -c "import json,sys;a=json.load(sys.stdin);print('alertas activas en AM:', [(x['labels'].get('faceops_ac_regla'), x['labels'].get('faceops_ac_objeto'), x['status']['state']) for x in a][:8])"
curl -s http://127.0.0.1:9093/api/v2/status | python3 -c "import json,sys;d=json.load(sys.stdin);c=d['config']['original'];print('receptor en config:', 'faceops-autocuidado' in c)"
(echo > /dev/tcp/127.0.0.1/5023) 2>/dev/null && echo "5023 escucha" || echo "5023 NO escucha"
ss -ltnp 2>/dev/null | grep -E ":502[23] " | sed 's/users.*//'
python3 - <<'PY'
import base64, glob, json, re, ssl, urllib.request
inv = glob.glob("/home/cquezada/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo")[0]
cl = re.search(r"^gateway_admin_password=(.*)$", open(inv).read(), re.M).group(1).strip()
A = "Basic " + base64.b64encode(f"admin:{cl}".encode()).decode(); ctx = ssl._create_unverified_context()
def g(u):
    with urllib.request.urlopen(urllib.request.Request("https://localhost/api/" + u, headers={"Authorization": A}), context=ctx, timeout=60) as r: return json.loads(r.read())
for a in g("eda/v1/activations/?page_size=50")["results"]:
    if "autocuidado" in a["name"] or "objetos" in a["name"]:
        print("activación", a["name"], a["status"], a.get("restart_count"), (a.get("status_message") or "")[:200])
r = g("controller/v2/jobs/?name=Autocuidado%20%C2%B7%20Atender%20alerta&order_by=-id&page_size=6")
print("Atender alerta:", r["count"], [(x["id"], x["status"], x["launch_type"], x["created"][:16]) for x in r["results"]])
for x in g("eda/v1/audit-rules/?page_size=5&order_by=-fired_at").get("results", []):
    print("regla EDA disparada:", x.get("name"), x.get("status"), (x.get("fired_at") or "")[:16])
PY
sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -6
from app import create_app
create_app()
from app.bd import q
for e in q("SELECT cuando, origen, regla, objeto, decision FROM autocuidado_eventos ORDER BY id DESC LIMIT 6"): print("evento", e["cuando"][:16], e["origen"], e["regla"], e["objeto"], "|", e["decision"][:90])
Q
