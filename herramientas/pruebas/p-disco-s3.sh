# Planes «FaceOps · Agregar disco» (C1–C3) y «Storage · Servicio S3 del data lake» (C1–C5), y paso del lake al storage
source ~/summit-redhat/herramientas/pruebas/comun.sh
energia nodo-storage encender; energia nodo-jboss-dev encender
IPS=$(ip_de nodo-storage); IPJ=$(ip_de nodo-jboss-dev); echo "nodo-storage $IPS · nodo-jboss-dev $IPJ"
# ── Agregar disco (solo si no está certificada: C1 crea un disco nuevo)
if [ "${SALTAR_AD:-0}" != 1 ]; then
N="FaceOps · Agregar disco"; SER="fo$(date +%s | tail -c 9)"
AD="{\"ad_equipo\":\"nodo-storage\",\"ad_gb\":\"5\",\"ad_origen\":\"hipervisor\",\"ad_nombre\":\"pruebaad\",\"ad_formatear\":true,\"ad_serial\":\"$SER\",\"ad_pool\":\"images\",\"ad_montaje\":\"/srv/prueba-ad\"}"
D0=$(en $IPS 'lsblk -dn -o NAME | wc -l')
caso "{\"auto\":\"$N\",\"params\":$AD,\"min\":20,\"caso\":\"C1\"}"
V=$(en $IPS "findmnt -no SOURCE,FSTYPE /srv/prueba-ad; grep /srv/prueba-ad /etc/fstab | cut -c1-5; lsblk -dn -o SERIAL | grep -c $SER"); echo "$V"
D1=$(en $IPS 'lsblk -dn -o NAME | wc -l')
[ "$(estado)" = exitoso ] && echo "$V" | grep -q xfs && echo "$V" | grep -q UUID= && [ "$D1" = $((D0+1)) ] && anotar C1 "Disco nuevo en Servidores habilitados" ok "5 GB serie $SER, XFS en /srv/prueba-ad, fstab por UUID" || anotar C1 "Disco nuevo en Servidores habilitados" falla "$(estado) $V discos $D0→$D1 $(jq_ "r.get('detalle')")"
U1=$(en $IPS 'findmnt -no UUID /srv/prueba-ad')
caso "{\"auto\":\"$N\",\"params\":$AD,\"min\":20,\"caso\":\"C2\"}"
D2=$(en $IPS 'lsblk -dn -o NAME | wc -l'); U2=$(en $IPS 'findmnt -no UUID /srv/prueba-ad')
[ "$(estado)" = exitoso ] && [ "$D2" = "$D1" ] && [ -n "$U1" ] && [ "$U1" = "$U2" ] && anotar C2 "Repetir: idempotente" ok "mismos $D2 discos y UUID $U2" || anotar C2 "Repetir: idempotente" falla "$(estado) discos $D1→$D2 UUID $U1→$U2"
PJ=$(sudo podman exec -i faceops python3 - 2>/dev/null <<'Q' | tail -1
from app import create_app
create_app()
import json
from app.bd import q1
t = q1("SELECT t.parametros FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE a.nombre_aap='FaceOps · Agregar disco' AND t.estado='exitoso' AND t.parametros LIKE '%nodo-jboss-dev%' ORDER BY t.id DESC LIMIT 1")
print(json.dumps({k: v for k, v in json.loads(t["parametros"]).items() if k.startswith("ad_")}) if t else "{}")
Q
)
MJ=$(echo "$PJ" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("ad_montaje",""))')
caso "{\"auto\":\"$N\",\"params\":$PJ,\"min\":20,\"caso\":\"C3\"}"
VJ=$(en $IPJ "findmnt -no FSTYPE $MJ")
[ "$(estado)" = exitoso ] && [ "$VJ" = xfs ] && anotar C3 "Máquina de Nodos demo (disco existente)" ok "nodo-jboss-dev $MJ sigue montado ($VJ)" || anotar C3 "Máquina de Nodos demo (disco existente)" falla "$(estado) $MJ=$VJ $(jq_ "r.get('detalle')")"
certificar "$N" "Plan agregar disco · C1–C3 (planes-prueba.yml)"
energia nodo-jboss-dev apagar
fi
# ── S3 del data lake (antes: nodo-storage registrado en Red Hat, para tener repositorios si hay que instalar nftables)
caso "{\"auto\":\"Sistema · Registrar en Red Hat\",\"equipo\":\"nodo-storage\",\"params\":{},\"rhsm\":true,\"min\":15,\"caso\":\"previo\"}"; echo "registro nodo-storage: $(estado) $(ticket)"
CASOS='[]'; TKS='[]'; N="Storage · Servicio S3 del data lake"
S3="{\"ls_modo\":\"instalar\",\"ls_dir\":\"/srv/datalake\",\"ls_clientes\":\"192.168.122.50/32,192.168.122.1/32\"}"
caso "{\"auto\":\"$N\",\"equipo\":\"nodo-storage\",\"params\":{\"ls_modo\":\"instalar\",\"ls_dir\":\"/opt/lake-raiz\",\"ls_clientes\":\"192.168.122.50/32\"},\"min\":15,\"caso\":\"C1\"}"
X=$(en $IPS 'test -e /opt/lake-raiz && echo existe || echo no; systemctl is-enabled lake-s3 2>&1 | head -1')
[ "$(estado)" = fallido ] && echo "$X" | head -1 | grep -q no && anotar C1 "Sin disco dedicado" ok "rechazado; no creó /opt/lake-raiz" || anotar C1 "Sin disco dedicado" falla "$(estado) $X"
caso "{\"auto\":\"$N\",\"equipo\":\"nodo-storage\",\"params\":$S3,\"min\":25,\"caso\":\"C2\"}"
A=$(en $IPS 'systemctl is-active lake-s3; systemctl is-enabled lake-s3; sudo firewall-cmd --list-rich-rules | grep -c "port=\"9000\""')
H1=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://$IPS:9000/); H2=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://$IPS:8888/)
T0=$(en $IPS 'systemctl show lake-s3 -p ActiveEnterTimestamp --value'); echo "$A · 9000=$H1 8888=$H2 · desde $T0"
[ "$(estado)" = exitoso ] && echo "$A" | head -1 | grep -q '^active' && echo "$A" | sed -n 2p | grep -q enabled && [ "$H1" != 000 ] && [ "$H2" != 000 ] && anotar C2 "Instalar sobre /srv/datalake" ok "lake-s3 activo y habilitado; 9000=$H1 8888=$H2; reglas por cliente" || anotar C2 "Instalar sobre /srv/datalake" falla "$(estado) $A $H1 $H2 $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"equipo\":\"nodo-storage\",\"params\":$S3,\"min\":15,\"caso\":\"C3\"}"
T1=$(en $IPS 'systemctl show lake-s3 -p ActiveEnterTimestamp --value')
[ "$(estado)" = exitoso ] && [ "$T0" = "$T1" ] && anotar C3 "Repetir instalar" ok "sin reinicio del servicio (desde $T1)" || anotar C3 "Repetir instalar" falla "$(estado) $T0 → $T1"
caso "{\"auto\":\"$N\",\"equipo\":\"nodo-storage\",\"params\":{\"ls_modo\":\"estado\",\"ls_dir\":\"/srv/datalake\",\"ls_clientes\":\"192.168.122.50/32\"},\"min\":15,\"caso\":\"C5a\"}"
E5=$(estado); S5=$(jq_ "s.get('faceops_lake_s3')"); echo "estado: $S5"
caso "{\"auto\":\"$N\",\"equipo\":\"nodo-storage\",\"params\":{\"ls_modo\":\"instalar\",\"ls_dir\":\"/srv/datalake\",\"ls_clientes\":\"10.0.0/33\"},\"min\":15,\"caso\":\"C5b\"}"
[ "$E5" = exitoso ] && [ "$S5" != None ] && { [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; } && anotar C5 "Estado y CIDR inválido" ok "estado informa el disco; CIDR inválido rechazado" || anotar C5 "Estado y CIDR inválido" falla "$E5/$S5/$(estado)"
# C4: el lake pasa al storage, se reinicia el storage y los datos siguen
NS=demo-datalake; OLD=$($OC -n $NS get pod -l app=minio -o jsonpath='{.items[0].status.podIP}')
cnt() { $OC -n demo-fiscalia exec deploy/inteligencia -- python3 -c "
from comun import trino
a=trino('SELECT COUNT(*) n FROM lake.fiscalia.denuncias_historico').iloc[0,0]; b=trino('SELECT COUNT(*) n FROM lake.telco.clientes').iloc[0,0]; c=trino('SELECT COUNT(*) n FROM lake.app_crm.clientes').iloc[0,0]; print(a,b,c)" 2>/dev/null | tail -1; }
C0=$(cnt); echo "antes: $C0 (fiscalía, telco, javia crm) · S3 viejo $OLD"
if [ -n "$OLD" ]; then
  $OC -n $NS exec -i deploy/consola -- sh -c 'cat > /tmp/migrar.py' < ~/summit-redhat/herramientas/pruebas/migrar_lake.py
  $OC -n $NS exec deploy/consola -- python3 /tmp/migrar.py http://$OLD:9000 http://$IPS:9000 && $OC -n $NS exec deploy/consola -- python3 /tmp/migrar.py http://$OLD:9000 http://$IPS:9000 && {
    $OC -n $NS scale deploy/minio --replicas=0; $OC -n $NS delete svc minio --wait=true >/dev/null
    sed "s/192.168.122.167/$IPS/" ~/summit-redhat/demo-datalake/lake-s3-en-storage.yaml | $OC apply -f - | grep -v unchanged; sleep 5; }
fi
C1_=$(cnt); echo "tras migrar: $C1_"
energia nodo-storage apagar; energia nodo-storage encender; sleep 30
C2_=$(cnt); A4=$(en $IPS 'systemctl is-active lake-s3; df -h /srv/datalake | tail -1'); echo "tras reiniciar: $C2_ · $A4"
EP=$($OC -n $NS get endpoints minio -o jsonpath='{.subsets[0].addresses[0].ip}'); echo "S3 del lake ahora en $EP"
[ "$EP" = "$IPS" ] && [ -n "$C0" ] && [ "$C0" = "$C1_" ] && [ "$C1_" = "$C2_" ] && echo "$A4" | head -1 | grep -q '^active' && anotar C4 "Lake en el storage y reinicio" ok "conteos $C2_ iguales antes, tras migrar y tras reiniciar; lake-s3 vuelve solo" || anotar C4 "Lake en el storage y reinicio" falla "S3 en $EP · $C0 / $C1_ / $C2_ · $A4"
certificar "$N" "Plan S3 del lake · C1–C5 (planes-prueba.yml)"
echo FIN-DISCO-S3
