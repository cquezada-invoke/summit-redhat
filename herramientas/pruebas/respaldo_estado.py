"""Estado de FaceOps de una máquina para el plan de «Recuperación · Respaldar volúmenes con restic» (solo lectura):
  sudo podman exec -i faceops python3 - <equipo> < respaldo_estado.py → «RD {json}». No imprime secretos: de la bóveda
  solo las llaves guardadas y un prefijo del sha256 de la clave (para compararlo con el de la máquina)."""
import hashlib
import json
import sys

from app import create_app

app = create_app()
with app.test_request_context():
    from flask import g
    from app.bd import j, q1
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
    g.cliente = cl
    e = dict(q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cl["id"], sys.argv[1]))
    d = j(e["datos"], {})
    out = {"respaldo": d.get("respaldo_datos")}
    try:
        from app.puntos import lista as _pr_lista
        from app.vistas import _radar_maquina
        prs = _pr_lista(cl["id"], e["id"])
        out["punto"] = prs[0]["codigo"] if prs else ""
        out["vols"] = {v["montaje"]: v["nivel"] for v in ((prs[0]["f"].get("datos") or {}).get("volumenes") or [])} if prs else {}
        r = _radar_maquina(e, [], prs)
        p = next((x for x in r.get("principios") or [] if x["nombre"] == "Recuperación"), {})
        out["rec"] = p.get("pct")
        out["falta"] = p.get("falta") or []
    except Exception as x:  # noqa: BLE001
        out["error"] = str(x)[:300]
    try:
        from app.boveda import leer
        b = leer(f"web/{e['nombre']}/restic") or {}
        out["boveda"] = {"llaves": sorted(b), "clave": hashlib.sha256(str(b.get("clave", "")).encode()).hexdigest()[:12] if b.get("clave") else "",
                         "repositorio": b.get("repositorio", "")}
    except Exception as x:  # noqa: BLE001
        out["boveda"] = {"error": str(x)[:120]}
    print("RD", json.dumps(out, ensure_ascii=False, default=str), flush=True)
