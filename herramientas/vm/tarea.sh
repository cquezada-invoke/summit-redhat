# Sonda de solo lectura antes de los planes de autocuidado
echo "AAP $(curl -sk -o /dev/null -m 8 -w '%{http_code}' https://127.0.0.1/api/controller/v2/ping/) · FaceOps $(curl -s -o /dev/null -m 8 -w '%{http_code}' http://192.168.122.1:8110/ingresar)"
df -h / | tail -1
echo "desplegado: $(cut -c1-9 ~/faceops-autodeploy/desplegado) · $(cut -c1-140 ~/faceops-autodeploy/estado)"
grep -a "autocuidado" ~/faceops-autodeploy/configurar.log 2>/dev/null | tail -8
echo "herramienta: $(test -x /usr/local/sbin/faceops-autocuidado && sudo /usr/local/sbin/faceops-autocuidado version || echo no)"
systemctl is-active faceops-autocuidado-exportador faceops-autocuidado-vigia.timer
curl -s -m 30 http://127.0.0.1:9469/metrics | grep -c '^faceops_ac_'
sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -3
from app import create_app
create_app()
from app.bd import q
print([a["nombre_aap"] for a in q("SELECT nombre_aap FROM automatizaciones WHERE nombre_aap LIKE 'Autocuidado%'")])
Q
