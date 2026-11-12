set -a; . ~/faceops/faceops.env; set +a
cat > /tmp/redq.py <<'PY'
import json, re
from app import create_app
from app.bd import q
create_app()
cl = {c["id"]: c["codigo"] for c in q("SELECT id, codigo FROM clientes")}
from collections import Counter
print("CATS", Counter((cl.get(e["cliente_id"]), e["categoria"]) for e in q("SELECT cliente_id, categoria FROM equipos")))
for e in q("SELECT * FROM equipos WHERE categoria NOT IN ('', 'servidor', 'clúster openshift', 'estación de trabajo') OR id=14 ORDER BY cliente_id, id"):
    d = {}
    try: d = json.loads(e.get("datos") or "{}")
    except Exception: pass
    print(cl.get(e["cliente_id"]), e["id"], e["nombre"], "|cat", e["categoria"], e.get("forma"), "|so", e["so"], "|", e["so_familia"], "|ip", e["ip"], "|", e["estado"], "|inv", e["inventarios"], "|grp", e["grupos"][:120], "|desc", (e.get("descripcion") or "")[:100], "|hw", str(d.get("hardware"))[:120])
PY
sudo podman cp /tmp/redq.py faceops:/tmp/redq.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/redq.py 2>&1 | grep -v -i warn | cut -c1-700
python3 - <<'PY'
import os, ssl, urllib.request, json, re
cx = ssl._create_unverified_context(); B = os.environ["FACEOPS_AAP_URL"].rstrip("/") + "/api/controller/v2/"
H = {"Authorization": "Bearer " + os.environ["FACEOPS_AAP_TOKEN"]}
def get(u): return json.loads(urllib.request.urlopen(urllib.request.Request(B + u, headers=H), context=cx).read())
SEC = re.compile(r"(?i)pass|clave|token|secret|llave|key|psk|cred|become")
for inv in get("inventories/?page_size=100")["results"]:
    if not re.search(r"(?i)red|network", inv["name"]): continue
    print("INV", inv["id"], inv["name"])
    for h in get(f"inventories/{inv['id']}/hosts/?page_size=200")["results"]:
        try: v = json.loads(h["variables"] or "{}")
        except Exception:
            import yaml; v = yaml.safe_load(h["variables"]) or {}
        print("  ", h["name"], {k: v[k] for k in v if not SEC.search(k)})
    for c in get("credentials/?page_size=200")["results"]:
        pass
print("CREDS", [ (c["id"], c["name"], c["credential_type"]) for c in get("credentials/?page_size=200")["results"] if re.search(r"(?i)red|vyos|net|frr|switch|fw", c["name"])])
print("COLS", [c["name"] for c in get("collections/?page_size=5").get("results", [])] if False else "")
PY
sudo podman exec faceops ls /opt/faceops/aap/collections 2>/dev/null | head
