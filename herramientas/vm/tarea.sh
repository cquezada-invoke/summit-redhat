# Reactivar las activaciones de EDA que quedaron en error tras el reinicio del host (Hub respondía 503 al arrancar)
python3 - <<'PY'
import base64, glob, json, re, ssl, time, urllib.request
inv = glob.glob("/home/cquezada/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo")[0]
cl = re.search(r"^gateway_admin_password=(.*)$", open(inv).read(), re.M).group(1).strip()
A = "Basic " + base64.b64encode(f"admin:{cl}".encode()).decode(); ctx = ssl._create_unverified_context()
def r(m, u, d=None):
    q = urllib.request.Request("https://localhost/api/eda/v1/" + u, method=m, data=json.dumps(d).encode() if d is not None else None, headers={"Authorization": A, "Content-Type": "application/json"})
    with urllib.request.urlopen(q, context=ctx, timeout=60) as x:
        b = x.read(); return json.loads(b) if b else {}
malas = [a for a in r("GET", "activations/?page_size=100")["results"] if a.get("is_enabled") and a["status"] not in ("running", "starting")]
for a in malas:
    r("POST", f"activations/{a['id']}/restart/", {}); print("reiniciada:", a["name"], "(estaba", a["status"] + ")")
time.sleep(90)
for a in r("GET", "activations/?page_size=100")["results"]:
    if a["id"] in {m["id"] for m in malas}: print("ahora:", a["name"], a["status"])
PY
(echo > /dev/tcp/127.0.0.1/5023) 2>/dev/null && echo "5023 escucha" || echo "5023 NO escucha"
