cd ~/summit-redhat && git pull -q origin main && bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && echo "faceops actualizado"
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc
$OC -n fabrica-codigo start-build fabrica --from-dir=fabrica-codigo --follow --wait 2>&1 | tail -1; $OC -n fabrica-codigo rollout status deploy/fabrica --timeout=300s | tail -1
cd portal-automatizacion && sudo podman build -q -t localhost/portal-automatizacion:latest -f Containerfile . >/dev/null && sudo systemctl restart portal-automatizacion && echo "portal actualizado"
sleep 12
cat > /tmp/ft2.py <<'PY'
from app import create_app
from app.bd import q1
a = create_app(); c = a.test_client()
with a.app_context():
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada' AND es_admin=1"); cli = q1("SELECT * FROM clientes WHERE codigo='lab'")
with c.session_transaction() as s:
    s["uid"] = u["id"]; s["csrf"] = "x"; s["mfa_ok"] = True; s["cliente_id"] = cli["id"]
d = c.get("/lab/fabrica/trabajos").get_json()
for r in (d.get("repos") or [])[:3]:
    fe = (r["features"] or [{}])[0]; print(r["repo"], "|", fe.get("titulo"), "|", fe.get("conversacion"))
r = c.get("/lab/fabrica/desarrollo?trabajo=mantenedor-usuarios@feature/mantenedor-usuarios-11091507&lanzar=1", headers={"Accept": "text/html"})
t = r.get_data(as_text=True); import re; print("iframe:", re.findall(r'<iframe src="([^"]+)"', t)[:1], "Lanzar pipeline" in t)
r = c.get("/lab/fabrica/app/api/conversacion?repo=mantenedor-usuarios&rama=feature/mantenedor-usuarios-11091507"); print("conversacion:", r.status_code, str(r.get_json())[:160])
PY
sudo podman cp /tmp/ft2.py faceops:/tmp/ft2.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/ft2.py 2>&1 | grep -v -i warn | tail -8
echo "== FaceAdmin"
O="-i $HOME/.ssh/id_demo -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10"
ssh $O cquezada@faceadmin.southamerica-west1-a.c.master-ia-320218.internal 'sudo /usr/local/sbin/faceadmin-actualizar 2>&1 | tail -1; sleep 12; curl -sk https://localhost:8843/version | cut -c1-60; echo; sudo sqlite3 /var/lib/faceadmin/faceadmin.db "SELECT COUNT(*), SUM(cargo<>\"\"), SUM(telefono<>\"\") FROM contactos; SELECT COUNT(*), SUM(COALESCE(industria,\"\")<>\"\") FROM clientes WHERE activo=1; SELECT id, estado, fin FROM contratos WHERE id=27" 2>&1 | tail -2'
echo "== Modelo GPU (fabrica-gpu us-west1-a 10.138.0.4)"
for i in $(seq 1 40); do curl -s -m 5 http://10.138.0.4:8000/v1/models | grep -q Qwen && break; sleep 30; done
curl -s -m 5 http://10.138.0.4:8000/v1/models | cut -c1-160; echo
if curl -s -m 5 http://10.138.0.4:8000/v1/models | grep -q Qwen; then
  $OC -n fabrica-codigo set env deploy/fabrica MODELO_LOCAL_URL=http://10.138.0.4:8000 >/dev/null && $OC -n fabrica-codigo rollout status deploy/fabrica --timeout=300s | tail -1
  $OC -n fabrica-codigo exec deploy/fabrica -- python3 -c "import requests;r=requests.post('http://10.138.0.4:8000/v1/chat/completions',json={'model':'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ','messages':[{'role':'user','content':'Responde solo: ok'}],'max_tokens':5},timeout=120);print('desde la fabrica:',r.status_code,r.json()['choices'][0]['message']['content'])" 2>&1 | tail -1
else echo "modelo aún no responde"; fi
