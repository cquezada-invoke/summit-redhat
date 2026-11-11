# Plan de «Migración · JBoss a EAP 8»: origen nodo-jboss-dev (JBoss 5.1 con catalogo-legacy y un EAP 7.4 emulado),
# destino nodo-jboss-qa (EAP 8; se enciende para la prueba y se apaga al final, como estaba).
source ~/summit-redhat/herramientas/pruebas/comun.sh
# ── El laboratorio se comparte y FaceOps se reinicia a menudo: «caso» sigue el mismo ticket si el arnés se corta
caso() {
  local out tk j="$1" i n
  RES=""
  for n in 1 2 3 4 5 6 7 8; do
    case "$1" in *'"rhsm":true'*) sudo podman cp ~/faceops/rhsm-activacion.json faceops:/tmp/rhsm.json 2>/dev/null ;; esac
    out=$(sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -u - "$j" < $P/lanzar.py 2>/dev/null)
    RES=$(echo "$out" | grep '^RES ' | tail -1 | cut -c5-)
    [ -n "$RES" ] && break
    tk=$(echo "$out" | grep '^TICKET ' | tail -1 | awk '{print $2}')
    for i in $(seq 1 40); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done; sleep 20
    [ -n "$tk" ] && j=$(python3 -c 'import json,sys;d=json.loads(sys.argv[1]);d["reanudar"]=sys.argv[2];print(json.dumps(d,ensure_ascii=False))' "$1" "$tk")
    echo "  (arnés cortado en $(echo "$1" | grep -o '"caso":"[^"]*"'); reanudo ${tk:-sin ticket})" >&2
  done
  case "$1" in *'"rhsm":true'*) sudo podman exec faceops rm -f /tmp/rhsm.json 2>/dev/null ;; esac
  [ -z "$RES" ] && RES='{"estado":"sin respuesta del arnés"}'
  echo "RES $RES"
}
ip_de() {  # la IP que tiene FaceOps o, si aún no la tiene, la del DHCP del anfitrión
  local ip; ip=$(sudo podman exec -i faceops python3 - "$1" 2>/dev/null <<'Q' | tail -1
import sys
from app import create_app
create_app()
from app.bd import q1
e = q1("SELECT * FROM equipos WHERE nombre=? AND estado!='retirado'", sys.argv[1]); print((e or {}).get("ip") or "")
Q
)
  [ -z "$ip" ] && ip=$(sudo virsh domifaddr "$1" --source lease 2>/dev/null | awk '/ipv4/{print $4}' | cut -d/ -f1 | head -1)
  echo "$ip"
}
ok_() { [ "$(estado)" = exitoso ]; }; no_() { [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; }
N="Migración · JBoss a EAP 8"; O=nodo-jboss-dev; D=nodo-jboss-qa
TOK=$(sudo podman exec faceops printenv FACEOPS_RESPALDO_TOKEN 2>/dev/null); G=http://192.168.122.1:3000/api/v1/repos/repositorio/migraciones-jboss
mj() {  # mj <caso> <equipo origen> '<params extra>' <min>
  caso "{\"auto\":\"$N\",\"equipo\":\"\",\"params\":{\"mj_origen\":\"$2\"${3:+,$3}},\"min\":${4:-20},\"caso\":\"$1\"}" >/dev/null   # elige sus equipos en el formulario
  echo "  $1: $(estado) $(ticket) · $(jq_ "json.dumps(s.get('faceops_migracion_jboss') or {}, ensure_ascii=False)[:300]") $(jq_ "(r.get('error') or r.get('detalle') or '')[:300]")"
}
st() { jq_ "json.dumps((s.get('faceops_migracion_jboss') or {}).get('$1'), ensure_ascii=False)"; }
git_ult() { curl -s -m 10 -H "Authorization: token $TOK" "$G/commits?path=$1/evaluacion.md&limit=1" | python3 -c 'import json,sys;l=json.load(sys.stdin);print(l[0]["commit"]["message"] if l else "")' 2>/dev/null; }
git_md() { curl -s -m 10 -H "Authorization: token $TOK" "$G/raw/$1/evaluacion.md"; }
IO=$(ip_de $O); IQ=$(ip_de $D)
huella5() { en $IO 'sudo find /opt/jboss-5.1.0.GA/server -type f -exec md5sum {} + 2>/dev/null | sort | md5sum | cut -c1-12'; }
huella7() { en $IO 'sudo find /opt/jboss-eap-7.4 -type f -exec md5sum {} + 2>/dev/null | sort | md5sum | cut -c1-12'; }
sano() { case "$1" in 200|301|302|401|403) return 0;; *) return 1;; esac; }
http() { en $IQ "curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8080/$1/"; }
dep() { en $IQ "sudo ls /opt/rh/eap8/root/usr/share/wildfly/standalone/data/content >/dev/null 2>&1; sudo /opt/rh/eap8/root/usr/share/wildfly/bin/jboss-cli.sh -c --command='deployment-info' 2>/dev/null | awk 'NR>1{print \$1}' | tr '\n' ' '"; }
dsn() { en $IQ "sudo /opt/rh/eap8/root/usr/share/wildfly/bin/jboss-cli.sh -c --command='/subsystem=datasources:read-children-names(child-type=data-source)' 2>/dev/null | grep -o '\"[A-Za-z0-9]*DS\"' | tr '\n' ' '"; }
QA_ANTES=$(sudo virsh domstate $D 2>/dev/null); echo "IP $O $IO · $D $IQ ($QA_ANTES)"
CASOS='[]'; TKS='[]'
H5=$(huella5)
mj C1 $O '"mj_modo":"evaluar","mj_apps":"catalogo-legacy"'
M1=$(git_ult $O); MD1=$(git_md $O | head -c 3000)
ok_ && [ "$(huella5)" = "$H5" ] && echo "$M1" | grep -q "$(ticket)" && echo "$MD1" | grep -q "catalogo-legacy" && echo "$MD1" | grep -q "java:jboss/datasources" \
  && anotar C1 "Evaluar JBoss 5.1" ok "JBoss $(st jboss) · apps $(st aplicaciones) · $(st puntos) puntos · Git: $M1 · origen intacto ($H5)" || anotar C1 "Evaluar JBoss 5.1" falla "$(estado) git=$M1 huella $H5/$(huella5) $(jq_ "r.get('detalle')")"
