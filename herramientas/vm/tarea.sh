# Planes de autocuidado: estado disco aap
cd ~/summit-redhat && git log -1 --format="repo %h" && bash herramientas/pruebas/p-autocuidado.sh estado disco aap 2>&1 | grep -E "^(==|  C|CASO|CERT|herramienta|SINCRONIZADO|alertmanager)" 
