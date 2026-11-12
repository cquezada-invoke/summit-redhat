cd ~/summit-redhat
echo "== prometheus jobs"; sudo grep -E "job_name|targets|textfile" /opt/summit-monitoreo/prometheus/prometheus.yml | head -40
ls /var/lib/node_exporter/textfile 2>&1 | head; systemctl cat node_exporter 2>/dev/null | grep ExecStart
sudo ss -ltnp 2>/dev/null | grep -E ":(5000|8000|8080|9090|9100|9115|5022) " | awk '{print $4, $6}' | cut -c1-90
sudo podman inspect faceops --format '{{.HostConfig.NetworkMode}} {{.Config.Env}}' 2>/dev/null | tr ' ' '\n' | grep -iE "^host|PORT|BIND|network" | head
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v Warn | tail -200
from app import create_app
from flask import session
app = create_app()
with app.test_request_context("/faceops/lab/plataformas", headers={"X-Requested-With":"fetch"}):
    from app import bd
    session["uid"] = 1; session["csrf"] = "x"; session["cliente_id"] = bd.q1("SELECT id FROM clientes WHERE lower(codigo)='lab'")["id"]
    app.preprocess_request()
    from flask import g
    from app.pruebas import estado, exceptuada
    for a in bd.q("SELECT * FROM automatizaciones WHERE cliente_id=? ORDER BY nombre_aap", g.cliente["id"]):
        try: ep = estado(dict(a, cliente_id=g.cliente["id"]))["estado"]
        except Exception as x: ep = "?"
        print(f"{ep[:4]}{'+X' if exceptuada(a['nombre_aap']) else '  '} | {a['nombre_aap']}")
Q
