"""Certifica (o registra como rechazada) una automatización con los casos ejecutados.
  sudo podman exec -i faceops python3 - '<nombre AAP>' '<plan>' '<json casos>' '<json tickets>' < certificar.py"""
import json
import sys

from app import create_app

app = create_app()
with app.app_context():
    from app.bd import q1
    from app.pruebas import certificar, estado
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
    n, plan, casos, tks = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), json.loads(sys.argv[4])
    ok = bool(casos) and all(c.get("resultado") == "ok" for c in casos)
    certificar(cl["id"], n, plan, casos, tks, "aprobada" if ok else "rechazada", "Claude (regla de Cristian)")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n)
    print("CERT", n, "→", estado(dict(a))["estado"], flush=True)
