# Planes de los pasos de disco: Infra · Agregar disco (anfitrión), Sistema · Preparar disco nuevo, FaceOps · Ampliar disco,
# Infra · Ampliar disco (anfitrión) y Sistema · Ampliar disco en la máquina (sobre discos de prueba de nodo-storage)
source ~/summit-redhat/herramientas/pruebas/comun.sh
energia nodo-storage encender
IPS=$(ip_de nodo-storage)
ok_() { [ "$(estado)" = exitoso ]; }; no_() { [ "$(estado)" = fallido ] || [ "$(estado)" = "rechazado al solicitar" ]; }
nd() { en $IPS 'lsblk -dn -o NAME | wc -l'; }
# ── Infra · Agregar disco (anfitrión)
CASOS='[]'; TKS='[]'; N="Infra · Agregar disco (anfitrión)"; SER="fa$(date +%s | tail -c 9)"
D0=$(nd); P="{\"ad_equipo\":\"nodo-storage\",\"ad_gb\":\"2\",\"ad_origen\":\"hipervisor\",\"ad_pool\":\"images\",\"ad_nombre\":\"pruebaanf\",\"ad_serial\":\"$SER\"}"
caso "{\"auto\":\"$N\",\"params\":$P,\"min\":15,\"caso\":\"C1\"}"; sleep 5
V1=$(en $IPS "lsblk -dn -o NAME,SERIAL | awk '\$2==\"$SER\"{print \$1}'"); D1=$(nd)
ok_ && [ -n "$V1" ] && [ "$D1" = $((D0+1)) ] && anotar C1 "Agregar 2 GB del pool" ok "la máquina ve /dev/$V1 con serie $SER" || anotar C1 "Agregar 2 GB del pool" falla "$(estado) dev=$V1 $D0→$D1 $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"params\":$P,\"min\":15,\"caso\":\"C2\"}"; D2=$(nd)
ok_ && [ "$D2" = "$D1" ] && anotar C2 "Repetir con la misma serie" ok "sigue con $D2 discos" || anotar C2 "Repetir con la misma serie" falla "$(estado) $D1→$D2"
caso "{\"auto\":\"$N\",\"params\":{\"ad_equipo\":\"nodo-storage\",\"ad_gb\":\"2\",\"ad_origen\":\"hipervisor\",\"ad_pool\":\"noexiste\",\"ad_nombre\":\"pruebamal\",\"ad_serial\":\"x$SER\"},\"min\":15,\"caso\":\"C3\"}"; D3=$(nd)
no_ && [ "$D3" = "$D2" ] && anotar C3 "Pool inexistente" ok "falla controlada; discos sin cambio" || anotar C3 "Pool inexistente" falla "$(estado) $D2→$D3"
certificar "$N" "Plan agregar disco en el anfitrión · C1–C3"
# ── Sistema · Preparar disco nuevo (sobre el disco recién agregado)
CASOS='[]'; TKS='[]'; N="Sistema · Preparar disco nuevo"
PP="{\"ad_equipo\":\"nodo-storage\",\"ad_serial\":\"$SER\",\"ad_origen\":\"hipervisor\",\"ad_montaje\":\"/srv/prueba-sis\",\"ad_formatear\":true}"
caso "{\"auto\":\"$N\",\"params\":$PP,\"min\":15,\"caso\":\"C1\"}"
F1=$(en $IPS "findmnt -no FSTYPE,UUID /srv/prueba-sis; grep -c '^UUID=.* /srv/prueba-sis ' /etc/fstab")
ok_ && echo "$F1" | grep -q xfs && [ "$(echo "$F1" | tail -1)" = 1 ] && anotar C1 "Particionar, XFS y montar" ok "$(echo $F1)" || anotar C1 "Particionar, XFS y montar" falla "$(estado) $F1 $(jq_ "r.get('detalle')")"
U1=$(en $IPS 'findmnt -no UUID /srv/prueba-sis')
caso "{\"auto\":\"$N\",\"params\":$PP,\"min\":15,\"caso\":\"C2\"}"; U2=$(en $IPS 'findmnt -no UUID /srv/prueba-sis')
ok_ && [ -n "$U1" ] && [ "$U1" = "$U2" ] && anotar C2 "Repetir" ok "no reformatea (UUID $U2)" || anotar C2 "Repetir" falla "$(estado) $U1→$U2"
DA=$(en $IPS "findmnt -no SOURCE /srv/prueba-ad | sed 's|/dev/||;s/[0-9]*$//'"); SA=$(en $IPS "lsblk -dn -o NAME,SERIAL | awk '\$1==\"$DA\"{print \$2}'"); UA=$(en $IPS 'findmnt -no UUID /srv/prueba-ad')
caso "{\"auto\":\"$N\",\"params\":{\"ad_equipo\":\"nodo-storage\",\"ad_serial\":\"$SA\",\"ad_origen\":\"hipervisor\",\"ad_montaje\":\"/srv/otro-montaje\",\"ad_formatear\":true},\"min\":15,\"caso\":\"C3\"}"
UA2=$(en $IPS 'findmnt -no UUID /srv/prueba-ad')
[ -n "$UA" ] && [ "$UA" = "$UA2" ] && anotar C3 "Disco que ya tiene datos" ok "no lo formatea ($(estado)); UUID $UA2 intacto" || anotar C3 "Disco que ya tiene datos" falla "$(estado) UUID $UA→$UA2"
certificar "$N" "Plan preparar disco nuevo · C1–C3"
# ── FaceOps · Ampliar disco (workflow)
CASOS='[]'; TKS='[]'; N="FaceOps · Ampliar disco"
sz() { en $IPS "df -BG --output=size $1 | tail -1 | tr -dc 0-9"; }
S0=$(sz /srv/prueba-ad)
caso "{\"auto\":\"$N\",\"params\":{\"ae_equipo\":\"nodo-storage\",\"ae_disco\":\"$DA\",\"ae_gb\":10,\"ae_montaje\":\"/srv/prueba-ad\"},\"min\":20,\"caso\":\"C1\"}"; S1=$(sz /srv/prueba-ad); UA3=$(en $IPS 'findmnt -no UUID /srv/prueba-ad')
ok_ && [ "${S1:-0}" -ge 9 ] && [ "$UA3" = "$UA" ] && anotar C1 "Ampliar a 10 GB en línea" ok "/srv/prueba-ad $S0 G → $S1 G, mismo UUID" || anotar C1 "Ampliar a 10 GB en línea" falla "$(estado) $S0→$S1 $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"params\":{\"ae_equipo\":\"nodo-storage\",\"ae_disco\":\"$DA\",\"ae_gb\":10,\"ae_montaje\":\"/srv/prueba-ad\"},\"min\":20,\"caso\":\"C2\"}"; S2=$(sz /srv/prueba-ad)
ok_ && [ "$S2" = "$S1" ] && anotar C2 "Repetir el mismo tamaño" ok "sigue en $S2 G" || anotar C2 "Repetir el mismo tamaño" falla "$(estado) $S1→$S2"
caso "{\"auto\":\"$N\",\"params\":{\"ae_equipo\":\"nodo-storage\",\"ae_disco\":\"$DA\",\"ae_gb\":3,\"ae_montaje\":\"/srv/prueba-ad\"},\"min\":20,\"caso\":\"C3\"}"; S3_=$(sz /srv/prueba-ad)
no_ && [ "$S3_" = "$S2" ] && anotar C3 "Tamaño menor" ok "rechazado; sigue en $S3_ G" || anotar C3 "Tamaño menor" falla "$(estado) $S3_"
certificar "$N" "Plan ampliar disco · C1–C3"
# ── Infra · Ampliar disco (anfitrión) y Sistema · Ampliar disco en la máquina (por separado)
CASOS='[]'; TKS='[]'; N="Infra · Ampliar disco (anfitrión)"
cap() { sudo virsh domblkinfo nodo-storage $DA 2>/dev/null | awk '/Capacity/{printf "%d", $2/1073741824}'; }
caso "{\"auto\":\"$N\",\"params\":{\"ae_equipo\":\"nodo-storage\",\"ae_disco\":\"$DA\",\"ae_gb\":12},\"min\":15,\"caso\":\"C1\"}"; C1v=$(cap); B1=$(en $IPS "lsblk -dn -b -o SIZE /dev/$DA" | awk '{printf "%d", $1/1073741824}')
ok_ && [ "$C1v" = 12 ] && [ "$B1" = 12 ] && anotar C1 "Ampliar a 12 GB encendida" ok "anfitrión $C1v G; la máquina ve $B1 G" || anotar C1 "Ampliar a 12 GB encendida" falla "$(estado) anfitrión=$C1v máquina=$B1"
caso "{\"auto\":\"$N\",\"params\":{\"ae_equipo\":\"nodo-storage\",\"ae_disco\":\"$DA\",\"ae_gb\":8},\"min\":15,\"caso\":\"C2\"}"; C2v=$(cap)
no_ && [ "$C2v" = 12 ] && anotar C2 "Tamaño menor" ok "rechazado; sigue en $C2v G" || anotar C2 "Tamaño menor" falla "$(estado) $C2v"
certificar "$N" "Plan ampliar disco en el anfitrión · C1–C2"
CASOS='[]'; TKS='[]'; N="Sistema · Ampliar disco en la máquina"
caso "{\"auto\":\"$N\",\"params\":{\"ae_equipo\":\"nodo-storage\",\"ae_disco\":\"$DA\",\"ae_montaje\":\"/srv/prueba-ad\"},\"min\":15,\"caso\":\"C1\"}"; S4=$(sz /srv/prueba-ad)
ok_ && [ "${S4:-0}" -ge 11 ] && anotar C1 "Crecer partición y XFS" ok "/srv/prueba-ad $S2 G → $S4 G sin desmontar" || anotar C1 "Crecer partición y XFS" falla "$(estado) $S4 $(jq_ "r.get('detalle')")"
caso "{\"auto\":\"$N\",\"params\":{\"ae_equipo\":\"nodo-storage\",\"ae_disco\":\"$DA\",\"ae_montaje\":\"/srv/prueba-ad\"},\"min\":15,\"caso\":\"C2\"}"; S5=$(sz /srv/prueba-ad)
ok_ && [ "$S5" = "$S4" ] && anotar C2 "Repetir" ok "sin cambios ($S5 G)" || anotar C2 "Repetir" falla "$(estado) $S4→$S5"
caso "{\"auto\":\"$N\",\"params\":{\"ae_equipo\":\"nodo-storage\",\"ae_disco\":\"$DA\",\"ae_montaje\":\"/noexiste\"},\"min\":15,\"caso\":\"C3\"}"
no_ && anotar C3 "Montaje inexistente" ok "falla controlada" || anotar C3 "Montaje inexistente" falla "$(estado)"
certificar "$N" "Plan ampliar disco en la máquina · C1–C3"
echo FIN-DISCOS2
