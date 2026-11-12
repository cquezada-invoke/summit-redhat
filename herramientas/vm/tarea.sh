#!/bin/bash
# Lectura: primeras cargas lentas con dependencias caídas (código desplegado). Sin secretos.
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
grep -v -i -E "warn|token|password|secret" /tmp/caidas.out | awk '/EN VIVO|PEOR|Trace|Error/ || ($5+0>700)' | tail -30
grep -E "^\[2\] /inventario" /tmp/caidas.out | awk '{n++; s+=$5; if($5>m)m=$5} END {printf "fichas (2a carga, caídas): n=%d prom=%.0f ms máx=%d ms\n", n, s/n, m}'
grep -E "^\[1\] /inventario" /tmp/caidas.out | awk '{n++; s+=$5; if($5>m)m=$5} END {printf "fichas (1a carga, caídas): n=%d prom=%.0f ms máx=%d ms\n", n, s/n, m}'
sudo podman exec faceops rm -f /tmp/caidas.py; rm -f /tmp/caidas.out
