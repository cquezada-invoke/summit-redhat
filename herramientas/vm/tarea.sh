#!/bin/bash
# Lectura: resultado completo de los tickets FO-LAB-02291 y 02295 (sin secretos).
cat > /tmp/p3006.py <<'PY'
from app import create_app
from app.bd import q1
create_app()
for n in ("FO-LAB-02369",):
    t = q1("SELECT numero, estado, resultado FROM tickets WHERE numero=?", n)
    print("==", n, t["estado"]); print("\n".join((t["resultado"] or "").splitlines()[:25])[:2500])
PY
sudo podman cp /tmp/p3006.py faceops:/tmp/p3006.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3006.py 2>&1 | grep -v -i -E "warn|token|password|secret|activationkey" | tail -60; sudo podman exec faceops rm -f /tmp/p3006.py
