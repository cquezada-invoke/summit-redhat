#!/bin/bash
# Medición (solo lectura): tiempos de la ficha de máquina y páginas afines, con trazas de llamadas externas. Sin secretos.
echo "== host $(date -u +%FT%TZ) · version: $(curl -sk --max-time 5 https://faceops.34.176.42.56.sslip.io:8843/faceops/version)"
sudo virsh list --all 2>/dev/null | awk 'NR>2 && $2!="" {printf "%s=%s ", $2, $3}'; echo
cat > /tmp/p3200.py <<'PY'
import cProfile, pstats, io, time, sys, traceback, socket, subprocess, re, threading
from collections import defaultdict
from app import create_app
from app.bd import q1
app = create_app()
with app.app_context():
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'")
    cl = q1("SELECT id FROM clientes WHERE codigo='lab'")
ext = []
def donde():
    for f in reversed(traceback.extract_stack()[:-2]):
        if "/opt/faceops/app/" in f.filename: return f"{f.filename.split('/app/')[-1]}:{f.lineno}:{f.name}"
    return "?"
def envolver(mod, nombre, etiqueta):
    orig = getattr(mod, nombre)
    def w(*a, **k):
        t0 = time.time(); err = ""
        try: return orig(*a, **k)
        except Exception as x: err = type(x).__name__; raise
        finally:
            d = time.time() - t0
            if threading.current_thread() is threading.main_thread():
                arg = a[0] if a else k.get("url", "")
                if etiqueta == "http": arg = a[1] if len(a) > 1 else k.get("url", "")
                arg = re.sub(r"(token|key|password|secret)=[^&]*", r"\1=***", str(arg))[:70]
                ext.append((d, etiqueta, donde(), arg, err))
    setattr(mod, nombre, w)
import requests
envolver(requests.Session, "request", "http")
envolver(socket, "create_connection", "tcp")
envolver(socket, "getaddrinfo", "dns")
envolver(subprocess, "run", "proc")
envolver(subprocess, "check_output", "proc")
try:
    import paramiko; envolver(paramiko.SSHClient, "connect", "ssh")
except Exception: pass
URLS = sys.argv[1:]
with app.test_client() as t:
    with t.session_transaction() as s: s["uid"] = u["id"]; s["usuario_id"] = u["id"]; s["cliente_id"] = cl["id"]
    for p in URLS:
        prof = p.endswith("!")
        p = p.rstrip("!")
        ext.clear(); pr = cProfile.Profile() if prof else None
        t0 = time.time()
        if pr: pr.enable()
        r = t.get(p)
        if pr: pr.disable()
        d = time.time() - t0
        print(f"### {p} -> {r.status_code} {d*1000:.0f} ms, {len(r.data)//1024} KB")
        agg = defaultdict(lambda: [0, 0.0])
        for x in ext:
            key = (x[1], x[2], x[3] if x[1] != "dns" else x[3]); agg[key][0] += 1; agg[key][1] += x[0]
        for (et, w, arg), (n, tt) in sorted(agg.items(), key=lambda z: -z[1][1])[:14]:
            if tt > 0.02: print(f"   {tt*1000:7.0f} ms x{n:<3} {et:5} {w:45} {arg}")
        if pr:
            sio = io.StringIO(); st = pstats.Stats(pr, stream=sio); st.sort_stats("cumulative")
            st.print_callees if False else None
            st.print_stats(r"/opt/faceops/app/", 30)
            for l in sio.getvalue().splitlines():
                if "/app/" in l and re.match(r"\s*\d", l):
                    c = l.split()
                    if float(c[3]) > 0.05: print("   PROF", c[3], c[-1].split("/app/")[-1])
PY
sudo podman cp /tmp/p3200.py faceops:/tmp/p3200.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops timeout 600 python3 /tmp/p3200.py \
  "/inventario/6!" /inventario/6 "/inventario/128!" "/inventario/131!" "/inventario/29!" /inventario/29 \
  /mapa /plataformas /edge /nubes 2>&1 | grep -v -i -E "warn|token|password|secret" | tail -150
sudo podman exec faceops rm -f /tmp/p3200.py
for p in inventario/6 inventario/29; do echo "curl lab/$p: $(curl -sk -o /dev/null -w '%{http_code} %{time_total}s' --max-time 60 https://faceops.34.176.42.56.sslip.io:8843/faceops/lab/$p)"; done
