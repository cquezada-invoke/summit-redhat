# Planes sobre servidores de prueba nuevos: Vault · Preparar servidor, Migración · CentOS a RHEL y Migración · RHEL a versión soportada.
# Las máquinas se crean con «FaceOps · Habilitar servidor» y se eliminan con «FaceOps · Eliminar servidor» al final.
source ~/summit-redhat/herramientas/pruebas/comun.sh
ok_() { [ "$(estado)" = exitoso ]; }; no_() { [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; }
hab() { caso "{\"auto\":\"FaceOps · Habilitar servidor\",\"params\":{\"hab_so\":\"$1\",\"hab_infraestructura\":\"KVM · Santiago\",\"hab_nombre\":\"$2\",\"hab_replicas\":\"1\",\"hab_cpu\":\"1\",\"hab_ram_gb\":\"2\",\"hab_disco_gb\":\"20\",\"hab_ambiente\":\"desarrollo\"},\"min\":40,\"caso\":\"alta $2\"}"; echo "habilitar $2 ($1): $(estado) $(ticket)"; }
hab rhel9 prueba-rhel9; hab rocky9 prueba-rocky; hab rhel8 prueba-rhel8
R9=srv-prueba-rhel9-01; RK=srv-prueba-rocky-01; R8=srv-prueba-rhel8-01
I9=$(ip_de $R9); IK=$(ip_de $RK); I8=$(ip_de $R8); echo "IPs $I9 $IK $I8"
# ── Vault · Preparar servidor
CASOS='[]'; TKS='[]'; N="Vault · Preparar servidor"
caso "{\"auto\":\"$N\",\"equipo\":\"$R9\",\"params\":{},\"min\":25,\"caso\":\"C1\"}"
H1=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://$I9:8200/v1/sys/health); T1=$(en $I9 'sudo stat -c %Y /root/vault-init.json 2>/dev/null || sudo ls /root')
ok_ && [ "$H1" = 200 ] && anotar C1 "Instalar Vault" ok "inicializado y desellado: /v1/sys/health $H1" || anotar C1 "Instalar Vault" falla "$(estado) health=$H1 $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"equipo\":\"$R9\",\"params\":{},\"min\":25,\"caso\":\"C2\"}"
H2=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://$I9:8200/v1/sys/health); T2=$(en $I9 'sudo stat -c %Y /root/vault-init.json 2>/dev/null || sudo ls /root')
ok_ && [ "$H2" = 200 ] && [ "$T1" = "$T2" ] && anotar C2 "Repetir" ok "no reinicializa (mismo archivo de inicio)" || anotar C2 "Repetir" falla "$(estado) $H2 $T1→$T2"
certificar "$N" "Plan preparar Vault · C1–C2"
# ── Migración · CentOS a RHEL (Rocky 9)
CASOS='[]'; TKS='[]'; N="Migración · CentOS a RHEL"
REL=$(en $IK 'cat /etc/redhat-release'); echo "antes: $REL"
caso "{\"auto\":\"$N\",\"equipo\":\"$RK\",\"params\":{\"mig_modo\":\"migrar\",\"mig_tipo\":\"centos\",\"mig_politica\":\"minimo\",\"mig_snapshot\":\"kvm\"},\"min\":90,\"caso\":\"C1\"}"
sleep 30; REL1=$(en $IK 'cat /etc/redhat-release; sudo subscription-manager identity 2>/dev/null | head -1')
ok_ && echo "$REL1" | grep -q "Red Hat" && anotar C1 "Rocky 9 → RHEL" ok "$(echo $REL1)" || anotar C1 "Rocky 9 → RHEL" falla "$(estado) $REL1 $(jq_ "r.get('detalle')")"
SN0=$(sudo virsh snapshot-list $RK --name 2>/dev/null | grep -c .)
caso "{\"auto\":\"$N\",\"equipo\":\"$RK\",\"params\":{\"mig_modo\":\"migrar\",\"mig_tipo\":\"centos\",\"mig_politica\":\"minimo\",\"mig_snapshot\":\"kvm\",\"mig_destino\":\"99\"},\"min\":30,\"caso\":\"C2\"}"
SN1=$(sudo virsh snapshot-list $RK --name 2>/dev/null | grep -c .)
no_ && [ "$SN0" = "$SN1" ] && anotar C2 "Destino no válido" ok "rechazado sin snapshot nuevo" || anotar C2 "Destino no válido" falla "$(estado) snapshots $SN0→$SN1"
caso "{\"auto\":\"$N\",\"equipo\":\"$RK\",\"params\":{\"mig_modo\":\"migrar\",\"mig_tipo\":\"centos\",\"mig_politica\":\"minimo\",\"mig_snapshot\":\"kvm\"},\"min\":30,\"caso\":\"C3\"}"
A3=$(jq_ "json.dumps(s.get('faceops_migracion') or s.get('faceops_migrar_so') or s, ensure_ascii=False)[:200]"); echo "C3 $A3"
ok_ && echo "$A3" | grep -qiE "sin acción|no aplica|al día|nada" && anotar C3 "Repetir ya convertida" ok "$A3" || anotar C3 "Repetir ya convertida" falla "$(estado) $A3"
certificar "$N" "Plan CentOS a RHEL · C1–C3"
# ── Migración · RHEL a versión soportada (RHEL 8)
CASOS='[]'; TKS='[]'; N="Migración · RHEL a versión soportada"
P0=$(en $I8 'rpm -qa | sort | sha256sum | cut -c1-12')
caso "{\"auto\":\"$N\",\"equipo\":\"$R8\",\"params\":{\"mig_modo\":\"evaluar\",\"mig_tipo\":\"rhel\",\"mig_politica\":\"ultima\",\"mig_preupgrade\":true},\"min\":60,\"caso\":\"C1\"}"
P1=$(en $I8 'rpm -qa | grep -v leapp | sort | sha256sum | cut -c1-12'); P0b=$(en $I8 'rpm -qa | grep -v leapp | sort | sha256sum | cut -c1-12')
A1=$(jq_ "json.dumps(s, ensure_ascii=False)[:220]"); echo "C1 $A1"
ok_ && [ -n "$A1" ] && anotar C1 "Evaluar con Leapp" ok "$A1" || anotar C1 "Evaluar con Leapp" falla "$(estado) $A1 $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"equipo\":\"$R8\",\"params\":{\"mig_modo\":\"migrar\",\"mig_tipo\":\"rhel\",\"mig_politica\":\"minimo\",\"mig_snapshot\":\"kvm\"},\"min\":60,\"caso\":\"C2\"}"
V2=$(en $I8 'cat /etc/redhat-release')
ok_ && echo "$V2" | grep -q "release 8" && anotar C2 "Política mínimo" ok "$V2" || anotar C2 "Política mínimo" falla "$(estado) $V2 $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"equipo\":\"$R8\",\"params\":{\"mig_modo\":\"migrar\",\"mig_tipo\":\"rhel\",\"mig_politica\":\"ultima\",\"mig_snapshot\":\"kvm\"},\"min\":120,\"caso\":\"C3\"}"
sleep 60; V3=$(en $I8 'cat /etc/redhat-release')
ok_ && echo "$V3" | grep -qE "release (9|10)" && anotar C3 "Salto con Leapp" ok "$V3" || anotar C3 "Salto con Leapp" falla "$(estado) $V3 $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"equipo\":\"$R8\",\"params\":{\"mig_modo\":\"migrar\",\"mig_tipo\":\"rhel\",\"mig_politica\":\"ultima\",\"mig_snapshot\":\"kvm\"},\"min\":30,\"caso\":\"C4\"}"; V4=$(en $I8 'cat /etc/redhat-release')
ok_ && [ "$V4" = "$V3" ] && anotar C4 "Repetir" ok "sin cambios ($V4)" || anotar C4 "Repetir" falla "$(estado) $V3→$V4"
certificar "$N" "Plan RHEL a versión soportada · C1–C4"
# ── limpieza: eliminar los servidores de prueba
for m in $R9 $RK $R8; do caso "{\"auto\":\"FaceOps · Eliminar servidor\",\"equipo\":\"$m\",\"params\":{},\"min\":20,\"caso\":\"baja $m\"}" >/dev/null; echo "eliminar $m: $(estado) $(ticket)"; done
echo FIN-SERVIDORES
