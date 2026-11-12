#!/bin/bash
# Verificación tras el autodeploy (solo lectura): espera la versión con «sin esperas» y mide la ficha servida por el gunicorn real. Sin secretos.
V=https://faceops.34.176.42.56.sslip.io:8843/faceops/version
for i in $(seq 1 90); do c=$(curl -sk --max-time 5 $V | python3 -c 'import sys,json;print(json.load(sys.stdin).get("commit",""))' 2>/dev/null); [[ "$c" == 120cf198* ]] && break; sleep 10; done
echo "== versión: $(curl -sk --max-time 5 $V) · $(date -u +%T)"
[[ "$c" == 120cf198* ]] || { echo "aún no desplegado"; exit 0; }
cat > /tmp/p3203.py <<'PY'
import time, sys, requests, warnings
warnings.filterwarnings("ignore")
from app import create_app
from app.bd import q1
app = create_app()
with app.app_context():
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'") or q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 LIMIT 1")
    cl = q1("SELECT id FROM clientes WHERE codigo='lab'")
    galleta = app.session_interface.get_signing_serializer(app).dumps({"uid": u["id"], "usuario_id": u["id"], "cliente_id": cl["id"]})
s = requests.Session(); s.verify = False
s.cookies.set(app.config.get("SESSION_COOKIE_NAME", "session"), galleta)
base = "https://faceops.34.176.42.56.sslip.io:8843/faceops/lab"
for rep in (1, 2, 3):
    for p in sys.argv[1:]:
        t0 = time.time(); r = s.get(base + p, allow_redirects=False, timeout=60); d = (time.time() - t0) * 1000
        print(f"[{rep}] {p} -> {r.status_code} {d:.0f} ms · {len(r.content)//1024} KB · marcadores={r.text.count(' data-pendiente')}")
    time.sleep(3)
PY
sudo podman cp /tmp/p3203.py faceops:/tmp/p3203.py
echo "== gunicorn real (HTTPS público), recién desplegado"
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops timeout 400 python3 /tmp/p3203.py /inventario/6 /inventario/128 /inventario/131 /inventario/29 /mapa /plataformas /edge /nubes 2>&1 | grep -v -i -E "warn|token|password|secret|cookie" | tail -30
sudo podman exec faceops rm -f /tmp/p3203.py
echo "== registro de paquetes en disco: $(sudo podman exec faceops sh -c 'ls -la /var/lib/faceops/registro-rpm.json 2>/dev/null | wc -l')"
cat > /tmp/caidas.py <<'PYC'
"""Mide páginas con TODAS las dependencias externas caídas (cada llamada de red tarda 1.5 s y falla) y lista las que ocurren en el hilo de la request."""
import sys, time, socket, threading, traceback, re
from collections import defaultdict
import requests
LOG = []
def donde():
    for f in reversed(traceback.extract_stack()[:-2]):
        if "/app/" in f.filename and "sin_espera" not in f.filename: return f"{f.filename.split('/app/')[-1]}:{f.lineno}:{f.name}"
    return "?"
def lento(et):
    def w(*a, **k):
        if threading.current_thread() is threading.main_thread():
            LOG.append((et, donde()))
        time.sleep(1.5); raise requests.ConnectionError("caída simulada") if et == "http" else OSError("caída simulada")
    return w
CAIDAS = "--caidas" in sys.argv
if CAIDAS:
    requests.Session.request = lento("http")
    socket.create_connection = lento("tcp")
from app import create_app
from app.bd import q1, q
app = create_app()
with app.app_context():
    u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 ORDER BY id LIMIT 1")
    cl = q1("SELECT c.id FROM clientes c JOIN equipos e ON e.cliente_id=c.id GROUP BY c.id ORDER BY (c.codigo='lab') DESC, count(*) DESC LIMIT 1")
urls = [x for x in sys.argv[1:] if not x.startswith("--")]
if "--todas" in sys.argv:
    with app.app_context():
        urls += [f"/inventario/{r['id']}" for r in q("SELECT id FROM equipos WHERE cliente_id=? AND estado!='retirado' ORDER BY id", cl["id"])]
peor = 0
with app.test_client() as t:
    with t.session_transaction() as s: s["uid"] = u["id"]; s["usuario_id"] = u["id"]; s["cliente_id"] = cl["id"]
    for rep in (1, 2):
        for p in urls:
            LOG.clear(); t0 = time.time(); r = t.get(p); d = (time.time() - t0) * 1000
            peor = max(peor, d) if rep == 1 else peor
            agg = defaultdict(int)
            for x in LOG: agg[x] += 1
            pend = len(re.findall(rb"<[a-z]+ [^>]*data-pendiente[ >]", r.data))
            print(f"[{rep}] {p} -> {r.status_code} {d:.0f} ms · pendientes={pend}" + ("" if not agg else " · EN VIVO: " + "; ".join(f"{et} {w} x{n}" for (et, w), n in agg.items())))
        if "--una" in sys.argv: break
        time.sleep(2.5)
print(f"PEOR primera carga: {peor:.0f} ms")
PYC
sudo podman cp /tmp/caidas.py faceops:/tmp/caidas.py
echo "== código desplegado con TODAS las dependencias caídas (test client; cada llamada de red tarda 1.5 s y falla)"
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops timeout 900 python3 /tmp/caidas.py --caidas --todas /mapa /plataformas /edge /nubes > /tmp/caidas.out 2>&1
grep -v -i -E "warn|token|password|secret" /tmp/caidas.out | awk '/EN VIVO|PEOR|Trace|Error|mapa|plataformas|edge|nubes/ || ($1=="[2]" && $5+0>450)' | tail -25
grep -E "^\[2\] /inventario" /tmp/caidas.out | awk '{n++; s+=$5; if($5>m)m=$5} END {printf "fichas (2a carga, caídas): n=%d prom=%.0f ms máx=%d ms\n", n, s/n, m}'
grep -E "^\[1\] /inventario" /tmp/caidas.out | awk '{n++; s+=$5; if($5>m)m=$5} END {printf "fichas (1a carga, caídas): n=%d prom=%.0f ms máx=%d ms\n", n, s/n, m}'
sudo podman exec faceops rm -f /tmp/caidas.py; rm -f /tmp/caidas.out
