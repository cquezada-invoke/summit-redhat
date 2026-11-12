set -a; . ~/faceops/faceops.env; set +a
GT=$(grep '^gitea_token=' ~/demo-drift/cicd-secretos.txt | cut -d= -f2-)
G=http://127.0.0.1:3000/api/v1/repos/invoke/red-laboratorio
cat > /tmp/rt.py <<'PY'
import sys, time
from app import create_app
from app.bd import q1
a = create_app()
APR = "Aprobado por Claude (regla de Cristian)"; FIN = ("exitoso", "fallido", "cancelado", "rechazado", "error")
nom, eq, et = sys.argv[1], sys.argv[2], sys.argv[3]
with a.test_request_context("/"):
    from flask import g
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada' AND es_admin=1"); cli = q1("SELECT * FROM clientes WHERE codigo='lab'")
    g.usuario, g.cliente = u, cli
    from app.operacion import solicitar_en_equipo, resolver_aprobacion
    au = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cli["id"], nom)
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cli["id"], eq)
    try:
        t = solicitar_en_equipo(u, cli, au, e, {}, f"Plan de prueba · {nom} · {et}", prueba=True)
    except Exception as x:
        print("RES - no-lanzado", str(x)[:200]); sys.exit()
    ok = False
    for _ in range(90):
        tk = q1("SELECT numero, estado, aap_job_id, resultado FROM tickets WHERE id=?", t["id"])
        if tk["estado"] == "pendiente_aprobacion" and not ok:
            resolver_aprobacion(u, t["id"], True, APR); ok = True
        if tk["estado"] in FIN: break
        time.sleep(8)
    print("RES", tk["numero"], tk["estado"], tk["aap_job_id"] or 0, (tk["resultado"] or "")[:200].replace("\n", " "))
PY
sudo podman cp /tmp/rt.py faceops:/tmp/rt.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/rt.py "Red · Respaldar configuración" fw dbg 2>&1 | grep -v -i warn | tail -15
curl -s -o /dev/null -w "gitea %{http_code}\n" -H "Authorization: token $GT" "$G"
