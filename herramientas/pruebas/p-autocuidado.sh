# Planes «Autocuidado · …» (datos/planes-prueba.yml). Corre en el anfitrión con el lab sano:  bash p-autocuidado.sh [plan ...]
# planes: estado disco vms aap despachador faceops respaldo cert purgar ampliar atender vigia (sin argumentos: todos menos vms, ampliar,
# despachador y vigia; «despachador» reinicia lo que ejecuta los trabajos de AAP: correrlo SOLO, sin otros planes en paralelo)
# La salida es pública: no imprime tokens, claves ni extra_vars sensibles (solo estados y conteos).
source ~/summit-redhat/herramientas/pruebas/comun.sh
H=/usr/local/sbin/faceops-autocuidado
PLANES=${*:-"estado disco aap faceops respaldo cert purgar atender"}
r_() { jq_ "(s.get('faceops_autocuidado') or {}).get('resultado', {})$1"; }   # campo del resultado de la herramienta
pedir() {  # pedir <plantilla> <caso> '<params json>' [min] [cambios]
  local extra=""; [ -n "${5:-}" ] && extra=',"cambios":true'
  caso "{\"auto\":\"$1\",\"equipo\":\"\",\"segmento\":\"\",\"params\":$3,\"min\":${4:-15},\"caso\":\"$2\"$extra}" >/dev/null
  echo "  $2 → $(estado) $(ticket)"
}
lineas_registro() { sudo cat /var/lib/faceops/autocuidado/registro.jsonl 2>/dev/null | wc -l; }
mover_herramienta() { sudo mv $H $H.prueba; }
volver_herramienta() { sudo mv $H.prueba $H; }
trap '[ -f $H.prueba ] && sudo mv $H.prueba $H; sudo systemctl is-active -q faceops || sudo systemctl start faceops; sudo systemctl is-active -q faceops-autodeploy.timer || sudo systemctl start faceops-autodeploy.timer' EXIT
echo "herramienta: $(sudo $H version 2>/dev/null) · AAP $(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1/api/controller/v2/ping/)"
sincronizar

# ───────────────────────────────────────────────────────────── Revisar estado
if [[ " $PLANES " == *" estado "* ]]; then
N="Autocuidado · Revisar estado"; CASOS='[]'; TKS='[]'; echo "== $N"
L0=$(lineas_registro); pedir "$N" C1 '{}' 10 si; E=$(estado)
OKC=$(echo "$RES" | python3 -c "import json,sys;r=json.load(sys.stdin);d=((r.get('stats') or {}).get('faceops_autocuidado') or {}).get('resultado') or {};print(all(k in d for k in ('disco','vms','contenedores','aap_api','faceops','autodeploy','respaldos_edad_s','certificados')), d.get('aap_api'), (d.get('faceops') or {}).get('codigo'), len(d.get('vms') or []), len(d.get('contenedores') or []))")
[ "$E" = exitoso ] && [[ "$OKC" == "True 200 200"* ]] && anotar C1 "Revisar el estado" ok "claves completas · api/faceops/vms/contenedores: $OKC" || anotar C1 "Revisar el estado" falla "$E $OKC $(jq_ "r.get('detalle','')[:300]")"
pedir "$N" C2 '{}' 10 si; E=$(estado); CH=$(jq_ "r.get('changed')"); L1=$(lineas_registro)
[ "$E" = exitoso ] && [ "$CH" = 0 ] && anotar C2 "Repetir" ok "changed=0 (solo lectura)" || anotar C2 "Repetir" falla "$E changed=$CH"
mover_herramienta; pedir "$N" C3 '{}' 10; E=$(estado); D=$(jq_ "r.get('detalle','')[:160]"); volver_herramienta
[ "$E" = fallido ] && [[ "$D" == *"versión de este proyecto"* ]] && sudo test -x $H && anotar C3 "Herramienta distinta" ok "fallido en «Es la versión de este proyecto»; herramienta restaurada" || anotar C3 "Herramienta distinta" falla "$E $D"
certificar "$N" "Revisar estado: lectura completa, repetir y herramienta distinta"
fi

