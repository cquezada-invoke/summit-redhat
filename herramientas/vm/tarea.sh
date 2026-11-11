cd ~/summit-redhat && git pull -q --ff-only origin main
bash herramientas/pruebas/p-agente-monitoreo.sh 2>&1 | grep -E "^(CASO|CERT|energía|FIN|nodo|EVIDENCIA|C4)" | cut -c1-3000
