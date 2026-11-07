# Plan «Infra · Mover máquina entre centros de datos» (C1–C6) sobre srv-rocky-prueba-01
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Infra · Mover máquina entre centros de datos"; M=srv-rocky-prueba-01
ub() { sudo podman exec -i faceops python3 - "$M" 2>/dev/null <<'Q' | tail -1
import sys
from app import create_app
create_app()
from app.bd import q1
e = q1("SELECT ip, ubicacion FROM equipos WHERE nombre=? AND estado!='retirado'", sys.argv[1]); print(f"{(e or {}).get('ip')}|{(e or {}).get('ubicacion')}")
Q
}
mv() { caso "{\"auto\":\"$N\",\"params\":{\"mv_maquina\":\"${3:-$M}\",\"mv_destino\":\"$1\",\"mv_modo\":\"$2\"},\"min\":40,\"caso\":\"$4\"}"; }
doms() { sudo virsh list --all --name | grep -E "^$M(-origen|-destino)?$" | tr '\n' ' '; }
echo "inicio: $(ub) · dominios: $(doms)"
mv dc-valpo evaluar "" C1; D1=$(doms)
[ "$(estado)" = exitoso ] && [ -n "$(jq_ "s.get('faceops_traslado')")" ] && anotar C1 "evaluar Santiago → Valparaíso" ok "$(jq_ "str(s.get('faceops_traslado'))[:150]") · sin cambios ($D1)" || anotar C1 "evaluar" falla "$(estado)"
mv dc-valpo mover "" C2; U2=$(ub); R2=$(jq_ "(s.get('faceops_traslado') or {}).get('responde')"); D2=$(doms)
echo "$U2 · responde=$R2 · $D2"
[ "$(estado)" = exitoso ] && echo "$U2" | grep -q "^192.168.150." && echo "$U2" | grep -q "Valpara" && [ "$R2" = True ] && echo "$D2" | grep -q -- "-origen" && anotar C2 "mover Santiago → Valparaíso" ok "$U2; responde; $D2" || anotar C2 "mover Santiago → Valparaíso" falla "$(estado) $U2 $R2 $D2"
mv dc-valpo revertir "" C3; U3=$(ub); D3=$(doms)
[ "$(estado)" = exitoso ] && echo "$U3" | grep -q "^192.168.122." && echo "$U3" | grep -q Santiago && ! echo "$D3" | grep -q -- "-destino\|-origen" && anotar C3 revertir ok "$U3; dominios $D3" || anotar C3 revertir falla "$(estado) $U3 $D3"
mv dc-valpo mover "" C4a; E4a=$(estado); mv dc-valpo confirmar "" C4b; U4=$(ub); D4=$(doms)
[ "$E4a" = exitoso ] && [ "$(estado)" = exitoso ] && echo "$U4" | grep -q "^192.168.150." && ! echo "$D4" | grep -q -- "-origen" && anotar C4 "mover y confirmar" ok "$U4; origen retirada ($D4)" || anotar C4 "mover y confirmar" falla "$E4a/$(estado) $U4 $D4"
mv default mover "" C5a; E5a=$(estado); mv default confirmar "" C5b; U5=$(ub)
[ "$E5a" = exitoso ] && [ "$(estado)" = exitoso ] && echo "$U5" | grep -q "^192.168.122." && echo "$U5" | grep -q Santiago && anotar C5 "Valparaíso → Santiago" ok "$U5" || anotar C5 "Valparaíso → Santiago" falla "$E5a/$(estado) $U5"
mv default evaluar "" C6a; E6a=$(estado); mv dc-valpo evaluar nodo-vault C6b
{ [ "$E6a" = fallido ] || [ "$E6a" = "rechazado al solicitar" ]; } && { [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; } && anotar C6 "destino actual y máquina protegida" ok "ambos rechazados sin tocar nada" || anotar C6 "destino actual y máquina protegida" falla "$E6a/$(estado)"
echo "final: $(ub) · $(doms) · estado VM $(sudo virsh domstate $M)"
certificar "$N" "Plan traslado · C1–C6 (planes-prueba.yml)"
sudo virsh shutdown $M >/dev/null 2>&1
echo FIN-MOVER
