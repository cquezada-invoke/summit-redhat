cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-60
python3 faceops/aap/configurar.py 2>&1 | grep -E "Traceback" | head -4
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 15
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v -i warn | tail -8
from app import create_app
from app.bd import q, q1
app = create_app()
with app.app_context():
    from flask import g
    from app import reportes as R
    g.cliente = q1("SELECT * FROM clientes WHERE codigo='lab'"); R.calcular(g.cliente)
    ini = q("SELECT estado, COUNT(*) n FROM reporte_iniciativas WHERE cliente_id=? GROUP BY 1", g.cliente["id"])
    print("iniciativas:", ini, "areas:", q1("SELECT COUNT(DISTINCT seccion) n FROM reporte_iniciativas WHERE cliente_id=?", g.cliente["id"])["n"])
    print("envios:", q("SELECT seccion, para, resultado FROM reporte_envios WHERE cliente_id=?", g.cliente["id"]))
Q
