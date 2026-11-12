# Plan «Plataforma · Medir salud» (C1–C5): medición sin agente de cada plataforma desde su máquina, solo lectura
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Plataforma · Medir salud"
sincronizar
med() { caso "{\"auto\":\"$N\",\"equipo\":\"$1\",\"params\":{\"pm_plataforma\":\"$2\"},\"min\":12,\"caso\":\"$3\"}"; }
v() { jq_ "((s.get('faceops_salud_plat') or {}).get('$1') or {}).get('$2', {}).get('valores', {}).get('$3')"; }
# C1: data lake en nodo-storage
med nodo-storage data-lake C1; E1=$(estado); T1=$(ticket)
S3=$(v nodo-storage data-lake s3_responde); CO=$(v nodo-storage data-lake comp_ok); CT=$(v nodo-storage data-lake comp_total)
LB=$(v nodo-storage data-lake espacio_libre_pct); BK=$(v nodo-storage data-lake buckets)
IP=$(ip_de nodo-storage); DF=$(en $IP "df -P /srv/datalake 2>/dev/null | awk 'NR==2{printf \"%.1f\", 100-\$5}'")
FICHA=$(sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>/dev/null <<'Q' | tail -1
import re
from app import create_app
app = create_app()
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    h = c.get("/lab/plataformas/PL-DATA-LAKE-0027").get_data(as_text=True)
    m = re.search(r'slb-tot"><small>Salud</small><b>([^<]*)</b>', h)
    print(("salud " + m.group(1)) if m else "sin barra", "· S3" if "S3 respondiendo" in h else "· sin indicadores")
Q
)
echo "C1 $E1 $T1 · s3=$S3 componentes=$CO/$CT libre=$LB (df $DF) buckets=$BK · ficha: $FICHA"
python3 -c "import sys;a,b=sys.argv[1:3];sys.exit(0 if a not in ('None','') and b not in ('None','') and abs(float(a)-float(b))<=2 else 1)" "$LB" "$DF" && OKDF=1 || OKDF=0
[ "$E1" = exitoso ] && [ "$S3" != None ] && [ "$CT" = 4 ] && [ $OKDF = 1 ] && echo "$FICHA" | grep -q "salud .*%" && \
  anotar C1 "Medir el data lake en nodo-storage" ok "s3=$S3 componentes $CO/$CT libre $LB% (df $DF%) buckets $BK · $FICHA" || anotar C1 "Medir el data lake en nodo-storage" falla "$E1 s3=$S3 $CO/$CT libre $LB/$DF · $FICHA"
# C2: Kafka en nodo-nginx
med nodo-nginx kafka C2; E2=$(estado)
BR=$(v nodo-nginx kafka brokers_arriba); SR=$(v nodo-nginx kafka particiones_subreplicadas); OF=$(v nodo-nginx kafka particiones_offline); LG=$(v nodo-nginx kafka lag_total)
SA=$(en $(ip_de nodo-nginx) "systemctl is-active kafka")
echo "C2 $E2 · servicio $SA · brokers=$BR subreplicadas=$SR offline=$OF lag=$LG"
{ [ "$E2" = exitoso ] && { [ "$SA" != active ] || [ "${BR:-0}" != None -a "${BR:-0}" -ge 1 ] 2>/dev/null; } && [ "$SR" != None ] && [ "$OF" != None ]; } && \
  anotar C2 "Medir Kafka en nodo-nginx" ok "servicio $SA · brokers $BR · sub-replicadas $SR · sin líder $OF · lag $LG" || anotar C2 "Medir Kafka en nodo-nginx" falla "$E2 $SA brokers=$BR sr=$SR of=$OF"
# C3: Redis (Valkey) en nodo-tomcat, sin mostrar la clave
med nodo-tomcat redis C3; E3=$(estado)
PG=$(v nodo-tomcat redis ping); PE=$(v nodo-tomcat redis persistencia_ok)
CLAVE=$(echo "$RES" | grep -ciE 'requirepass|REDISCLI_AUTH=')
echo "C3 $E3 · ping=$PG persistencia=$PE · rastros de clave en la salida: $CLAVE"
[ "$E3" = exitoso ] && [ "$PG" != None ] && [ "$CLAVE" = 0 ] && anotar C3 "Medir Redis (Valkey) en nodo-tomcat" ok "ping $PG · persistencia $PE · sin clave en la salida" || anotar C3 "Medir Redis (Valkey) en nodo-tomcat" falla "$E3 ping=$PG clave=$CLAVE"
# C4: repetir C1 (solo lectura)
med nodo-storage data-lake C4; E4=$(estado); S34=$(v nodo-storage data-lake s3_responde); CT4=$(v nodo-storage data-lake comp_total)
[ "$E4" = exitoso ] && [ "$S34" = "$S3" ] && [ "$CT4" = "$CT" ] && anotar C4 "Repetir la medición del data lake" ok "mismos indicadores (s3=$S34, $CT4 componentes); solo lectura" || anotar C4 "Repetir la medición del data lake" falla "$E4 $S3→$S34 $CT→$CT4"
# C5: plataforma que no corresponde (iis en Linux)
med nodo-storage iis C5; E5=$(estado); F5=$(echo "$RES" | grep -o "Datos válidos[^\"]*" | head -1)
echo "C5 $E5 · $F5"
[ "$E5" = fallido ] && [ -n "$F5" ] && anotar C5 "Plataforma que no corresponde" ok "fallido en «Datos válidos»: ${F5:0:160}" || anotar C5 "Plataforma que no corresponde" falla "$E5 · $F5"
certificar "$N" "Plan plataforma · C1–C5 (planes-prueba.yml)"
echo FIN-PLATAFORMA-SALUD
