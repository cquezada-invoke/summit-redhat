# Plan «Migración · IIS a OpenShift» (evaluar en nodo-windows; migrar aún no existe)
source ~/summit-redhat/herramientas/pruebas/comun.sh
energia nodo-windows encender; sleep 90
N="Migración · IIS a OpenShift"
caso "{\"auto\":\"$N\",\"equipo\":\"nodo-windows\",\"params\":{\"iis_modo\":\"evaluar\"},\"min\":15,\"caso\":\"C1\"}"
S=$(jq_ "json.dumps(s, ensure_ascii=False)[:300]"); echo "C1 $S"
[ "$(estado)" = exitoso ] && [ "$S" != "{}" ] && anotar C1 "evaluar" ok "$S" || anotar C1 "evaluar" falla "$(estado) $S $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"equipo\":\"nodo-windows\",\"params\":{\"iis_modo\":\"migrar\"},\"min\":15,\"caso\":\"C2\"}"
{ [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; } && echo "$RES" | grep -q "construcción" && anotar C2 "migrar" ok "rechazado: en construcción" || anotar C2 "migrar" falla "$(estado)"
certificar "$N" "Plan IIS a OpenShift · C1–C2"
energia nodo-windows apagar
echo FIN-IIS
