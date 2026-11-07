# Plan «OpenShift · Remediar alerta» (C1–C6)
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="OpenShift · Remediar alerta"; NS=faceops-prueba-ra
$OC create ns $NS >/dev/null 2>&1
$OC -n $NS create deployment web --image=registry.access.redhat.com/ubi9/ubi-minimal:latest -- sleep infinity >/dev/null 2>&1
$OC -n $NS apply -f - >/dev/null <<'Y'
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: datos}
spec: {accessModes: [ReadWriteOnce], resources: {requests: {storage: 1Gi}}}
Y
$OC -n $NS rollout status deploy/web --timeout=180s | tail -1
POD=$($OC -n $NS get pod -l app=web -o jsonpath='{.items[0].metadata.name}'); echo "pod $POD"
c() { caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":$1,\"min\":10,\"caso\":\"$2\"}"; }
RV=$($OC -n $NS get deploy web -o jsonpath='{.metadata.resourceVersion}')
c "{\"ra_accion\":\"diagnosticar\",\"ra_namespace\":\"$NS\"}" C1
RV2=$($OC -n $NS get deploy web -o jsonpath='{.metadata.resourceVersion}')
[ "$(estado)" = exitoso ] && [ "$RV" = "$RV2" ] && [ "$($OC -n $NS get pod -l app=web -o name)" = "pod/$POD" ] && anotar C1 diagnosticar ok "sin cambios (resourceVersion $RV)" || anotar C1 diagnosticar falla "$(estado) $(jq_ "r.get('detalle')")"
c "{\"ra_accion\":\"reiniciar-pod\",\"ra_namespace\":\"$NS\",\"ra_pod\":\"$POD\"}" C2
sleep 15; P2=$($OC -n $NS get pod -l app=web -o jsonpath='{.items[0].metadata.name}')
[ "$(estado)" = exitoso ] && [ -n "$P2" ] && [ "$P2" != "$POD" ] && anotar C2 reiniciar-pod ok "$POD → $P2" || anotar C2 reiniciar-pod falla "$(estado) $POD→$P2 $(jq_ "r.get('detalle')")"
c "{\"ra_accion\":\"escalar\",\"ra_namespace\":\"$NS\",\"ra_deployment\":\"web\",\"ra_max_replicas\":2}" C3a
R1=$($OC -n $NS get deploy web -o jsonpath='{.spec.replicas}'); E3a=$(estado)
c "{\"ra_accion\":\"escalar\",\"ra_namespace\":\"$NS\",\"ra_deployment\":\"web\",\"ra_max_replicas\":2}" C3b
R2=$($OC -n $NS get deploy web -o jsonpath='{.spec.replicas}')
[ "$E3a" = exitoso ] && [ "$(estado)" = exitoso ] && [ "$R1" = 2 ] && [ "$R2" = 2 ] && anotar C3 "escalar con tope 2" ok "1→$R1 y con tope se mantiene en $R2" || anotar C3 "escalar con tope 2" falla "$E3a/$(estado) réplicas $R1 $R2"
SC=$($OC -n $NS get pvc datos -o jsonpath='{.spec.storageClassName}'); EXP=$($OC get sc "$SC" -o jsonpath='{.allowVolumeExpansion}' 2>/dev/null)
$OC -n $NS set volume deploy/web --add --name=datos -t pvc --claim-name=datos -m /datos >/dev/null 2>&1; $OC -n $NS rollout status deploy/web --timeout=180s | tail -1
c "{\"ra_accion\":\"expandir-pvc\",\"ra_namespace\":\"$NS\",\"ra_pvc\":\"datos\"}" C4
CAP=$($OC -n $NS get pvc datos -o jsonpath='{.spec.resources.requests.storage}')
echo "sc=$SC expansible=$EXP capacidad pedida=$CAP"
if [ "$EXP" = true ]; then [ "$(estado)" = exitoso ] && [ "$CAP" = 2Gi ] && anotar C4 expandir-pvc ok "1Gi → $CAP ($SC)" || anotar C4 expandir-pvc falla "$(estado) $CAP"
else [ "$(estado)" = fallido ] && [ "$CAP" = 1Gi ] && anotar C4 expandir-pvc ok "StorageClass $SC no expande: error claro, sin cambios" || anotar C4 expandir-pvc falla "$(estado) $CAP $(jq_ "r.get('detalle')")"; fi
c "{\"ra_accion\":\"cordonar-nodo\",\"ra_nodo\":\"sno\"}" C5
U=$($OC get node sno -o jsonpath='{.spec.unschedulable}'); $OC adm uncordon sno >/dev/null
[ "$(estado)" = exitoso ] && [ "$U" = true ] && anotar C5 cordonar-nodo ok "unschedulable=$U; descordonado al final" || anotar C5 cordonar-nodo falla "$(estado) $U"
c "{\"ra_accion\":\"reiniciar-pod\",\"ra_namespace\":\"openshift-console\",\"ra_pod\":\"x\"}" C6
[ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ] && anotar C6 "acción sobre openshift-*" ok "rechazada: $(estado)" || anotar C6 "acción sobre openshift-*" falla "$(estado)"
$OC delete ns $NS --wait=false >/dev/null 2>&1; $OC get node sno -o jsonpath='{.spec.unschedulable}{"\n"}'
certificar "$N" "Plan remediar · C1–C6 (planes-prueba.yml)"
