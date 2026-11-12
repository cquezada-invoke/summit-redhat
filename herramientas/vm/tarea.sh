cd ~/summit-redhat; source herramientas/pruebas/comun.sh >/dev/null 2>&1
caso '{"auto":"FaceOps · Exportador de plataforma","equipo":"nodo-jboss","params":{"ex_plataforma":"redis"},"min":1,"caso":"diag"}' | cut -c1-400
sudo podman exec -i faceops python3 - <<'Q' 2>&1 | tail -3
from app import create_app
create_app()
from app.bd import q1
a = q1("SELECT id, nombre_aap, visible, usa_limite, inventario, campos FROM automatizaciones WHERE nombre_aap='FaceOps · Exportador de plataforma'")
print(dict(a) if a else None)
Q
L=nodo-jboss; IL=$(ip_de $L)
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -u - "{\"auto\":\"Monitoreo · Configurar alertas del equipo\",\"params\":{\"al_equipo\":\"$L\",\"al_ip\":\"$IL\",\"al_tipo\":\"linux\",\"al_ambito\":\"plataforma:jboss\",\"al_alertas\":[{\"id\":\"J01\",\"accion\":\"avisar\"},{\"id\":\"J02\",\"aviso\":85,\"critico\":95,\"accion\":\"avisar\"}],\"al_retirar\":false},\"min\":1,\"caso\":\"diag2\"}" < herramientas/pruebas/lanzar.py 2>&1 | tail -6 | cut -c1-500
