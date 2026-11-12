# Planes de autocuidado: faceops
for i in $(seq 1 160); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); [ -n "$D" ] && git -C ~/summit-redhat merge-base --is-ancestor 3d7975f8a "$D" 2>/dev/null && break; sleep 15; done; echo "desplegado $(cut -c1-9 ~/faceops-autodeploy/desplegado)"; grep -a "autocuidado" ~/faceops-autodeploy/configurar.log | tail -3
cd ~/summit-redhat && git log -1 --format="repo %h" && bash herramientas/pruebas/p-autocuidado.sh faceops 2>&1 | grep -E "^(==|  C|CASO|CERT|herramienta|SINCRONIZADO|alertmanager)"
