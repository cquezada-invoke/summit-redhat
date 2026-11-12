#!/bin/bash
# Mide (sin secretos) cuánto tarda crear y aprobar un ticket de una acción de solo lectura certificada.
cat > /tmp/medir_cola.py <<'PY'
import time, json
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q, q1
    from app.operacion import solicitar_en_equipo, resolver_aprobacion, actualizar_tickets
    from app.pruebas import estado
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); u = q1("SELECT * FROM usuarios WHERE id=1")
    cert = [a["nombre_aap"] for a in q("SELECT * FROM automatizaciones WHERE cliente_id=?", cl["id"]) if estado(dict(a, cliente_id=cl["id"]))["estado"] == "certificada"]
    print("CERTIFICADAS", len(cert), "|", " ; ".join(sorted(cert))[:3000])
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], "Plataforma · Medir salud")
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='nodo-storage'", cl["id"])
    t0 = time.time()
    t = solicitar_en_equipo(u, cl, a, dict(e), {"pm_plataforma": "data-lake"}, "Medición de tiempos de la cola de acciones", None, prueba=True)
    dc = time.time() - t0
    print(f"CREAR {dc*1000:.0f} ms · {t['numero']} estado={t['estado']} job={t['aap_job_id']}")
    fin = time.time() + 300; da = None
    while time.time() < fin:
        time.sleep(4)
        try: actualizar_tickets()
        except Exception as x: pass
        x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
        if x["estado"] == "pendiente_aprobacion" and da is None:
            t1 = time.time(); resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)"); da = time.time() - t1
            print(f"APROBAR {da*1000:.0f} ms")
        if x["estado"] in ("exitoso", "fallido", "rechazado", "vencido", "cancelado"):
            break
    x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
    print("FINAL", x["numero"], x["estado"], (x["resultado"] or "")[:160].replace("\n", " "))
PY
sudo podman cp /tmp/medir_cola.py faceops:/tmp/medir_cola.py
timeout 600 sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/medir_cola.py 2>&1 | grep -E "^(CERTIFICADAS|CREAR|APROBAR|FINAL|Traceback|  File|[A-Za-z]+Error)" | grep -v -i -E "token|authorization|password"
sudo podman exec faceops rm -f /tmp/medir_cola.py
cat ~/faceops-autodeploy/estado
