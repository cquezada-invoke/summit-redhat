# Plan «Monitoreo · Grafana de OpenShift» (C1–C4). Requiere ocp-sno encendido (no lo enciende).
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Monitoreo · Grafana de OpenShift"; CASOS='[]'; TKS='[]'; CL=ocp-sno
caso_() { caso "$@"; if [ "$(estado)" = "sin respuesta del arnés" ]; then for i in $(seq 1 30); do sleep 10; curl -s -o /dev/null -w '%{http_code}' http://192.168.122.1:8110/sesion/valida | grep -q 401 && break; done; sleep 20; caso "$@"; fi; }
GV=$(sed -n 's/^grafana_visitante_clave=//p' ~/demo-drift/cicd-secretos.txt)
G=http://127.0.0.1:3001/grafana
inicio() { sudo podman inspect summit-grafana --format '{{.State.StartedAt}}'; }
paneles() { curl -s -u "visitante:$GV" $G/api/dashboards/uid/faceops-ocp-$CL | python3 -c "import json,sys;d=json.load(sys.stdin)['dashboard'];print(len(d['panels']))" 2>/dev/null; }
consulta() { curl -s -u "visitante:$GV" -H 'Content-Type: application/json' $G/api/ds/query -d "{\"queries\":[{\"refId\":\"A\",\"datasource\":{\"type\":\"prometheus\",\"uid\":\"faceops-$CL\"},\"expr\":\"$1\",\"range\":true,\"intervalMs\":60000,\"maxDataPoints\":100}],\"from\":\"now-1h\",\"to\":\"now\"}" | python3 -c "import json,sys;r=json.load(sys.stdin)['results']['A'];print(len(r.get('frames',[])), r.get('error','')[:120])" 2>/dev/null; }
# C1 · conectar
caso_ "{\"auto\":\"$N\",\"params\":{\"go_cluster\":\"$CL\"},\"min\":15,\"caso\":\"C1\"}" >/dev/null; E1=$(estado); sleep 20
P1=$(paneles); Q1=$(consulta 'up{job=\"apiserver\"}'); Q1b=$(consulta 'sum(kube_node_status_condition{condition=\"Ready\",status=\"true\"})')
[ "$E1" = exitoso ] && [ "${P1:-0}" -ge 19 ] && [ "${Q1%% *}" -ge 1 ] && [ "${Q1b%% *}" -ge 1 ] && anotar C1 "Conectar $CL" ok "datasource faceops-$CL responde (up apiserver: ${Q1%% *} series; nodos Ready: ${Q1b%% *}); tablero con $P1 paneles" || anotar C1 "Conectar $CL" falla "$E1 paneles=$P1 consultas=[$Q1] [$Q1b] $(jq_ "(r.get('detalle') or '')[:200]")"
# C2 · repetir
I0=$(inicio); caso_ "{\"auto\":\"$N\",\"params\":{\"go_cluster\":\"$CL\"},\"min\":15,\"caso\":\"C2\"}" >/dev/null; E2=$(estado); I2=$(inicio)
[ "$E2" = exitoso ] && [ "$I0" = "$I2" ] && [ "$(paneles)" = "$P1" ] && anotar C2 "Repetir" ok "sin reiniciar Grafana ($I2); mismo tablero" || anotar C2 "Repetir" falla "$E2 grafana $I0→$I2"
# C3 · solo lectura
C3=$($OC auth can-i create pods -A --as=system:serviceaccount:faceops:faceops-grafana 2>/dev/null); C3b=$($OC auth can-i delete nodes --as=system:serviceaccount:faceops:faceops-grafana 2>/dev/null)
C3c=$($OC auth can-i get prometheuses.monitoring.coreos.com/api -n openshift-monitoring --as=system:serviceaccount:faceops:faceops-grafana 2>/dev/null)
[ "$C3" = no ] && [ "$C3b" = no ] && anotar C3 "Solo lectura" ok "faceops-grafana: create pods=$C3, delete nodes=$C3b, leer el monitoreo=$C3c" || anotar C3 "Solo lectura" falla "create pods=$C3 delete nodes=$C3b"
# C4 · nombre inválido
I3=$(inicio); caso_ "{\"auto\":\"$N\",\"params\":{\"go_cluster\":\"Mal Nombre!\"},\"min\":10,\"caso\":\"C4\"}" >/dev/null; E4=$(estado); I4=$(inicio)
[ "$E4" = fallido ] && [ "$I3" = "$I4" ] && anotar C4 "Nombre inválido" ok "falla en la validación; Grafana sin cambios" || anotar C4 "Nombre inválido" falla "$E4 $I3→$I4"
echo "EVIDENCIA $CASOS"
certificar "$N" "Plan Grafana de OpenShift · C1–C4 (planes-prueba.yml)"
echo FIN-GRAFANA-OCP
