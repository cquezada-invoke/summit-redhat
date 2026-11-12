cd ~/summit-redhat && git pull -q --ff-only origin main
bash herramientas/pruebas/p-migraciones-so.sh limpiar 2>&1 | grep -E "^(  \(arnés|eliminar|FIN)"
for v in srv-mig-rocky-01 srv-mig-rhel8-01 nodo-jboss-qa nodo-jboss3; do echo "virsh $v: $(sudo virsh domstate $v 2>&1 | head -1) · snapshots $(sudo virsh snapshot-list $v --name 2>/dev/null | grep -c .)"; done
cat > /tmp/m.py <<'PY'
import re
from app import create_app
from app.bd import q1
a = create_app()
u = q1("SELECT * FROM usuarios WHERE id=1")
c = a.test_client()
with c.session_transaction() as s: s["uid"] = u["id"]
for ruta in ("/lab/sistemas-operativos/modernizacion", "/lab/arquitectura/modernizacion"):
    r = c.get(ruta); t = r.get_data(as_text=True)
    print("PAGINA", ruta, r.status_code)
    for m in re.finditer(r'<div class="auto auto-hub([^"]*)"', t):
        trozo = t[m.end(): m.end() + 1500]
        nom = re.search(r'<a class="t"[^>]*>([^<]+)<', trozo)
        print("  ", "BLOQUEADA" if "bloqueada-pr" in m.group(1) else "habilitada", "|", (nom.group(1).strip() if nom else trozo[:120].replace("\n", " ")))
PY
sudo podman cp /tmp/m.py faceops:/tmp/m.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/m.py 2>&1 | grep -v -i "warn" | tail -25; sudo podman exec faceops rm -f /tmp/m.py
sudo podman exec -i faceops python3 - <<'Q' 2>&1 | grep -v -i warn | tail -12
from app import create_app
create_app()
from app.bd import q1
from app import pruebas
for n in ("Migración · Evaluar sistema operativo","Migración · CentOS a RHEL","Migración · RHEL a versión soportada","Migración · JBoss a EAP 8"):
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=1 AND nombre_aap=?", n); e = pruebas.estado(dict(a)); print(n, "→", e["estado"], e["huella"], (e["ultima"] or {}).get("fecha","")[:16])
Q
