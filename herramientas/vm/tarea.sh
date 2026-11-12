cd ~/summit-redhat && git pull -q --ff-only origin main; git log -1 --format=%h
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 25
# sin configurar.py: la plantilla ya está publicada
:  2>&1 | grep -i -E "Traceback|error|Exportador de plataforma|Grafana de OpenShift" | grep -v -i -E "clave|password|token" | tail -4
source herramientas/pruebas/comun.sh >/dev/null 2>&1; sincronizar
echo "== plan exportador"
bash herramientas/pruebas/p-exportador.sh 2>&1 | grep -E "^(CASO|CERT|FIN|EVIDENCIA)"
echo "== alertas de JBoss con exportador en nodo-jboss (J01 + J02)"
L=nodo-jboss; IL=$(ip_de $L)
caso "{\"auto\":\"Monitoreo · Configurar alertas del equipo\",\"params\":{\"al_equipo\":\"$L\",\"al_ip\":\"$IL\",\"al_tipo\":\"linux\",\"al_ambito\":\"plataforma:jboss\",\"al_alertas\":[{\"id\":\"J01\",\"accion\":\"avisar\"},{\"id\":\"J02\",\"aviso\":85,\"critico\":95,\"accion\":\"avisar\"}],\"al_retirar\":false},\"min\":15,\"caso\":\"jboss-exportador\"}" | python3 -c "import sys,json;l=sys.stdin.read();r=json.loads(l.split('RES ',1)[1]) if 'RES ' in l else {};print('ALERTAS JBoss', r.get('ticket'), r.get('estado'), (r.get('detalle') or '')[:200])"
sleep 60
curl -s 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=up{job="faceops-exportadores"}' | python3 -c "import json,sys;print('PROM exportadores', [(x['metric'].get('faceops_equipo'),x['metric'].get('faceops_plataforma'),x['value'][1]) for x in json.load(sys.stdin)['data']['result']])"
curl -s 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=100 * max by (faceops_equipo) (base_memory_usedHeap_bytes{faceops_equipo="nodo-jboss"}) / max by (faceops_equipo) (base_memory_maxHeap_bytes{faceops_equipo="nodo-jboss"})' | python3 -c "import json,sys;print('PROM heap JBoss %', [round(float(x['value'][1]),1) for x in json.load(sys.stdin)['data']['result']])"
