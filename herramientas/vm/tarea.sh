# Diagnóstico 3: ¿están los playbooks de autocuidado en Gitea y en el proyecto de AAP? (sin secretos)
python3 - <<'PY'
import base64, glob, json, re, ssl, urllib.request
inv = glob.glob("/home/cquezada/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo")[0]
cl = re.search(r"^gateway_admin_password=(.*)$", open(inv).read(), re.M).group(1).strip()
A = "Basic " + base64.b64encode(f"admin:{cl}".encode()).decode(); ctx = ssl._create_unverified_context()
def g(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"Authorization": A}), context=ctx, timeout=60) as r: return json.loads(r.read())
gi = json.loads(urllib.request.urlopen("http://127.0.0.1:3000/api/v1/repos/invoke/faceops-automatizaciones/contents/playbooks?ref=main", timeout=30).read())
print("gitea autocuidado:", sorted(x["name"] for x in gi if x["name"].startswith("autocuidado")))
p = g("https://localhost/api/controller/v2/projects/?name=FaceOps%20%C2%B7%20automatizaciones")["results"][0]
print("proyecto", p["id"], p["status"], p.get("scm_revision", "")[:8], p.get("last_job_failed"))
u = g(f"https://localhost/api/controller/v2/projects/{p['id']}/project_updates/?order_by=-id&page_size=1")["results"][0]
print("última actualización", u["id"], u["status"], u.get("job_explanation", "")[:200])
pl = g(f"https://localhost/api/controller/v2/projects/{p['id']}/playbooks/")
print("playbooks autocuidado en AAP:", [x for x in pl if "autocuidado" in x], "total", len(pl))
if u["status"] != "successful":
    ev = g(f"https://localhost/api/controller/v2/project_updates/{u['id']}/events/?event=runner_on_failed&page_size=3")["results"]
    for e in ev: print("falla:", e.get("task"), str((e.get("event_data") or {}).get("res", {}).get("msg", ""))[:300])
PY
