cat > /tmp/cert.py <<'PY'
from app import create_app
from app.bd import q1
create_app()
from app import pruebas as P
cli = q1("SELECT * FROM clientes WHERE codigo='lab'")
R = [{"id": "C1", "resultado": "ok", "nota": "FO-LAB-02113: fw.cfg 61 líneas set, 1 secreto enmascarado, 0 en claro"},
     {"id": "C2", "resultado": "ok", "nota": "FO-LAB-02117 rtr-wan-01 (router bgp) · FO-LAB-02120 sw-core-01 (tabla de VLAN)"},
     {"id": "C3", "resultado": "ok", "nota": "FO-LAB-02122 lb-sede-01: frontend y backend"},
     {"id": "C4", "resultado": "ok", "nota": "FO-LAB-02124 sin cambios: commits de fw.cfg 1 → 1"},
     {"id": "C5", "resultado": "ok", "nota": "FO-LAB-02127 fallido en la lectura (suc1 no responde por SSH); no se creó suc1.cfg"}]
D = [{"id": "C1", "resultado": "ok", "nota": "FO-LAB-02129 sin cambios"},
     {"id": "C2", "resultado": "ok", "nota": "FO-LAB-02130 CAMBIOS +1/-0 (ip route quitada de la base)"},
     {"id": "C3", "resultado": "ok", "nota": "FO-LAB-02132 fw y FO-LAB-02133 lb-sede-01 sin cambios"},
     {"id": "C4", "resultado": "ok", "nota": "FO-LAB-02134 nuevo respaldo y FO-LAB-02135 sin cambios"},
     {"id": "C5", "resultado": "ok", "nota": "FO-LAB-02136 fallido «no tiene línea base»; Git sin sw-acc-01.cfg"}]
P.certificar(cli["id"], "Red · Respaldar configuración", "datos/planes-prueba.yml", R, ["FO-LAB-02113", "FO-LAB-02117", "FO-LAB-02120", "FO-LAB-02122", "FO-LAB-02124", "FO-LAB-02127"], "aprobada", "Claude (regla de Cristian)", "plan completo")
P.certificar(cli["id"], "Red · Detectar cambios de configuración", "datos/planes-prueba.yml", D, ["FO-LAB-02129", "FO-LAB-02130", "FO-LAB-02132", "FO-LAB-02133", "FO-LAB-02134", "FO-LAB-02135", "FO-LAB-02136"], "aprobada", "Claude (regla de Cristian)", "plan completo")
for n in ("Red · Respaldar configuración", "Red · Detectar cambios de configuración"):
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cli["id"], n)
    print(n, P.estado(dict(a, cliente_id=cli["id"]))["estado"])
PY
sudo podman cp /tmp/cert.py faceops:/tmp/cert.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/cert.py 2>&1 | grep -v -i warn | tail -5