# ───────────────────────────────────────────────────────────── Liberar disco
if [[ " $PLANES " == *" disco "* ]]; then
N="Autocuidado · Liberar disco"; CASOS='[]'; TKS='[]'; echo "== $N"
sudo bash -c 'echo prueba > /var/log/faceops-ac-prueba.log.1; touch -d "-6 days" /var/log/faceops-ac-prueba.log.1
              echo prueba > /var/log/faceops-ac-prueba-reciente.log.1
              mkdir -p /tmp/awx_9999_acprueba /tmp/awx_9998_acreciente; echo x > /tmp/awx_9999_acprueba/a; touch -d "-4 days" /tmp/awx_9999_acprueba /tmp/awx_9999_acprueba/a'
sudo podman create --name automation-acprueba-protegido registry.access.redhat.com/ubi9/ubi-minimal:latest true >/dev/null 2>&1 || true
VOLS0=$(sudo podman volume ls -q | sort | sha256sum | cut -c1-10)
marcas() { for f in /var/log/faceops-ac-prueba.log.1 /var/log/faceops-ac-prueba-reciente.log.1 /tmp/awx_9999_acprueba /tmp/awx_9998_acreciente; do sudo test -e $f && printf 1 || printf 0; done
           sudo podman container exists automation-acprueba-protegido && printf 1 || printf 0; }
