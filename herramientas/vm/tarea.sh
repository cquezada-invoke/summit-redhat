#!/bin/bash
# Diagnóstico (solo lectura): menú del perfil lector y perfil de rendimiento de las pantallas lentas del núcleo. Sin secretos.
cat > /tmp/n4.py <<'PY'
import time, requests
from app import create_app
from app.bd import q1
app = create_app()
B = "http://192.168.122.1:8110/lab"
def ses(uid):
    ck = app.session_interface.get_signing_serializer(app).dumps({"uid": uid, "cliente_id": 1, "csrf": "x"})
    s = requests.Session(); s.cookies.set(app.config.get("SESSION_COOKIE_NAME") or "session", ck); return s
lec = q1("SELECT id FROM usuarios WHERE usuario='qa.lector'")["id"]; adm = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")["id"]
with app.test_request_context("/lab/"):
    from app import vistas
    print("EP_MENU consola.inicio:", vistas.EP_MENU.get("consola.inicio"), "| cola:", vistas.EP_MENU.get("consola.cola_acciones"))
s = ses(lec)
for p in ("/", "/inventario", "/tickets", "/cola", "/monitoreo/eventos-aap"):
    r = s.get(B + p, allow_redirects=False, timeout=60); print("lector", p, r.status_code, r.headers.get("Location", ""))
with app.test_request_context("/lab/"):
    from app import vistas
    print("EP_MENU consola.inicio (tras cargar menú):", vistas.EP_MENU.get("consola.inicio"))
s = ses(adm)
for p in ("/", "/inventario", "/tickets/2380", "/automatizaciones/95", "/inventario/26"):
    for i in range(2):
        t0 = time.time(); r = s.get(B + p, headers={"X-FaceOps-Perfil": "1"}, timeout=120); dt = time.time() - t0
    print(f"PERFIL {p} {r.status_code} {dt:.2f}s ::", (r.headers.get("X-FaceOps-Perfil") or "")[:900])
PY
sudo podman cp /tmp/n4.py faceops:/tmp/n4.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/n4.py 2>&1 | grep -v -i -E "warn|token|secret" | tail -30; sudo podman exec faceops rm -f /tmp/n4.py
uptime
