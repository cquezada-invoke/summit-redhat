# Diagnóstico: configurar el autocuidado (no imprime secretos)
cd ~/summit-redhat && git log -1 --format="repo %h"
grep -n "autocuidado\|LISTO\|Traceback" ~/faceops-autodeploy/configurar.log | tail -8
timeout 1100 python3 faceops/aap/autocuidado_configurar.py 2>&1 | grep -v -i -E "token|clave|password" | tail -15