LIB0=$(df -BM --output=avail / | tail -1 | tr -dc 0-9)
pedir "$N" C1 '{"ac_modo":"evaluar"}' 15; E=$(estado); M1=$(marcas); LIB1=$(df -BM --output=avail / | tail -1 | tr -dc 0-9)
[ "$E" = exitoso ] && [ "$M1" = 11111 ] && [ $(( ${LIB1:-0} - ${LIB0:-0} )) -lt 300 ] && [ $(( ${LIB0:-0} - ${LIB1:-0} )) -lt 300 ] && anotar C1 "Evaluar" ok "marcas intactas ($M1); libre $LIB0→$LIB1 MB" || anotar C1 "Evaluar" falla "$E marcas=$M1 libre $LIB0→$LIB1"
L0=$(lineas_registro); pedir "$N" C2 '{"ac_modo":"seguro"}' 30; E=$(estado); M2=$(marcas); VOLS2=$(sudo podman volume ls -q | sort | sha256sum | cut -c1-10); L2=$(lineas_registro); LM=$(r_ ".get('liberado_mb')")
[ "$E" = exitoso ] && [ "$M2" = 01011 ] && [ "$VOLS0" = "$VOLS2" ] && [ "$L2" -gt "$L0" ] && anotar C2 "Modo seguro" ok "borra solo lo viejo ($M2); volúmenes intactos; liberado ${LM} MB; registro +$((L2-L0))" || anotar C2 "Modo seguro" falla "$E marcas=$M2 vols $VOLS0→$VOLS2 registro $L0→$L2"
pedir "$N" C3 '{"ac_modo":"seguro"}' 30; E=$(estado); M3=$(marcas)
[ "$E" = exitoso ] && [ "$M3" = 01011 ] && anotar C3 "Repetir" ok "las marcas que quedaron siguen ($M3)" || anotar C3 "Repetir" falla "$E marcas=$M3"
EN0=$(sudo podman ps --format '{{.Image}}' | sort -u | sha256sum | cut -c1-10); AAPI0=$(podman images -q | sort | sha256sum | cut -c1-10)
pedir "$N" C4 '{"ac_modo":"profundo"}' 40; E=$(estado); EN4=$(sudo podman ps --format '{{.Image}}' | sort -u | sha256sum | cut -c1-10); AAPI4=$(podman images -q | sort | sha256sum | cut -c1-10)
F4=$(curl -s -o /dev/null -w '%{http_code}' http://192.168.122.1:8110/ingresar)
[ "$E" = exitoso ] && [ "$EN0" = "$EN4" ] && [ "$AAPI0" = "$AAPI4" ] && [ "$F4" = 200 ] && anotar C4 "Modo profundo" ok "contenedores en ejecución con sus imágenes; imágenes de AAP intactas; FaceOps 200" || anotar C4 "Modo profundo" falla "$E en $EN0→$EN4 aap $AAPI0→$AAPI4 faceops $F4"
pedir "$N" C5 '{"ac_modo":"borrar"}' 10; E=$(estado); D=$(jq_ "r.get('detalle','')[:120]")
[ "$E" = fallido ] && [[ "$D" == *"Datos válidos"* ]] && anotar C5 "Modo inválido" ok "fallido en «Datos válidos»" || anotar C5 "Modo inválido" falla "$E $D"
sudo rm -rf /var/log/faceops-ac-prueba-reciente.log.1 /tmp/awx_9998_acreciente; sudo podman rm -f automation-acprueba-protegido >/dev/null 2>&1
certificar "$N" "Liberar disco: evaluar, seguro, repetir, profundo y modo inválido"
fi

# ───────────────────────────────────────────────────────────── Reiniciar componentes de AAP
if [[ " $PLANES " == *" aap "* ]]; then
N="Autocuidado · Reiniciar componentes de AAP"; CASOS='[]'; TKS='[]'; echo "== $N"
pedir "$N" C1 '{"ac_modo":"evaluar"}' 10; E=$(estado); X=$(r_ "") ; MAL=$(jq_ "len(((s.get('faceops_autocuidado') or {}).get('resultado') or {}).get('malos') or [])"); API=$(r_ ".get('api')")
NR=$(jq_ "len(((s.get('faceops_autocuidado') or {}).get('resultado') or {}).get('reiniciaria') or [])")
[ "$E" = exitoso ] && [ "$API" = 200 ] && [ "$MAL" = "$NR" ] && anotar C1 "Evaluar" ok "api=200; detecta $MAL con problemas y reiniciaría $NR sin tocar nada" || anotar C1 "Evaluar" falla "$E malos=$MAL reiniciaría=$NR api=$API $(jq_ "r.get('detalle','')[:200]")"
C=automation-eda-scheduler; podman container exists $C || C=automation-hub-web
RP0=$(podman inspect -f '{{.RestartCount}}' automation-controller-task 2>/dev/null)
systemctl --user stop $C.service 2>/dev/null || podman stop -t 10 $C >/dev/null; sleep 5; S0=$(podman inspect -f '{{.State.Status}}' $C)
pedir "$N" C2 '{"ac_modo":"aplicar"}' 20; E=$(estado); S2=$(podman inspect -f '{{.State.Status}}' $C); API=$(r_ ".get('api')"); RP2=$(podman inspect -f '{{.RestartCount}}' automation-controller-task 2>/dev/null)
[ "$E" = exitoso ] && [ "$S0" != running ] && [ "$S2" = running ] && [ "$API" = 200 ] && [ "$RP0" = "$RP2" ] && anotar C2 "Componente detenido" ok "$C $S0→$S2; api 200; controller-task sin reinicio" || anotar C2 "Componente detenido" falla "$E $C $S0→$S2 api=$API ctask $RP0→$RP2"
podman start $C >/dev/null 2>&1; sleep 20
RC0=$(podman ps --format '{{.Names}} {{.Restarts}}' | sort | sha256sum | cut -c1-10)
pedir "$N" C3 '{"ac_modo":"aplicar"}' 15; E=$(estado); NOTA=$(r_ ".get('nota','')"); RC3=$(podman ps --format '{{.Names}} {{.Restarts}}' | sort | sha256sum | cut -c1-10)
[ "$E" = exitoso ] && [[ "$NOTA" == *"nada que reiniciar"* ]] && [ "$RC0" = "$RC3" ] && anotar C3 "Repetir con todo sano" ok "nada que reiniciar; sin reinicios" || anotar C3 "Repetir con todo sano" falla "$E '$NOTA' $RC0→$RC3"
pedir "$N" C4 '{"ac_modo":"todo"}' 10; E=$(estado); D=$(jq_ "r.get('detalle','')[:120]")
[ "$E" = fallido ] && [[ "$D" == *"Datos válidos"* ]] && anotar C4 "Modo inválido" ok "fallido en «Datos válidos»" || anotar C4 "Modo inválido" falla "$E $D"
certificar "$N" "Reiniciar componentes de AAP: evaluar, componente detenido, repetir y modo inválido"
fi

# ───────────────────────────────────────────────────────────── Reiniciar despachador de AAP (correr solo)
if [[ " $PLANES " == *" despachador "* ]]; then
N="Autocuidado · Reiniciar despachador de AAP"; CASOS='[]'; TKS='[]'; echo "== $N"
inicio_() { podman inspect -f '{{.State.StartedAt}}' $1 2>/dev/null | cut -c1-19; }
pedir "$N" C1 '{"ac_modo":"evaluar"}' 10; E=$(estado); AT=$(r_ ".get('atascado')"); LAT=$(jq_ "max((((s.get('faceops_autocuidado') or {}).get('resultado') or {}).get('latidos') or {'x': 999}).values())")
[ "$E" = exitoso ] && [ "$AT" = False ] && [ "${LAT:-999}" -lt 120 ] && anotar C1 "Evaluar con AAP sano" ok "atascado=False; latido ${LAT} s" || anotar C1 "Evaluar con AAP sano" falla "$E atascado=$AT latido=$LAT"
T0=$(inicio_ automation-controller-task); R0=$(inicio_ receptor)
pedir "$N" C2 '{"ac_modo":"aplicar"}' 10; E=$(estado); sleep 150
T2=$(inicio_ automation-controller-task); R2=$(inicio_ receptor)
pedir "$N" C3 '{"ac_modo":"evaluar"}' 15; E3=$(estado); LAT3=$(jq_ "max((((s.get('faceops_autocuidado') or {}).get('resultado') or {}).get('latidos') or {'x': 999}).values())")
[ "$E" = exitoso ] && [ "$T0" != "$T2" ] && [ "$R0" != "$R2" ] && [ "$E3" = exitoso ] && anotar C2 "Aplicar programado" ok "controller-task $T0→$T2, receptor $R0→$R2; un trabajo nuevo corrió (C3)" || anotar C2 "Aplicar programado" falla "$E task $T0→$T2 receptor $R0→$R2 siguiente=$E3"
[ "$E3" = exitoso ] && [ "${LAT3:-999}" -lt 120 ] && anotar C3 "Repetir tras el reinicio" ok "latido ${LAT3} s; AAP ejecuta" || anotar C3 "Repetir tras el reinicio" falla "$E3 latido=$LAT3"
T4=$(inicio_ automation-controller-task); pedir "$N" C4 '{"ac_modo":"ya"}' 10; E=$(estado); D=$(jq_ "r.get('detalle','')[:120]"); sleep 60; T5=$(inicio_ automation-controller-task)
[ "$E" = fallido ] && [[ "$D" == *"Datos válidos"* ]] && [ "$T4" = "$T5" ] && anotar C4 "Modo inválido" ok "fallido en «Datos válidos»; sin reinicio" || anotar C4 "Modo inválido" falla "$E $D task $T4→$T5"
certificar "$N" "Reiniciar despachador de AAP: evaluar, aplicar programado, repetir y modo inválido"
fi

# ───────────────────────────────────────────────────────────── Reiniciar FaceOps y despliegue
if [[ " $PLANES " == *" faceops "* ]]; then
N="Autocuidado · Reiniciar FaceOps y despliegue"; CASOS='[]'; TKS='[]'; echo "== $N"
pedir "$N" C1 '{"ac_modo":"evaluar"}' 10; E=$(estado); F=$(r_ ".get('faceops',{}).get('codigo')"); T=$(r_ ".get('autodeploy',{}).get('timer_activo')")
[ "$E" = exitoso ] && [ "$F" = 200 ] && anotar C1 "Evaluar" ok "faceops 200 · timer activo $T" || anotar C1 "Evaluar" falla "$E faceops=$F"
P0=$(sudo podman inspect -f '{{.State.Pid}}' faceops); pedir "$N" C2 '{"ac_modo":"aplicar"}' 10; E=$(estado); NA=$(jq_ "len(((s.get('faceops_autocuidado') or {}).get('resultado') or {}).get('acciones') or [])"); P2=$(sudo podman inspect -f '{{.State.Pid}}' faceops)
[ "$E" = exitoso ] && [ "$NA" = 0 ] && [ "$P0" = "$P2" ] && anotar C2 "Aplicar con todo sano" ok "sin acciones; FaceOps sin reinicio" || anotar C2 "Aplicar con todo sano" falla "$E acciones=$NA pid $P0→$P2"
sudo systemctl stop faceops-autodeploy.timer; pedir "$N" C3 '{"ac_modo":"aplicar"}' 10; E=$(estado); T3=$(systemctl is-active faceops-autodeploy.timer)
[ "$E" = exitoso ] && [ "$T3" = active ] && anotar C3 "Timer detenido" ok "timer vuelve a active" || anotar C3 "Timer detenido" falla "$E timer=$T3"
# C4: FaceOps detenido → AAP lanza directo (con la API de AAP del administrador; la clave no se imprime)
DESDE=$(date -u +%Y-%m-%dT%H:%M:%SZ); sudo systemctl stop faceops; sleep 3
CL=$(grep -h '^gateway_admin_password=' ~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo | head -1 | cut -d= -f2-)
JT=$(curl -sk -u "admin:$CL" "https://127.0.0.1/api/controller/v2/job_templates/?name=$(python3 -c 'import urllib.parse;print(urllib.parse.quote("Autocuidado · Reiniciar FaceOps y despliegue"))')" | python3 -c "import json,sys;print(json.load(sys.stdin)['results'][0]['id'])")
JB=$(curl -sk -u "admin:$CL" -H 'Content-Type: application/json' -X POST "https://127.0.0.1/api/controller/v2/job_templates/$JT/launch/" -d '{"extra_vars":{"autocuidado_sin_faceops":true,"autocuidado_regla":"AC20"}}' | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
for i in $(seq 1 60); do sleep 10; ST=$(curl -sk -u "admin:$CL" "https://127.0.0.1/api/controller/v2/jobs/$JB/" | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])"); [[ "$ST" =~ ^(successful|failed|error|canceled)$ ]] && break; done
unset CL; F4=$(curl -s -o /dev/null -w '%{http_code}' http://192.168.122.1:8110/ingresar); sleep 150
T4=$(sudo podman exec -i faceops python3 - "$JB" <<'Q' 2>/dev/null | tail -1
import sys
from app import create_app
create_app()
from app.bd import q1
t = q1("SELECT numero, motivo FROM tickets WHERE aap_job_id=?", int(sys.argv[1])); print(f"{t['numero']}|{t['motivo']}" if t else "sin ticket")
Q
)
[ "$ST" = successful ] && [ "$F4" = 200 ] && [[ "$T4" == *"sin FaceOps"* ]] && { TKS=$(python3 -c 'import json,sys;l=json.loads(sys.argv[1]);l.append(sys.argv[2]);print(json.dumps(l))' "$TKS" "${T4%%|*}"); anotar C4 "FaceOps detenido" ok "trabajo $JB successful; FaceOps 200; registrado ${T4%%|*}"; } || anotar C4 "FaceOps detenido" falla "job $JB $ST faceops=$F4 ticket=$T4"
pedir "$N" C5 '{"ac_modo":"reiniciar"}' 10; E=$(estado); D=$(jq_ "r.get('detalle','')[:120]")
[ "$E" = fallido ] && [[ "$D" == *"Datos válidos"* ]] && anotar C5 "Modo inválido" ok "fallido en «Datos válidos»" || anotar C5 "Modo inválido" falla "$E $D"
certificar "$N" "Reiniciar FaceOps y despliegue: evaluar, sano, timer detenido, FaceOps detenido y modo inválido"
fi

# ───────────────────────────────────────────────────────────── Respaldar
if [[ " $PLANES " == *" respaldo "* ]]; then
N="Autocuidado · Respaldar bases de AAP y FaceOps"; CASOS='[]'; TKS='[]'; echo "== $N"; B=/var/backups/faceops-autocuidado
n_() { sudo bash -c "ls $B/$1-*.gz 2>/dev/null | wc -l"; }
A0=$(n_ aap); F0=$(n_ faceops); pedir "$N" C1 '{"ac_modo":"evaluar"}' 10; E=$(estado)
[ "$E" = exitoso ] && [ "$(n_ aap)" = "$A0" ] && [ "$(n_ faceops)" = "$F0" ] && anotar C1 "Evaluar" ok "sin archivos nuevos" || anotar C1 "Evaluar" falla "$E"
pedir "$N" C2 '{"ac_modo":"aplicar"}' 40; E=$(estado)
UA=$(sudo bash -c "ls -t $B/aap-*.gz | head -1"); UF=$(sudo bash -c "ls -t $B/faceops-*.gz | head -1")
OK2=$(sudo bash -c "gzip -t $UA && gzip -t $UF && zcat $UA | grep -m1 -c 'CREATE DATABASE' && stat -c %a $UA $UF | sort -u | tr '\n' ' ' && zcat $UF > /tmp/ac-prueba.db && python3 -c \"import sqlite3;print(sqlite3.connect('/tmp/ac-prueba.db').execute('PRAGMA integrity_check').fetchone()[0])\"; rm -f /tmp/ac-prueba.db" 2>&1 | tr '\n' ' ')
[ "$E" = exitoso ] && [[ "$OK2" == "1 600 ok"* ]] && anotar C2 "Aplicar" ok "gzip ok, CREATE DATABASE, 0600, integrity ok" || anotar C2 "Aplicar" falla "$E $OK2"
pedir "$N" C3 '{"ac_modo":"aplicar"}' 40; E=$(estado); A3=$(n_ aap); F3=$(n_ faceops)
[ "$E" = exitoso ] && [ "$A3" -le 7 ] && [ "$F3" -le 7 ] && [ "$A3" -ge 2 ] && anotar C3 "Repetir" ok "aap=$A3 faceops=$F3 (≤7)" || anotar C3 "Repetir" falla "$E aap=$A3 faceops=$F3"
sudo cp -p /etc/faceops-autocuidado.conf /etc/faceops-autocuidado.conf.prueba; sudo mkdir -p /mnt/ac-prueba-chico; sudo mount -t tmpfs -o size=16m tmpfs /mnt/ac-prueba-chico
sudo python3 -c "import json;p='/etc/faceops-autocuidado.conf';d=json.load(open(p));d['respaldos']='/mnt/ac-prueba-chico';json.dump(d,open(p,'w'))"
pedir "$N" C4 '{"ac_modo":"aplicar"}' 10; E=$(estado); D=$(jq_ "(s.get('faceops_autocuidado') or {}).get('resultado',{}).get('error','')[:120]"); RES4=$(sudo ls /mnt/ac-prueba-chico | wc -l)
sudo mv /etc/faceops-autocuidado.conf.prueba /etc/faceops-autocuidado.conf; sudo umount /mnt/ac-prueba-chico; sudo rmdir /mnt/ac-prueba-chico
[ "$E" = fallido ] && [[ "$D" == *"sin espacio seguro"* ]] && [ "$RES4" = 0 ] && anotar C4 "Sin espacio seguro" ok "fallido con «sin espacio seguro»; sin archivos" || anotar C4 "Sin espacio seguro" falla "$E '$D' archivos=$RES4"
certificar "$N" "Respaldar: evaluar, aplicar, repetir y sin espacio"
fi

# ───────────────────────────────────────────────────────────── Renovar certificados
if [[ " $PLANES " == *" cert "* ]]; then
N="Autocuidado · Renovar certificados"; CASOS='[]'; TKS='[]'; echo "== $N"
venc() { echo | openssl s_client -connect 127.0.0.1:8843 -servername faceops.34.176.42.56.sslip.io 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null; }
V0=$(venc); pedir "$N" C1 '{"ac_modo":"evaluar"}' 10; E=$(estado); NC=$(jq_ "len([c for c in ((s.get('faceops_autocuidado') or {}).get('resultado') or {}).get('certificados',[]) if c.get('dias') is not None])")
[ "$E" = exitoso ] && [ "$NC" = 2 ] && anotar C1 "Evaluar" ok "días de AAP y FaceOps" || anotar C1 "Evaluar" falla "$E certificados=$NC"
pedir "$N" C2 '{"ac_modo":"aplicar"}' 15; E=$(estado); V2=$(venc); P2=$(curl -sk -o /dev/null -w '%{http_code}' --resolve faceops.34.176.42.56.sslip.io:8843:127.0.0.1 https://faceops.34.176.42.56.sslip.io:8843/faceops/ingresar)
[ "$E" = exitoso ] && [ "$V0" = "$V2" ] && [ "$P2" = 200 ] && anotar C2 "Aplicar (vigente)" ok "certbot sin renovar; $V2; público 200" || anotar C2 "Aplicar (vigente)" falla "$E $V0→$V2 público=$P2"
pedir "$N" C3 '{"ac_modo":"aplicar"}' 15; E=$(estado); V3=$(venc)
[ "$E" = exitoso ] && [ "$V3" = "$V2" ] && anotar C3 "Repetir" ok "mismo vencimiento" || anotar C3 "Repetir" falla "$E $V2→$V3"
pedir "$N" C4 '{"ac_modo":"forzar"}' 10; E=$(estado); D=$(jq_ "r.get('detalle','')[:120]")
[ "$E" = fallido ] && [[ "$D" == *"Datos válidos"* ]] && anotar C4 "Modo inválido" ok "fallido en «Datos válidos»" || anotar C4 "Modo inválido" falla "$E $D"
certificar "$N" "Renovar certificados: evaluar, aplicar, repetir y modo inválido"
fi

# ───────────────────────────────────────────────────────────── Purgar historial de AAP
if [[ " $PLANES " == *" purgar "* ]]; then
N="Autocuidado · Purgar historial de AAP"; CASOS='[]'; TKS='[]'; echo "== $N"
pedir "$N" C1 '{"ac_dias":365}' 30; E=$(estado); OKP=$(r_ ".get('estados')")
[ "$E" = exitoso ] && [[ "$OKP" == *successful* ]] && anotar C1 "Conservar 365 días" ok "cleanup_jobs $OKP" || anotar C1 "Conservar 365 días" falla "$E $OKP $(jq_ "r.get('detalle','')[:160]")"
pedir "$N" C2 '{"ac_dias":365}' 30; E=$(estado)
[ "$E" = exitoso ] && anotar C2 "Repetir" ok "exitoso" || anotar C2 "Repetir" falla "$E"
pedir "$N" C3 '{"ac_dias":7}' 10; E=$(estado); D=$(jq_ "r.get('detalle','')[:120]")
[ "$E" = fallido ] && [[ "$D" == *"Datos válidos"* ]] && anotar C3 "Bajo el mínimo" ok "fallido en «Datos válidos»" || anotar C3 "Bajo el mínimo" falla "$E $D"
certificar "$N" "Purgar historial de AAP: 365 días, repetir y bajo el mínimo"
fi

# ───────────────────────────────────────────────────────────── Atender alerta (requiere «Reiniciar FaceOps y despliegue» certificada para C2/C4)
if [[ " $PLANES " == *" atender "* ]]; then
N="Autocuidado · Atender alerta"; CASOS='[]'; TKS='[]'; echo "== $N"
ev() { sudo podman exec -i faceops python3 - "$1" <<'Q' 2>/dev/null | tail -1
import sys
from app import create_app
create_app()
from app.bd import q1
e = q1("SELECT e.decision, e.auto_aprobar, t.numero, t.estado, t.respuesta FROM autocuidado_eventos e LEFT JOIN tickets t ON t.id=e.ticket_id WHERE e.regla=? ORDER BY e.id DESC LIMIT 1", sys.argv[1])
print(f"{e['decision']}|{e['numero']}|{e['estado']}|{e['respuesta']}" if e else "sin evento")
Q
}
pedir "$N" C1 '{"ac_regla":"AC01","ac_accion":"avisar","ac_severidad":"warning","ac_resumen":"prueba de aviso","ac_objeto":"/"}' 10; E=$(estado); V=$(ev AC01)
[ "$E" = exitoso ] && [[ "$V" == "solo aviso"* ]] && anotar C1 "Solo avisar" ok "$V" || anotar C1 "Solo avisar" falla "$E $V"
pedir "$N" C2 '{"ac_regla":"AC22","ac_accion":"reiniciar_faceops","ac_severidad":"warning","ac_resumen":"prueba de despliegue detenido"}' 10; E=$(estado)
for i in $(seq 1 40); do sleep 15; V=$(ev AC22); [[ "$V" == *"|exitoso|"* || "$V" == *"|fallido|"* ]] && break; done
[ "$E" = exitoso ] && [[ "$V" == *"|exitoso|Aprobado por Claude (regla de Cristian)" ]] && { T2=$(echo "$V" | cut -d'|' -f2); TKS=$(python3 -c 'import json,sys;l=json.loads(sys.argv[1]);l.append(sys.argv[2]);print(json.dumps(l))' "$TKS" "$T2"); anotar C2 "Riesgo bajo certificada" ok "$T2 aprobado con la regla y exitoso"; } || anotar C2 "Riesgo bajo certificada" falla "$E $V"
pedir "$N" C3 '{"ac_regla":"AC07","ac_accion":"ampliar_disco","ac_severidad":"warning","ac_resumen":"prueba de capacidad"}' 10; E=$(estado); V=$(ev AC07)
[ "$E" = exitoso ] && [[ "$V" == "propuesta"*"|None|"* ]] && anotar C3 "Riesgo alto" ok "propuesta sin ticket" || anotar C3 "Riesgo alto" falla "$E $V"
pedir "$N" C4a '{"ac_regla":"AC22","ac_accion":"reiniciar_faceops","ac_severidad":"warning","ac_resumen":"prueba de agrupación"}' 10
sleep 5; pedir "$N" C4b '{"ac_regla":"AC22","ac_accion":"reiniciar_faceops","ac_severidad":"warning","ac_resumen":"prueba de agrupación"}' 10; E=$(estado); V=$(ev AC22)
[ "$E" = exitoso ] && [[ "$V" == "agrupada"* ]] && anotar C4 "Agrupar" ok "${V%%|*}" || anotar C4 "Agrupar" falla "$E $V"
pedir "$N" C5 '{"ac_regla":"xx; rm","ac_accion":"avisar"}' 10; E=$(estado); D=$(jq_ "r.get('detalle','')[:120]")
[ "$E" = fallido ] && [[ "$D" == *"Datos válidos"* ]] && anotar C5 "Regla inválida" ok "fallido en «Datos válidos»" || anotar C5 "Regla inválida" falla "$E $D"
# C6: alerta sintética en Alertmanager (regla AC01, solo avisar) → EDA :5023 → Atender alerta → FaceOps
AHORA=$(date -u +%Y-%m-%dT%H:%M:%SZ); FIN=$(date -u -d '+3 min' +%Y-%m-%dT%H:%M:%SZ)
curl -s -o /dev/null -w 'alertmanager %{http_code}\n' -H 'Content-Type: application/json' -X POST http://127.0.0.1:9093/api/v2/alerts \
  -d "[{\"labels\":{\"alertname\":\"FaceOpsAutocuidadoAC01\",\"faceops_autocuidado\":\"si\",\"faceops_ac_regla\":\"AC01\",\"faceops_ac_accion\":\"avisar\",\"faceops_ac_objeto\":\"prueba-cadena\",\"severity\":\"warning\"},\"annotations\":{\"summary\":\"Prueba de la cadena completa\"},\"startsAt\":\"$AHORA\",\"endsAt\":\"$FIN\"}]"
V=""; for i in $(seq 1 24); do sleep 10; V=$(sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -1
from app import create_app
create_app()
from app.bd import q1
e = q1("SELECT decision FROM autocuidado_eventos WHERE objeto='prueba-cadena' ORDER BY id DESC LIMIT 1"); print(e["decision"] if e else "")
Q
); [ -n "$V" ] && break; done
[[ "$V" == "solo aviso"* ]] && anotar C6 "Cadena completa" ok "Alertmanager → EDA → Atender alerta → FaceOps ($V)" || anotar C6 "Cadena completa" falla "sin evento en FaceOps ($V)"
certificar "$N" "Atender alerta: aviso, riesgo bajo, riesgo alto, agrupar, regla inválida y cadena completa"
fi

# ───────────────────────────────────────────────────────────── Vigía externo (no es una plantilla de AAP: informe, sin certificación)
if [[ " $PLANES " == *" vigia "* ]]; then
echo "== Vigía externo"
C=automation-gateway; sudo $H pausar --minutos 0 >/dev/null
systemctl --user stop $C.service 2>/dev/null || podman stop -t 10 $C >/dev/null
echo "  gateway detenido: API $(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1/api/controller/v2/ping/)"
for i in $(seq 1 60); do sleep 20; A=$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1/api/controller/v2/ping/); [ "$A" = 200 ] && break; done
echo "  API de AAP tras el vigía: $A en $((i*20)) s · decisión: $(sudo python3 -c "import json;print(json.load(open('/var/lib/faceops/autocuidado/vigia.json')).get('decision'))")"
echo "  última actuación: $(sudo python3 -c "import json;d=json.load(open('/var/lib/faceops/autocuidado/vigia.json'));print(json.dumps(d.get('ultima_actuacion'),ensure_ascii=False)[:300])")"
fi
