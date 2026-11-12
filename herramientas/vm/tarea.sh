sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | tail -60
import json
from app import create_app
from app.bd import q, q1
app = create_app()
with app.app_context():
    for c in q("SELECT id, codigo, nombre FROM clientes WHERE activo=1"):
        print("CLIENTE", c)
        for s in q("SELECT id, nombre, grupo, area, substr(datos,1,300) d FROM segmentos WHERE cliente_id=?", c["id"]):
            n = q1("SELECT COUNT(*) n, SUM(estado='exitoso') ok, MIN(creado) mi FROM tickets WHERE cliente_id=? AND segmento=?", c["id"], s["nombre"])
            print(" SEG", s["id"], s["nombre"], s["grupo"], s["area"], n)
    from app.segmento_ops import objeto
    s = q1("SELECT * FROM segmentos WHERE nombre LIKE '%pagos%'")
    print("PAGOS", s and objeto(s["cliente_id"], s, [])["nombre"], s and s["datos"][:600])
    print(q("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'reporte%'"))
    print(q("PRAGMA table_info(tickets)")[-12:])
    print(q("SELECT * FROM ticket_etapas ORDER BY ticket_id DESC LIMIT 2"))
    print(q("SELECT id, usuario, correo, es_admin FROM usuarios WHERE es_admin=1 LIMIT 5"))
    from app.grupos_dueno import grupos
    print([(x["clave"], x.get("correo")) for x in grupos(1)][:20])
Q
for qq in 'count by (faceops_equipo, faceops_rol) (probe_success{job="faceops-segmentos"})' 'avg_over_time(probe_success{job="faceops-segmentos",faceops_rol="vip"}[30d])' 'ALERTS{faceops_alerta=~"G.*"}'; do
 curl -s -G http://127.0.0.1:9090/api/v1/query --data-urlencode "query=$qq" | head -c 1500; echo; done
curl -s -G http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=min_over_time(timestamp(probe_success{job="faceops-segmentos",faceops_rol="vip"})[30d:1h])' | head -c 600; echo
grep -n "SMTP\|ZONA\|TZ" /etc/faceops/*.env 2>/dev/null | sed 's/=.*/=.../' | head
