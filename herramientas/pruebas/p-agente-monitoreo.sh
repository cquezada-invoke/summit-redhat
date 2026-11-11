# Plan «FaceOps · Agente de monitoreo (Linux)» (C1–C4): node_exporter + métricas de seguridad (faceops-seguridad.prom)
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="FaceOps · Agente de monitoreo (Linux)"; CASOS='[]'; TKS='[]'
# si FaceOps se reinicia a mitad de un caso (otro despliegue), el arnés no responde: espera a que vuelva y repite el caso una vez
caso_() { caso "$@"; if [ "$(estado)" = "sin respuesta del arnés" ]; then for i in $(seq 1 30); do sleep 10; curl -s -o /dev/null -w '%{http_code}' http://192.168.122.1:8110/sesion/valida | grep -q 401 && break; done; sleep 20; caso "$@"; fi; }
L=nodo-jboss; energia $L encender; IL=""; for i in $(seq 1 12); do IL=$(ip_de $L); [ -n "$IL" ] && break; sleep 10; done; echo "$L ${IL:+con IP}"   # FaceOps puede estar reiniciándose por otro despliegue
seg() { curl -s --max-time 8 http://$IL:9100/metrics | grep '^faceops_seg_' ; }
# C1 · instalar
caso_ "{\"auto\":\"$N\",\"equipo\":\"$L\",\"params\":{},\"reinstalar\":true,\"min\":15,\"caso\":\"C1\"}" >/dev/null; E1=$(estado); sleep 75
M1=$(seg | wc -l); T1=$(en $IL 'systemctl is-active faceops-seguridad.timer node_exporter' | tr '\n' ' ')
[ "$E1" = exitoso ] && [ "$M1" -ge 8 ] && [ "$T1" = "active active " ] && anotar C1 "Instalar" ok "node_exporter y temporizador activos; $M1 métricas faceops_seg_*: $(seg | cut -d' ' -f1 | sed 's/{.*//' | sort -u | tr '\n' ' ')" || anotar C1 "Instalar" falla "$E1 métricas=$M1 servicios=$T1 $(jq_ "(r.get('detalle') or '')[:200]")"
# C2 · repetir
P0=$(en $IL 'systemctl show -p ExecMainPID --value node_exporter'); caso_ "{\"auto\":\"$N\",\"equipo\":\"$L\",\"params\":{},\"reinstalar\":true,\"min\":15,\"caso\":\"C2\"}" >/dev/null; E2=$(estado)
P2=$(en $IL 'systemctl show -p ExecMainPID --value node_exporter'); U2=$(en $IL 'systemctl list-timers --all --no-legend faceops-seguridad.timer | wc -l')
[ "$E2" = exitoso ] && [ "$P0" = "$P2" ] && [ "$U2" = 1 ] && [ "$(seg | wc -l)" -ge 8 ] && anotar C2 "Repetir" ok "sin reiniciar el agente (PID $P2), un temporizador, métricas presentes" || anotar C2 "Repetir" falla "$E2 pid $P0→$P2 timers=$U2"
# C3 · 6 ingresos SSH fallidos desde el anfitrión (usuario inexistente, clave errada)
A=$(mktemp); printf '#!/bin/sh\necho clave-errada\n' > $A; chmod +x $A
for i in 1 2 3 4 5 6; do SSH_ASKPASS=$A SSH_ASKPASS_REQUIRE=force DISPLAY=:0 timeout 15 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PubkeyAuthentication=no \
  -o PreferredAuthentications=password,keyboard-interactive -o NumberOfPasswordPrompts=1 -o ConnectTimeout=5 -o LogLevel=ERROR faceops-prueba-inexistente@$IL true </dev/null >/dev/null 2>&1; done; rm -f $A
F3=0; for i in $(seq 1 15); do sleep 10; F3=$(seg | sed -n 's/^faceops_seg_ssh_fallidos_5m //p'); [ "${F3:-0}" -ge 6 ] && break; done
[ "${F3:-0}" -ge 6 ] && anotar C3 "SSH fallidos" ok "faceops_seg_ssh_fallidos_5m=$F3 tras 6 intentos" || anotar C3 "SSH fallidos" falla "faceops_seg_ssh_fallidos_5m=$F3"
# C4 · equipo apagado
# usa una máquina de prueba que ya esté apagada (otros planes usan las encendidas); si no hay, apaga srv-prueba-rocky-01
X=$(sudo podman exec -i faceops python3 - 2>/dev/null <<'Q' | tail -1
from app import create_app
create_app()
from app.bd import q1
r = q1("SELECT nombre FROM equipos WHERE nombre LIKE 'srv-prueba-%' AND estado!='retirado' AND energia='apagado' AND lower(so) NOT LIKE '%windows%' AND ip!='' ORDER BY nombre LIMIT 1")
print((r or {}).get("nombre") or "")
Q
)
if [ -z "$X" ]; then X=srv-prueba-rocky-01; energia $X apagar >/dev/null; sleep 20; fi
caso_ "{\"auto\":\"$N\",\"equipo\":\"$X\",\"params\":{},\"reinstalar\":true,\"min\":10,\"caso\":\"C4\"}" >/dev/null; E4=$(estado)
[ "$E4" = fallido ] && anotar C4 "Equipo apagado" ok "$X apagado: el ticket falla ($(jq_ "(r.get('detalle') or '')[:120]"))" || anotar C4 "Equipo apagado" falla "$X: $E4 $(jq_ "(r.get('detalle') or r.get('error') or '')[:200]")"
echo "C4 $X $E4"
echo "EVIDENCIA $CASOS"
certificar "$N" "Plan agente de monitoreo · C1–C4 (planes-prueba.yml)"
echo FIN-AGENTE
