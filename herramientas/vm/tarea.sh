up() { curl -s -o /dev/null -w '%{http_code}' http://192.168.122.1:8110/sesion/valida; }
echo "== FaceOps antes: $(up)"
cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-60
timeout 900 python3 faceops/aap/configurar.py 2>&1 | grep -E "operador|Traceback" | grep -v -i -E "clave|password|token" | tail -3
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops
for i in $(seq 1 30); do sleep 5; [ "$(up)" = 401 ] && break; done; echo "== FaceOps después: $(up)"
timeout 2700 bash herramientas/pruebas/p-operadores.sh operadores 2>&1 | grep -E "^(CASO|CERT|FIN|EVIDENCIA)" | cut -c1-1500
