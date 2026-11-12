#!/bin/bash
# Lectura: tickets del plan de parches en nodo-so y estado de la regla de prueba. Sin secretos.
cat > /tmp/p3005.py <<'PY'
from app import create_app
from app.bd import q, q1
create_app()
from app import pruebas
cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Seguridad · Aplicar parches de seguridad'", cl["id"])
print("ESTADO", pruebas.estado(dict(a, cliente_id=cl["id"]))["estado"])
for t in q("SELECT numero, estado, creado, motivo, parametros, resultado FROM tickets WHERE cliente_id=? AND automatizacion_id=? AND motivo LIKE 'Plan de prueba%' ORDER BY id", cl["id"], a["id"]):
    print(t["numero"], t["estado"], t["creado"][11:19], t["motivo"], "|", " | ".join((t["resultado"] or "").strip().splitlines()[-2:])[:220])
PY
sudo podman cp /tmp/p3005.py faceops:/tmp/p3005.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3005.py 2>&1 | grep -v -i -E "warn|token|password|secret" | tail -20; sudo podman exec faceops rm -f /tmp/p3005.py
ps -eo etimes,cmd | grep -E "p3004|tarea.sh" | grep -v grep | cut -c1-120
