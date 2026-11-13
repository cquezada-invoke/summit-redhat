#!/bin/bash
TK=$(sudo podman exec faceops printenv FACEOPS_RESPALDO_TOKEN | tr -d "\r"); G=http://192.168.122.1:3000
curl -s -H "Authorization: token $TK" "$G/api/v1/repos/repositorio/maquina-srv-e1prueba-01/git/commits/db9a986cbbd308139dc0e1646fbdff5c7e918bbb" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("C9 commit:", d["commit"]["message"][:300]); print([f["filename"] for f in d.get("files",[])])'
curl -s -H "Authorization: token $TK" "$G/api/v1/repos/repositorio/maquina-srv-e1prueba-01/git/commits/db9a986cbbd308139dc0e1646fbdff5c7e918bbb.diff" | head -40 | cut -c1-160
cat > /tmp/e1s24.py <<'PY'
from app import create_app
app = create_app()
from app.bd import q1
from app import pruebas
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='srv-e1prueba-01' AND estado!='retirado'", cl["id"])
p = q1("SELECT codigo FROM puntos_restauracion WHERE equipo_id=? ORDER BY id DESC LIMIT 1", e["id"])
c = app.test_client(); u = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")["id"]
with c.session_transaction() as s: s["uid"] = u; s["csrf"] = "x"; s["cliente_id"] = cl["id"]
r = c.get(f"/lab/inventario/{e['id']}/puntos/{p['codigo']}"); h = r.get_data(as_text=True)
print("PUNTO", p["codigo"], r.status_code, r.headers.get("Location"), len(h), "4 · Configuración" in h, "7 · Cobertura" in h, "motd" in h, "cubierto" in h, "app-grande" in h)
a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='IIS · Preparar servidor'", cl["id"]); print("IIS", a and pruebas.estado(dict(a))["estado"])
PY
sudo podman cp /tmp/e1s24.py faceops:/tmp/e1s24.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/e1s24.py 2>&1 | grep -E "^(PUNTO|IIS)"
