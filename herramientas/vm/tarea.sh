cat > /tmp/d3.py <<'PY'
import re
from app import create_app
from app.bd import q1
app = create_app()
from app.aap import AAP
with app.app_context():
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
    a = AAP.de_cliente(cl)
    ups = a.get("inventory_updates/", inventory_source=1199, order_by="-id", page_size=3)["results"]
    for u in ups[:2]:
        print("UPD", u["id"], u["status"], u.get("created"))
        txt = (u.get("result_traceback") or "") + "\n" + (u.get("job_explanation") or "")
        try: txt += "\n" + a.salida(u["id"], 40) if False else ""
        except Exception: pass
        r = a.get(f"inventory_updates/{u['id']}/stdout/", format="json")
        t = r.get("content", "") if isinstance(r, dict) else ""
        t = re.sub(r"(?i)[^\n]*(private_key|password|secret|BEGIN)[^\n]*", "[oculto]", txt + "\n" + t)
        print("\n".join([l for l in t.splitlines() if l.strip()][-25:]))
PY
for i in $(seq 1 40); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done
sudo podman cp /tmp/d3.py faceops:/tmp/d3.py && sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/d3.py 2>&1 | grep -v -i warn | tail -55
