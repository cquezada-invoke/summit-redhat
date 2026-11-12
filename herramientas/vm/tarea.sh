cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-60
M=$(md5sum faceops/aap/openshift-operador-retirar.yml | cut -c1-32)
for i in $(seq 1 60); do [ "$(sudo podman exec faceops md5sum /opt/faceops/aap/openshift-operador-retirar.yml 2>/dev/null | cut -c1-32)" = "$M" ] && break; sleep 20; done
echo "== desplegado: $(sudo podman exec faceops md5sum /opt/faceops/aap/openshift-operador-retirar.yml 2>/dev/null | cut -c1-32 | grep -c $M) · FaceOps $(curl -s -o /dev/null -w '%{http_code}' http://192.168.122.1:8110/sesion/valida)"
timeout 3000 bash herramientas/pruebas/p-operadores.sh operadores 2>&1 | grep -E "^(LIMPIEZA|CASO|CERT|FIN|EVIDENCIA)" | cut -c1-1500
