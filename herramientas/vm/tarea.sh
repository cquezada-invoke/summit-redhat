cat > /tmp/dbg3.py <<'PY'
import traceback
from app import create_app
from app.bd import q1
a = create_app()
with a.test_request_context("/"):
    from flask import g
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada' AND es_admin=1"); cli = q1("SELECT * FROM clientes WHERE codigo='lab'")
    g.usuario, g.cliente = u, cli
    from app.operacion import solicitar_en_equipo
    import app.operacion as O
    au = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Red · Respaldar configuración'", cli["id"])
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='rtr-wan-01'", cli["id"])
    orig = O.solicitar
    def falso(*a, **k):   # no lanza: solo muestra hasta dónde llega
        raise RuntimeError("STOP antes de crear el ticket")
    try:
        solicitar_en_equipo(u, cli, au, e, {}, "diag", prueba=True)
    except Exception:
        traceback.print_exc()
PY
sudo podman cp /tmp/dbg3.py faceops:/tmp/dbg3.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/dbg3.py 2>&1 | grep -v -i warn | tail -25
