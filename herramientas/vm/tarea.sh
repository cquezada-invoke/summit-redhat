cd ~/summit-redhat && git pull -q --ff-only origin main; git log -1 --format=%h
grep -q 'thanos-querier-openshift-monitoring.apps.sno.invoke.lab' /etc/hosts || echo '192.168.122.50 thanos-querier-openshift-monitoring.apps.sno.invoke.lab' | sudo tee -a /etc/hosts >/dev/null
getent hosts thanos-querier-openshift-monitoring.apps.sno.invoke.lab
source herramientas/pruebas/comun.sh >/dev/null 2>&1
caso '{"auto":"Monitoreo · Grafana de OpenShift","params":{"go_cluster":"ocp-sno"},"min":15,"caso":"activar-19"}' | python3 -c "import json,sys;l=sys.stdin.read();r=json.loads(l.split('RES ',1)[1]) if 'RES ' in l else {};print('OCP ticket', r.get('ticket'), r.get('estado'), json.dumps(r.get('stats',{}), ensure_ascii=False)[:400], (r.get('detalle') or '')[:300])"
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc
sleep 60
$OC -n openshift-monitoring get prometheusrule faceops-alertas-cluster --no-headers 2>&1 | head -1
$OC -n openshift-monitoring exec prometheus-k8s-0 -c prometheus -- curl -s localhost:9090/api/v1/rules 2>/dev/null | python3 -c "
import json,sys;d=json.load(sys.stdin);g=[x for x in d['data']['groups'] if x['name']=='faceops-ocp-sno'];r=[y for x in g for y in x['rules']]
print('OCP reglas cargadas', len(r), '| alertas distintas', len({y['labels'].get('faceops_alerta') for y in r}), '| salud err', sum(1 for y in r if y.get('health')=='err'), '| disparadas', sum(1 for y in r if y.get('state')=='firing'))"
GV=$(sed -n 's/^grafana_visitante_clave=//p' ~/demo-drift/cicd-secretos.txt)
curl -s -u "visitante:$GV" http://127.0.0.1:3001/grafana/api/dashboards/uid/faceops-ocp-ocp-sno | python3 -c "import json,sys;d=json.load(sys.stdin)['dashboard'];print('GRAFANA', d['title'], len(d['panels']), 'paneles')" 2>&1 | tail -1
curl -s -u "visitante:$GV" -H 'Content-Type: application/json' http://127.0.0.1:3001/grafana/api/ds/query -d '{"queries":[{"refId":"A","datasource":{"type":"prometheus","uid":"faceops-ocp-sno"},"expr":"up{job=\"apiserver\"}","range":true,"intervalMs":60000,"maxDataPoints":50}],"from":"now-1h","to":"now"}' | python3 -c "import json,sys;r=json.load(sys.stdin)['results']['A'];print('GRAFANA consulta Thanos:', len(r.get('frames',[])), 'series', r.get('error',''))"
sudo systemctl restart faceops; sleep 25
sudo podman exec -i faceops python3 - <<'Q' 2>&1 | grep -E "^(OCP|Traceback|\w+Error)"
import re
from app import create_app
app = create_app()
from app.bd import q1
cl = q1("SELECT * FROM clientes WHERE lower(codigo)='lab'") or q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
c = app.test_client()
with c.session_transaction() as s:
    s["uid"] = 1; s["cliente_id"] = cl["id"]
with app.test_request_context():
    from flask import url_for
    u = url_for("consola.equipo", eid=29)
h = c.get(u, headers={"Accept": "text/html"}, follow_redirects=True).get_data(as_text=True)
d = h[h.find('id="al-dlg"'):h.find("</dialog>", h.find('id="al-dlg"'))]
b = re.search(r'<button type="button" id="alertas"[^>]*class="([^"]+)"[\s\S]*?</button>', h)
print("OCP flotante · campana", b.group(1) if b else "-", re.findall(r'class="al-n">(\d+)<', b.group(0) if b else ""), "| filas", len(re.findall(r"al-rev", d)),
      "| cargadas en el monitoreo", d.count("Regla en el monitoreo"), "| disparadas", d.count(">disparada<"), "| enlaces Grafana", d.count("Ver en Grafana"), "| estado:", re.findall(r'Estado leído ahora[^<]+', d)[:1])
Q
