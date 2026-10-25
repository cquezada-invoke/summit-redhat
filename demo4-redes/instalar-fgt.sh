#!/bin/bash
# Demo 4 · FortiGate en el laboratorio (caso 1: regla de firewall pedida por la app). Idempotente.
#   bash instalar-fgt.sh             → imagen, clave en Vault, despliegue del laboratorio, consola web y AAP
#   bash instalar-fgt.sh desplegar   → solo vuelve a desplegar el laboratorio completo (lo usa reiniciar-lab.sh)
# Requiere en el home el archivo de Fortinet: FGT_VM64_KVM-v7.6.8.M-build3744-FORTINET.out.kvm.zip
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd); L=/opt/summit-red; V=7.6.8; IMG=localhost/vr-fortios:$V
export PATH=$PATH:/usr/local/bin
log(){ echo "== $*"; }
VAULT=http://192.168.122.1:8202/v1
TOK=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"])')

clave_fgt(){  # lee (o crea la primera vez) la clave de admin del FortiGate en Vault: secret/red/fortigate
  local c
  c=$(curl -s -H "X-Vault-Token: $TOK" $VAULT/secret/data/red/fortigate | python3 -c 'import sys,json
try: print(json.load(sys.stdin)["data"]["data"]["clave"])
except Exception: pass')
  if [ -z "$c" ]; then
    c="Fgt-$(openssl rand -hex 8)"
    curl -s -o /dev/null -H "X-Vault-Token: $TOK" -X POST $VAULT/secret/data/red/fortigate \
      -d "{\"data\":{\"usuario\":\"admin\",\"clave\":\"$c\"}}"
  fi
  printf '%s' "$c"
}

desplegar(){
  log "Desplegar el laboratorio con el FortiGate (el FortiGate tarda 3 a 6 minutos en arrancar)"
  sudo mkdir -p $L/config
  sudo cp "$D"/lab/config/* $L/config/
  local C P
  C=$(clave_fgt); P=$(cat ~/demo4/llave-lab.pub)
  sed "s|__FGT_CLAVE__|$C|" "$D"/lab/summit-red.clab.yml | sudo tee $L/summit-red.clab.yml >/dev/null
  sudo sed -i "s|__LLAVE_PUB__|$P|" $L/config/fgt.conf
  sudo chmod 600 $L/summit-red.clab.yml; unset C
  sudo chcon -R -t container_file_t $L/config 2>/dev/null || true
  cd $L && sudo containerlab deploy --runtime podman -t summit-red.clab.yml --reconfigure 2>&1 | tail -12
  printf "   esperando al FortiGate"
  for i in $(seq 1 90); do
    ssh -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 \
      -i ~/demo4/llave-lab admin@172.20.20.12 "get system status" 2>/dev/null | grep -q Version && { echo " listo"; break; }
    printf "."; sleep 10
  done
  for i in $(seq 1 30); do
    sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null | grep -q Established && break; sleep 2
  done
  token_api
  bash "$D/herramientas/probar-red.sh"
}

token_api(){  # genera el token REST del usuario "aap" del FortiGate y lo guarda en Vault (no se muestra)
  local T
  T=$(ssh -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 \
      -i ~/demo4/llave-lab admin@172.20.20.12 "execute api-user generate-key aap" 2>/dev/null | sed -n 's/^New API key: *//p' | tr -d '\r ')
  if [ -n "$T" ]; then
    curl -s -H "X-Vault-Token: $TOK" $VAULT/secret/data/red/fortigate | T="$T" VAULT=$VAULT TOK=$TOK python3 -c '
import sys,json,os,urllib.request
d=json.load(sys.stdin)["data"]["data"]; d["api_token"]=os.environ["T"]
r=urllib.request.Request(os.environ["VAULT"]+"/secret/data/red/fortigate",data=json.dumps({"data":d}).encode(),
  headers={"X-Vault-Token":os.environ["TOK"]},method="POST"); urllib.request.urlopen(r)'
    echo "   token de API del FortiGate guardado en Vault"
  else
    echo "   aviso: no se pudo generar el token de API (¿FortiGate sin licencia?)"
  fi
}

consola_web(){
  log "Consola web del FortiGate en el puerto 8025 de la VM (ya permitido en el firewall de GCP)"
  sudo tee /etc/systemd/system/fortigate-web.socket >/dev/null <<'U'
[Unit]
Description=Consola web del FortiGate del laboratorio (Demo 4) en el puerto 8025
[Socket]
ListenStream=0.0.0.0:8025
[Install]
WantedBy=sockets.target
U
  sudo tee /etc/systemd/system/fortigate-web.service >/dev/null <<'U'
[Unit]
Description=Reenvío 8025 → FortiGate 172.20.20.12:443
Requires=fortigate-web.socket
After=fortigate-web.socket
[Service]
ExecStart=/usr/lib/systemd/systemd-socket-proxyd 172.20.20.12:443
U
  sudo systemctl daemon-reload && sudo systemctl enable --now fortigate-web.socket >/dev/null 2>&1
  echo "   https://34.176.42.56:8025  (usuario admin; clave: bash ~/claves-stand.sh)"
}

if [ "${1:-}" = desplegar ]; then desplegar; exit 0; fi

log "1. Imagen del FortiGate ($IMG) con vrnetlab"
if ! sudo podman image exists $IMG; then
  Z=$(ls ~/FGT_VM64_KVM-v$V*.out.kvm.zip | head -1)
  [ -d ~/vrnetlab ] || git clone -q --depth 1 https://github.com/hellt/vrnetlab ~/vrnetlab
  T=$(mktemp -d); unzip -o -q "$Z" -d "$T"
  cp "$T"/fortios.qcow2 ~/vrnetlab/fortinet/fortigate/docker/fortios-v$V.qcow2; rm -rf "$T"
  cp -r ~/vrnetlab/common ~/vrnetlab/fortinet/fortigate/docker/
  (cd ~/vrnetlab/fortinet/fortigate/docker && sudo podman build -q --build-arg IMAGE=fortios-v$V.qcow2 --build-arg VERSION=$V -t $IMG . >/dev/null)
fi
echo "   $(sudo podman image inspect $IMG --format '{{.Id}}' | cut -c1-12)"

log "2. Clave del FortiGate en Vault (secret/red/fortigate)"
clave_fgt >/dev/null; echo "   lista"

desplegar
consola_web

log "3. AAP: host fgt, credencial desde Vault y plantilla 'Red · Solicitar acceso'"
python3 "$D/configurar_fgt_aap.py"
log "LISTO"
