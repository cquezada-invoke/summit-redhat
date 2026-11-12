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
# C5 · reglas en el monitoreo del clúster
R5=""; for i in $(seq 1 12); do R5=$($OC -n openshift-monitoring exec prometheus-k8s-0 -c prometheus -- curl -s localhost:9090/api/v1/rules 2>/dev/null | python3 -c "
import json,sys;d=json.load(sys.stdin);g=[x for x in d['data']['groups'] if x['name']=='faceops-$CL'];r=[y for x in g for y in x['rules']]
print(len(r), sum(1 for y in r if y.get('health')=='err'), sum(1 for y in r if y['labels'].get('faceops_alerta')), sum(1 for y in r if y.get('health')=='unknown'))" 2>/dev/null); [ -n "$R5" ] && [ "$(echo $R5 | cut -d' ' -f4)" = 0 ] && break; sleep 20; done
set -- $R5; [ "${1:-0}" -ge 19 ] && [ "${2:-1}" = 0 ] && [ "$3" = "$1" ] && anotar C5 "Reglas en el monitoreo del clúster" ok "grupo faceops-$CL con $1 reglas sanas, todas con faceops_alerta" || anotar C5 "Reglas en el monitoreo del clúster" falla "reglas/err/etiquetadas/sin evaluar: $R5"
# C6 · cadena de aviso real: X10 (CPU de nodos) con umbral bajado de forma temporal → Alertmanager del clúster → FaceOps (correo) y EDA
evento() { sudo podman exec -i faceops python3 - "$1" <<'Q' 2>/dev/null | tail -1
import sys
from app import create_app
create_app()
from app.bd import q1
try:
    r = q1("SELECT estado, para, resultado, fecha FROM alerta_eventos WHERE equipo='ocp-sno' AND alerta='X10' AND estado=? ORDER BY id DESC LIMIT 1", sys.argv[1])
except Exception:
    r = None
print(f"{r['fecha']} {r['para']} {r['resultado']}" if r else "")
Q
}
F0=$(evento firing)
caso_ "{\"auto\":\"$N\",\"params\":{\"go_cluster\":\"$CL\",\"go_prueba\":{\"id\":\"X10\",\"aviso\":1,\"para\":\"0m\"}},\"min\":15,\"caso\":\"C6a\"}" >/dev/null; E6a=$(estado)
F6=""; for i in $(seq 1 30); do sleep 10; F6=$(evento firing); [ -n "$F6" ] && [ "$F6" != "$F0" ] && break; done
R0=$(evento resolved)
caso_ "{\"auto\":\"$N\",\"params\":{\"go_cluster\":\"$CL\"},\"min\":15,\"caso\":\"C6b\"}" >/dev/null; E6b=$(estado)
R6=""; for i in $(seq 1 42); do sleep 10; R6=$(evento resolved); [ -n "$R6" ] && [ "$R6" != "$R0" ] && break; done
[ "$E6a" = exitoso ] && [ "$E6b" = exitoso ] && [ "$F6" != "$F0" ] && echo "$F6" | grep -q enviado && [ "$R6" != "$R0" ] \
  && anotar C6 "Cadena de aviso real (X10)" ok "disparada → FaceOps: $F6 · umbral restablecido → resuelta: $R6" \
  || anotar C6 "Cadena de aviso real (X10)" falla "$E6a/$E6b disparada=[$F6] resuelta=[$R6]"
# C4 · nombre inválido
I3=$(inicio); caso_ "{\"auto\":\"$N\",\"params\":{\"go_cluster\":\"Mal Nombre!\"},\"min\":10,\"caso\":\"C4\"}" >/dev/null; E4=$(estado); I4=$(inicio)
[ "$E4" = fallido ] && [ "$I3" = "$I4" ] && anotar C4 "Nombre inválido" ok "falla en la validación; Grafana sin cambios" || anotar C4 "Nombre inválido" falla "$E4 $I3→$I4"
echo "EVIDENCIA $CASOS"
certificar "$N" "Plan Grafana de OpenShift · C1–C6 (planes-prueba.yml)"
echo FIN-GRAFANA-OCP
