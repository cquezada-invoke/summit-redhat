# Sonda de solo lectura: ¿el lab está sano? (no cambia nada)
echo "AAP ping: $(curl -sk -o /dev/null -m 8 -w '%{http_code}' https://127.0.0.1/api/controller/v2/ping/)"
echo "FaceOps: $(curl -s -o /dev/null -m 8 -w '%{http_code}' http://192.168.122.1:8110/ingresar)"
df -h / | tail -1
echo "VMs pausadas: $(sudo virsh list --state-paused --name 2>/dev/null | grep -c .)"
echo "autodeploy: $(cat ~/faceops-autodeploy/estado 2>/dev/null | cut -c1-160) · timer $(systemctl is-active faceops-autodeploy.timer)"
echo "desplegado: $(cut -c1-9 ~/faceops-autodeploy/desplegado 2>/dev/null)"
echo "autocuidado instalado: $(test -x /usr/local/sbin/faceops-autocuidado && sudo /usr/local/sbin/faceops-autocuidado version || echo no)"
