# Planes: Ciclo de vida · Evaluar destino y FaceOps · Apagar equipo
source ~/summit-redhat/herramientas/pruebas/comun.sh
ok_() { [ "$(estado)" = exitoso ]; }; no_() { [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; }
energia nodo-jboss-dev encender; IPJ=$(ip_de nodo-jboss-dev)
N="Ciclo de vida · Evaluar destino"
ev() { jq_ "json.dumps(list((s.get('faceops_evaluacion_destino') or {}).values())[:1], ensure_ascii=False)[:300]"; }
P0=$(en $IPJ 'rpm -qa | sort | sha256sum | cut -c1-12')
caso "{\"auto\":\"$N\",\"equipo\":\"nodo-jboss-dev\",\"params\":{\"cv_producto\":\"java\",\"cv_actual\":\"17\",\"cv_destino\":\"21\"},\"min\":12,\"caso\":\"C1\"}"; E1=$(ev); P1=$(en $IPJ 'rpm -qa | sort | sha256sum | cut -c1-12'); echo "C1 $E1"
ok_ && [ "$E1" != "[]" ] && [ "$P0" = "$P1" ] && anotar C1 "Java 17 → 21" ok "$E1" || anotar C1 "Java 17 → 21" falla "$(estado) $E1 paquetes $P0→$P1"
caso "{\"auto\":\"$N\",\"equipo\":\"nodo-jboss-dev\",\"params\":{\"cv_producto\":\"java\",\"cv_actual\":\"17\",\"cv_destino\":\"99\"},\"min\":12,\"caso\":\"C2\"}"; E2=$(ev); echo "C2 $E2"
ok_ && echo "$E2" | grep -qiE "rojo|no existe|no está|no disponible|falta" && anotar C2 "Destino inexistente" ok "$E2" || anotar C2 "Destino inexistente" falla "$(estado) $E2"
caso "{\"auto\":\"$N\",\"equipo\":\"nodo-jboss-dev\",\"params\":{\"cv_producto\":\"java\",\"cv_actual\":\"17\",\"cv_destino\":\"21\"},\"min\":12,\"caso\":\"C3\"}"; E3=$(ev)
q() { echo "$1" | python3 -c 'import json,sys,re;d=json.loads(sys.stdin.read() or "[]");print(json.dumps([{k:v for k,v in x.items() if not re.search("fecha|medido|libre|mb|gb",k)} for x in d],sort_keys=True) if d and isinstance(d[0],dict) else d)'; }
ok_ && [ "$(q "$E3")" = "$(q "$E1")" ] && anotar C3 "Repetir" ok "mismo semáforo (solo lectura)" || anotar C3 "Repetir" falla "$(estado) difiere"
certificar "$N" "Plan ciclo de vida · C1–C3"
energia nodo-jboss-dev apagar
CASOS='[]'; TKS='[]'; N="FaceOps · Apagar equipo"; M=srv-test-ubuntu-01
energia $M encender
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{},\"min\":12,\"caso\":\"C1\"}"
for i in $(seq 1 18); do [ "$(sudo virsh domstate $M)" = "shut off" ] && break; sleep 10; done; S1=$(sudo virsh domstate $M)
ok_ && [ "$S1" = "shut off" ] && anotar C1 "Apagar máquina vacía" ok "$M quedó apagada ($S1)" || anotar C1 "Apagar máquina vacía" falla "$(estado) $S1"
OTRAS=$(sudo virsh list --name | sort | tr '\n' ' ')
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{},\"min\":10,\"caso\":\"C2\"}"; OTRAS2=$(sudo virsh list --name | sort | tr '\n' ' ')
no_ && [ "$OTRAS" = "$OTRAS2" ] && anotar C2 "Repetir apagada" ok "falla controlada; ninguna otra máquina cambió" || anotar C2 "Repetir apagada" falla "$(estado) $OTRAS → $OTRAS2"
certificar "$N" "Plan apagar equipo · C1–C2"
echo FIN-VARIOS
