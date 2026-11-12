# Diagnóstico 2: configurar autocuidado y ver plantillas (sin secretos)
cd ~/summit-redhat && git log -1 --format="repo %h"
grep -a -n "autocuidado" ~/faceops-autodeploy/configurar.log | tail -6; tail -3 ~/faceops-autodeploy/configurar.log | grep -v -i -E "token|clave"
timeout 1100 python3 faceops/aap/autocuidado_configurar.py 2>&1 | grep -v -i -E "token|clave|password" | tail -12