mj C2 $O '"mj_modo":"emular-origen"'
E2=$(en $IO 'cat /opt/jboss-eap-7.4/version.txt; ls /opt/jboss-eap-7.4/standalone/deployments | tr "\n" " "; sudo test -f /opt/jboss-eap-7.4/.faceops-emulado && echo marca; grep -c InventarioDS /opt/jboss-eap-7.4/standalone/configuration/standalone.xml'); H7=$(huella7)
mj C2b $O '"mj_modo":"emular-origen"'; E2b=$(st estado)
ok_ && echo "$E2" | grep -q "7.4.0" && echo "$E2" | grep -q "inventario-eap7.war" && echo "$E2" | grep -q marca && [ "$(huella7)" = "$H7" ] && echo "$E2b" | grep -q "ya emulado" \
  && anotar C2 "emular-origen EAP 7.4" ok "$(echo $E2) · repetir: $E2b, sin cambios ($H7)" || anotar C2 "emular-origen EAP 7.4" falla "$(estado) $E2 · $E2b"
mj C3 $O '"mj_modo":"evaluar","mj_jboss_home":"/opt/jboss-eap-7.4"'
MD3=$(git_md $O | head -c 4000)
ok_ && echo "$MD3" | grep -q "eap6-7" && echo "$MD3" | grep -q "inventario-eap7" && echo "$MD3" | grep -q "InventarioDS" && ! echo "$MD3" | grep -q "clave-emulada" && [ "$(huella7)" = "$H7" ] \
  && anotar C3 "Evaluar EAP 7.4" ok "JBoss $(st jboss) · apps $(st aplicaciones) · datasource InventarioDS desde standalone.xml, sin contraseña en el informe" || anotar C3 "Evaluar EAP 7.4" falla "$(estado) $(st jboss) $(echo "$MD3" | head -c 300)"
