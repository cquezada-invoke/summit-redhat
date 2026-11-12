cd ~/summit-redhat; set -a; . ~/faceops/faceops.env; set +a
python3 - <<'PY'
import base64, glob, json, os, re, ssl, urllib.request, urllib.parse
INV_F = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CL = re.search(r"^gateway_admin_password=(.*)$", open(INV_F).read(), re.M).group(1).strip()
H = {"Authorization": "Basic " + base64.b64encode(f"admin:{CL}".encode()).decode()}
cx = ssl._create_unverified_context(); C = "https://localhost/api/controller/v2/"
g = lambda p: json.loads(urllib.request.urlopen(urllib.request.Request(C + p, headers=H), context=cx).read())
jt = g("job_templates/?" + urllib.parse.urlencode({"name": "Nube · Operar máquina"}))["results"][0]
for j in g(f"jobs/?unified_job_template={jt['id']}&order_by=-id&page_size=30")["results"]:
    if j["status"] == "failed":
        out = urllib.request.urlopen(urllib.request.Request(C + f"jobs/{j['id']}/stdout/?format=txt", headers=H), context=cx).read().decode(errors="replace")
        print("JOB", j["id"], j["status"], j["elapsed"], (j.get("extra_vars") or "")[:0])
        print("\n".join(l for l in out.splitlines()[-25:] if not re.search(r"private_key|token", l, re.I))[-2500:])
        break
PY
