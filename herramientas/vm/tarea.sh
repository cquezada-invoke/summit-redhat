#!/bin/bash
# Lectura: espera el despliegue con «parametros_parche» y lista candidatas para el plan de prueba de parches. Sin secretos.
for i in $(seq 1 90); do sudo podman exec faceops grep -q parametros_parche /opt/faceops/app/cves.py 2>/dev/null && break; sleep 10; done
sudo podman exec faceops grep -c parametros_parche /opt/faceops/app/cves.py
cat > /tmp/p3002.py <<'PY'
from app import create_app
from app.bd import q, q1, j
create_app()
from app import pruebas
from app.cves import pendientes_parche
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); cid = cl["id"]
a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Seguridad · Aplicar parches de seguridad'", cid)
print("ESTADO", pruebas.estado(dict(a, cliente_id=cid))["estado"], "campos=", [c["var"] for c in j(a["campos"], [])])
for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' AND (so LIKE 'RedHat%' OR so LIKE 'Red Hat%' OR so LIKE 'RHEL%') ORDER BY nombre", cid):
    d = j(e["datos"], {})
    rev, p = pendientes_parche(cid, dict(e))
    print("EQ", e["id"], e["nombre"], e["so"], e["ambiente"] or "-", "energia=", d.get("energia") or e["estado"], "rev=", rev, "pend=", len(p), "cpu=", e["cpu"], "ram=", e["ram_mb"])
PY
sudo podman cp /tmp/p3002.py faceops:/tmp/p3002.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3002.py 2>&1 | grep -v -i -E "warn|token|password|secret" | tail -45; sudo podman exec faceops rm -f /tmp/p3002.py
echo "== VMs"; sudo virsh list --all 2>/dev/null | head -40
