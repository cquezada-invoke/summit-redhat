# Plan «OpenShift · Modelo de alertamiento» (C1–C6), que además ejercita EDA y «OpenShift · Remediar alerta (con aprobación)»
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="OpenShift · Modelo de alertamiento"; NS=faceops-prueba-al
reglas() { $OC get prometheusrule -A --no-headers 2>/dev/null | grep -c " faceops-"; }
receptor() { $OC -n openshift-monitoring get secret alertmanager-main -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d | grep -c "faceops-eda"; }
salud_reglas() { $OC -n openshift-monitoring exec prometheus-k8s-0 -c prometheus -- curl -s localhost:9090/api/v1/rules 2>/dev/null | python3 -c '
import json,sys;d=json.load(sys.stdin);g=[x for x in d["data"]["groups"] if "faceops" in x["file"]+x["name"]]
r=[y for x in g for y in x["rules"]];print(len(r), sum(1 for y in r if y.get("health")!="ok"))'; }
alerta() { $OC -n openshift-monitoring exec prometheus-k8s-0 -c prometheus -- curl -s "localhost:9090/api/v1/alerts" 2>/dev/null | python3 -c "
import json,sys;d=json.load(sys.stdin);print(sorted({a['labels']['alertname']+':'+a['labels'].get('severity','') for a in d['data']['alerts'] if 'CrashLoop' in a['labels']['alertname'] and a['state']=='firing' and a['labels'].get('namespace')=='$NS'}))"; }
c() { caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{\"al_modo\":\"$1\"},\"min\":15,\"caso\":\"$2\"}"; }
R0=$(reglas); c evaluar C1; R1=$(reglas)
[ "$(estado)" = exitoso ] && [ "$R0" = "$R1" ] && anotar C1 evaluar ok "sin cambios (reglas faceops-* $R1)" || anotar C1 evaluar falla "$(estado) $R0→$R1 $(jq_ "r.get('detalle')")"
c aplicar C2; sleep 60; R2=$(reglas); RC=$(receptor); SR=$(salud_reglas)
echo "reglas $R2 · receptor $RC · reglas evaluadas/con error: $SR"
[ "$(estado)" = exitoso ] && [ "$R2" = 3 ] && [ "$RC" -ge 1 ] && [ "$(echo $SR | cut -d' ' -f2)" = 0 ] && anotar C2 aplicar ok "3 PrometheusRule faceops-*; $SR (evaluadas, con error); receptor faceops-eda" || anotar C2 aplicar falla "$(estado) reglas $R2 receptor $RC salud $SR"
# C3 y C4: un pod en crash loop real
$OC create ns $NS >/dev/null 2>&1
$OC -n $NS create deployment cae --image=registry.access.redhat.com/ubi9/ubi-minimal:latest -- sh -c 'sleep 5; exit 1' >/dev/null
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ); P0=$($OC -n $NS get pod -l app=cae -o jsonpath='{.items[0].metadata.name}'); echo "crash loop desde $T0 · pod $P0"
OK3=0; OK4=0; WF=""
for i in $(seq 1 60); do
  sleep 40; AL=$(alerta)
  J=$(aap_ jobs "OpenShift · Remediar alerta" "$T0"); W=$(aap_ jobs "OpenShift · Remediar alerta (con aprobación)" "$T0")
  echo "$(date +%H:%M) alertas $AL · diag $(echo $J | cut -c1-160) · wf $(echo $W | cut -c1-160)"
  echo "$J" | grep -q '"ra_accion": "diagnosticar"' && echo "$J" | grep -q "$NS" && OK3=1
  WF=$(echo "$W" | sed 's/^JOBS //' | python3 -c 'import json,sys;l=json.load(sys.stdin);print(next((str(x["id"]) for x in l if x["tipo"]=="workflow_job"),""))' 2>/dev/null)
  if [ -n "$WF" ]; then aap_ aprobar $WF; break; fi
done
[ $OK3 = 1 ] && anotar C3 "Aviso real: crash loop → diagnóstico por EDA" ok "alerta de aviso y diagnóstico automático en $NS" || anotar C3 "Aviso real: crash loop → diagnóstico por EDA" falla "sin diagnóstico ($AL)"
if [ -n "$WF" ]; then
  for i in $(seq 1 30); do sleep 15; ST=$(aap_ jobs "OpenShift · Remediar alerta (con aprobación)" "$T0" | grep -o "\"id\": $WF, \"tipo\": \"workflow_job\", \"estado\": \"[a-z]*\"" | grep -o '[a-z]*"$' | tr -d '"'); [ "$ST" = successful ] || [ "$ST" = failed ] && break; done
  P1=$($OC -n $NS get pod -l app=cae -o jsonpath='{.items[0].metadata.name}')
  [ "$ST" = successful ] && [ "$P1" != "$P0" ] && OK4=1
fi
[ $OK4 = 1 ] && anotar C4 "Crítico real: workflow con aprobación reinicia el pod" ok "workflow $WF aprobado y exitoso; pod $P0 → $P1" || anotar C4 "Crítico real: workflow con aprobación reinicia el pod" falla "wf=$WF estado=$ST pod $P0→$P1"
$OC delete ns $NS --wait=false >/dev/null 2>&1
# Workflow «Remediar alerta (con aprobación)»: C3 rechazo de un segundo disparo (otro namespace, para no caer en el throttle)
OK5=0; WF2=""
if [ $OK4 = 1 ]; then
  NS2=faceops-prueba-al2; $OC create ns $NS2 >/dev/null 2>&1
  $OC -n $NS2 create deployment cae --image=registry.access.redhat.com/ubi9/ubi-minimal:latest -- sh -c 'sleep 5; exit 1' >/dev/null
  T1=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  for i in $(seq 1 60); do sleep 40
    W=$(aap_ jobs "OpenShift · Remediar alerta (con aprobación)" "$T1")
    WF2=$(echo "$W" | sed 's/^JOBS //' | python3 -c "import json,sys;l=json.load(sys.stdin);print(next((str(x['id']) for x in l if x['tipo']=='workflow_job' and x.get('ra_namespace')=='$NS2'),''))" 2>/dev/null)
    [ -n "$WF2" ] && { aap_ rechazar $WF2; break; }
  done
  if [ -n "$WF2" ]; then sleep 40; NOD=$(aap_ nodos $WF2); echo "nodos $NOD"
    echo "$NOD" | grep -q '"tipo": "job"' || OK5=1; fi
  $OC delete ns $NS2 --wait=false >/dev/null 2>&1
fi
c aplicar C5; R5=$(reglas); RC5=$(receptor)
[ "$(estado)" = exitoso ] && [ "$R5" = 3 ] && [ "$RC5" = "$RC" ] && anotar C5 "aplicar de nuevo" ok "idempotente: $R5 reglas, receptor sin duplicar ($RC5)" || anotar C5 "aplicar de nuevo" falla "$(estado) $R5 $RC→$RC5"
c retirar C6; sleep 30; R6=$(reglas); RC6=$(receptor); AM=$($OC -n openshift-monitoring get pod alertmanager-main-0 -o jsonpath='{.status.containerStatuses[*].ready}')
[ "$(estado)" = exitoso ] && [ "$R6" = 0 ] && [ "$RC6" = 0 ] && ! echo "$AM" | grep -q false && anotar C6 retirar ok "sin reglas ni receptor; Alertmanager listo ($AM)" || anotar C6 retirar falla "$(estado) $R6 $RC6 $AM"
certificar "$N" "Plan alertamiento · C1–C6 (planes-prueba.yml)"
CASOS='[]'; TKS="[\"AAP workflow $WF\", \"AAP workflow $WF2\"]"
[ $OK4 = 1 ] && CASOS=$(python3 -c "import json;print(json.dumps([{'id':'C1','caso':'Alerta crítica real llega a EDA','resultado':'ok','evidencia':'workflow $WF lanzado por EDA'},{'id':'C2','caso':'Aprobar en AAP','resultado':'ok','evidencia':'pod $P0 → $P1'},{'id':'C3','caso':'Rechazar un segundo disparo','resultado':'ok' if $OK5==1 else 'falla','evidencia':'workflow $WF2 rechazado sin ejecutar la acción'}]))")
[ $OK4 = 1 ] && certificar "OpenShift · Remediar alerta (con aprobación)" "Plan remediar con aprobación · C1–C3 (planes-prueba.yml)"
echo FIN-ALERTAS
