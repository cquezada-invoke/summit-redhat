#!/bin/bash
D=~/faceops-autodeploy; echo "desplegado $(cut -c1-8 $D/desplegado) · $(cat $D/estado)"; tail -5 $D/autodeploy.log | cut -c1-200
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>&1 <<'Q' | tail -25
import re, html, time
from app import create_app
app = create_app()
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    for u in ("/lab/nubes", "/lab/nubes/2"):
        t = time.time(); r = cl.get(u); h = r.get_data(as_text=True)
        print(u, r.status_code, round(time.time() - t, 1), "s", len(h))
        for k in ("recuperando", "Recuperando", "cargando", "Cargando", "Traceback", "sin datos"):
            for m in re.finditer(k, h):
                print("  ", k, "→", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h[max(0, m.start()-200): m.end()+150]))[:300]); break
with app.app_context():
    from app.bd import q
    from app import nube_consumo as N
    print("cache", q("SELECT infra_id, ts, job FROM nube_resumen"))
    print("trabajos", q("SELECT * FROM nube_resumen_trabajos ORDER BY id DESC LIMIT 5"))
    print("tickets", q("SELECT numero, estado, substr(resultado,1,200) r FROM tickets WHERE motivo LIKE '%consumo%' ORDER BY id DESC LIMIT 4"))
Q
