#!/bin/bash
# Red corporativa estándar (containerlab): borde con dos ISP, firewall perimetral, DMZ, firewall interno, core y zonas.
# Idempotente. Uso en la VM:  bash ~/summit-redhat/demo4-redes/instalar-red-corporativa.sh
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd); L=/opt/summit-red/corporativa
export PATH=$PATH:/usr/local/bin
log(){ echo "== $*"; }
VAULT=http://192.168.122.1:8202/v1
TOK=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"])')
C=$(curl -s -H "X-Vault-Token: $TOK" $VAULT/secret/data/red/firewall | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["data"]["clave"])')
read -r T K _ < ~/demo4/llave-lab.pub

log "1. Archivos del laboratorio en $L"
sudo mkdir -p $L/config
sudo cp "$D"/lab/corporativa/red-corporativa.clab.yml $L/; sudo cp "$D"/lab/corporativa/config/* $L/config/
for f in fw-perimetral fw-interno; do
  sudo sed -i -e "s|__FW_CLAVE__|$C|" -e "s|__LLAVE_TIPO__|$T|" -e "s|__LLAVE_CLAVE__|$K|" $L/config/$f.vbash
  sudo chmod 600 $L/config/$f.vbash
done
unset C
sudo chmod 755 $L/config/fwc-arranque.sh
sudo chcon -R -t container_file_t $L/config /opt/summit-red/config 2>/dev/null || true

log "2. Switch de la DMZ (bridge del anfitrión)"
ip link show sw-dmz >/dev/null 2>&1 || sudo ip link add sw-dmz type bridge
sudo ip link set sw-dmz up

log "3. Desplegar"
cd $L && sudo containerlab deploy --runtime podman -t red-corporativa.clab.yml --reconfigure 2>&1 | tail -18
for n in fw-perimetral fw-interno; do
  printf "   esperando $n"
  for i in $(seq 1 100); do sudo podman exec clab-red-corporativa-$n test -f /tmp/fw-listo 2>/dev/null && { echo " listo"; break; }; printf "."; sleep 3; done
done
for i in $(seq 1 30); do
  [ "$(sudo podman exec clab-red-corporativa-borde vtysh -c 'show bgp summary json' 2>/dev/null | grep -c Established)" -ge 2 ] && break; sleep 2
done
log "4. Pruebas de punta a punta"
p(){ printf "   %-58s " "$1"; shift; if out=$("$@" 2>&1); then echo "OK  ${out:0:60}"; else echo "FALLA ${out:0:80}"; fi; }
ex(){ sudo podman exec clab-red-corporativa-$1 "${@:2}"; }
http='import sys,urllib.request;print(urllib.request.urlopen(sys.argv[1],timeout=5).read().decode().strip())'
tcp='import socket,sys;socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=4);print("abierto")'
p "BGP del borde con los dos ISP" sh -c "sudo podman exec clab-red-corporativa-borde vtysh -c 'show bgp summary' | grep -c -E '^100\.64\.[12]\.1 .*[0-9]+ +[0-9]+ *\$' >/dev/null && echo 2 sesiones"
p "Internet (ISP A) → www 198.51.100.10 (publicada)" ex isp-a python3 -c "$http" http://198.51.100.10/
p "Internet (ISP B) → www 198.51.100.10 (publicada)" ex isp-b python3 -c "$http" http://198.51.100.10/
p "Campus → aplicación 10.20.0.10:8080 (permitido)" ex pc-campus-01 python3 -c "$http" http://10.20.0.10:8080/
p "Aplicación → base de datos 10.30.0.10:5432 (permitido)" ex srv-app-01 python3 -c "$tcp" 10.30.0.10 5432
p "Campus → base de datos (debe estar BLOQUEADO)" sh -c "! sudo podman exec clab-red-corporativa-pc-campus-01 python3 -c '$tcp' 10.30.0.10 5432 && echo bloqueado"
p "Campus → Internet 203.0.113.1 (salida con NAT)" ex pc-campus-01 ping -c1 -W2 203.0.113.1

log "5. AAP: equipos en el inventario «Red · laboratorio»"
python3 "$D/configurar_red_corporativa_aap.py"
log "LISTO"