[ "$QA_ANTES" = running ] || energia $D encender
IQ=$(ip_de $D)
mj C4 $O "\"mj_modo\":\"migrar\",\"mj_apps\":\"catalogo-legacy\",\"mj_destino\":\"$D\"" 30
C4H=$(http catalogo-legacy); C4D=$(dsn)
ok_ && sano "$C4H" && [ "$(huella5)" = "$H5" ] && anotar C4 "Migrar catalogo-legacy (JBoss 5)" ok "migradas $(st migradas) · HTTP $C4H en EAP 8 · datasources $C4D · origen intacto" || anotar C4 "Migrar catalogo-legacy" falla "$(estado) http=$C4H ds=$C4D $(jq_ "r.get('detalle')")"
mj C5 $O "\"mj_modo\":\"migrar\",\"mj_apps\":\"inventario-eap7\",\"mj_jboss_home\":\"/opt/jboss-eap-7.4\",\"mj_destino\":\"$D\"" 30
C5H=$(http inventario-eap7); C5B=$(en $IQ "curl -s -m 10 http://127.0.0.1:8080/inventario-eap7/")
ok_ && [ "$C5H" = 200 ] && echo "$C5B" | grep -q "inventario-eap7 ok" && anotar C5 "Migrar inventario-eap7 (EAP 7.4)" ok "HTTP $C5H: $(echo $C5B | head -c 80) · datasources $(dsn)" || anotar C5 "Migrar inventario-eap7" falla "$(estado) http=$C5H $C5B $(jq_ "r.get('detalle')")"
mj C6 $O "\"mj_modo\":\"migrar\",\"mj_apps\":\"catalogo-legacy\",\"mj_destino\":\"$D\"" 30
C6D=$(dsn)
ok_ && sano "$(http catalogo-legacy)" && [ -z "$(echo $C6D | tr ' ' '\n' | sort | uniq -d)" ] \
  && anotar C6 "Repetir C4" ok "sigue HTTP 200; datasources sin duplicar: $C6D" || anotar C6 "Repetir C4" falla "$(estado) $C4D → $C6D"
mj C7 $O "\"mj_modo\":\"migrar\",\"mj_apps\":\"app-rota\",\"mj_jboss_home\":\"/opt/jboss-eap-7.4\",\"mj_destino\":\"$D\"" 30
C7D=$(dep); D7=$(jq_ "' · '.join(r.get('fallos') or [])")
no_ && ! echo "$C7D" | grep -q app-rota && sano "$(http catalogo-legacy)" && [ "$(http inventario-eap7)" = 200 ] && [ "$(huella7)" = "$H7" ] && echo "$D7" | grep -q "se retiraron del destino" \
  && anotar C7 "Migrar app-rota" ok "ticket fallido controlado: $(echo "$D7" | grep -o 'No quedaron[^·]*' | head -1) · desplegadas: $C7D · origen intacto" || anotar C7 "Migrar app-rota" falla "$(estado) dep=$C7D $D7"
mj C8 nodo-postgres '"mj_modo":"evaluar"'
D8=$(jq_ "(r.get('error') or r.get('detalle') or '')")
no_ && echo "$D8" | grep -qi "No se encontró una instalación de JBoss" && anotar C8 "Origen sin JBoss" ok "falla controlada: $(echo "$D8" | grep -o 'No se encontró[^(]*' | head -1)" || anotar C8 "Origen sin JBoss" falla "$(estado) $D8"
mj C9 $O '"mj_modo":"migrar","mj_apps":"catalogo-legacy"'
D9=$(jq_ "(r.get('error') or r.get('detalle') or '')")
no_ && echo "$D9" | grep -q "equipo de destino" && [ "$(huella5)" = "$H5" ] && anotar C9 "Migrar sin destino" ok "rechazado: $(echo "$D9" | head -c 200)" || anotar C9 "Migrar sin destino" falla "$(estado) $D9"
echo "JBOSS: $CASOS" | cut -c1-2500
grep -q '"falla"' <<<"$CASOS" || certificar "$N" "planes-prueba.yml · Migración · JBoss a EAP 8 (C1–C9)"
[ "$QA_ANTES" = running ] || energia $D apagar
echo "FIN-JBOSS $D: $(sudo virsh domstate $D)"
