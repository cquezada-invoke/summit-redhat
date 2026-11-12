cd ~/summit-redhat && git pull -q --ff-only origin main
bash herramientas/pruebas/p-quitar-agente.sh 2>&1 | grep -E "^(CASO|CERT|prep|antes|restaurar|energía|maquina|EVIDENCIA|FIN)" | cut -c1-3000
