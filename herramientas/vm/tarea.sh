cat > /tmp/d2.py <<'PY'
import re
from app import create_app
from app.bd import q1
app = create_app()
from app.aap import AAP
with app.app_context():
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
    a = AAP.de_cliente(cl)
    inv = a.get("inventories/", name="Nube GCP")["results"][0]
    for s in a.get("inventory_sources/", inventory=inv["id"])["results"]:
        print("FUENTE", s["id"], s["name"], s["source"], "cred", s.get("credential"), "vars", s.get("source_vars"), "estado", s.get("status"))
        u = (s.get("summary_fields") or {}).get("last_job") or {}
        if u.get("id"):
            t = a.sesion.get(a.url + f"inventory_updates/{u['id']}/stdout/?format=txt", timeout=30).text if hasattr(a, "sesion") else ""
            if not t:
                try: t = a.get(f"inventory_updates/{u['id']}/stdout/", format="json").get("content", "")
                except Exception as e: t = str(e)
            t = re.sub(r"(?i)(private_key|password|secret|BEGIN[^\n]*)[^\n]*", "[oculto]", t)
            print("\n".join(t.splitlines()[-30:]))
PY
sudo podman cp /tmp/d2.py faceops:/tmp/d2.py && sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/d2.py 2>&1 | grep -v -i warn | tail -40
