cd ~/summit-redhat
python3 - <<'PY'
import base64, glob, json, os, re, ssl, urllib.request, urllib.parse
INV_F = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CL = re.search(r"^gateway_admin_password=(.*)$", open(INV_F).read(), re.M).group(1).strip()
H = {"Authorization": "Basic " + base64.b64encode(f"admin:{CL}".encode()).decode()}
cx = ssl._create_unverified_context(); C = "https://localhost/api/controller/v2/"
g = lambda p: json.loads(urllib.request.urlopen(urllib.request.Request(C + p, headers=H), context=cx).read())
for jid in (29223, 29217):
    j = g(f"jobs/{jid}/")
    print("JOB", jid, j["status"], j["elapsed"], "| explicación:", (j.get("job_explanation") or "")[:300], "| result_traceback:", (j.get("result_traceback") or "")[-400:])
    ev = g(f"jobs/{jid}/job_events/?order_by=-counter&page_size=8")["results"]
    for e in ev:
        res = (e.get("event_data") or {}).get("res") or {}
        msg = res.get("msg") if isinstance(res, dict) else ""
        print("  ", e["counter"], e["event"], (e.get("task") or "")[:60], "|", str(msg)[:300] if not re.search(r"private_key|token", str(msg), re.I) else "[oculto]", "|", (e.get("stdout") or "")[-200:].replace("\n", " "))
PY
