#!/bin/bash
# Humo sin crear tickets: ficha con la skill de parches, datos del formulario y rechazos del servidor. Sin secretos.
cat > /tmp/p3003.py <<'PY'
from app import create_app
from app.bd import q1
a = create_app()
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); cid = cl["id"]
u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 LIMIT 1")
n0 = q1("SELECT max(id) m FROM tickets")["m"]
c = a.test_client()
with c.session_transaction() as s: s["uid"] = u["id"]; s["csrf"] = "humo"
for eid in (8, 6):
    r = c.get(f"/lab/inventario/{eid}")
    h = r.get_data(as_text=True)
    print("FICHA", eid, r.status_code, "boton_parches=", h.count("data-parches-hid"), "form=", "fxParches" in h)
    i = h.find('data-parches-hid');
    if i > 0: print("   ", h[i-60:i+40].replace("\n", " "))
    j_ = c.get(f"/lab/inventario/{eid}/parches").get_json()
    print("DATOS", eid, j_.get("maquina"), "rev=", j_.get("revisado"), "pend=", len(j_.get("pendientes") or []), "en_curso=", j_.get("en_curso"))
for d in ({}, {"pp_alcance": "todos"}, {"pp_alcance": "todos", "pp_reinicio": "no", "pp_limite_min": "60"}):
    for eid in (8, 6):
        r = c.post(f"/lab/inventario/{eid}/cve/parche", data=dict(d, _csrf="humo")) if d.get("pp_limite_min") is None or eid == 6 else None
        if r is not None: print("POST", eid, d, r.status_code, (r.get_json() or {}).get("error"))
r = c.post("/lab/inventario/8/habilidad", data={"_csrf": "humo", "hid": "parches"}); print("SKILL", r.status_code, (r.get_json() or {}).get("error"))
print("tickets nuevos:", q1("SELECT max(id) m FROM tickets")["m"] - n0)
PY
sudo podman cp /tmp/p3003.py faceops:/tmp/p3003.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3003.py 2>&1 | grep -v -i -E "warn|token|password|secret" | tail -30; sudo podman exec faceops rm -f /tmp/p3003.py
