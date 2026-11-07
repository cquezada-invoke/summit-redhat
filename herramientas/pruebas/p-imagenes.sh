# Plan «OpenShift · Relevamiento de imágenes» (C1–C4)
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="OpenShift · Relevamiento de imágenes"
$OC create ns faceops-prueba-img >/dev/null 2>&1
$OC -n faceops-prueba-img import-image sinuso:v1 --from=registry.access.redhat.com/ubi9/ubi-minimal:latest --confirm >/dev/null 2>&1; sleep 5
REAL=$($OC get pods -A -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.imageID}{"\n"}{end}{range .status.initContainerStatuses[*]}{.imageID}{"\n"}{end}{end}' | sed 's/.*@//' | sort -u | grep -c .)
caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{},\"min\":12,\"caso\":\"C1\"}"
REAL2=$($OC get pods -A -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.imageID}{"\n"}{end}{range .status.initContainerStatuses[*]}{.imageID}{"\n"}{end}{end}' | sed 's/.*@//' | sort -u | grep -c .)
U=$(jq_ "s.get('faceops_imagenes',{}).get('en_uso',{}).get('imagenes')"); R1=$(jq_ "s.get('faceops_imagenes',{}).get('registro',{}).get('revisiones')")
A1=$(jq_ "len(s.get('faceops_imagenes',{}).get('antiguas',[]))"); M1=$(jq_ "len(s.get('faceops_imagenes',{}).get('muchas_revisiones',[]))")
K1=$(jq_ "s.get('faceops_imagenes',{}).get('cache_nodos',{}).get('imagenes')")
echo "en uso informe=$U real(oc)=$REAL · registro=$R1 · caché=$K1 · antiguas=$A1 · muchas_rev=$M1"
D=$(python3 -c "a,b=sorted([int('${REAL:-0}'),int('${REAL2:-0}')]);print(a-3<=int('${U:-0}')<=b+3)")
[ "$(estado)" = exitoso ] && [ "$D" = True ] && [ "${K1:-0}" -gt 0 ] && anotar C1 "Relevamiento por defecto" ok "en uso $U vs oc $REAL–$REAL2; registro $R1; caché $K1" || anotar C1 "Relevamiento por defecto" falla "$(estado) en uso $U vs $REAL caché $K1"
S=$(jq_ "any(x.get('is')=='sinuso' and x.get('ns')=='faceops-prueba-img' for x in s.get('faceops_imagenes',{}).get('registro',{}).get('sin_uso_detalle',[]))")
[ "$S" = True ] && anotar C2 "ImageStream de prueba sin uso" ok "faceops-prueba-img/sinuso listado sin uso" || anotar C2 "ImageStream de prueba sin uso" falla "no aparece ($S)"
ANTES=$($OC get is -A --no-headers | wc -l)
caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{\"im_dias\":30,\"im_max_revisiones\":1},\"min\":12,\"caso\":\"C3\"}"
A3=$(jq_ "len(s.get('faceops_imagenes',{}).get('antiguas',[]))"); M3=$(jq_ "len(s.get('faceops_imagenes',{}).get('muchas_revisiones',[]))")
DESP=$($OC get is -A --no-headers | wc -l)
echo "antiguas $A1→$A3 · muchas_rev $M1→$M3 · IS $ANTES→$DESP"
[ "$(estado)" = exitoso ] && [ "${A3:-0}" -ge "${A1:-0}" ] && [ "${M3:-0}" -ge "${M1:-0}" ] && [ "$ANTES" = "$DESP" ] && anotar C3 "Umbral 30 días y 1 revisión" ok "antiguas $A1→$A3, muchas revisiones $M1→$M3, ImageStreams sin cambio ($DESP)" || anotar C3 "Umbral 30 días y 1 revisión" falla "$(estado) $A1→$A3 $M1→$M3 IS $ANTES→$DESP"
caso "{\"auto\":\"$N\",\"equipo\":\"ocp-sno\",\"params\":{\"im_dias\":10},\"min\":8,\"caso\":\"C4\"}"
E=$(estado); echo "$RES" | grep -o 'umbral[^"]*' | head -1
[ "$E" = fallido ] || [ "$E" = "rechazado al solicitar" ] && anotar C4 "Umbral no válido (10 días)" ok "rechazado: $E" || anotar C4 "Umbral no válido (10 días)" falla "$E"
$OC delete ns faceops-prueba-img --wait=false >/dev/null 2>&1
certificar "$N" "Plan imágenes · C1–C4 (planes-prueba.yml)"
