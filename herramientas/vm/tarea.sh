cat > /tmp/redver.py <<'PY'
import re, time
from app import create_app
from app.bd import q, q1
app = create_app()
with app.test_request_context("/"):
    from flask import g
    from app.operacion import sincronizar_catalogo
    cli = q1("SELECT * FROM clientes WHERE codigo='lab'")
    sincronizar_catalogo(cli)
    for a in q("SELECT id, nombre_aap, nombre, visible, usa_limite, inventario, requiere_aprobacion FROM automatizaciones WHERE cliente_id=? AND nombre_aap LIKE 'Red · %'", cli["id"]):
        print("AUTO", dict(a))
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    for eid in (14, 16, 17, 121, 122, 123, 124, 125):
        h = c.get(f"/faceops/lab/inventario/{eid}").get_data(as_text=True)
        i = h.find('id="hb-dlg"'); k = h.find("</dialog>", i)
        filas = re.findall(r'<tr class="hb-it( bloq)?".*?<b title="([^"]+)".*?class="hb-amb">([^<]*)<.*?(?:Bloqueada: ([^<"]*))?</td>', h[i:k], re.S)
        print(f"== {eid} skills={len(filas)} habilitadas={sum(1 for f in filas if not f[0])}")
        for f in filas:
            print("   ", "OFF" if f[0] else "ON ", f[1], "|", f[2], "|", (f[3] or "")[:70])
PY
sudo podman cp /tmp/redver.py faceops:/tmp/redver.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/redver.py 2>&1 | grep -v -i warn | cut -c1-220
