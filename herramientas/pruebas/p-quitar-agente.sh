# Plan «Sistema · Quitar agente» (C1–C3) sobre una máquina de prueba: respaldo continuo, node_exporter y agente desconocido
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Sistema · Quitar agente"; CASOS='[]'; TKS='[]'
# máquina de prueba: nodo-jboss-qa (nunca nodo-jboss, la principal de la demo); se enciende por FaceOps y al final queda apagada
M=""; for c_ in nodo-jboss-qa; do existe $c_ && [ -n "$(ip_de $c_)" ] && { M=$c_; break; }; done
[ -z "$M" ] && { echo "CASO C0 falla · nodo-jboss-qa no disponible"; exit 0; }
for i in 1 2 3; do energia $M encender; [ "$(estado)" = exitoso ] && break; sleep 90; done   # AAP o FaceOps pueden estar reiniciándose
IP=$(ip_de $M); echo "maquina $M"
[ "$(estado)" = exitoso ] || { echo "CASO C0 falla · no se pudo encender $M ($(ticket))"; exit 0; }
sk() { sudo podman exec -i faceops python3 - "$M" "$1" 2>/dev/null <<'Q' | tail -1
import sys, json
from app import create_app
create_app()
from app.bd import q1
e = q1("SELECT skills_auto, skills_manual FROM equipos WHERE nombre=? AND estado!='retirado'", sys.argv[1])
print("si" if e and sys.argv[2] in json.loads(e["skills_auto"] or "[]") + json.loads(e["skills_manual"] or "[]") else "no")
Q
}
O_RES=$(sk respaldo-git); O_MON=$(sk node-exporter); echo "prep originales respaldo=$O_RES monitoreo=$O_MON"
# preparación: la máquina de prueba con ambos agentes (si ya los tiene, FaceOps no relanza)
caso "{\"auto\":\"FaceOps · Respaldo continuo en Git\",\"equipo\":\"$M\",\"params\":{},\"min\":15,\"caso\":\"prep-respaldo\"}" >/dev/null; echo "prep respaldo: $(estado) $(ticket)"
caso "{\"auto\":\"FaceOps · Agente de monitoreo (Linux)\",\"equipo\":\"$M\",\"params\":{},\"min\":15,\"caso\":\"prep-monitoreo\"}" >/dev/null; echo "prep monitoreo: $(estado) $(ticket)"
R0=$(en $IP 'systemctl is-active faceops-respaldo.timer node_exporter' | tr '\n' ' '); echo "antes: $R0"
# C1 · quitar el respaldo continuo
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"qa_agente\":\"respaldo-git\"},\"min\":10,\"caso\":\"C1\"}" >/dev/null; E1=$(estado)
U1=$(en $IP 'ls /etc/systemd/system/faceops-respaldo* /usr/local/sbin/faceops-respaldo* 2>/dev/null | wc -l'); S1=$(sk respaldo-git); N1=$(en $IP 'systemctl is-active node_exporter')
[ "$E1" = exitoso ] && [ "$U1" = 0 ] && [ "$S1" = no ] && [ "$N1" = active ] && anotar C1 "Quitar respaldo continuo" ok "timers y scripts retirados (0 archivos); skill respaldo-git fuera del equipo (Agentes: no instalado); node_exporter intacto; la rama en Git no se toca" || anotar C1 "Quitar respaldo continuo" falla "$E1 archivos=$U1 skill=$S1 node=$N1 $(jq_ "(r.get('detalle') or '')[:160]")"
# C2 · quitar node_exporter
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"qa_agente\":\"node-exporter\"},\"min\":10,\"caso\":\"C2\"}" >/dev/null; E2=$(estado)
A2=$(en $IP 'systemctl is-active node_exporter; test -e /usr/local/bin/node_exporter && echo bin || echo sinbin' | tr '\n' ' '); P2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://$IP:9100/metrics); S2=$(sk node-exporter)
[ "$E2" = exitoso ] && echo "$A2" | grep -q sinbin && ! echo "$A2" | grep -qw active && [ "$P2" = 000 ] && anotar C2 "Quitar node_exporter" ok "servicio $A2; puerto 9100 cerrado (HTTP $P2); skill=$S2" || anotar C2 "Quitar node_exporter" falla "$E2 $A2 http=$P2 skill=$S2"
# C3 · agente desconocido
H0=$(en $IP 'ls /etc/systemd/system | md5sum | cut -c1-10')
caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{\"qa_agente\":\"agente-inexistente\"},\"min\":10,\"caso\":\"C3\"}" >/dev/null; E3=$(estado)
H1=$(en $IP 'ls /etc/systemd/system | md5sum | cut -c1-10')
{ [ "$E3" = fallido ] || { [ "$E3" = "rechazado al solicitar" ] && [ -n "$(jq_ "r.get('error','') if __import__('re').search(r'(?i)agente desconocido|qa_agente|opci[oó]n|no v[aá]lid', r.get('error','')) else ''")" ]; }; } && [ "$H0" = "$H1" ] && anotar C3 "Agente desconocido" ok "$E3 ($(jq_ "(r.get('detalle') or r.get('error') or '')[:140]")); la máquina no cambia (huella $H0)" || anotar C3 "Agente desconocido" falla "$E3 $H0→$H1"
echo "EVIDENCIA $CASOS"
certificar "$N" "Plan quitar agente · C1–C3 (planes-prueba.yml)"
# deja la máquina de prueba como estaba (solo reinstala los agentes que tenía antes del plan)
[ "$O_RES" = si ] && { caso "{\"auto\":\"FaceOps · Respaldo continuo en Git\",\"equipo\":\"$M\",\"params\":{},\"min\":15,\"caso\":\"restaurar\"}" >/dev/null; echo "restaurar respaldo: $(estado) $(ticket)"; }
[ "$O_MON" = si ] && { caso "{\"auto\":\"FaceOps · Agente de monitoreo (Linux)\",\"equipo\":\"$M\",\"params\":{},\"min\":15,\"caso\":\"restaurar\"}" >/dev/null; echo "restaurar monitoreo: $(estado) $(ticket)"; }
energia $M apagar   # queda apagada, como estaba
echo FIN-QUITAR
