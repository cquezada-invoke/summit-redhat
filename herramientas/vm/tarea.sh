# Planes de autocuidado: respaldo cert purgar
for i in $(seq 1 120); do grep -q "^4a9e2f3" ~/faceops-autodeploy/desplegado 2>/dev/null && break; sleep 15; done; echo "desplegado $(cut -c1-9 ~/faceops-autodeploy/desplegado)"; grep -a "autocuidado" ~/faceops-autodeploy/configurar.log | tail -4
cd ~/summit-redhat && git log -1 --format="repo %h" && bash herramientas/pruebas/p-autocuidado.sh respaldo cert purgar 2>&1 | grep -E "^(==|  C|CASO|CERT|herramienta|SINCRONIZADO|alertmanager)"
