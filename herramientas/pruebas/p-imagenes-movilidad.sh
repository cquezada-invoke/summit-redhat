# Plan «OpenShift · Evaluar imágenes (uso y antigüedad)» (C1–C6) sobre ocp-sno, con el namespace de prueba faceops-prueba-mov
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="OpenShift · Evaluar imágenes (uso y antigüedad)"
NS=faceops-prueba-mov; R=image-registry.openshift-image-registry.svc:5000/$NS
# Escenario: en uso (Deployment con pod), referenciada (Deployment en cero), rollout con revisión anterior, y una sin despliegue
$OC create ns $NS >/dev/null 2>&1
# imágenes construidas en el clúster (quedan en el registro interno); la imagen base que importa new-build es externa y no debe listarse
for x in "sinuso:v1 ubi-minimal:9.4" "enuso:v1 ubi-minimal:latest" "cero:v1 ubi-micro:latest" "rollout:v1 ubi-minimal:9.5" "rollout:v2 ubi-minimal:9.6"; do
  set -- $x; b=b-${1/:/-}
  $OC -n $NS get istag $1 >/dev/null 2>&1 || $OC -n $NS get bc $b >/dev/null 2>&1 || $OC -n $NS new-build --name=$b --dockerfile="FROM registry.access.redhat.com/ubi9/$2
LABEL faceops.prueba=$1" --to=$1 >/dev/null 2>&1
done
for i in $(seq 1 40); do n=0; for t in sinuso:v1 enuso:v1 cero:v1 rollout:v1 rollout:v2; do $OC -n $NS get istag $t >/dev/null 2>&1 && n=$((n+1)); done; [ $n = 5 ] && break; sleep 15; done
mk() { $OC -n $NS get deploy $1 >/dev/null 2>&1 || $OC -n $NS create deployment $1 --image=$2 --replicas=$3 -- sleep infinity >/dev/null; }
mk enuso $R/enuso:v1 1; mk cero $R/cero:v1 0
if ! $OC -n $NS get deploy rollout >/dev/null 2>&1; then mk rollout $R/rollout:v1 1; sleep 40; $OC -n $NS set image deploy/rollout rollout=$R/rollout:v2 >/dev/null; fi
$OC -n $NS rollout status deploy/rollout --timeout=180s >/dev/null 2>&1; $OC -n $NS rollout status deploy/enuso --timeout=180s >/dev/null 2>&1
n=0; for t in sinuso:v1 enuso:v1 cero:v1 rollout:v1 rollout:v2; do $OC -n $NS get istag $t >/dev/null 2>&1 && n=$((n+1)); done
for i in $(seq 1 20); do [ "$($OC -n $NS get pods --field-selector=status.phase=Running -o name 2>/dev/null | grep -v build | wc -l)" -ge 2 ] && break; sleep 15; done
[ $n = 5 ] && [ "$($OC -n $NS get pods --field-selector=status.phase=Running -o name 2>/dev/null | grep -v build | wc -l)" -ge 2 ] || { echo "CASO -- SIN ESCENARIO: no se pudo preparar $NS ($n de 5 imágenes)"; exit 1; }
caso() {  # como el de comun.sh, pero si el arnés se corta (FaceOps reiniciado) sigue esperando el mismo ticket
  local out tk
  out=$(sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -u - "$1" < $P/lanzar.py 2>/dev/null)
  RES=$(echo "$out" | grep '^RES ' | tail -1 | cut -c5-); tk=$(echo "$out" | grep '^TICKET ' | tail -1 | cut -d' ' -f2)
  for k in 1 2 3; do
    [ -n "$RES" ] || [ -z "$tk" ] && break
    sleep 20; RES=$(sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -u - "{\"auto\":\"$N\",\"reanudar\":\"$tk\",\"min\":12}" < $P/lanzar.py 2>/dev/null | grep '^RES ' | tail -1 | cut -c5-)
  done
  [ -z "$RES" ] && RES='{"estado":"sin respuesta del arnés"}'
  echo "RES $(echo "$RES" | cut -c1-300)"
}
foto() { $OC -n $NS get is,rs,deploy,pods -o name 2>/dev/null | sort | md5sum | cut -c1-8; }
est_() { jq_ "next((x['estado'] for x in s.get('faceops_imagenes_movilidad',{}).get('detalle',[]) if x['id']=='$NS/$1' and x['rev']==0),'-')"; }

caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{},\"min\":12,\"caso\":\"C1\"}"
C1=$(jq_ "len([p for p in s.get('faceops_imagenes_movilidad',{}).get('plan',[]) if p['id'].startswith('$NS/')])")
SYS=$(jq_ "any(x['id'].startswith(('openshift','kube-','$NS/ubi-')) for x in s.get('faceops_imagenes_movilidad',{}).get('detalle',[]))")
RG=$(jq_ "len(s.get('faceops_imagenes_movilidad',{}).get('rangos',[]))")
E="enuso=$(est_ enuso:v1) cero=$(est_ cero:v1) rollout:v1=$(est_ rollout:v1) rollout:v2=$(est_ rollout:v2) sinuso=$(est_ sinuso:v1)"; echo "$E"
[ "$(estado)" = exitoso ] && [ "$C1" = 0 ] && [ "$SYS" = False ] && [ "${RG:-0}" -ge 2 ] && echo "$E" | grep -q "enuso=en uso cero=referenciada rollout:v1=sin uso 0–3 meses rollout:v2=en uso sinuso=sin uso 0–3 meses" \
  && anotar C1 "Valores por defecto" ok "$E; candidatas del ns de prueba 0; $RG rangos con tamaño; sin namespaces del sistema ni las imágenes base externas (ubi-*)" || anotar C1 "Valores por defecto" falla "$(estado) $E cand=$C1 sys=$SYS rangos=$RG"

caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{\"im_meses\":0,\"im_rangos\":\"1,2\",\"im_quay\":\"quay.lab.local\"},\"min\":12,\"caso\":\"C2\"}"
P2=$(jq_ "sorted(p['id']+'|'+p['riesgo'] for p in s.get('faceops_imagenes_movilidad',{}).get('plan',[]) if p['id'].startswith('$NS/'))")
D2=$(jq_ "next((p['destino']+' · '+p['comando'] for p in s.get('faceops_imagenes_movilidad',{}).get('plan',[]) if p['id']=='$NS/sinuso:v1'),'-')")
H2=$(jq_ "next(('historial' if 'rollback de $NS/rollout' in p['referencias'] else p['referencias']) for p in s.get('faceops_imagenes_movilidad',{}).get('plan',[]) if p['id']=='$NS/rollout:v1')")
T2=$(jq_ "'oc tag -d $NS/sinuso:v1' in s.get('faceops_imagenes_movilidad',{}).get('poda',[])")
echo "plan=$P2 · $D2 · rollout=$H2 · poda=$T2"
[ "$(estado)" = exitoso ] && [ "$P2" = "['$NS/rollout:v1|medio', '$NS/sinuso:v1|bajo']" ] && echo "$D2" | grep -q "^quay.lab.local/$NS/sinuso:v1 · skopeo copy --all --preserve-digests docker://$R/sinuso@sha256:" && [ "$H2" = historial ] && [ "$T2" = True ] \
  && anotar C2 "Umbral 0, rangos 1,2 y Quay" ok "candidatas $P2; $(echo $D2 | cut -c1-160); rollout:v1 con historial; poda oc tag -d" || anotar C2 "Umbral 0, rangos 1,2 y Quay" falla "$(estado) $P2 $D2 $H2 $T2"

caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{\"im_incluir\":\"sin-imagenes-*\",\"im_quay\":\"quay.lab.local\",\"im_quay_org\":\"movilidad\"},\"min\":12,\"caso\":\"C7\"}"
Q7=$(jq_ "(s.get('faceops_imagenes_movilidad',{}).get('revisiones'), s.get('faceops_imagenes_movilidad',{}).get('candidatas'), (s.get('faceops_imagenes_movilidad',{}).get('quay_detectado') or {}).get('url'))")
[ "$(estado)" = exitoso ] && [ "$Q7" = "(0, 0, 'https://quay.lab.local/organization/movilidad')" ] && anotar C7 "Sin imágenes internas y con Quay" ok "revisiones, candidatas, enlace = $Q7" || anotar C7 "Sin imágenes internas y con Quay" falla "$(estado) $Q7"
caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{\"im_incluir\":\"sin-imagenes-*\"},\"min\":12,\"caso\":\"C8\"}"
Q8=$(jq_ "(s.get('faceops_imagenes_movilidad',{}).get('revisiones'), s.get('faceops_imagenes_movilidad',{}).get('quay_detectado'))")
[ "$(estado)" = exitoso ] && [ "$Q8" = "(0, {})" ] && anotar C8 "Sin imágenes internas ni Quay" ok "revisiones y Quay = $Q8 (mensaje breve en la ficha)" || anotar C8 "Sin imágenes internas ni Quay" falla "$(estado) $Q8"
caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{\"im_meses\":0,\"im_incluir\":\"$NS\",\"im_quay\":\"quay.lab.local\",\"im_quay_org\":\"movilidad\"},\"min\":12,\"caso\":\"C3\"}"
A3=$(jq_ "all(x['id'].startswith('$NS/') for x in s.get('faceops_imagenes_movilidad',{}).get('detalle',[]))"); N3=$(jq_ "s.get('faceops_imagenes_movilidad',{}).get('revisiones')")
D3=$(jq_ "next((p['destino'] for p in s.get('faceops_imagenes_movilidad',{}).get('plan',[]) if p['id']=='$NS/sinuso:v1'),'-')")
G3=$(jq_ "s.get('faceops_imagenes_movilidad',{}).get('informe_git')"); GR=$(jq_ "s.get('faceops_imagenes_movilidad',{}).get('git')"); P3=$(jq_ "sorted(p['id'] for p in s.get('faceops_imagenes_movilidad',{}).get('plan',[]))")
TK3=$(ticket)
[ "$(estado)" = exitoso ] && [ "$A3" = True ] && [ "$N3" = 5 ] && [ "$D3" = "quay.lab.local/movilidad/$NS-sinuso:v1" ] \
  && anotar C3 "Solo el namespace de prueba y organización fija" ok "$N3 revisiones, todas de $NS; destino $D3" || anotar C3 "Solo el namespace de prueba y organización fija" falla "$(estado) todas=$A3 n=$N3 $D3"
sleep 20
FI=$(sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -1
import json
from app import create_app
create_app()
from app.bd import q1
d = json.loads(q1("SELECT datos FROM equipos WHERE nombre='ocp-sno' AND estado!='retirado'")["datos"] or "{}").get("imagenes_movilidad") or {}
print(d.get("ticket", "-"), d.get("candidatas", "-"))
Q
)
RAW=$(curl -s -o /dev/null -w '%{http_code}' "http://192.168.122.1:3000/repositorio/openshift-manifiestos/raw/branch/main/imagenes-movilidad/ocp-sno/plan.md")
echo "git=$G3 $GR · ficha=$FI · raw=$RAW"
[ "$G3" = True ] && [ "${FI%% *}" = "$TK3" ] && anotar C4 "Informe en Git y en la ficha del clúster" ok "Git $GR (plan.md/csv/json; HTTP $RAW); ficha ocp-sno con $FI" || anotar C4 "Informe en Git y en la ficha del clúster" falla "git=$G3 $GR ficha=$FI tk=$TK3"

F1=$(foto)
caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{\"im_meses\":0,\"im_incluir\":\"$NS\",\"im_quay\":\"quay.lab.local\",\"im_quay_org\":\"movilidad\"},\"min\":12,\"caso\":\"C5\"}"
P5=$(jq_ "sorted(p['id'] for p in s.get('faceops_imagenes_movilidad',{}).get('plan',[]))"); F2=$(foto)
[ "$(estado)" = exitoso ] && [ "$P5" = "$P3" ] && [ "$F1" = "$F2" ] && anotar C5 "Repetir" ok "mismo plan $P5; ImageStreams/ReplicaSets/pods sin cambios ($F1)" || anotar C5 "Repetir" falla "$(estado) $P5 vs $P3 foto $F1→$F2"

caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{\"im_rangos\":\"a,b\"},\"min\":8,\"caso\":\"C6\"}"
E6=$(estado); echo "$RES" | grep -o 'Datos[^"]*' | head -1
[ "$E6" = fallido ] || [ "$E6" = "rechazado al solicitar" ] && anotar C6 "Rangos no válidos" ok "$E6: $(echo "$RES" | grep -o 'Datos[^"]*' | head -1 | cut -c1-120)" || anotar C6 "Rangos no válidos" falla "$E6"
$OC delete ns $NS --wait=true --timeout=300s >/dev/null 2>&1
caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{},\"min\":12,\"caso\":\"C9\"}"
R9=$(jq_ "(s.get('faceops_imagenes_movilidad',{}).get('revisiones'), any(x['id'].startswith('$NS/') for x in s.get('faceops_imagenes_movilidad',{}).get('detalle',[])))")
[ "$(estado)" = exitoso ] && [ "${R9##*, }" = "False)" ] && anotar C9 "Evaluación por defecto sin el escenario" ok "revisiones del registro interno y escenario presente = $R9; la ficha queda con el estado real" || anotar C9 "Evaluación por defecto sin el escenario" falla "$(estado) $R9"
echo "CASOS $CASOS"
certificar "$N" "Plan imágenes y movilidad · C1–C9 (planes-prueba.yml)"
