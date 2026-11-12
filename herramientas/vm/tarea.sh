# Diagnóstico 4: cola y capacidad de AAP (solo lectura, sin secretos)
python3 - <<'PY'
import base64, glob, json, re, ssl, urllib.request
inv = glob.glob("/home/cquezada/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo")[0]
cl = re.search(r"^gateway_admin_password=(.*)$", open(inv).read(), re.M).group(1).strip()
A = "Basic " + base64.b64encode(f"admin:{cl}".encode()).decode(); ctx = ssl._create_unverified_context()
def g(u):
    with urllib.request.urlopen(urllib.request.Request("https://localhost/api/controller/v2/" + u, headers={"Authorization": A}), context=ctx, timeout=60) as r: return json.loads(r.read())
for s in ("pending", "waiting", "running"):
    r = g(f"unified_jobs/?status={s}&order_by=id&page_size=5")
    print(s, r["count"], [(x["id"], x["name"][:40], x["created"][:16]) for x in r["results"][:3]])
for i in g("instances/")["results"]:
    print("instancia", i["hostname"], i["node_type"], "enabled" if i["enabled"] else "disabled", i.get("node_state"), "cap", i["capacity"], "consumida", i.get("consumed_capacity"), "errores", (i.get("errors") or "")[:120], "visto", (i.get("last_seen") or "")[:19])
for x in g("instance_groups/")["results"]:
    print("grupo", x["name"], "cap", x["capacity"], "consumida", x.get("consumed_capacity"), "%libre", x.get("percent_capacity_remaining"))
PY
