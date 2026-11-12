cd ~/summit-redhat && git pull -q --ff-only origin main; git log -1 --format=%h
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 25
sudo podman exec -i faceops python3 - <<'Q' 2>&1 | grep -E "^(TABLA|Traceback|  File|\w+Error)"
import re
from app import create_app
app = create_app()
from app.bd import q1
cl = q1("SELECT * FROM clientes WHERE lower(codigo)='lab'") or q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
c = app.test_client()
with c.session_transaction() as s:
    s["uid"] = 1; s["cliente_id"] = cl["id"]
with app.test_request_context():
    from flask import url_for
    u = url_for("consola.alertas")
r = c.get(u, headers={"Accept": "text/html"}, follow_redirects=True); h = r.get_data(as_text=True)
print("TABLA", r.status_code, "| filas:", h.count('data-estado="'), "| encendidas:", h.count('data-estado="encendida'), "| disparando:", h.count(' disparando"'), "| objetos:", sorted(set(re.findall(r'class="mono">([^<]+)</a>', h)))[:12])
print("TABLA contadores:", re.findall(r'<div class="n"[^>]*>(\d+)</div><div class="l">([^<]+)', h))
Q
