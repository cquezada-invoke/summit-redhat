# Plan «Monitoreo · Configurar alertas del equipo» (C1–C9 y C11)
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Monitoreo · Configurar alertas del equipo"; CASOS='[]'; TKS='[]'
PM=http://127.0.0.1:9090
reglas() { curl -s $PM/api/v1/rules?type=alert | python3 -c "import json,sys;d=json.load(sys.stdin);g=[x for x in d['data']['groups'] if x['name']=='faceops-$1'];print(len(g[0]['rules']) if g else 0)"; }
reglas_txt() { curl -s $PM/api/v1/rules?type=alert | python3 -c "import json,sys;d=json.load(sys.stdin);g=[x for x in d['data']['groups'] if x['name']=='faceops-$1'];print(json.dumps(sorted((r['name'],r['query'],r['labels'].get('faceops_accion',''),r['labels'].get('faceops_automatizacion','')) for r in (g[0]['rules'] if g else []))))"; }
up() { curl -s "$PM/api/v1/query" --data-urlencode "query=up{job=\"faceops-equipos\",faceops_equipo=\"$1\"}" | python3 -c "import json,sys;r=json.load(sys.stdin)['data']['result'];print(r[0]['value'][1] if r else 'sin-serie')"; }
pedir() {  # pedir <caso> <equipo> <ip> <tipo> '<alertas json>' [min]
  caso "{\"auto\":\"$N\",\"params\":{\"al_equipo\":\"$2\",\"al_ip\":\"$3\",\"al_tipo\":\"$4\",\"al_alertas\":$5,\"al_retirar\":$( [ "$5" = "[]" ] && echo true || echo false )},\"min\":${6:-20},\"caso\":\"$1\"}"
}
L=nodo-jboss; energia $L encender; IL=$(ip_de $L); echo "$L $IL"
# C1 · activar en Linux con agente
pedir C1 $L $IL linux '[{"id":"D01","accion":"encender"},{"id":"C01","aviso":80,"critico":95,"accion":"recursos"},{"id":"C02","aviso":85,"critico":95,"accion":"avisar"},{"id":"C03","aviso":80,"critico":90,"accion":"avisar"}]'
E1=$(estado); T1=$(ticket); sleep 40; R1=$(reglas $L); U1=$(up $L); B1=$(jq_ "len(((s.get('faceops_alertas') or {}).get('alertas')) or [])")
echo "C1 $E1 reglas=$R1 up=$U1 reflejo=$B1"
[ "$E1" = exitoso ] && [ "$R1" = 7 ] && [ "$U1" = 1 ] && [ "$B1" = 4 ] && anotar C1 "Activar en Linux con agente" ok "7 reglas faceops-$L, up=1, 4 alertas en la ficha ($T1)" || anotar C1 "Activar en Linux con agente" falla "$E1 reglas=$R1 up=$U1 ficha=$B1"
# C9 · acción por alerta y ruta a EDA
TX=$(reglas_txt $L); AM=$(sudo grep -c "faceops-objetos" /opt/summit-monitoreo/alertmanager/alertmanager.yml)
echo "$TX" | grep -q '"encender", "Infra · Encender o apagar equipo"' && echo "$TX" | grep -q '"avisar", ""' && [ "$AM" -ge 2 ] \
  && anotar C9 "Acción por alerta y ruta a EDA" ok "faceops_accion y faceops_automatizacion en las reglas; Alertmanager enruta faceops-objetos → :5022" \
  || anotar C9 "Acción por alerta y ruta a EDA" falla "AM=$AM $(echo $TX | cut -c1-200)"
