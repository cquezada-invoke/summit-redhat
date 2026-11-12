#!/bin/bash
# Lectura: última certificación de las acciones del núcleo pendientes y máquinas de prueba disponibles. Sin secretos.
cat > /tmp/n7.py <<'PY'
import json
from app import create_app
from app.bd import q, q1
create_app()
for n in ("Sistema · Inventariar máquina", "Sistema · Paquetes · inventario y respaldo", "FaceOps · Vincular máquina on-premise"):
    for r in q("SELECT id, fecha, resultado, huella, plan, tickets, nota FROM pruebas_automatizacion WHERE cliente_id=1 AND nombre_aap=? ORDER BY id DESC LIMIT 2", n):
        print("CERT", n, "|", r["fecha"], r["resultado"], r["huella"], "| plan:", (r["plan"] or "")[:60], "| tickets:", (r["tickets"] or "")[:200], "| nota:", (r["nota"] or "")[:120])
cols = [c["name"] for c in q("PRAGMA table_info(pruebas_automatizacion)")]; print("cols", cols)
for e in q("SELECT nombre, energia, estado, so FROM equipos WHERE cliente_id=1 AND COALESCE(estado,'')!='retirado' AND (nombre LIKE 'srv-prueba%' OR nombre LIKE 'srv-pn%' OR nombre LIKE 'srv-legado%' OR nombre LIKE 'fo-prueba%' OR so LIKE '%5.%' OR so LIKE '%6.%')"):
    print("EQ", e["nombre"], e["energia"], e["estado"], (e["so"] or "")[:40])
PY
sudo podman cp /tmp/n7.py faceops:/tmp/n7.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/n7.py 2>&1 | grep -v -i -E "warn|token|secret" | tail -30; sudo podman exec faceops rm -f /tmp/n7.py
