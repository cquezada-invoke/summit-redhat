cd ~/summit-redhat && for i in 1 2 3 4 5; do git pull -q --ff-only origin main && break; sleep 10; done; git log -1 --format=%h
python3 faceops/aap/configurar.py > /tmp/conf-al.log 2>&1; echo "configurar: $?"; grep -iE "error|traceback" /tmp/conf-al.log | head -5; tail -2 /tmp/conf-al.log
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 15 && echo "faceops actualizado"
CL=$(grep -m1 '^gateway_admin_password=' ~/aap/*/inventory-demo | cut -d= -f2-) python3 - <<'PY'
import base64, json, os, ssl, time, urllib.request, urllib.parse, urllib.error
AU = "Basic " + base64.b64encode(f"admin:{os.environ['CL']}".encode()).decode(); cx = ssl._create_unverified_context()
def eda(m, p, d=None):
    q = urllib.request.Request(f"https://127.0.0.1/api/eda/v1/{p}", method=m, data=json.dumps(d).encode() if d is not None else None, headers={"Authorization": AU, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(q, context=cx, timeout=30) as r: b = r.read(); return json.loads(b) if b else {}
    except urllib.error.HTTPError as e: print("HTTP", e.code, p, e.read()[:200]); return None
N = "FaceOps · Alertas de objetos"
a = eda("GET", "activations/?" + urllib.parse.urlencode({"name": N}))["results"][0]
a = eda("GET", f"activations/{a['id']}/")
p = eda("GET", "projects/?" + urllib.parse.urlencode({"name": "FaceOps · automatizaciones"}))["results"][0]
eda("POST", f"projects/{p['id']}/sync/", {})
for _ in range(60):
    time.sleep(3)
    if eda("GET", f"projects/{p['id']}/")["import_state"] in ("completed", "failed"): break
rb = eda("GET", f"rulebooks/?project_id={p['id']}&name=alertas-objetos.yml")["results"][0]
print("rulebook con ra_credencial:", "ra_credencial" in (eda("GET", f"rulebooks/{rb['id']}/").get("rulesets") or ""))
cuerpo = {"name": N, "description": a.get("description"), "is_enabled": True, "decision_environment_id": a["decision_environment"]["id"] if isinstance(a.get("decision_environment"), dict) else a.get("decision_environment_id"),
          "rulebook_id": rb["id"], "eda_credentials": [c["id"] for c in a.get("eda_credentials") or []], "organization_id": a["organization"]["id"] if isinstance(a.get("organization"), dict) else a.get("organization_id"),
          "restart_policy": a.get("restart_policy", "on-failure"), "log_level": a.get("log_level", "info")}
eda("POST", f"activations/{a['id']}/disable/", {}); time.sleep(5); eda("DELETE", f"activations/{a['id']}/")
for _ in range(20):
    time.sleep(3)
    if not eda("GET", "activations/?" + urllib.parse.urlencode({"name": N}))["results"]: break
n = eda("POST", "activations/", cuerpo)
for _ in range(40):
    time.sleep(5); st = eda("GET", f"activations/{n['id']}/")["status"]
    if st in ("running", "failed", "error"): break
print("activación", n["id"], st)
PY
cat > /tmp/m.py <<'PY'
import re
from app import create_app
from app.bd import q1
a = create_app(); u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 LIMIT 1"); c = a.test_client()
with c.session_transaction() as s: s["uid"] = u["id"]
for eid in (6, 7):
    t = c.get(f"/lab/inventario/{eid}").get_data(as_text=True)
    print("ficha", eid, "J01:", t.count('name="on_J01"'), "para_C01:", t.count('name="para_C01"'), "reiniciar_jboss:", t.count('value="reiniciar_jboss"'))
x = q1("SELECT id FROM equipos WHERE cliente_id=1 AND nombre='nodo-tomcat' AND estado!='retirado'")
if x: print("nodo-tomcat J01:", c.get(f"/lab/inventario/{x['id']}").get_data(as_text=True).count('name="on_J01"'))
PY
sudo podman cp /tmp/m.py faceops:/tmp/m.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/m.py 2>&1 | grep -v -i warn | tail -6; sudo podman exec faceops rm -f /tmp/m.py
sudo journalctl -u faceops --since "-2 min" --no-pager 2>/dev/null | grep -iE "Traceback" | tail -2
