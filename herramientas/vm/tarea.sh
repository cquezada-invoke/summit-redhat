cat > /tmp/dbg4.py <<'PY'
from app import create_app
from app.bd import q
create_app()
for a in q("SELECT id, cliente_id, aap_id, nombre_aap, nombre, visible, actualizado FROM automatizaciones WHERE nombre_aap LIKE '%espald%conf%' OR nombre_aap LIKE '%Detectar cambios%'"):
    print(dict(a), [hex(ord(c)) for c in a["nombre_aap"][3:6]])
PY
sudo podman cp /tmp/dbg4.py faceops:/tmp/dbg4.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/dbg4.py 2>&1 | grep -v -i warn
cd ~/summit-redhat && grep -rn "Respaldar configuración" faceops/app faceops/datos | grep -v "habilidades.yml\|planes-prueba" | cut -c1-200
