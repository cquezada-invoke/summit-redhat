#!/bin/bash
# Verifica que el autodeploy tomó main (aviso AAP en la barra) y mide el mapa del segmento por ambiente en vivo. Sin secretos.
df -h / | awk 'NR==2{print "disco: "$4" libres ("$5")"}'
for i in $(seq 1 40); do sudo podman exec faceops grep -q "inicio de FaceOps" /opt/faceops/app/templates/base.html 2>/dev/null && break; sleep 30; done
sudo podman exec faceops grep -q "inicio de FaceOps" /opt/faceops/app/templates/base.html && echo "desplegado: sí (59aa3e30 o posterior)" || echo "desplegado: NO"
echo "AAP controller: $(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 https://127.0.0.1/api/controller/v2/ping/)"
cat > /tmp/p3111.py <<'PY'
import time, requests, urllib3; urllib3.disable_warnings()
from app import create_app
app = create_app()
ck = app.session_interface.get_signing_serializer(app).dumps({"uid": 1, "csrf": "x"})
S = requests.Session(); S.cookies.set(app.config.get("SESSION_COOKIE_NAME") or "session", ck)
B = "http://192.168.122.1:8110"; SEG = "App de pagos"
def g(p, q):
    t0 = time.time(); r = S.get(B + p, params=q, timeout=120, allow_redirects=False); return time.time() - t0, r.status_code, r.text
for v in range(3):
    for amb in ("produccion", "qa", "desarrollo"):
        a = g('/lab/mapa', {'vista': 'aplicaciones', 'segmento': SEG, 'ambiente': amb}); d = g('/lab/mapa/datos', {'vista': 'negocio', 'nivel': 'segmento', 'valor': SEG, 'ambiente': amb})
        print(f"v{v} {amb}: pagina {a[0]:.2f}s[{a[1]}] · grafica {d[0]:.2f}s[{d[1]}] · total {a[0]+d[0]:.2f}s{' · AVISO AAP visible' if 'aviso-aap' in a[2] else ''}")
c = g('/lab/certificados', {}); print(f"certificados {c[0]:.2f}s[{c[1]}] error_503={'503' in c[2]}")
PY
sudo podman cp /tmp/p3111.py faceops:/tmp/p3111.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3111.py 2>&1 | grep -v -i -E "token|password|secret|clave|warn" | tail -20; sudo podman exec faceops rm -f /tmp/p3111.py
