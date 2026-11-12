#!/bin/bash
# Medición (solo lectura) contra el FaceOps en vivo por HTTP + perfil en frío de /mapa/datos del segmento. Sin secretos.
cat > /tmp/p3103.py <<'PY'
import time, sys, cProfile, pstats, io, threading
import requests, urllib3; urllib3.disable_warnings()
from app import create_app
app = create_app()
ck = app.session_interface.get_signing_serializer(app).dumps({"uid": 1, "csrf": "x"})
nombre = app.config.get("SESSION_COOKIE_NAME") or "session"
S = requests.Session(); S.cookies.set(nombre, ck)
B = None
for base in ("http://192.168.122.1:8110", "http://192.168.122.1:8110/faceops"):
    r = S.get(base + "/lab/mapa", params={"vista": "aplicaciones"}, allow_redirects=False, timeout=60)
    print("probe", base.replace("192.168.122.1", "h"), r.status_code)
    if r.status_code == 200: B = base; break
SEG = "App de pagos"
def g(path, params):
    t0 = time.time(); r = S.get(B + path, params=params, timeout=120, allow_redirects=False); return time.time() - t0, r.status_code
DAT = {"vista": "negocio", "nivel": "segmento", "valor": SEG, "ambiente": "produccion"}
otros = [("/lab/mapa/rendimiento", {"segmento": SEG}), ("/lab/arquitectura/recorrido", {"segmento": SEG})]
for i in range(4):
    a = g("/lab/mapa", {"vista": "aplicaciones", "segmento": SEG})
    res = {}
    def run(k, p, q): res[k] = g(p, q)
    ths = [threading.Thread(target=run, args=("datos", "/lab/mapa/datos", DAT))] + [threading.Thread(target=run, args=(p, p, q)) for p, q in otros]
    t0 = time.time(); [t.start() for t in ths]; [t.join() for t in ths]; par = time.time() - t0
    solo = g("/lab/mapa/datos", DAT)
    print(f"vuelta {i}: pagina {a[0]:.2f}s[{a[1]}] · en paralelo: " + " ".join(f"{k.split('/')[-1]}={v[0]:.2f}s[{v[1]}]" for k, v in res.items()) + f" · total visible {a[0]+par:.2f}s · datos solo {solo[0]:.2f}s")
    time.sleep(2)
for i in range(2):
    a = g("/lab/mapa", {"vista": "aplicaciones"}); print(f"global pagina {a[0]:.2f}s")
# perfil en frío en este proceso (sin cachés previas)
with app.test_client() as c:
    with c.session_transaction() as s: s["uid"] = 1; s["csrf"] = "x"
    pr = cProfile.Profile(); t0 = time.time(); pr.enable(); r = c.get("/lab/mapa/datos", query_string=DAT); pr.disable()
    print("FRIO datos", f"{time.time()-t0:.2f}s", r.status_code)
    s_ = io.StringIO(); pstats.Stats(pr, stream=s_).sort_stats("cumulative").print_stats(r"/app/|requests/api|socket|sqlite|urlopen|subprocess", 45)
    print("\n".join(l[:190] for l in s_.getvalue().splitlines()[8:60] if l.strip()))
PY
sudo podman cp /tmp/p3103.py faceops:/tmp/p3103.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3103.py 2>&1 | grep -v -i -E "token|password|secret|clave|warn" | tail -120; sudo podman exec faceops rm -f /tmp/p3103.py
