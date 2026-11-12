#!/bin/bash
# Cierra los tickets de prueba fallidos del plan de parches (motivo «Plan de prueba …»): quedan cancelados y marcados como prueba.
cat > /tmp/p3009.py <<'PY'
from app import create_app
from app.bd import ahora, auditar, ej, q, q1
create_app()
cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
a = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Seguridad · Aplicar parches de seguridad'", cl["id"])
ts = q("SELECT id, numero, estado, resultado FROM tickets WHERE cliente_id=? AND automatizacion_id=? AND motivo LIKE 'Plan de prueba%' "
       "AND estado IN ('fallido','error') ORDER BY id", cl["id"], a["id"])
for t in ts:
    ej("UPDATE tickets SET estado='cancelado', resultado=? WHERE id=?",
       "Prueba (regla de prueba) · cerrado por Claude: corrida del plan con nodo-so sin acceso a los repositorios de Red Hat; no cuenta como uso\n" + (t["resultado"] or ""), t["id"])
    ej("UPDATE ticket_etapas SET estado='cancelada', fin=? WHERE ticket_id=? AND estado='pendiente'", ahora(), t["id"])
    auditar("Claude (regla de Cristian)", cl["id"], "cerrar ticket de prueba", t["numero"], {"antes": t["estado"]})
print("cerrados:", len(ts), ts[0]["numero"] if ts else "", "…", ts[-1]["numero"] if ts else "")
PY
sudo podman cp /tmp/p3009.py faceops:/tmp/p3009.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3009.py 2>&1 | grep -v -i warn | tail -5; sudo podman exec faceops rm -f /tmp/p3009.py
