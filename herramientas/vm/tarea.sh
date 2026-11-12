sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v Warn | tail -80
import json
from app import create_app
from app.bd import q, q1, j
app = create_app()
with app.app_context():
    cid = 1
    cl = q1("SELECT clasificacion FROM clientes WHERE id=?", cid)
    topo = j(cl["clasificacion"], {}).get("_topologia", {})
    for k in ("centrales", "redes"):
        for f in (topo.get(k) or []): print("TOPO", k, json.dumps(f, ensure_ascii=False)[:260])
    for a in q("SELECT accion, objeto, detalle, fecha FROM auditoria WHERE cliente_id=? AND (accion LIKE '%traslad%' OR accion LIKE '%alta disponib%' OR accion LIKE '%vpn%' OR accion LIKE '%respaldo%') ORDER BY id DESC LIMIT 12", cid):
        print("AUD", a["fecha"], a["accion"], a["objeto"], (a["detalle"] or "")[:220])
    for t in q("SELECT t.numero, t.estado, t.limite, substr(t.parametros,1,260) p, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND (a.nombre_aap LIKE '%Mover%' OR a.nombre_aap LIKE '%VPN%' OR a.nombre_aap LIKE '%Replicar%' OR a.nombre_aap LIKE '%alta disponibilidad%' OR a.nombre_aap LIKE '%restic%' OR a.nombre_aap LIKE '%S3%') ORDER BY t.id DESC LIMIT 14", cid):
        print("TK", t["numero"], t["estado"], t["nombre_aap"], t["limite"], t["p"])
    for x in q("SELECT id, nombre, detalle FROM infraestructuras WHERE cliente_id=?", cid):
        d = j(x["detalle"], {}); d = {k: v for k, v in d.items() if not any(s in k.lower() for s in ("clave", "token", "pass", "secret", "usuario", "llave"))}
        print("INFD", x["id"], x["nombre"], json.dumps(d, ensure_ascii=False)[:300])
    for n in ("srv-windows-iis-01", "fw", "rtr", "lb-sede-01", "nodo-nginx", "nodo-postgres"):
        e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cid, n)
        if e:
            d = j(e["datos"], {})
            print("EQD", n, "red=", json.dumps(d.get("red"), ensure_ascii=False)[:400], "| comp=", json.dumps(d.get("componentes_internos"), ensure_ascii=False)[:200])
    for e in q("SELECT nombre, datos FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid):
        d = j(e["datos"], {})
        for k in ("vpn", "respaldo_datos", "ha", "balanceo", "replicacion", "always_on", "sqlserver"):
            if d.get(k): print("EXTRA", e["nombre"], k, json.dumps(d[k], ensure_ascii=False)[:250])
Q
