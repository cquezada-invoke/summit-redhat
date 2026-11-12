for i in $(seq 1 60); do sudo podman exec faceops test -f /opt/faceops/app/paridad.py 2>/dev/null && break; sleep 20; done
sudo podman exec faceops test -f /opt/faceops/app/paridad.py && echo DESPLEGADO || echo SIN_DESPLIEGUE
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -vi warn | tail -40
from app import create_app
from app.bd import q, q1
app = create_app()
with app.test_request_context():
    from app.paridad import evaluar, por_maquina
    from app.arquitectura import vigentes
    from app.inventario import segmentos_de, skills_de
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); cid = cl["id"]
    segs = q("SELECT * FROM segmentos WHERE cliente_id=? ORDER BY nombre", cid)
    eqs = [dict(e) for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid)]
    for e in eqs:
        e["segmentos"] = segmentos_de(e, segs); e["skills"] = skills_de(e)
    arqs = vigentes(cid)
    for s in segs:
        r = evaluar(cid, s["nombre"], eqs, arqs.get(s["nombre"]))
        prod = [f"{f['componente']}={f['n']}" for f in r["produccion"] if not f["ok"]]
        prev = {a: len(v) for a, v in r["previos"].items()}
        print("PAR", "CUMPLE" if r["cumple"] else "NO", "|", s["nombre"], "| sin_prod" if r["sin_productivo"] else "", "| prod<2:", prod, "| previos:", prev, "| ocp:", len(r["ocp"]))
    pm = por_maquina(cid); print("D3", sum(1 for v in pm.values() if v[0]), "/", len(pm))
Q
