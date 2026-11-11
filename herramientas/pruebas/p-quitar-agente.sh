# Plan «Sistema · Quitar agente» (C1–C3) sobre una máquina de prueba: respaldo continuo, node_exporter y agente desconocido
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Sistema · Quitar agente"; CASOS='[]'; TKS='[]'
# máquina de prueba vigente (no retirada) con RHEL/Rocky 8 o superior (el respaldo continuo usa dnf); primero la de respaldos
M=$(sudo podman exec -i faceops python3 - 2>/dev/null <<'Q' | tail -1
import re
from app import create_app
create_app()
from app.bd import q
ok = []
for e in q("SELECT nombre, so FROM equipos WHERE nombre LIKE 'srv-prueba-%' AND estado!='retirado' AND ip!='' ORDER BY nombre"):
    so = (e["so"] or "").lower(); m = re.search(r"(\d+)", so)
    if "windows" not in so and "ubuntu" not in so and m and int(m.group(1)) >= 8:
        ok.append(e["nombre"])
print(next((x for x in ok if "resp" in x), ok[0] if ok else ""))
Q
)
[ -z "$M" ] && { echo "CASO C0 falla · sin máquina de prueba Linux vigente"; exit 0; }
energia $M encender; IP=$(ip_de $M); echo "maquina $M"
sk() { sudo podman exec -i faceops python3 - "$M" "$1" 2>/dev/null <<'Q' | tail -1
import sys, json
from app import create_app
create_app()
from app.bd import q1
e = q1("SELECT skills_auto, skills_manual FROM equipos WHERE nombre=? AND estado!='retirado'", sys.argv[1])
print("si" if e and sys.argv[2] in json.loads(e["skills_auto"] or "[]") + json.loads(e["skills_manual"] or "[]") else "no")
Q
}
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
{ [ "$E3" = fallido ] || [ "$E3" = "rechazado al solicitar" ]; } && [ "$H0" = "$H1" ] && anotar C3 "Agente desconocido" ok "$E3 ($(jq_ "(r.get('detalle') or r.get('error') or '')[:140]")); la máquina no cambia (huella $H0)" || anotar C3 "Agente desconocido" falla "$E3 $H0→$H1"
echo "EVIDENCIA $CASOS"
certificar "$N" "Plan quitar agente · C1–C3 (planes-prueba.yml)"
# deja la máquina de prueba como estaba (con monitoreo)
caso "{\"auto\":\"FaceOps · Agente de monitoreo (Linux)\",\"equipo\":\"$M\",\"params\":{},\"min\":15,\"caso\":\"restaurar\"}" >/dev/null; echo "restaurar monitoreo: $(estado) $(ticket)"
# la máquina queda encendida: otros planes la usan en paralelo
echo FIN-QUITAR
