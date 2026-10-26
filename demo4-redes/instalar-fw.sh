#!/bin/bash
# Demo 4 · Firewall VyOS (open source) en el laboratorio, para el caso 1 (regla de firewall pedida por la app). Idempotente.
#   bash instalar-fw.sh            → imagen de VyOS, clave en Vault, despliegue del laboratorio y AAP
#   bash instalar-fw.sh desplegar  → solo vuelve a desplegar el laboratorio completo (lo usa reiniciar-lab.sh)
# Se usa VyOS porque la licencia de evaluación del FortiGate no se pudo activar (FortiCare exige MFA).
# El flujo de la demo es el mismo; con FortiGate se usaría la colección certificada fortinet.fortios (instalar-fgt.sh).
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd); L=/opt/summit-red
VER=2026.10.23-0026-rolling; IMG=localhost/vyos:2026.10.23
ISO=https://github.com/vyos/vyos-nightly-build/releases/download/$VER/vyos-$VER-generic-amd64.iso
export PATH=$PATH:/usr/local/bin
log(){ echo "== $*"; }
VAULT=http://192.168.122.1:8202/v1
TOK=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"])')

clave_fw(){  # lee (o crea la primera vez) la clave del usuario vyos en Vault: secret/red/firewall
  local c
  c=$(curl -s -H "X-Vault-Token: $TOK" $VAULT/secret/data/red/firewall | python3 -c 'import sys,json
try: print(json.load(sys.stdin)["data"]["data"]["clave"])
except Exception: pass')
  if [ -z "$c" ]; then
    c="Fw-$(openssl rand -hex 8)"
    curl -s -o /dev/null -H "X-Vault-Token: $TOK" -X POST $VAULT/secret/data/red/firewall \
      -d "{\"data\":{\"usuario\":\"vyos\",\"clave\":\"$c\",\"equipo\":\"VyOS $VER\"}}"
  fi
  printf '%s' "$c"
}

imagen(){
  log "Imagen de VyOS ($IMG) desde la ISO pública"
  if ! sudo podman image exists $IMG; then
    W=/opt/vyos-build; sudo mkdir -p $W/iso; cd $W
    [ -f vyos.iso ] || sudo curl -sSL -o vyos.iso "$ISO"
    mountpoint -q iso || sudo mount -o loop,ro vyos.iso iso
    sudo rm -rf rootfs && sudo mkdir rootfs
    sudo unsquashfs -q -f -d rootfs iso/live/filesystem.squashfs >/dev/null
    sudo umount iso
    sudo tar -C rootfs -c . | sudo podman import --change 'CMD ["/sbin/init"]' - $IMG >/dev/null
    sudo rm -rf rootfs; cd - >/dev/null
  fi
  echo "   $(sudo podman image inspect $IMG --format '{{.Id}}' | cut -c1-12)"
}

desplegar(){
  log "Desplegar el laboratorio con el firewall VyOS"
  sudo mkdir -p $L/config
  sudo cp "$D"/lab/config/* $L/config/
  local C T K
  C=$(clave_fw); read -r T K _ < ~/demo4/llave-lab.pub
  sudo sed -i -e "s|__FW_CLAVE__|$C|" -e "s|__LLAVE_TIPO__|$T|" -e "s|__LLAVE_CLAVE__|$K|" $L/config/fw-base.vbash
  sudo chmod 600 $L/config/fw-base.vbash; sudo chmod 755 $L/config/fw-arranque.sh; unset C
  sudo cp "$D"/lab/summit-red.clab.yml $L/summit-red.clab.yml
  sudo chcon -R -t container_file_t $L/config 2>/dev/null || true
  # El FortiGate (si quedó de un despliegue anterior) se detiene; su disco se conserva en $L/fgt-disco
  sudo podman rm -f clab-summit-red-fgt >/dev/null 2>&1 || true
  sudo systemctl disable --now fortigate-web.socket fortigate-web.service >/dev/null 2>&1 || true
  cd $L && sudo containerlab deploy --runtime podman -t summit-red.clab.yml --reconfigure 2>&1 | tail -12
  printf "   esperando al firewall"
  for i in $(seq 1 90); do
    sudo podman exec clab-summit-red-fw test -f /tmp/fw-listo 2>/dev/null && { echo " listo"; break; }
    printf "."; sleep 3
  done
  for i in $(seq 1 30); do
    sudo podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json' 2>/dev/null | grep -q Established && break; sleep 2
  done
  bash "$D/herramientas/probar-red.sh"
}

case "${1:-}" in
  desplegar) desplegar; exit 0 ;;
esac

imagen
log "Clave del firewall en Vault (secret/red/firewall)"
clave_fw >/dev/null; echo "   lista"
desplegar
log "AAP: host fw, credencial desde Vault y plantilla 'Red · Solicitar acceso'"
python3 "$D/configurar_fw_aap.py"
log "LISTO"
