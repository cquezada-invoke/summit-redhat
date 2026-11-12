up() { curl -s -o /dev/null -w '%{http_code}' http://192.168.122.1:8110/sesion/valida; }
echo "== FaceOps antes: $(up)"
cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-60
timeout 900 python3 faceops/aap/configurar.py 2>&1 | grep -E "operador|Traceback|Error" | grep -v -i -E "clave|password|token" | tail -5
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops
for i in $(seq 1 30); do sleep 5; [ "$(up)" = 401 ] && break; done; echo "== FaceOps después: $(up)"
source herramientas/pruebas/comun.sh >/dev/null 2>&1; sincronizar
bash herramientas/pruebas/p-operadores.sh recolectar 2>&1 | grep -E "^(R1|CASO|CERT|FIN|EVIDENCIA)" | cut -c1-1500
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -E "^(HTTP|RES|OP|Traceback|  File|\w+Error)"
import re
from app import create_app
app = create_app()
from app.bd import q1
e = q1("SELECT * FROM equipos WHERE id=29")
c = app.test_client()
with c.session_transaction() as s:
    s["uid"] = 1; s["csrf"] = "x"; s["cliente_id"] = e["cliente_id"]
with app.test_request_context():
    from flask import url_for
    u = url_for("consola.equipo", eid=29)
h = c.get(u, follow_redirects=True).get_data(as_text=True)
print("HTTP", u, "agregar" , "Agregar plataforma" in h, "automatizaciones" in h.lower())
with app.app_context():
    from app.operadores_ocp import disponibles, resumen
    cats = disponibles(dict(e), e["cliente_id"])
    print("RES", resumen(cats))
    for c_ in cats:
        for o in c_["operadores"]: print("OP", o["estado"], "|", o["nombre"], "|", o["motivo"][:90], "|", "; ".join(o["avisos"])[:160])
Q
