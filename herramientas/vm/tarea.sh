#!/bin/bash
cat > /tmp/e1s07.py <<'PY'
from app import create_app
from app.bd import q, q1
create_app()
t = q1("SELECT * FROM tickets WHERE numero='FO-LAB-02458'"); print("RES", (t["resultado"] or "")[-900:])
for e in q("SELECT id, nombre, estado, ip, so, inventario, energia FROM equipos WHERE nombre LIKE '%e1%prueba%' OR nombre LIKE '%e1prueba%'"): print(dict(e))
for x in q("SELECT t.numero, t.estado, t.limite, t.motivo FROM tickets t WHERE t.id > ? ORDER BY t.id", t["id"]): print(dict(x))
PY
sudo podman cp /tmp/e1s07.py faceops:/tmp/e1s07.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/e1s07.py 2>&1 | grep -v -i warn | tail -30
sudo virsh list --all | grep -i e1
cut -c1-200 ~/faceops-autodeploy/estado
