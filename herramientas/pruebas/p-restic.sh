# Plan «Recuperación · Respaldar volúmenes con restic» (C1–C9, datos/planes-prueba.yml) sobre nodo-jboss, con el S3 de
# respaldos del laboratorio (nodo-storage). Nunca imprime secretos: de la clave solo un prefijo de su sha256.
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Recuperación · Respaldar volúmenes con restic"; M=${M:-nodo-jboss}; MALO="http://192.168.122.167:9999"
energia nodo-storage encender >/dev/null; energia $M encender >/dev/null
IPJ=$(ip_de $M); echo "$M $IPJ"
rd() { RD=$(sudo podman exec -i faceops python3 - $M < $P/respaldo_estado.py 2>/dev/null | grep '^RD ' | tail -1 | cut -c4-); [ -z "$RD" ] && RD='{}'; }
rdq() { echo "$RD" | python3 -c "import json,sys;r=json.load(sys.stdin);print($1)" 2>/dev/null; }
sq() { echo "$RES" | python3 -c "import json,sys;r=json.load(sys.stdin);d=(r.get('stats') or {}).get('faceops_respaldo_datos') or {};x=d.get('$M') or (list(d.values())[0] if d else {});print($1)" 2>/dev/null; }
maq() { en $IPJ 'test -x /usr/local/bin/restic && /usr/local/bin/restic version | cut -d" " -f2 || echo no
  systemctl is-enabled faceops-restic.timer 2>/dev/null || echo ausente
  ls /etc/systemd/system/faceops-restic.* 2>/dev/null | wc -l
  sudo stat -c %Y /etc/systemd/system/faceops-restic.timer /etc/systemd/system/faceops-restic.service /etc/faceops-restic/clave 2>/dev/null | paste -sd,
  sudo sed -n "s/^OnCalendar=//p" /etc/systemd/system/faceops-restic.timer 2>/dev/null | head -1
  sudo sha256sum /etc/faceops-restic/clave 2>/dev/null | cut -c1-12'; }
l() { echo "$MQ" | sed -n "${1}p"; }
ok_() { [ "$(estado)" = exitoso ]; }; no_() { [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; }
det() { jq_ "r.get('detalle','')[:200]"; }
# inventario fresco (el pilar Recuperación descuenta un inventario de más de 26 h)
caso "{\"auto\":\"Sistema · Inventariar máquina\",\"equipo\":\"$M\",\"params\":{},\"min\":25,\"caso\":\"inventario\"}" >/dev/null; echo "inventario $M: $(estado) $(ticket)"
rd; R0=$(rdq "r.get('rec')"); echo "antes: Recuperación $R0 · respaldo $(rdq "r.get('respaldo')") · vols $(rdq "r.get('vols')")"
MQ=$(maq); echo "máquina antes: $(echo $MQ)"
# ── C1 datos no válidos
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"rr_hora\":\"25:00\"},\"min\":10,\"caso\":\"C1a\"}"; E1a=$(estado)
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"rr_rutas\":\"/\"},\"min\":10,\"caso\":\"C1b\"}"; E1b=$(estado); D1b=$(det)
MQ=$(maq); rd
{ [ "$E1a" = fallido ] || [ "$E1a" = "rechazado al solicitar" ]; } && no_ && [ "$(l 1)" = no ] && [ "$(l 3)" = 0 ] && [ "$(rdq "r.get('respaldo')")" = None ] \
  && anotar C1 "Datos no válidos y volumen del sistema" ok "hora 25:00 → $E1a; «/» → $(estado) ($D1b); sin restic ni timer; ficha sin cambios" \
  || anotar C1 "Datos no válidos y volumen del sistema" falla "$E1a/$(estado) restic=$(l 1) unidades=$(l 3) $D1b"
# ── C2 S3 inalcanzable sin respaldo previo
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"rr_s3\":\"$MALO\"},\"min\":20,\"caso\":\"C2\"}"
VA=$(sq "x.get('vuelta_atras','')"); MQ=$(maq); rd
[ "$(estado)" = fallido ] && [ "$(l 3)" = 0 ] && [ "$(l 2)" = ausente ] && { echo "$VA$(jq_ "r.get('resultado','')")" | grep -q "retirado el timer"; } && [ "$(rdq "r.get('respaldo')")" = None ] \
  && anotar C2 "S3 inalcanzable sin respaldo previo" ok "fallido con vuelta atrás: $VA; sin timer ni unidades; ficha sin cambios" \
  || anotar C2 "S3 inalcanzable sin respaldo previo" falla "$(estado) timer=$(l 2) unidades=$(l 3) va=$VA $(det)"
# ── C3 respaldar con los valores por defecto
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{},\"min\":25,\"caso\":\"C3\"}"
S3S=$(sq "x.get('snapshot','')"); DET=$(sq "','.join(x.get('detectadas') or [])"); NUEVO=$(sq "x.get('repositorio_nuevo')"); REPO=$(sq "x.get('repositorio','')")
MQ=$(maq); MT3=$(l 4); CL3=$(l 6); rd; R1=$(rdq "r.get('rec')")
echo "C3: snapshot ${S3S:0:8} detectadas=$DET repo=$REPO nuevo=$NUEVO · máquina $(echo $MQ | cut -c1-80) · Recuperación $R0 → $R1 · vols $(rdq "r.get('vols')") · bóveda $(rdq "r.get('boveda',{}).get('llaves')")"
ok_ && [ -n "$S3S" ] && [ "$DET" = /datos ] && [ "$NUEVO" = True ] && [ "$(l 1)" = 0.17.3 ] && [ "$(l 2)" = enabled ] && [ "$(sq "x.get('verificacion')")" = "restic check ok" ] \
  && [ "$(rdq "r['respaldo'].get('snapshot')")" = "$S3S" ] && [ "$(rdq "r['vols'].get('/datos')")" = respaldado ] && [ "$(rdq "r['boveda'].get('clave')")" = "$CL3" ] && [ -n "$CL3" ] \
  && [ "$((R1 - R0))" = 15 ] \
  && anotar C3 "Respaldar nodo-jboss (valores por defecto)" ok "solo /datos; restic 0.17.3; repo $REPO creado; snapshot ${S3S:0:8} check ok; timer $(l 5); clave en bóveda = máquina; punto $(rdq "r.get('punto')") con /datos respaldado; Recuperación $R0 → $R1" \
  || anotar C3 "Respaldar nodo-jboss (valores por defecto)" falla "$(estado) det=$DET nuevo=$NUEVO restic=$(l 1) timer=$(l 2) bov=$(rdq "r['boveda']") vols=$(rdq "r.get('vols')") rec $R0→$R1 $(det)"
# ── C4 repetir
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{},\"min\":20,\"caso\":\"C4\"}"
S4S=$(sq "x.get('snapshot','')"); N4=$(sq "x.get('snapshots')"); MQ=$(maq)
ok_ && [ "$(sq "x.get('repositorio_nuevo')")" = False ] && [ "$(l 4)" = "$MT3" ] && [ "$(l 6)" = "$CL3" ] && [ -n "$S4S" ] && [ "$S4S" != "$S3S" ] && [ "$N4" = 1 ] \
  && anotar C4 "Repetir respaldar" ok "repositorio existente; unidades y clave sin cambios; snapshot ${S4S:0:8} nuevo; retención deja $N4 por día" \
  || anotar C4 "Repetir respaldar" falla "$(estado) nuevo=$(sq "x.get('repositorio_nuevo')") mtimes $MT3→$(l 4) snap $S3S→$S4S n=$N4 $(det)"
# ── C5 el timer corre solo
H5=$(en $IPJ 'date -d "+5 min" +%H:%M')
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"rr_hora\":\"$H5\"},\"min\":20,\"caso\":\"C5\"}"
S5S=$(sq "x.get('snapshot','')"); E5=$(estado); MQ=$(maq); echo "C5: hora $H5 · timer $(l 5) · snapshot del trabajo ${S5S:0:8}"
S5T=""; for i in $(seq 1 60); do S5T=$(en $IPJ 'sudo python3 -c "import json;print(json.load(open(\"/var/lib/faceops-restic/ultimo.json\"))[\"snapshot\"])"' 2>/dev/null); [ -n "$S5T" ] && [ "$S5T" != "$S5S" ] && break; sleep 15; done
J5=$(en $IPJ 'sudo journalctl -u faceops-restic.service --since "-20 min" --no-pager -o cat | grep -c "Finished\|Deactivated successfully"')
[ "$E5" = exitoso ] && [ "$(l 5)" = "*-*-* $H5:00" ] && [ -n "$S5T" ] && [ "$S5T" != "$S5S" ] && [ "$J5" -ge 1 ] \
  && anotar C5 "El timer corre solo a la hora programada" ok "timer $H5: snapshot ${S5T:0:8} creado por systemd (el del trabajo era ${S5S:0:8})" \
  || anotar C5 "El timer corre solo a la hora programada" falla "$E5 cal=$(l 5) $S5S→$S5T journal=$J5"
# ── C6 estado
MQa=$(maq)
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"rr_modo\":\"estado\"},\"min\":15,\"caso\":\"C6\"}"
S6=$(sq "x.get('snapshot','')"); P6=$(sq "x.get('proxima','')"); MQ=$(maq); rd
ok_ && [ "$(sq "x.get('configurado')")" = True ] && [ "$S6" = "$S5T" ] && [ -n "$P6" ] && [ "$(sq "','.join(x.get('rutas') or [])")" = /datos ] && [ "$MQ" = "$MQa" ] && [ "$(rdq "r['respaldo'].get('snapshot')")" = "$S6" ] \
  && anotar C6 "Estado" ok "snapshot ${S6:0:8} ($(sq "x.get('snapshots')") en el repositorio), /datos, próxima $P6; máquina sin cambios" \
  || anotar C6 "Estado" falla "$(estado) conf=$(sq "x.get('configurado')") $S6/$S5T próxima=$P6 cambio=$([ "$MQ" = "$MQa" ] && echo no || echo si) $(det)"
# ── C7 restaurar
L7a=$(en $IPJ 'sudo find /datos -type f -printf "%p %s\n" | sort | sha256sum | cut -c1-12')
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"rr_modo\":\"restaurar\"},\"min\":20,\"caso\":\"C7\"}"
D7=$(sq "x.get('directorio','')"); A7=$(sq "x.get('archivos_restaurados')"); B7=$(sq "x.get('archivos_snapshot')")
L7b=$(en $IPJ 'sudo find /datos -type f -printf "%p %s\n" | sort | sha256sum | cut -c1-12'); C7=$(en $IPJ "sudo test -f '$D7/datos/.faceops-restic-canario' && echo si"); rd
ok_ && [ "$(sq "x.get('canario')")" = True ] && [ -n "$A7" ] && [ "$A7" = "$B7" ] && [ "$C7" = si ] && [ "$L7a" = "$L7b" ] && [ "$(rdq "(r['respaldo'].get('ultima_restauracion') or {}).get('directorio')")" = "$D7" ] \
  && anotar C7 "Restaurar en un directorio aparte" ok "$A7 de $B7 archivos en $D7; canario presente; /datos intacto; ficha con la restauración" \
  || anotar C7 "Restaurar en un directorio aparte" falla "$(estado) $A7/$B7 canario=$C7 datos $L7a→$L7b $(det)"
# ── C8 S3 inalcanzable con respaldo ya programado
MQa=$(maq); rd; S8a=$(rdq "r['respaldo'].get('snapshot')")
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"rr_s3\":\"$MALO\",\"rr_hora\":\"03:15\"},\"min\":20,\"caso\":\"C8\"}"
VA=$(sq "x.get('vuelta_atras','')"); MQ=$(maq); rd
[ "$(estado)" = fallido ] && { echo "$VA$(jq_ "r.get('resultado','')")" | grep -q "restaurado el timer anterior"; } && [ "$(l 2)" = enabled ] && [ "$(l 5)" = "$(echo "$MQa" | sed -n 5p)" ] && [ "$(rdq "r['respaldo'].get('snapshot')")" = "$S8a" ] \
  && en $IPJ 'sudo grep -q "^RESTIC_REPOSITORY=s3:http://192.168.122.167:9000/" /etc/faceops-restic/env' \
  && anotar C8 "S3 inalcanzable con respaldo programado" ok "fallido con vuelta atrás ($VA): timer habilitado con su horario $(l 5) y repositorio anterior; ficha conserva ${S8a:0:8}" \
  || anotar C8 "S3 inalcanzable con respaldo programado" falla "$(estado) va=$VA timer=$(l 2) cal=$(l 5) ficha=$(rdq "r['respaldo'].get('snapshot')")/$S8a $(det)"
# ── C9 restaurar sobre el volumen respaldado
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"rr_modo\":\"restaurar\",\"rr_destino\":\"/datos\"},\"min\":10,\"caso\":\"C9\"}"
L9=$(en $IPJ 'sudo find /datos -type f -printf "%p %s\n" | sort | sha256sum | cut -c1-12')
no_ && [ "$L9" = "$L7b" ] && anotar C9 "Restaurar sobre /datos" ok "rechazado ($(det)); /datos sin cambios" || anotar C9 "Restaurar sobre /datos" falla "$(estado) datos $L7b→$L9"
certificar "$N" "Plan respaldo restic · C1–C9 (planes-prueba.yml)"
echo "CASOS $CASOS" | cut -c1-3000
echo FIN-RESTIC
