# Planes de las migraciones de sistema operativo (Evaluar SO, CentOS a RHEL, RHEL a versión soportada).
# Una máquina de prueba a la vez (licencia de AAP al límite): fase «rocky» (srv-mig-rocky-01) y fase «rhel8» (srv-mig-rhel8-01).
# Los casos de «Evaluar sistema operativo» de ambas fases se acumulan en ~/pruebas-mig/ y se certifica al terminar la segunda.
#   bash p-migraciones-so.sh rocky-ev (crear + evaluar, corta) | rocky-cr (CentOS a RHEL, larga) | rhel8-ev | rhel8-rv
source ~/summit-redhat/herramientas/pruebas/comun.sh
FASE=${1:-rocky}; mkdir -p ~/pruebas-mig; EST=~/pruebas-mig
ok_() { [ "$(estado)" = exitoso ]; }; no_() { [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; }
mig() { jq_ "json.dumps((s.get('faceops_migracion') or {}).get('$1') or {}, ensure_ascii=False)"; }
campo() { echo "$1" | python3 -c "import json,sys;d=json.load(sys.stdin);v=d.get('$2');print(json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v)" 2>/dev/null; }
libres() { set -a; . ~/faceops/faceops.env; set +a; python3 -c "
import json,os,ssl,urllib.request;cx=ssl._create_unverified_context()
print(json.loads(urllib.request.urlopen(urllib.request.Request(os.environ['FACEOPS_AAP_URL'].rstrip('/')+'/api/controller/v2/config/',headers={'Authorization':'Bearer '+os.environ['FACEOPS_AAP_TOKEN']}),context=cx).read())['license_info']['free_instances'])"; }
hab() { caso "{\"auto\":\"FaceOps · Habilitar servidor\",\"params\":{\"hab_so\":\"$1\",\"hab_infraestructura\":\"KVM · Santiago\",\"hab_nombre\":\"$2\",\"hab_replicas\":\"1\",\"hab_cpu\":\"1\",\"hab_ram_gb\":\"2\",\"hab_disco_gb\":\"20\",\"hab_ambiente\":\"desarrollo\"},\"rhsm\":true,\"min\":40,\"caso\":\"alta $2\"}" >/dev/null; echo "habilitar $2 ($1): $(estado) $(ticket) $(jq_ "r.get('detalle') or ''")"; }
inv() { caso "{\"auto\":\"Sistema · Inventariar máquina\",\"equipo\":\"$1\",\"params\":{},\"min\":20,\"caso\":\"inventario $1\"}" >/dev/null; echo "inventariar $1: $(estado) $(ticket)"; }
snaps() { sudo virsh snapshot-list $1 --name 2>/dev/null | grep -c .; }
guardar() { echo "$CASOS" > $EST/$1.casos; echo "$TKS" > $EST/$1.tks; }
cargar() { CASOS=$(cat $EST/$1.casos 2>/dev/null || echo '[]'); TKS=$(cat $EST/$1.tks 2>/dev/null || echo '[]'); }
EV="Migración · Evaluar sistema operativo"; CR="Migración · CentOS a RHEL"; RV="Migración · RHEL a versión soportada"
evaluar() {  # evaluar <id> <equipo> '<params json sin llaves>' <min>
  caso "{\"auto\":\"$EV\",\"equipo\":\"$2\",\"params\":{\"mig_modo\":\"evaluar\"${3:+,$3}},\"min\":${4:-20},\"caso\":\"$1\"}" >/dev/null; M=$(mig $2)
  echo "  $1 $2: $(estado) $(ticket) · $(campo "$M" origen) → $(campo "$M" destino) · $(campo "$M" accion) · $(campo "$M" semaforo) · análisis $(campo "$M" analisis) $(jq_ "r.get('detalle') or ''")"
}

RK=srv-mig-rocky-01
if [ "$FASE" = rocky-ev ]; then
  if ! existe $RK; then
    L=$(libres); echo "licencia AAP libres: $L"; [ "${L:-0}" -ge 1 ] || { echo "SIN LICENCIA LIBRE: no se crea la máquina"; exit 0; }
    hab rocky9 mig-rocky; inv $RK
  fi
  IK=$(ip_de $RK); echo "IP $RK $IK · $(en $IK 'cat /etc/redhat-release')"
  # ── Evaluar sistema operativo (casos de la fase rocky)
  CASOS='[]'; TKS='[]'
  R0=$(en $IK 'cat /etc/redhat-release'); P0=$(en $IK 'rpm -qa | sort | sha256sum | cut -c1-12')
  evaluar C1 $RK '"mig_politica":"minimo"'; M1=$M
  R1=$(en $IK 'cat /etc/redhat-release'); P1=$(en $IK 'rpm -qa | sort | sha256sum | cut -c1-12')
  ok_ && [ "$(campo "$M1" accion)" = convertir ] && [ "$(campo "$M1" destino)" = "RHEL 9" ] && [ "$R0" = "$R1" ] && [ "$P0" = "$P1" ] \
    && anotar C1 "Rocky 9 política mínimo" ok "$(campo "$M1" origen) → $(campo "$M1" destino) · convertir · $(campo "$M1" semaforo) · camino $(campo "$M1" camino) · paquetes $P0=$P1" \
    || anotar C1 "Rocky 9 política mínimo" falla "$(estado) $M1 $R0/$R1 $P0/$P1"
  evaluar C2 $RK '"mig_politica":"minimo","mig_preupgrade":"true"' 30; M2=$M
  R2=$(en $IK 'cat /etc/redhat-release; sudo test -e /root/.convert2rhel.ini && echo LLAVE-QUEDO || echo sin-llave')
  A2=$(campo "$M2" analisis)
  ok_ && echo "$A2" | grep -q "convert2rhel" && echo "$R2" | grep -q Rocky && echo "$R2" | grep -q sin-llave \
    && anotar C2 "Rocky 9 con convert2rhel analyze" ok "análisis $A2 · $(echo $R2)" || anotar C2 "Rocky 9 con convert2rhel analyze" falla "$(estado) $A2 $R2 $(jq_ "r.get('detalle')")"
  evaluar C4 nodo-jboss-dev '"mig_politica":"minimo"'; M4=$M
  ok_ && [ "$(campo "$M4" accion)" = "sin acción" ] && anotar C4 "RHEL 9 al día (nodo-jboss-dev)" ok "$(campo "$M4" origen) · sin acción · $(campo "$M4" semaforo)" || anotar C4 "RHEL 9 al día" falla "$(estado) $M4"
  evaluar C5 $RK '"mig_destino":"99"'; M5=$M
  ok_ && [ "$(campo "$M5" semaforo)" = rojo ] && campo "$M5" hallazgos | grep -q "no válida" && [ "$(en $IK 'cat /etc/redhat-release')" = "$R0" ] \
    && anotar C5 "Destino no válido (99)" ok "semáforo rojo: $(campo "$M5" hallazgos | grep -o 'Versión de destino 99[^"]*' | head -1)" || anotar C5 "Destino no válido" falla "$(estado) $M5"
  evaluar C6 $RK '"mig_politica":"minimo"'; M6=$M
  ok_ && [ "$(campo "$M6" accion)" = "$(campo "$M1" accion)" ] && [ "$(campo "$M6" camino)" = "$(campo "$M1" camino)" ] && [ "$(campo "$M6" semaforo)" = "$(campo "$M1" semaforo)" ] \
    && anotar C6 "Repetir C1" ok "mismo resultado: $(campo "$M6" accion) · $(campo "$M6" semaforo)" || anotar C6 "Repetir C1" falla "$(estado) $M6"
  caso "{\"auto\":\"$EV\",\"equipo\":\"nodo-jboss-qa\",\"params\":{\"mig_modo\":\"evaluar\"},\"min\":5,\"caso\":\"C7\"}" >/dev/null
  E7=$(jq_ "r.get('error') or r.get('detalle') or ''")
  [ "$(estado)" = "rechazado al solicitar" ] && echo "$E7" | grep -qi apagad && anotar C7 "Equipo apagado (nodo-jboss-qa)" ok "FaceOps no lanza: $E7" || anotar C7 "Equipo apagado" falla "$(estado) $E7"
  guardar evaluar; echo "EVALUAR fase rocky: $CASOS" | cut -c1-600
fi
if [ "$FASE" = rocky-cr ]; then
  IK=$(ip_de $RK); echo "IP $RK $IK · $(en $IK 'cat /etc/redhat-release')"
  # ── CentOS a RHEL
  CASOS='[]'; TKS='[]'
  S0=$(snaps $RK)
  caso "{\"auto\":\"$CR\",\"equipo\":\"$RK\",\"params\":{\"mig_politica\":\"minimo\",\"mig_snapshot\":\"kvm\",\"mig_destino\":\"99\"},\"min\":20,\"caso\":\"C1\"}" >/dev/null
  S1=$(snaps $RK); R1=$(en $IK 'cat /etc/redhat-release'); D1=$(jq_ "r.get('detalle') or ''")
  no_ && [ "$S0" = "$S1" ] && echo "$R1" | grep -q Rocky && echo "$D1" | grep -q "no válida" && anotar C1 "Destino no válido (99)" ok "rechazado en validaciones: snapshots $S0=$S1, sigue $R1" || anotar C1 "Destino no válido" falla "$(estado) $S0/$S1 $R1 $D1"
  caso "{\"auto\":\"$CR\",\"equipo\":\"$RK\",\"params\":{\"mig_politica\":\"minimo\",\"mig_snapshot\":\"kvm\",\"mig_prueba_falla\":true},\"min\":30,\"caso\":\"C2\"}" >/dev/null
  D2=$(jq_ "r.get('detalle') or ''"); R2=$(en $IK 'cat /etc/redhat-release; sudo test -e /root/faceops-prueba-falla && echo MARCA || echo sin-marca')
  no_ && echo "$D2" | grep -q "volvió al snapshot" && echo "$R2" | grep -q Rocky && echo "$R2" | grep -q sin-marca \
    && anotar C2 "Falla tras el snapshot" ok "vuelta atrás: $(echo "$D2" | grep -o 'volvió al snapshot [^ ]*') · $(echo $R2)" || anotar C2 "Falla tras el snapshot" falla "$(estado) $D2 $R2"
  S2=$(snaps $RK)
  caso "{\"auto\":\"$CR\",\"equipo\":\"$RK\",\"params\":{\"mig_politica\":\"minimo\",\"mig_snapshot\":\"kvm\"},\"min\":100,\"caso\":\"C3\"}" >/dev/null
  sleep 20; R3=$(en $IK 'cat /etc/redhat-release; sudo subscription-manager identity 2>/dev/null | grep -c "org ID"; sudo test -e /root/.convert2rhel.ini && echo LLAVE-QUEDO || echo sin-llave'); S3=$(snaps $RK)
  ok_ && echo "$R3" | grep -q "Red Hat Enterprise Linux release 9" && echo "$R3" | grep -q sin-llave && [ "$S3" -gt "$S2" ] \
    && anotar C3 "Rocky 9 → RHEL" ok "$(echo $R3) · snapshot previo ($S2→$S3)" || anotar C3 "Rocky 9 → RHEL" falla "$(estado) $R3 $S2/$S3 $(jq_ "r.get('detalle')")"
  caso "{\"auto\":\"$CR\",\"equipo\":\"$RK\",\"params\":{\"mig_politica\":\"minimo\",\"mig_snapshot\":\"kvm\"},\"min\":20,\"caso\":\"C4\"}" >/dev/null
  M4=$(mig $RK); S4=$(snaps $RK)
  ok_ && [ "$(campo "$M4" accion)" = "sin acción" ] && [ "$S4" = "$S3" ] && anotar C4 "Repetir ya convertida" ok "$(campo "$M4" resultado) · sin snapshot nuevo" || anotar C4 "Repetir" falla "$(estado) $M4 $S3/$S4 $(jq_ "r.get('detalle')")"
  guardar centos; echo "CENTOS: $CASOS" | cut -c1-900
  grep -q '"falla"' <<<"$CASOS" || certificar "$CR" "planes-prueba.yml · Migración · CentOS a RHEL (C1–C4)"
  echo FIN-ROCKY
fi
