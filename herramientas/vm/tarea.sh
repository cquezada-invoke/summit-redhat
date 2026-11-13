#!/bin/bash
cat > /tmp/e1s25.py <<'PY'
from app import create_app
app = create_app(); app.config["PROPAGATE_EXCEPTIONS"] = True; app.testing = True
from app.bd import q1
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='srv-e1prueba-01' AND estado!='retirado'", cl["id"])
c = app.test_client(); u = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")["id"]
with c.session_transaction() as s: s["uid"] = u; s["csrf"] = "x"; s["cliente_id"] = cl["id"]
for cod in ("PR-SRV-E1PRUEBA-01-0010", "PR-SRV-E1PRUEBA-01-0001"):
    try:
        r = c.get(f"/lab/inventario/{e['id']}/puntos/{cod}"); print(cod, r.status_code)
    except Exception:
        import traceback; print("\n".join(traceback.format_exc().splitlines()[-12:]))
o = q1("SELECT codigo FROM puntos_restauracion WHERE equipo_id=(SELECT id FROM equipos WHERE nombre='nodo-vault') ORDER BY id DESC LIMIT 1")
if o:
    r = c.get(f"/lab/inventario/26/puntos/{o['codigo']}"); print("nodo-vault", o["codigo"], r.status_code)
PY
sudo podman cp /tmp/e1s25.py faceops:/tmp/e1s25.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/e1s25.py 2>&1 | grep -v -i warn | tail -30
