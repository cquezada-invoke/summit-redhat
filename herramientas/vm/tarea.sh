cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-60
python3 faceops/aap/configurar.py 2>&1 | grep -E "imágenes \(uso|Mover imágenes|Traceback" | head -4
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 15
cd ~/summit-redhat && git log -1 --format=%h
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v -i "warn" | tail -40
import os, json
from app import create_app
from app.bd import q, q1
from flask import g
app = create_app()
print("smtp", bool(os.environ.get("FACEOPS_SMTP_HOST")))
with app.test_request_context():
    from app import sla
    from app import operacion
    c = q1("SELECT * FROM clientes WHERE codigo='lab'"); g.cliente = c
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'") or q1("SELECT * FROM usuarios WHERE es_admin=1 ORDER BY id LIMIT 1")
    g.usuario = u
    seg = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre='App de pagos'", c["id"])
    print("monitor", sla.monitor_de(c["id"], seg))
    if not sla.vigente(c["id"], seg["nombre"]) and not q1("SELECT 1 FROM sla_versiones WHERE cliente_id=? AND segmento=? AND estado='propuesto'", c["id"], seg["nombre"]):
        d = dict(sla.DEFECTO, dueno="aplicaciones", consumidor="Área de Pagos (negocio)", correo_consumidor="cquezada@redhat.com",
                 vigencia_desde="2026-11-01", vigencia_hasta="2027-10-31")
        r = sla.proponer(c, u, seg, d, "SLA de ejemplo para la demo (99,5 % mensual, p95 800 ms, P1–P4, aprobación ≤ 24 h)", demo=True)
        print("propuesto", r)
        operacion.resolver_aprobacion(u, r["ticket_id"], True, "Aprobado por Claude (regla de Cristian)")
    v = sla.vigente(c["id"], seg["nombre"])
    print("vigente", v and (v["version"], v["estado"], v["ticket_numero"], v["aprobado_por"], v["comentario"], v["demo"]))
    print("ticket", q1("SELECT numero, estado, resultado FROM tickets WHERE id=?", v["ticket_id"]))
    r = sla.evaluar(c, seg["nombre"], forzar=True)
    print("estado", r["estado"], "cump", r["cumplimiento"], "consumo", r["consumo"], "parcial", r.get("parcial"), "tickets", r["n_tickets"])
    for o in r["objetivos"]:
        print(" -", o["nombre"], o["estado"], o["valor"], "/", o["objetivo"], "|", (o.get("detalle") or "")[:150])
    print("violaciones", len(r["violaciones"]), [x["texto"][:90] for x in r["violaciones"][:5]])
    if not q1("SELECT 1 FROM sla_envios WHERE cliente_id=? AND tipo='prueba'", c["id"]):
        print("envio", sla.enviar(c, "prueba", "Claude (regla de Cristian)"))
    print("envios", q("SELECT fecha, tipo, para, asunto, resultado FROM sla_envios WHERE cliente_id=?", c["id"]))
    print("programa", sla.programa(c["id"]))
    print("itil", sla.itil_sla(c["id"]))
    print("salud", sla.salud_items(c, seg["nombre"]))
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = u["id"]; s["csrf"] = "x"; s["cliente_id"] = c["id"]
    for p in ("/gobierno/sla", f"/gobierno/sla?servicio={seg['id']}", "/gobierno/reportes/sla", "/gobierno/reportes", "/monitoreo/itil", "/mapa?vista=aplicaciones&segmento=App+de+pagos"):
        r_ = cl.get(p); print(p, r_.status_code)
    import re
    h = cl.get("/monitoreo/itil").get_data(as_text=True); i = h.find("Gestión de niveles de servicio")
    print(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h[i:i+1200]))[:500])
Q
for qq in 'count by (faceops_equipo, faceops_rol) (probe_success{job="faceops-segmentos"})'; do
 curl -s -G http://127.0.0.1:9090/api/v1/query --data-urlencode "query=$qq" | head -c 800; echo; done
