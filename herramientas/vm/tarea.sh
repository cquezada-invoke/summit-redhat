#!/bin/bash
# Diagnóstico (solo lectura): AAP del lab (dónde corre, responde, disco/memoria) y tiempos del mapa por ambiente con perfil. Sin secretos.
echo "== host"; df -h / /var 2>/dev/null | tail -n +1; free -m | head -2; uptime
echo "== contenedores no Up"; sudo podman ps -a --format '{{.Names}} {{.Status}}' | grep -v " Up " | head -20
echo "== VMs"; sudo virsh list --all 2>/dev/null | grep -iE "aap|ansible|controller|shut|paused|crash" | head -20
cat > /tmp/p3107.py <<'PY'
import time, cProfile, pstats, io, requests, urllib3; urllib3.disable_warnings()
from urllib.parse import urlsplit
from app import create_app
app = create_app()
from app.bd import q1
cl = q1("SELECT * FROM clientes WHERE codigo='lab'") or q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
u = cl.get("aap_url") or ""; print("AAP host:", urlsplit(u).netloc)
for path in ("api/controller/v2/ping/", "api/v2/ping/", "api/gateway/v1/ping/"):
    t0 = time.time()
    try:
        r = requests.get(u.split("/api/")[0].rstrip("/") + "/" + path, timeout=10, verify=False); s = r.status_code
    except Exception as e: s = type(e).__name__
    print(f"  ping {path}: {s} {time.time()-t0:.2f}s")
SEG = "App de pagos"
with app.test_client() as c:
    with c.session_transaction() as s: s["uid"] = 1; s["csrf"] = "x"
    for amb in ("produccion", "qa", "desarrollo"):
        for url, qs in (("/lab/mapa", {"vista": "aplicaciones", "segmento": SEG, "ambiente": amb}), ("/lab/mapa/datos", {"vista": "negocio", "nivel": "segmento", "valor": SEG, "ambiente": amb})):
            pr = cProfile.Profile(); t0 = time.time(); pr.enable(); r = c.get(url, query_string=qs); pr.disable(); dt = time.time() - t0
            t1 = time.time(); c.get(url, query_string=qs); dt2 = time.time() - t1
            print(f"## {url} {amb}: 1a {dt:.2f}s · 2a {dt2:.2f}s [{r.status_code}]")
            if dt > 0.6:
                s_ = io.StringIO(); pstats.Stats(pr, stream=s_).sort_stats("cumulative").print_stats(r"/app/|requests/api|socket.py|sqlite", 22)
                print("\n".join(l[:180] for l in s_.getvalue().splitlines()[8:34] if l.strip()))
PY
sudo podman cp /tmp/p3107.py faceops:/tmp/p3107.py; timeout 900 sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3107.py 2>&1 | grep -v -i -E "token|password|secret|clave|warn" | tail -150; sudo podman exec faceops rm -f /tmp/p3107.py
