#!/bin/bash
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>&1 <<'Q' | grep -E "^(T|Q|P|H)" | grep -v -i -E "token|authorization|password|secret"
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q, q1, ajuste
    for t in q("SELECT t.id, t.numero, t.estado, t.aap_job_id, t.aap_job_tipo, substr(replace(t.resultado, char(10), ' '),1,260) r, a.nombre_aap n, t.creado FROM tickets t LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id ORDER BY t.id DESC LIMIT 14"):
        print("T", t)
    for c in q("SELECT id, ticket_id, tipo, estado, intentos, esperas, substr(motivo,1,200) m, job_id, creado FROM cola_acciones ORDER BY id DESC LIMIT 14"):
        print("Q", c)
    print("Q aap", ajuste("cola_aap_1"), "simulado", ajuste("cola_prueba_aap_caido_hasta"))
    from app.pruebas import estado
    for n in ("Plataforma · Medir salud", "Plataforma · Programar medición", "OpenShift · Relevamiento de imágenes"):
        a = q1("SELECT * FROM automatizaciones WHERE nombre_aap=? ORDER BY cliente_id LIMIT 1", n); print("P", n, estado(dict(a))["estado"])
    for r in q("SELECT * FROM pruebas_automatizacion ORDER BY rowid DESC LIMIT 0"): pass
Q
sudo podman logs --since 70m faceops 2>&1 | grep -E "^cola|Traceback" | tail -20 | sed 's/^/H /'
cd ~/summit-redhat && git log --since="3 hours ago" --format='H %h %s' -- faceops/app/pruebas.py faceops/datos/planes-prueba.yml | head
