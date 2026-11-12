up() { curl -s -o /dev/null -w '%{http_code}' http://192.168.122.1:8110/sesion/valida; }
echo "== FaceOps: $(up)"
cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-60
source herramientas/pruebas/comun.sh >/dev/null 2>&1
source <(sed -n '/^fo() {/,/^}/p' herramientas/pruebas/p-operadores.sh)
echo "DBG $(fo '(len(o.get("operadores") or []), sum(1 for x in o.get("operadores") or [] if "pods" in x), len(o.get("paquetes") or {}), o.get("paquetes_completo"), sorted((x["n"], x.get("pods_listos"), x.get("pods")) for x in o.get("operadores") or []))')"
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -E "^AUT"
import re
from app import create_app
app = create_app()
from app.bd import q1
e = q1("SELECT * FROM equipos WHERE id=29")
c = app.test_client()
with c.session_transaction() as s:
    s["uid"] = 1; s["csrf"] = "x"; s["cliente_id"] = e["cliente_id"]
with app.test_request_context():
    from flask import url_for
    u = url_for("consola.equipo", eid=29)
h = c.get(u, follow_redirects=True).get_data(as_text=True)
for m in re.finditer(r"(?i)automatizaciones", h): print("AUT", re.sub(r"\s+", " ", h[max(0, m.start()-120):m.end()+40]))
Q
timeout 3000 bash herramientas/pruebas/p-operadores.sh operadores 2>&1 | grep -E "^(CASO|CERT|FIN|EVIDENCIA)" | cut -c1-1500
