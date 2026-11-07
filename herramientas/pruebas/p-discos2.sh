# Pasos de disco, probados a través de sus workflows (así los lanza FaceOps): «FaceOps · Agregar disco» = Infra · Agregar disco
# (anfitrión) + Sistema · Preparar disco nuevo; «FaceOps · Ampliar disco» = Infra · Ampliar disco (anfitrión) + Sistema · Ampliar disco
# en la máquina. Cada paso se certifica con sus propios casos y la evidencia de los tickets del workflow.
source ~/summit-redhat/herramientas/pruebas/comun.sh
ok_() { [ "$(estado)" = exitoso ]; }; no_() { [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; }
energia nodo-storage encender
IPS=$(ip_de nodo-storage)
nd() { en $IPS 'lsblk -dn -o NAME | wc -l'; }
sz() { en $IPS "df -BG --output=size $1 | tail -1 | tr -dc 0-9"; }
declare -A CA TK   # casos y tickets por automación
an() { local k="$1"; shift; CA[$k]=$(python3 -c 'import json,sys;l=json.loads(sys.argv[1] or "[]");l.append({"id":sys.argv[2],"caso":sys.argv[3],"resultado":sys.argv[4],"evidencia":sys.argv[5][:300]});print(json.dumps(l,ensure_ascii=False))' "${CA[$k]}" "$@"); TK[$k]="${TK[$k]} $(ticket)"; echo "CASO [$k] $1 $3 · $2"; }
cert() { CASOS="${CA[$1]}"; TKS=$(python3 -c 'import json,sys;print(json.dumps(sorted(set(sys.argv[1].split()))))' "${TK[$1]}"); certificar "$1" "$2"; }
AG="FaceOps · Agregar disco"; AM="FaceOps · Ampliar disco"
A1="Infra · Agregar disco (anfitrión)"; A2="Sistema · Preparar disco nuevo"; A3="Infra · Ampliar disco (anfitrión)"; A4="Sistema · Ampliar disco en la máquina"
SER="fa$(date +%s | tail -c 9)"
agregar() { caso "{\"auto\":\"$AG\",\"params\":{\"ad_equipo\":\"nodo-storage\",\"ad_gb\":\"2\",\"ad_origen\":\"hipervisor\",\"ad_nombre\":\"pruebaanf\",\"ad_formatear\":true,\"ad_serial\":\"$1\",\"ad_pool\":\"$2\",\"ad_montaje\":\"$3\"},\"min\":20,\"caso\":\"$4\"}"; }
D0=$(nd)
agregar $SER images /srv/prueba-sis C1; D1=$(nd); DEV=$(en $IPS "lsblk -dn -o NAME,SERIAL | awk '\$2==\"$SER\"{print \$1}'"); F1=$(en $IPS "findmnt -no FSTYPE /srv/prueba-sis; grep -c '^UUID=.* /srv/prueba-sis ' /etc/fstab")
ok_ && [ -n "$DEV" ] && [ "$D1" = $((D0+1)) ] && an "$A1" C1 "Agregar 2 GB del pool" ok "/dev/$DEV con serie $SER (workflow $(ticket))" || an "$A1" C1 "Agregar 2 GB del pool" falla "$(estado) dev=$DEV $D0→$D1 $(jq_ "r.get('detalle')")"
ok_ && echo "$F1" | head -1 | grep -q xfs && [ "$(echo "$F1" | tail -1)" = 1 ] && an "$A2" C1 "Particionar, XFS y montar" ok "/srv/prueba-sis XFS, fstab por UUID" || an "$A2" C1 "Particionar, XFS y montar" falla "$(estado) $F1"
U1=$(en $IPS 'findmnt -no UUID /srv/prueba-sis')
agregar $SER images /srv/prueba-sis C2; D2=$(nd); U2=$(en $IPS 'findmnt -no UUID /srv/prueba-sis')
ok_ && [ "$D2" = "$D1" ] && an "$A1" C2 "Repetir con la misma serie" ok "sigue con $D2 discos" || an "$A1" C2 "Repetir con la misma serie" falla "$(estado) $D1→$D2"
ok_ && [ -n "$U1" ] && [ "$U1" = "$U2" ] && an "$A2" C2 "Repetir" ok "no reformatea (UUID $U2)" || an "$A2" C2 "Repetir" falla "$(estado) $U1→$U2"
agregar x$SER noexiste /srv/prueba-mal C3; D3=$(nd)
no_ && [ "$D3" = "$D2" ] && an "$A1" C3 "Pool inexistente" ok "falla controlada; discos sin cambio" || an "$A1" C3 "Pool inexistente" falla "$(estado) $D2→$D3"
DA=$(en $IPS "findmnt -no SOURCE /srv/prueba-ad | sed 's|/dev/||;s/[0-9]*$//'"); SA=$(en $IPS "lsblk -dn -o NAME,SERIAL | awk '\$1==\"$DA\"{print \$2}'"); UA=$(en $IPS 'findmnt -no UUID /srv/prueba-ad')
agregar "$SA" images /srv/otro-montaje C3b; UA2=$(en $IPS 'findmnt -no UUID /srv/prueba-ad')
[ -n "$UA" ] && [ "$UA" = "$UA2" ] && an "$A2" C3 "Disco que ya tiene datos" ok "no lo formatea ($(estado)); UUID $UA2 intacto" || an "$A2" C3 "Disco que ya tiene datos" falla "$(estado) $UA→$UA2"
cert "$A1" "Plan agregar disco en el anfitrión · C1–C3 (vía workflow)"; cert "$A2" "Plan preparar disco nuevo · C1–C3 (vía workflow)"
ampliar() { caso "{\"auto\":\"$AM\",\"params\":{\"ae_equipo\":\"nodo-storage\",\"ae_disco\":\"$DA\",\"ae_gb\":$1,\"ae_montaje\":\"$2\"},\"min\":20,\"caso\":\"$3\"}"; }
cap() { sudo virsh domblkinfo nodo-storage $DA 2>/dev/null | awk '/Capacity/{printf "%d", $2/1073741824}'; }
S0=$(sz /srv/prueba-ad); G0=$(cap); T=$((G0+3))
ampliar $T /srv/prueba-ad C1; S1=$(sz /srv/prueba-ad); G1=$(cap); UA3=$(en $IPS 'findmnt -no UUID /srv/prueba-ad')
ok_ && [ "$G1" = "$T" ] && an "$A3" C1 "Ampliar encendida" ok "anfitrión $G0 → $G1 GB" || an "$A3" C1 "Ampliar encendida" falla "$(estado) $G0→$G1"
ok_ && [ "${S1:-0}" -gt "${S0:-0}" ] && [ "$UA3" = "$UA" ] && an "$A4" C1 "Crecer partición y XFS" ok "/srv/prueba-ad $S0 → $S1 G sin desmontar" || an "$A4" C1 "Crecer partición y XFS" falla "$(estado) $S0→$S1"
ampliar $T /srv/prueba-ad C2; S2=$(sz /srv/prueba-ad); G2=$(cap)
ok_ && [ "$S2" = "$S1" ] && [ "$G2" = "$G1" ] && an "$A4" C2 "Repetir" ok "sin cambios ($S2 G)" || an "$A4" C2 "Repetir" falla "$(estado) $S1→$S2"
ampliar $((G0)) /srv/prueba-ad C3; G3=$(cap)
no_ && [ "$G3" = "$G1" ] && an "$A3" C2 "Tamaño menor" ok "rechazado; sigue en $G3 GB" || an "$A3" C2 "Tamaño menor" falla "$(estado) $G3"
ampliar $T /noexiste C4
no_ && an "$A4" C3 "Montaje inexistente" ok "falla controlada" || an "$A4" C3 "Montaje inexistente" falla "$(estado)"
cert "$A3" "Plan ampliar disco en el anfitrión · C1–C2 (vía workflow)"; cert "$A4" "Plan ampliar disco en la máquina · C1–C3 (vía workflow)"
# El workflow «FaceOps · Ampliar disco» con su propio plan: C1 ampliar, C2 repetir, C3 menor
CASOS=$(python3 -c 'import json,sys;print(json.dumps([{"id":"C1","caso":"Ampliar en línea","resultado":"ok" if sys.argv[1]=="1" else "falla","evidencia":sys.argv[2]},{"id":"C2","caso":"Repetir el mismo tamaño","resultado":"ok" if sys.argv[3]=="1" else "falla","evidencia":""},{"id":"C3","caso":"Tamaño menor","resultado":"ok" if sys.argv[4]=="1" else "falla","evidencia":""}]))' \
  "$([ "${S1:-0}" -gt "${S0:-0}" ] && [ "$G1" = "$T" ] && echo 1)" "/srv/prueba-ad $S0→$S1 G" "$([ "$S2" = "$S1" ] && echo 1)" "$([ "$G3" = "$G1" ] && echo 1)")
TKS=$(python3 -c 'import json,sys;print(json.dumps(sorted(set(sys.argv[1].split()))))' "${TK[$A3]} ${TK[$A4]}"); certificar "$AM" "Plan ampliar disco · C1–C3"
echo FIN-DISCOS2
