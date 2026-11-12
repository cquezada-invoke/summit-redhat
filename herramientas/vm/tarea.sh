#!/bin/bash
# Perfil (solo lectura) de /lab/mapa/datos del segmento en producción. Sin secretos.
cat > /tmp/p3112.py <<'PY'
import time, cProfile, pstats, io
from app import create_app
app = create_app()
with app.test_client() as c:
    with c.session_transaction() as s: s["uid"] = 1; s["csrf"] = "x"
    qs = {"vista": "negocio", "nivel": "segmento", "valor": "App de pagos", "ambiente": "produccion"}
    c.get("/lab/mapa/datos", query_string=qs); time.sleep(2); c.get("/lab/mapa/datos", query_string=qs)
    pr = cProfile.Profile(); t0 = time.time(); pr.enable(); c.get("/lab/mapa/datos", query_string=qs); pr.disable(); print(f"datos prod {time.time()-t0:.2f}s")
    s_ = io.StringIO(); pstats.Stats(pr, stream=s_).sort_stats("cumulative").print_stats(r"/app/|requests/api|socket.py|sqlite", 30)
    print("\n".join(l[:180] for l in s_.getvalue().splitlines()[8:42] if l.strip()))
PY
sudo podman cp /tmp/p3112.py faceops:/tmp/p3112.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3112.py 2>&1 | grep -v -i -E "token|password|secret|clave|warn" | tail -40; sudo podman exec faceops rm -f /tmp/p3112.py
