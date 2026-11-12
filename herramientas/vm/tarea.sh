cat > /tmp/d.py <<'PY'
from app import create_app
from app.bd import q, q1
a = create_app()
tabs = [r["name"] for r in q("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'audit%'")]
print("tablas", tabs)
for t in tabs:
    for r in q(f"SELECT * FROM {t} WHERE (accion LIKE '%inventario autom%' OR accion LIKE '%vinculaci%') ORDER BY rowid DESC LIMIT 6"):
        print(dict(r))
for r in q("SELECT t.numero, t.estado, t.limite, t.motivo, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.limite LIKE 'fo-prueba-1112%' OR t.motivo LIKE '%FO-LAB-02246%' ORDER BY t.id"):
    print(dict(r))
from app import pruebas
for n in ("Sistema · Inventariar máquina", "FaceOps · Vincular máquina a la bóveda"):
    au = q1("SELECT * FROM automatizaciones WHERE nombre_aap=? AND cliente_id=(SELECT id FROM clientes WHERE codigo='lab')", n)
    print(n, au and pruebas.estado(dict(au))["estado"])
PY
for i in $(seq 1 20); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done
sudo podman cp /tmp/d.py faceops:/tmp/d.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/d.py 2>&1 | grep -v "Warning\|warnings.warn" | cut -c1-500 | tail -30
