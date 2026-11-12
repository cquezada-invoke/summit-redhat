#!/bin/bash
# Perfil (solo lectura) de la gráfica en producción en el FaceOps en vivo (cabecera de diagnóstico). Sin secretos.
cat > /tmp/p3113.py <<'PY'
import time, requests
from app import create_app
app = create_app()
ck = app.session_interface.get_signing_serializer(app).dumps({"uid": 1, "csrf": "x"})
S = requests.Session(); S.cookies.set(app.config.get("SESSION_COOKIE_NAME") or "session", ck)
for amb in ("produccion", "qa"):
    for i in range(2):
        t0 = time.time(); r = S.get("http://192.168.122.1:8110/lab/mapa/datos", params={"vista": "negocio", "nivel": "segmento", "valor": "App de pagos", "ambiente": amb}, headers={"X-FaceOps-Perfil": "1"}, timeout=60)
        print(f"{amb} {time.time()-t0:.2f}s ::", (r.headers.get("X-FaceOps-Perfil") or "")[:900])
PY
sudo podman cp /tmp/p3113.py faceops:/tmp/p3113.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3113.py 2>&1 | grep -v -i -E "token|password|secret|clave|warn" | tail -8; sudo podman exec faceops rm -f /tmp/p3113.py
