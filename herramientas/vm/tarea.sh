cd ~/summit-redhat && git pull -q --ff-only origin main
for i in $(seq 1 60); do c=$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1/api/controller/v2/ping/); [ "$c" = 200 ] && break; sleep 20; done; echo "AAP ping $c"
source herramientas/pruebas/comun.sh
sincronizar
bash herramientas/pruebas/p-imagenes-movilidad.sh 2>&1 | grep -E "^(CASO|CERT|enuso|plan=|git=|RES .*sin respuesta)" | cut -c1-600
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>/dev/null | tail -8
import re
from app import create_app
app = create_app()
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    for u in ("/lab/inventario/29", "/inventario/29"):
        r = c.get(u); h = r.get_data(as_text=True); i = h.find('id="imagenes-movilidad"')
        print(u, "HTTP", r.status_code, "seccion", i > 0)
        if i > 0:
            t = re.sub(r"<[^>]+>", " ", h[i:i+7000]); t = re.sub(r"\s+", " ", t); print(t[:1000]); break
Q
