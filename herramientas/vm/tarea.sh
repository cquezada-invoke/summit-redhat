source ~/summit-redhat/herramientas/pruebas/comun.sh >/dev/null 2>&1
IP=$(ip_de nodo-jboss)
ficha() { sudo podman exec -i -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 - "$1" 2>/dev/null <<'Q' | grep -E "^(TK|FICHA|FIN|ERR)"
import sys, time, re, html
from wsgi import app
acc = sys.argv[1]
c = app.test_client()
with c.session_transaction() as s: s["uid"] = 1; s["csrf"] = "x"
r = c.post("/lab/inventario/6/agente", data={"_csrf": "x", "agente": "node-exporter", "accion": acc}).get_json()
if not r or r.get("error"): print("ERR", r); sys.exit(0)
print("TK", acc, r["numero"], r["estado"])
with app.app_context():
    from app.bd import q1
    from app.operacion import actualizar_tickets, resolver_aprobacion
    u = q1("SELECT * FROM usuarios WHERE id=1")
    h = c.get("/lab/inventario/6").get_data(as_text=True)
    m = re.search(r'data-ag-poll="%s"[^>]*>([^<]*)<' % r["id"], h); print("FICHA durante:", html.unescape(m.group(1)) if m else "sin chip")
    vistos = set(); fin = time.time() + 900
    while time.time() < fin:
        time.sleep(8)
        try: actualizar_tickets()
        except Exception: pass
        x = q1("SELECT * FROM tickets WHERE id=?", r["id"]); k = (x.get("aap_aprobacion_id"), x.get("aprobado_en"))
        if x["estado"] == "pendiente_aprobacion" and k not in vistos:
            vistos.add(k); resolver_aprobacion(u, r["id"], True, "Aprobado por Claude (regla de Cristian)")
        if x["estado"] in ("exitoso", "fallido", "rechazado", "vencido", "cancelado", "error"): break
    x = q1("SELECT numero, estado, resultado FROM tickets WHERE id=?", r["id"])
    print("FIN", acc, x["numero"], x["estado"], (x["resultado"] or "")[:160].replace("\n", " "))
Q
}
echo "antes: $(en $IP 'systemctl is-active node_exporter')"
ficha apagar; echo "tras apagar: $(en $IP 'systemctl is-active node_exporter') · http $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://$IP:9100/metrics)"
ficha encender; echo "tras encender: $(en $IP 'systemctl is-active node_exporter') · http $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://$IP:9100/metrics)"