# C11 · tablero de Grafana y umbrales como serie (la clave del visitante no se imprime)
GV=$(sed -n 's/^grafana_visitante_clave=//p' ~/demo-drift/cicd-secretos.txt); sleep 15
G11=$(curl -s -u "visitante:$GV" http://127.0.0.1:3001/grafana/api/dashboards/uid/faceops-alertas | python3 -c "import json,sys;d=json.load(sys.stdin)['dashboard'];print(sum(1 for p in d['panels'] if p['type']=='timeseries'))" 2>/dev/null)
U11=$(curl -s "$PM/api/v1/query" --data-urlencode "query=faceops_alerta_umbral{faceops_equipo=\"$L\",faceops_alerta=\"C01\"}" | python3 -c "import json,sys;print(','.join(sorted(r['metric']['severity']+'='+r['value'][1] for r in json.load(sys.stdin)['data']['result'])))")
[ "${G11:-0}" -ge 20 ] && [ "$U11" = "critical=95,warning=80" ] && anotar C11 "Tablero en Grafana" ok "faceops-alertas con $G11 paneles; umbrales C01 $U11" || anotar C11 "Tablero en Grafana" falla "paneles=$G11 umbrales=$U11"
# C2 · disparo real (umbral de disco bajo el uso actual, 30 s)
pedir C2 $L $IL linux '[{"id":"D01","accion":"encender"},{"id":"C03","aviso":1,"critico":99,"para":"30s","accion":"avisar"}]'
E2=$(estado); F2=""; for i in $(seq 1 18); do sleep 10; F2=$(curl -s $PM/api/v1/alerts | python3 -c "import json,sys;print(sum(1 for a in json.load(sys.stdin)['data']['alerts'] if a['labels'].get('faceops_equipo')=='$L' and a['labels'].get('faceops_alerta')=='C03' and a['state']=='firing'))"); [ "$F2" -ge 1 ] && break; done
AMA=$(curl -s http://127.0.0.1:9093/api/v2/alerts | python3 -c "import json,sys;print(sum(1 for a in json.load(sys.stdin) if a['labels'].get('faceops_equipo')=='$L'))")
[ "$E2" = exitoso ] && [ "${F2:-0}" -ge 1 ] && [ "${AMA:-0}" -ge 1 ] && anotar C2 "Disparo real" ok "C03 firing en Prometheus ($F2) y en Alertmanager ($AMA)" || anotar C2 "Disparo real" falla "$E2 firing=$F2 alertmanager=$AMA"
# C3 · ajustar umbrales y quitar una
pedir C3 $L $IL linux '[{"id":"D01","accion":"avisar"},{"id":"C01","aviso":70,"critico":90,"accion":"avisar"}]'
E3=$(estado); sleep 30; R3=$(reglas $L); TX3=$(reglas_txt $L)
[ "$E3" = exitoso ] && [ "$R3" = 3 ] && echo "$TX3" | grep -q "> 70" && ! echo "$TX3" | grep -q "C03" && anotar C3 "Ajustar y quitar" ok "3 reglas, CPU con aviso 70; disco retirado" || anotar C3 "Ajustar y quitar" falla "$E3 reglas=$R3"
# C4 · repetir
H0=$(reglas_txt $L | sha256sum | cut -c1-10); pedir C4 $L $IL linux '[{"id":"D01","accion":"avisar"},{"id":"C01","aviso":70,"critico":90,"accion":"avisar"}]'
E4=$(estado); sleep 30; H4=$(reglas_txt $L | sha256sum | cut -c1-10); J4=$(sudo grep -c "job_name: faceops-equipos" /opt/summit-monitoreo/prometheus/prometheus.yml)
[ "$E4" = exitoso ] && [ "$H0" = "$H4" ] && [ "$J4" = 1 ] && anotar C4 "Repetir" ok "mismas reglas ($H4); un solo trabajo faceops-equipos" || anotar C4 "Repetir" falla "$E4 $H0→$H4 jobs=$J4"
# C5 · storage y equipo de red
S=nodo-storage; energia $S encender; IS=$(ip_de $S)
RD=$(sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -1
from app import create_app
create_app()
from app.bd import q1
e = q1("SELECT nombre, ip FROM equipos WHERE categoria IN ('firewall','router') AND ip!='' AND estado!='retirado' ORDER BY nombre LIMIT 1"); print(f"{e['nombre']} {e['ip']}" if e else "")
Q
); RN=${RD% *}; RI=${RD#* }; echo "storage $S $IS · red $RN $RI"; [ -n "$RN" ] && energia $RN encender
pedir C5a $S $IS storage '[{"id":"C03","aviso":80,"critico":90,"accion":"ampliar_volumen"},{"id":"C08","aviso":80,"critico":95,"accion":"avisar"}]'; E5a=$(estado)
pedir C5b $RN $RI red '[{"id":"D02","accion":"avisar"},{"id":"D05","aviso":99,"critico":95,"accion":"avisar"}]'; E5b=$(estado); sleep 45
R5a=$(reglas $S); R5b=$(reglas $RN); P5=$(curl -s "$PM/api/v1/query" --data-urlencode "query=probe_success{job=\"faceops-sondeo\",faceops_equipo=\"$RN\"}" | python3 -c "import json,sys;r=json.load(sys.stdin)['data']['result'];print(r[0]['value'][1] if r else 'sin-serie')")
[ "$E5a" = exitoso ] && [ "$E5b" = exitoso ] && [ "$R5a" = 4 ] && [ "$R5b" = 3 ] && [ "$P5" = 1 ] && anotar C5 "Storage y equipo de red" ok "$S 4 reglas; $RN 3 reglas y probe_success=1" || anotar C5 "Storage y equipo de red" falla "$E5a/$E5b reglas=$R5a/$R5b probe=$P5"
# C6 · equipo sin agente (rocky de prueba o el primero sin :9100)
X=srv-prueba-rocky-01; IX=$(ip_de $X); [ -z "$IX" ] && { X=srv-prueba-rhel9-01; IX=$(ip_de $X); }
F0=$(sudo ls /opt/summit-monitoreo/prometheus/faceops/reglas/ 2>/dev/null | sha256sum | cut -c1-10)
pedir C6 $X "${IX:-192.168.122.250}" linux '[{"id":"D01","accion":"avisar"}]' 12; E6=$(estado); D6=$(echo "$RES" | grep -o "agente de monitoreo" | head -1)
F6=$(sudo ls /opt/summit-monitoreo/prometheus/faceops/reglas/ 2>/dev/null | sha256sum | cut -c1-10)
[ "$E6" = fallido ] && [ -n "$D6" ] && [ "$F0" = "$F6" ] && anotar C6 "Equipo sin agente" ok "falla con «instala el agente de monitoreo»; Prometheus sin cambios" || anotar C6 "Equipo sin agente" falla "$E6 '$D6' $F0→$F6"
# C7 · umbral inválido: FaceOps lo rechaza antes del ticket y el playbook también
V7=$(sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -1
from app import create_app
create_app()
from app import alertas_objeto as ao
from app.bd import q1
e = q1("SELECT * FROM equipos WHERE nombre='nodo-jboss'")
class F(dict):
    def get(self, k, d=None): return dict.get(self, k, d)
try:
    ao.pedido_de_formulario(e, F({"on_C01": "1", "aviso_C01": "95", "critico_C01": "80", "acc_C01": "avisar"})); print("acepto")
except ValueError as x:
    print("rechazo")
Q
)
H7a=$(reglas_txt $L | sha256sum | cut -c1-10)
pedir C7 $L $IL linux '[{"id":"C01","aviso":95,"critico":80,"accion":"avisar"}]' 12; E7=$(estado); sleep 20; H7b=$(reglas_txt $L | sha256sum | cut -c1-10)
[ "$V7" = rechazo ] && [ "$E7" = fallido ] && [ "$H7a" = "$H7b" ] && anotar C7 "Umbral inválido" ok "FaceOps lo rechaza; el playbook falla y deja las reglas como estaban" || anotar C7 "Umbral inválido" falla "$V7 $E7 $H7a→$H7b"
# C8 · retirar todas
pedir C8 $L $IL linux '[]'; E8=$(estado); sleep 40; R8=$(reglas $L); U8=$(up $L); OK8=$(curl -s -o /dev/null -w '%{http_code}' $PM/-/ready)
pedir C8b $S $IS storage '[]' >/dev/null; [ -n "$RN" ] && pedir C8c $RN $RI red '[]' >/dev/null
[ "$E8" = exitoso ] && [ "$R8" = 0 ] && [ "$U8" = sin-serie ] && [ "$OK8" = 200 ] && anotar C8 "Retirar todas" ok "sin reglas ni objetivo; Prometheus listo" || anotar C8 "Retirar todas" falla "$E8 reglas=$R8 up=$U8 ready=$OK8"
certificar "$N" "Plan alertas por objeto · C1–C9 y C11 (planes-prueba.yml)"
echo FIN-ALERTAS-OBJ
