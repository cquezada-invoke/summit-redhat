#!/bin/bash
# Cola de acciones: espera el despliegue, mide crear/aprobar, prueba AAP caído (encola) → vuelve (lanza) y corre los planes
# «Plataforma · Medir salud» y «Plataforma · Programar medición». Sin secretos en la salida.
OBJ=9ac33429
cd ~/summit-redhat && git fetch -q origin main
for i in $(seq 1 90); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); [ -n "$D" ] && git merge-base --is-ancestor $OBJ $D 2>/dev/null && break; sleep 20; done
echo "desplegado: ${D:0:9} · $(cut -c1-140 ~/faceops-autodeploy/estado)"
sleep 20
echo "== salud previa"; sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>/dev/null <<'Q' | tail -1
from app import create_app
create_app()
from app.bd import ajuste
print("cola_aap:", ajuste("cola_aap_1"))
Q
echo "== planes de prueba"
echo "-- OpenShift · Relevamiento de imágenes"; bash ~/summit-redhat/herramientas/pruebas/p-imagenes.sh 2>&1 | grep -E "^(CASO|CERT|en uso|antiguas)" | grep -v -i -E "token|authorization|password"
sed -n '1,16p' ~/summit-redhat/herramientas/pruebas/p-varios.sh > /tmp/p-cv.sh
echo "-- Ciclo de vida · Evaluar destino"; bash /tmp/p-cv.sh 2>&1 | grep -E "^(CASO|CERT|C[0-9]|energía)" | grep -v -i -E "token|authorization|password"; rm -f /tmp/p-cv.sh
