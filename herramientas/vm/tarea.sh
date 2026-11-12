#!/bin/bash
# Tiempo «antes» de la cola: mismo ticket por el camino síncrono anterior (lanzar en AAP dentro de la petición y responder
# la aprobación en AAP en la misma petición). Sin secretos.
cat > /tmp/cola_antes.py <<'PY'
import time
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app import operacion, cola
    from app.aap import AAP
    operacion.ejecutar = lambda tid, sec=None, apr=False: operacion.lanzar_ahora(tid, sec, apr)   # camino anterior
    def encolar_sincrono(tid, tipo="lanzar", datos=None, secretos=None, usuario=""):
        if tipo == "aprobar":
            try: AAP.de_cliente(cl).responder_aprobacion(datos["aprobacion"], bool(datos.get("aprobar")))
            except Exception as x: print("resp", str(x)[:80])
    cola.encolar = encolar_sincrono
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); u = q1("SELECT * FROM usuarios WHERE id=1")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], "Plataforma · Medir salud")
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='nodo-storage'", cl["id"])
    for n in range(2):
        t0 = time.time()
        t = operacion.solicitar_en_equipo(u, cl, a, dict(e), {"pm_plataforma": "data-lake"}, "Medición de tiempos: camino anterior sin cola", None)
        print(f"ANTES CREAR {(time.time()-t0)*1000:.0f} ms · {t['numero']} job={t['aap_job_id']}", flush=True)
        fin = time.time() + 400; da = None
        while time.time() < fin:
            time.sleep(3)
            try: operacion.actualizar_tickets()
            except Exception: pass
            x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
            if x["estado"] == "pendiente_aprobacion" and da is None:
                t1 = time.time(); operacion.resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)"); da = time.time() - t1
                print(f"ANTES APROBAR {da*1000:.0f} ms", flush=True)
            if x["estado"] in ("exitoso", "fallido", "rechazado", "vencido", "cancelado"): break
        print("ANTES FINAL", x["numero"], x["estado"], flush=True)
PY
sudo podman cp /tmp/cola_antes.py faceops:/tmp/cola_antes.py
timeout 1200 sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/cola_antes.py 2>&1 | grep -E "^(ANTES|resp)|Traceback|Error" | grep -v -i -E "token|authorization|password"
sudo podman exec faceops rm -f /tmp/cola_antes.py
