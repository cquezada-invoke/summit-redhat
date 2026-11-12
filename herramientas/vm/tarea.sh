#!/bin/bash
# Perfil de crear un ticket (no se lanza: AAP simulado caído y el trabajo se cancela en la cola). Sin secretos.
cat > /tmp/perfil_cola.py <<'PY'
import time, cProfile, pstats, io
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1, ajuste
    from app.operacion import solicitar_en_equipo
    from app import cola
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); u = q1("SELECT * FROM usuarios WHERE id=1")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], "Plataforma · Medir salud")
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='nodo-tomcat'", cl["id"])
    ajuste("cola_prueba_aap_caido_hasta", str(time.time() + 120))
    for i in range(2):
        pr = cProfile.Profile(); t0 = time.time(); pr.enable()
        t = solicitar_en_equipo(u, cl, a, dict(e), {"pm_plataforma": "redis"}, "Perfil de tiempos de la cola (se cancela sin lanzar)", None, prueba=True)
        pr.disable(); dt = time.time() - t0
        c = q1("SELECT id FROM cola_acciones WHERE ticket_id=? ORDER BY id DESC", t["id"]); cola.cancelar(cl["id"], c["id"], "perfil")
        print(f"CREAR {i+1}: {dt*1000:.0f} ms · {t['numero']}")
        s_ = io.StringIO(); pstats.Stats(pr, stream=s_).sort_stats("cumulative").print_stats(r"/app/|requests/|socket|sqlite|subprocess|ssl", 30)
        print("\n".join("P " + l[:200] for l in s_.getvalue().splitlines()[8:40] if l.strip()))
    ajuste("cola_prueba_aap_caido_hasta", "0")
PY
sudo podman cp /tmp/perfil_cola.py faceops:/tmp/perfil_cola.py
timeout 600 sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/perfil_cola.py 2>&1 | grep -E "^(CREAR|P )|Traceback|Error" | grep -v -i -E "token|authorization|password"
sudo podman exec faceops rm -f /tmp/perfil_cola.py
