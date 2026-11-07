# Planes «FaceOps · Volver a una versión» y «FaceOps · Recuperar servidor» sobre un servidor de prueba con respaldo continuo
source ~/summit-redhat/herramientas/pruebas/comun.sh
ok_() { [ "$(estado)" = exitoso ]; }; no_() { [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; }
M=srv-prueba-resp-01
existe $M || caso "{\"auto\":\"FaceOps · Habilitar servidor\",\"params\":{\"hab_so\":\"rhel9\",\"hab_infraestructura\":\"KVM · Santiago\",\"hab_nombre\":\"prueba-resp\",\"hab_replicas\":\"1\",\"hab_cpu\":\"1\",\"hab_ram_gb\":\"2\",\"hab_disco_gb\":\"40\",\"hab_ambiente\":\"desarrollo\"},\"rhsm\":true,\"min\":40,\"caso\":\"alta\"}"; echo "alta $M: $(estado) $(ticket)"
caso "{\"auto\":\"Sistema · Inventariar máquina\",\"equipo\":\"$M\",\"params\":{},\"min\":20,\"caso\":\"inventario\"}" >/dev/null; echo "inventariar $M: $(estado) $(ticket)"
IP=$(ip_de $M); echo "IP $IP"
caso "{\"auto\":\"FaceOps · Respaldo continuo en Git\",\"equipo\":\"$M\",\"params\":{},\"min\":20,\"caso\":\"respaldo\"}" >/dev/null; echo "respaldo: $(estado) $(ticket)"
TOK=$(sudo podman exec faceops printenv FACEOPS_RESPALDO_TOKEN 2>/dev/null)
ult() { curl -s -m 10 -H "Authorization: token $TOK" "http://192.168.122.1:3000/api/v1/repos/repositorio/respaldo-servidores/commits?sha=$M&limit=1" | python3 -c 'import json,sys;l=json.load(sys.stdin);print(l[0]["sha"][:12] if l else "")' 2>/dev/null; }
esperar_commit() { for i in $(seq 1 18); do s=$(ult); [ -n "$s" ] && [ "$s" != "$1" ] && { echo $s; return; }; sleep 10; done; echo "$1"; }
pk() { en $IP "rpm -qa --qf '%{NAME}\n' | sort | sha256sum | cut -c1-12"; }
S0=$(esperar_commit ""); Z0=$(en $IP 'timedatectl show -p Timezone --value'); P0=$(pk); echo "versión inicial $S0 · zona $Z0"
caso "{\"auto\":\"Sistema · Cambio de hora del país\",\"equipo\":\"$M\",\"params\":{\"tz_modo\":\"aplicar\",\"tz_zona\":\"America/Bogota\",\"tz_reiniciar\":\"crond\"},\"min\":15,\"caso\":\"cambio\"}" >/dev/null
Z1=$(en $IP 'timedatectl show -p Timezone --value'); S1=$(esperar_commit "$S0"); echo "cambio: zona $Z1 · versión $S1"
# ── Volver a una versión
N="FaceOps · Volver a una versión"
caso "{\"auto\":\"$N\",\"segmento\":\"\",\"params\":{\"rec_equipo\":\"$M\",\"rec_version\":\"$S0\",\"rec_ip\":\"$IP\"},\"min\":25,\"caso\":\"C1\"}"
Z2=$(en $IP 'timedatectl show -p Timezone --value; readlink /etc/localtime | sed "s:.*zoneinfo/::"' | tail -1); P2=$(pk)
ok_ && [ "$Z1" != "$Z0" ] && [ "$Z2" = "$Z0" ] && [ "$P2" = "$P0" ] && anotar C1 "Volver a la versión anterior" ok "zona $Z0 → $Z1 → $Z2 (versión $S0); paquetes iguales" || anotar C1 "Volver a la versión anterior" falla "$(estado) $Z0→$Z1→$Z2 paquetes $P0→$P2 $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"segmento\":\"\",\"params\":{\"rec_equipo\":\"$M\",\"rec_version\":\"deadbee0\",\"rec_ip\":\"$IP\"},\"min\":15,\"caso\":\"C2\"}"; Z3=$(en $IP 'readlink /etc/localtime | sed "s:.*zoneinfo/::"')
no_ && [ "$Z3" = "$Z2" ] && anotar C2 "Versión inexistente" ok "falla controlada; la máquina no cambió" || anotar C2 "Versión inexistente" falla "$(estado) $Z3"
certificar "$N" "Plan volver a una versión · C1–C2"
# ── Recuperar servidor
CASOS='[]'; TKS='[]'; N="FaceOps · Recuperar servidor"
sleep 90; SR=$(ult); ZR=$(en $IP 'readlink /etc/localtime | sed "s:.*zoneinfo/::"'); PR=$(pk)
caso "{\"auto\":\"FaceOps · Eliminar servidor\",\"equipo\":\"$M\",\"params\":{},\"min\":20,\"caso\":\"baja\"}" >/dev/null; echo "eliminar: $(estado) · VM $(sudo virsh domstate $M 2>&1 | head -1)"
REC="\"hab_interno\":true,\"hab_so\":\"rhel9\",\"hab_destino\":\"onpremise\",\"hab_reemplazar\":true,\"hab_nombre\":\"recuperado\",\"hab_replicas\":\"1\",\"hab_cpu\":\"1\",\"hab_ram_gb\":\"2\",\"hab_disco_gb\":\"40\",\"hab_ambiente\":\"desarrollo\""
caso "{\"auto\":\"$N\",\"segmento\":\"\",\"params\":{$REC,\"hab_nombre_exacto\":\"$M\",\"rec_equipo\":\"$M\",\"rec_version\":\"\"},\"min\":60,\"caso\":\"C1\"}"
IP2=$(ip_de $M); ZN=$(en $IP2 'readlink /etc/localtime | sed "s:.*zoneinfo/::"'); PN=$(pk_ip=1; en $IP2 "rpm -qa --qf '%{NAME}\n' | sort | sha256sum | cut -c1-12"); HN=$(en $IP2 hostname)
echo "recuperada: $HN $IP2 zona $ZN paquetes $PN (respaldo $SR: zona $ZR paquetes $PR)"
ok_ && [ "$ZN" = "$ZR" ] && [ "$PN" = "$PR" ] && echo "$HN" | grep -q "prueba-resp" && anotar C1 "Recuperar desde el respaldo" ok "$HN en $IP2: zona y lista de paquetes iguales al respaldo $SR" || anotar C1 "Recuperar desde el respaldo" falla "$(estado) $HN zona $ZN/$ZR paquetes $PN/$PR $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"segmento\":\"\",\"params\":{$REC,\"hab_nombre_exacto\":\"srv-no-existe-99\",\"rec_equipo\":\"srv-no-existe-99\",\"rec_version\":\"\"},\"min\":30,\"caso\":\"C2\"}"
VM=$(sudo virsh list --all --name | grep -c "^srv-no-existe-99$")
no_ && [ "$VM" = 0 ] && anotar C2 "Máquina sin respaldo" ok "rechazado; no se creó nada" || anotar C2 "Máquina sin respaldo" falla "$(estado) VM creada=$VM"
certificar "$N" "Plan recuperar servidor · C1–C2"
caso "{\"auto\":\"FaceOps · Eliminar servidor\",\"equipo\":\"$M\",\"params\":{},\"min\":20,\"caso\":\"limpieza\"}" >/dev/null; echo "limpieza $M: $(estado)"
[ "$VM" != 0 ] && { caso "{\"auto\":\"FaceOps · Eliminar servidor\",\"equipo\":\"srv-no-existe-99\",\"params\":{},\"min\":20,\"caso\":\"limpieza\"}" >/dev/null; echo "limpieza srv-no-existe-99: $(estado)"; }
echo FIN-RESPALDO
