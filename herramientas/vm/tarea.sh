#!/bin/bash
# Medición (solo lectura): tiempo de /lab/mapa del segmento «App de pagos» y de /lab/mapa/datos, con perfil. Sin secretos.
cat > /tmp/p3102.py <<'PY'
import time, cProfile, pstats, io, sys
from app import create_app
app = create_app()
SEG = sys.argv[1] if len(sys.argv) > 1 else "App de pagos"
def t(c, url, qs, n=2, prof=False):
    out = []
    for i in range(n):
        pr = cProfile.Profile() if prof and i == n - 1 else None
        t0 = time.time()
        if pr: pr.enable()
        r = c.get(url, query_string=qs)
        if pr: pr.disable()
        dt = time.time() - t0
        out.append(f"{dt:.2f}s[{r.status_code},{len(r.data)//1024}KB]")
    print("##", url, qs.get("segmento") or qs.get("valor") or "-", " ".join(out))
    if pr:
        s_ = io.StringIO(); pstats.Stats(pr, stream=s_).sort_stats("cumulative").print_stats(r"/app/|requests/api|socket|sqlite|urlopen", 45)
        print("\n".join(l[:190] for l in s_.getvalue().splitlines()[8:60] if l.strip()))
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    t(c, "/lab/mapa", {"vista": "aplicaciones"}, 2)
    t(c, "/lab/mapa", {"vista": "aplicaciones", "segmento": SEG}, 3, prof=True)
    t(c, "/lab/mapa/datos", {"vista": "negocio", "nivel": "segmento", "valor": SEG, "ambiente": "produccion"}, 3, prof=True)
    t(c, "/lab/mapa/datos", {"vista": "negocio", "nivel": "global", "valor": ""}, 2)
PY
sudo podman cp /tmp/p3102.py faceops:/tmp/p3102.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3102.py 2>&1 | grep -v -i -E "token|password|secret|clave|warn" | tail -150; sudo podman exec faceops rm -f /tmp/p3102.py
