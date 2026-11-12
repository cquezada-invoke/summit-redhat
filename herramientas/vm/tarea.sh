set -a; . ~/faceops/faceops.env; set +a
cat > /tmp/redq.py <<'PY'
import json, re
from app import create_app
from app.bd import q
create_app()
cl = {c["id"]: c["codigo"] for c in q("SELECT id, codigo FROM clientes")}
SEC = re.compile(r"(?i)pass|clave|token|secret|llave|key|psk|cred")
for e in q("SELECT * FROM equipos ORDER BY cliente_id, id"):
    d = {}
    try: d = json.loads(e.get("datos") or "{}")
    except Exception: pass
    cat = d.get("categoria") or ""
    if e["id"] != 14 and cat in ("", "servidor", "clúster openshift", "estación de trabajo", "almacenamiento"):
        continue
    dd = {k: (str(v)[:80]) for k, v in d.items() if not SEC.search(k) and not isinstance(v, (list, dict))}
    print(cl.get(e["cliente_id"]), e["id"], e["nombre"], "|so", e["so"], e["so_version"], "|ip", e["ip"], "|", e["estado"], "|inv", e["inventarios"], "|skills", e["skills_auto"], "|datos", dd)
    print("   claves-datos:", sorted(d.keys())[:60])
PY
sudo podman cp /tmp/redq.py faceops:/tmp/redq.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/redq.py 2>&1 | grep -v -i warn | cut -c1-900
