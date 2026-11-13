#!/bin/bash
sudo podman exec -i -e FACEOPS_VIGIA=0 -w /opt/faceops -e PYTHONPATH=/opt/faceops faceops python3 - 2>/dev/null <<'PY'
import json
from app import create_app
app = create_app()
from app.bd import q, q1
cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
for e in q("SELECT * FROM equipos WHERE cliente_id=? AND forma='fisico' AND estado!='retirado' ORDER BY id", cl["id"]):
    d = json.loads(e["datos"] or "{}")
    print("FIS", e["id"], e["nombre"], "|", e["categoria"], "|", e["so"], "|", e["ip"], "| skills", e["skills"], "| ubic", e["ubicacion"], "| claves", sorted(d)[:40])
cols = [r["name"] for r in q("PRAGMA table_info(infraestructuras)")]
print("INFCOLS", cols)
for i in q("SELECT * FROM infraestructuras WHERE cliente_id=?", cl["id"]):
    x = dict(i); print("INF", x.get("id"), x.get("tipo"), x.get("nombre"), "| ubic", x.get("ubicacion"), "|", {k: str(v)[:80] for k, v in x.items() if k not in ("id","tipo","nombre","ubicacion","cliente_id","credencial","secreto","clave","token","password")})
PY
