cd ~/summit-redhat && git pull -q --ff-only origin main
bash herramientas/pruebas/p-migraciones-so.sh rocky-cr 2>&1 | grep -E "^(  \(arnés|energía|IP|inventariar|  C|CASO|CERT|CENTOS|FIN)" | cut -c1-2500
