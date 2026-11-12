cd ~/summit-redhat && git pull -q --ff-only origin main; git log -1 --format=%h
timeout 900 python3 faceops/aap/configurar.py 2>&1 | grep -i -E "Traceback|error|Exportador de plataforma" | grep -v -i -E "clave|password|token" | tail -3
echo "== plan exportador"
bash herramientas/pruebas/p-exportador.sh 2>&1 | grep -E "^(CASO|CERT|FIN|EVIDENCIA)"
