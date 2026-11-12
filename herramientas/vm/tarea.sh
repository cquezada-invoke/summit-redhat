sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | tail -150
import json, re
from app import create_app
from app.bd import q, q1, j
app = create_app()
with app.app_context():
    tabs = [r["name"] for r in q("SELECT name FROM sqlite_master WHERE type='table'")]
    print("TABLAS", " ".join(tabs))
    for c in q("SELECT id,nombre FROM clientes"): print("CLI", c["id"], c["nombre"])
    cid = 1
    from app.infraestructuras import lista
    eqs = q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid)
    for x in lista(cid, eqs):
        print("INF", x["id"], x["tipo"], "|", x["nombre"], "| centro=", x["centro"], "| ubi=", x["ubicacion"], "| det=", list(x["detalle_d"].keys())[:12])
    for e in eqs:
        d = j(e["datos"], {})
        print("EQ", e["id"], e["nombre"], "| ubi=", e["ubicacion"], "| ip=", e["ip"], "| cat=", d.get("categoria"), "| dk=", [k for k in d.keys()][:25])
    for t in ("replicas_maquina", "edge_grupos", "edge_sitios"):
        if t in tabs:
            for r in q(f"SELECT * FROM {t}"): print(t.upper(), json.dumps(dict(r), default=str)[:300])
    cl = q1("SELECT clasificacion FROM clientes WHERE id=?", cid)
    topo = j(cl["clasificacion"], {}).get("_topologia", {})
    print("TOPO keys", list(topo.keys()))
    for k in ("enlaces", "flujos"):
        for f in (topo.get(k) or [])[:30]: print("TOPO", k, json.dumps(f)[:250])
    for a in q("SELECT segmento, estado, contenido FROM arquitecturas WHERE cliente_id=? AND estado='aprobada'", cid):
        print("ARQ", a["segmento"], a["contenido"][:600].replace("\n", " / "))
    for s in q("SELECT nombre, tipo, regla FROM segmentos WHERE cliente_id=?", cid): print("SEG", s["nombre"], s["tipo"], s["regla"][:150])
Q
