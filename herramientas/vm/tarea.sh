sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v Warn | tail -40
import re, json
from app import create_app
app = create_app()
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    r = c.get("/lab/infraestructuras/centros")
    if r.status_code == 404: r = c.get("/infraestructuras/centros")
    h = r.get_data(as_text=True)
    print("HTTP", r.status_code, "diagrama", 'id="cdd-diagrama"' in h, "lineas", h.count('class="cdd-l '), "cajas", h.count('class="cdd-caja'), "balanceadores", h.count('class="cdd-lb"'))
    m = re.search(r'<script type="application/json" id="cdd-datos">(.*?)</script>', h, re.S)
    for x in json.loads(m.group(1)) if m else []:
        print("LINEA", x["t"], "|", x["p"], "|", x["d"][:6])
    for t in re.findall(r'class="cdd-ct">([^<]+)<', h): print("CAJA", t)
    for t in re.findall(r'class="cdd-pct"[^>]*>([^<]+)<', h): print("SALUD", t)
    for t in re.findall(r'class="cdd-bs"[^>]*>([^<]+)<', h): print("LB", t)
    print("HTML", len(h))
    open("/tmp/centros.html", "w").write(h)
Q
