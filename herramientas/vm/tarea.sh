# Planes de autocuidado: respaldo cert purgar
cd ~/summit-redhat && git log -1 --format="repo %h" && bash herramientas/pruebas/p-autocuidado.sh respaldo cert purgar 2>&1 | grep -E "^(==|  C|CASO|CERT|herramienta|SINCRONIZADO|alertmanager)" 
