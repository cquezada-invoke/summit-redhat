#!/bin/bash
# Sede Providencia (containerlab): router WAN, firewall VyOS, switch core, switch de acceso y balanceador, gestionados
# por AAP y visibles en FaceOps (Redes). Idempotente. Uso en la VM:  bash ~/summit-redhat/demo4-redes/instalar-red-sede.sh
# Claves: la del firewall sale de Vault (secret/red/firewall); la de netadmin se genera una vez en secret/red/sede.
# Ninguna se imprime.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd); L=/opt/summit-red/sede
export PATH=$PATH:/usr/local/bin
log(){ echo "== $*"; }
VAULT=http://192.168.122.1:8202/v1
TOK=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"])')
vget(){ curl -s -H "X-Vault-Token: $TOK" $VAULT/secret/data/$1 | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['data']['$2'])" 2>/dev/null || true; }

log "1. Claves en Vault"
NET=$(vget red/sede netadmin)
if [ -z "$NET" ]; then
  NET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(15).replace("-","x").replace("_","y"))')
  python3 - "$NET" <<'PY'
import json, os, sys, urllib.request
tok = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"]
urllib.request.urlopen(urllib.request.Request("http://192.168.122.1:8202/v1/secret/data/red/sede", method="POST",
    data=json.dumps({"data": {"netadmin": sys.argv[1], "usuario": "netadmin"}}).encode(), headers={"X-Vault-Token": tok}), timeout=10)
PY
  echo "   clave de netadmin generada en secret/red/sede"
else
  echo "   clave de netadmin vigente en secret/red/sede"
fi
FW=$(vget red/firewall clave)
[ -n "$FW" ] || { echo "Falta secret/red/firewall en Vault (instalar-fw.sh)"; exit 1; }
read -r T K _ < ~/demo4/llave-lab.pub

log "2. Archivos del laboratorio en $L"
sudo mkdir -p $L/config $L/frr-red
sudo cp "$D"/lab/sede/red-sede.clab.yml $L/; sudo cp "$D"/lab/sede/config/* $L/config/
sudo cp "$D"/lab/frr-red/* $L/frr-red/
sudo test -f /opt/summit-red/config/daemons || sudo cp "$D"/lab/config/daemons /opt/summit-red/config/daemons
sudo sed -i -e "s|__FW_CLAVE__|$FW|" -e "s|__NET_CLAVE__|$NET|" -e "s|__LLAVE_TIPO__|$T|" -e "s|__LLAVE_CLAVE__|$K|" $L/config/fw-sede-01.vbash
printf '%s' "$NET" | sudo tee $L/config/clave-netadmin >/dev/null
sudo chmod 600 $L/config/fw-sede-01.vbash $L/config/clave-netadmin
sudo chmod 755 $L/config/fws-arranque.sh
unset FW
sudo chcon -R -t container_file_t $L/config /opt/summit-red/config 2>/dev/null || true

log "3. Imagen de los equipos abiertos (FRR + puente Linux + LLDP + HAProxy)"
sudo podman build -q -t localhost/frr-red:1 $L/frr-red >/dev/null && echo "   localhost/frr-red:1 lista"

log "4. Desplegar"
cd $L && sudo containerlab deploy --runtime podman -t red-sede.clab.yml --reconfigure 2>&1 | tail -16
printf "   esperando fw-sede-01"
for i in $(seq 1 100); do sudo podman exec clab-red-sede-fw-sede-01 test -f /tmp/fw-listo 2>/dev/null && { echo " listo"; break; }; printf "."; sleep 3; done
for i in $(seq 1 30); do sudo podman exec clab-red-sede-rtr-wan-01 vtysh -c 'show bgp summary json' 2>/dev/null | grep -q Established && break; sleep 2; done
sleep 35   # LLDP: el primer anuncio sale a los ~30 s

log "5. Pruebas"
p(){ printf "   %-52s " "$1"; shift; if out=$("$@" 2>&1); then echo "OK  ${out:0:70}"; else echo "FALLA ${out:0:90}"; fi; }
ex(){ sudo podman exec clab-red-sede-$1 "${@:2}"; }
p "BGP rtr-wan-01 ↔ proveedor" sh -c "sudo podman exec clab-red-sede-rtr-wan-01 vtysh -c 'show bgp summary json' | grep -c Established"
p "Usuario piso 1 (VLAN 10) → VIP web 10.20.30.100" ex pc-piso1 wget -qO- -T 4 http://10.20.30.100/
p "Teléfono piso 1 (VLAN 20) → servidores" ex pc-piso2 ping -c1 -W2 10.20.30.21
p "Sede → WAN 203.0.113.1 (pasa por el firewall)" ex pc-piso1 ping -c1 -W2 203.0.113.1
p "WAN → VIP web publicado (permitido)" ex pe-isp wget -qO- -T 4 http://10.20.30.100/
p "WAN → servidor directo :8080 (debe estar BLOQUEADO)" sh -c "! sudo podman exec clab-red-sede-pe-isp wget -qO- -T 3 http://10.20.30.21:8080/ && echo bloqueado"
p "Vecinos LLDP de sw-core-01" sh -c "sudo podman exec clab-red-sede-sw-core-01 lldpcli show neighbors summary | grep -c SysName"
p "Backends del balanceador arriba" sh -c "sudo podman exec clab-red-sede-lb-sede-01 wget -qO- 'http://127.0.0.1:8404/stats;csv' | grep -c ',UP,'"
cat > /tmp/askpass-sede.sh <<'EOF'
#!/bin/sh
sudo cat /opt/summit-red/sede/config/clave-netadmin
EOF
chmod 700 /tmp/askpass-sede.sh
for ip in 172.20.20.73 172.20.20.72; do
  p "SSH netadmin@$ip (clave de la bóveda)" sh -c "SSH_ASKPASS=/tmp/askpass-sede.sh SSH_ASKPASS_REQUIRE=force setsid ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PubkeyAuthentication=no -o ConnectTimeout=6 netadmin@$ip 'show version' < /dev/null | grep -m1 -iE 'FRRouting|Version'"
done
rm -f /tmp/askpass-sede.sh
unset NET

log "6. AAP: equipos en «Red · laboratorio» (grupo red-sede) y credencial de consola en la bóveda de FaceOps"
python3 "$D/configurar_red_sede_aap.py"
log "LISTO"
