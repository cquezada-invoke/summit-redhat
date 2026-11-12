# Plan «FaceOps · Exportador de plataforma» (C1–C6)
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="FaceOps · Exportador de plataforma"; CASOS='[]'; TKS='[]'
caso_() { caso "$@"; if [ "$(estado)" = "sin respuesta del arnés" ]; then for i in $(seq 1 30); do sleep 10; curl -s -o /dev/null -w '%{http_code}' http://192.168.122.1:8110/sesion/valida | grep -q 401 && break; done; sleep 20; caso "$@"; fi; }
con() {  # con <familia>: primer equipo vigente con esa plataforma en su inventario
  sudo podman exec -i faceops python3 - "$1" 2>/dev/null <<'Q' | tail -1
import sys
from app import create_app
create_app()
from app.bd import q1
f = sys.argv[1]
r = q1("SELECT nombre FROM equipos WHERE estado!='retirado' AND ip!='' AND lower(datos) LIKE ? ORDER BY (nombre LIKE 'nodo-%') DESC, nombre LIMIT 1", f"%{f}%")
print((r or {}).get("nombre") or "")
Q
}
met() { curl -s --max-time 8 "http://$1/metrics" | grep -c "$2"; }
pedir() { caso_ "{\"auto\":\"$N\",\"equipo\":\"$1\",\"params\":{\"ex_plataforma\":\"$2\"},\"min\":15,\"caso\":\"$3\"}" >/dev/null; }
JB=nodo-jboss; IJ=$(ip_de $JB)
pedir $JB jboss C1; E1=$(estado); M1=$(met $IJ:9990 '^base_memory_usedHeap_bytes'); U1=$(met $IJ:9990 '^wildfly_undertow_request_count_total')
[ "$E1" = exitoso ] && [ "${M1:-0}" -ge 1 ] && anotar C1 "JBoss" ok "$JB :9990/metrics con heap ($M1) y Undertow ($U1)" || anotar C1 "JBoss" falla "$E1 heap=$M1 undertow=$U1 $(jq_ "(r.get('detalle') or '')[:200]")"
RD=$(con redis); IR=$(ip_de $RD)
pedir $RD redis C2; E2=$(estado); M2=$(curl -s --max-time 8 http://$IR:9121/metrics | sed -n 's/^redis_up //p')
[ "$E2" = exitoso ] && [ "$M2" = 1 ] && anotar C2 "Redis ($RD)" ok "redis_exporter :9121 con redis_up 1" || anotar C2 "Redis ($RD)" falla "$E2 redis_up=$M2 $(jq_ "(r.get('detalle') or '')[:200]")"
NG=nodo-nginx; IN=$(ip_de $NG)
pedir $NG nginx C3; E3=$(estado); M3=$(curl -s --max-time 8 http://$IN:9113/metrics | sed -n 's/^nginx_up //p'); W3=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 https://$IN/ )
[ "$E3" = exitoso ] && [ "$M3" = 1 ] && anotar C3 "nginx" ok "nginx-prometheus-exporter :9113 con nginx_up 1; nginx responde ($W3)" || anotar C3 "nginx" falla "$E3 nginx_up=$M3 web=$W3 $(jq_ "(r.get('detalle') or '')[:200]")"
PG=$(con postgres); IP=$(ip_de $PG)
pedir $PG postgresql C4; E4=$(estado); M4=$(curl -s --max-time 8 http://$IP:9187/metrics | sed -n 's/^pg_up //p')
[ "$E4" = exitoso ] && [ "$M4" = 1 ] && anotar C4 "PostgreSQL ($PG)" ok "postgres_exporter :9187 con pg_up 1" || anotar C4 "PostgreSQL ($PG)" falla "$E4 pg_up=$M4 $(jq_ "(r.get('detalle') or '')[:200]")"
P0=$(en $IR 'systemctl show -p ExecMainPID --value faceops-exportador-redis'); pedir $JB jboss C5a; E5a=$(estado); C5a=$(jq_ "str(r.get('resultado',''))[-300:]")
pedir $RD redis C5b; E5b=$(estado); P1=$(en $IR 'systemctl show -p ExecMainPID --value faceops-exportador-redis')
[ "$E5a" = exitoso ] && [ "$E5b" = exitoso ] && [ "$P0" = "$P1" ] && anotar C5 "Repetir" ok "JBoss y Redis sin cambios; exportador de Redis sin reiniciar (PID $P1)" || anotar C5 "Repetir" falla "$E5a/$E5b pid $P0→$P1"
pedir $JB mysql C6; E6=$(estado)
[ "$E6" = fallido ] || [ "$E6" = "rechazado al solicitar" ] && anotar C6 "Plataforma inválida" ok "rechazada ($E6)" || anotar C6 "Plataforma inválida" falla "$E6"
echo "EVIDENCIA $CASOS"
certificar "$N" "Plan exportador de plataforma · C1–C6 (planes-prueba.yml)"
echo FIN-EXPORTADOR
