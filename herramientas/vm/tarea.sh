cd ~/summit-redhat
for i in $(seq 1 100); do git pull -q --ff-only origin main 2>/dev/null; sudo podman exec faceops grep -q "PROGRAMAR =" /opt/faceops/app/ficha_plataforma.py 2>/dev/null && break; sleep 15; done
echo "desplegado: $(sudo podman exec faceops grep -c 'PROGRAMAR =' /opt/faceops/app/ficha_plataforma.py)"
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>/dev/null | tail -2
from app import create_app
create_app()
from app.bd import q1
from app.aap import AAP
cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); print("cliente", cl["codigo"])
a = AAP.de_cliente(cl); print("plantillas", [x["name"] for x in a.get("job_templates/", name__startswith="Plataforma · ").get("results")])
Q
bash herramientas/pruebas/p-plataforma-salud.sh 2>&1 | grep -E "^(CASO|CERT|C[0-9]|grupos|FIN)" 
