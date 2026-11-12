#!/bin/bash
# Medición (solo lectura): /lab/mapa y /lab/mapa/datos del segmento por ambiente, en vivo y con perfil. Sin secretos.
cd "${GITHUB_WORKSPACE:-.}" 2>/dev/null; echo "desplegado: $(sudo podman exec faceops cat /opt/faceops/VERSION 2>/dev/null | head -1)"
cat > /tmp/p3106.py <<'PY'
import time, cProfile, pstats, io, requests, urllib3; urllib3.disable_warnings()
from app import create_app
app = create_app()
ck = app.session_interface.get_signing_serializer(app).dumps({"uid": 1, "csrf": "x"})
S = requests.Session(); S.cookies.set(app.config.get("SESSION_COOKIE_NAME") or "session", ck)
B = "http://192.168.122.1:8110"; SEG = "App de pagos"
def g(p, q):
    t0 = time.time(); r = S.get(B + p, params=q, timeout=120, allow_redirects=False); return f"{time.time()-t0:.2f}s[{r.status_code}]"
for v in range(2):
    for amb in ("produccion", "qa", "desarrollo"):
        print(f"vivo v{v} {amb}: pagina {g('/lab/mapa', {'vista':'aplicaciones','segmento':SEG,'ambiente':amb})} · datos {g('/lab/mapa/datos', {'vista':'negocio','nivel':'segmento','valor':SEG,'ambiente':amb})}")
with app.test_client() as c:
    with c.session_transaction() as s: s["uid"] = 1; s["csrf"] = "x"
    for amb in ("qa", "produccion"):
        for url, qs in (("/lab/mapa", {"vista": "aplicaciones", "segmento": SEG, "ambiente": amb}), ("/lab/mapa/datos", {"vista": "negocio", "nivel": "segmento", "valor": SEG, "ambiente": amb})):
            c.get(url, query_string=qs)
            pr = cProfile.Profile(); t0 = time.time(); pr.enable(); r = c.get(url, query_string=qs); pr.disable()
            print(f"## PERFIL {url} {amb} {time.time()-t0:.2f}s")
            s_ = io.StringIO(); pstats.Stats(pr, stream=s_).sort_stats("cumulative").print_stats(r"/app/|requests/api|socket|sqlite", 28)
            print("\n".join(l[:180] for l in s_.getvalue().splitlines()[8:40] if l.strip()))
PY
sudo podman cp /tmp/p3106.py faceops:/tmp/p3106.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3106.py 2>&1 | grep -v -i -E "token|password|secret|clave|warn" | tail -150; sudo podman exec faceops rm -f /tmp/p3106.py
