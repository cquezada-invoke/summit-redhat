#!/bin/bash
# Corre un playbook del data center desde la VM del stand (mismo resultado que la plantilla de AAP).
# Uso: correr.sh playbooks/ad.yml [dc1] [opciones de ansible-playbook...]
# Las claves salen de ~/datacenter/secretos-<dc>.txt y viajan como la variable dc_secretos (igual que la credencial de AAP).
set -euo pipefail
D=$(cd "$(dirname "$0")/.." && pwd)
PB=$1; DC=${2:-dc1}; shift; [ $# -gt 0 ] && shift
L=$HOME/datacenter
export ANSIBLE_COLLECTIONS_PATH=$L/collections ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_STDOUT_CALLBACK=yaml \
       ANSIBLE_ROLES_PATH=$D/roles ANSIBLE_PRIVATE_KEY_FILE=$HOME/.ssh/id_demo ANSIBLE_REMOTE_USER=ansible
if [ ! -f "$L/collections/.listo" ] || [ "$D/collections/requirements.yml" -nt "$L/collections/.listo" ]; then
  ansible-galaxy collection install -r "$D/collections/requirements.yml" -p "$L/collections" >/dev/null && touch "$L/collections/.listo"
fi
python3 -c "import winrm" 2>/dev/null || { rpm -q python3-pip >/dev/null || sudo dnf -y -q install python3-pip; python3 -m pip install -q --user pywinrm; }
# Inventario temporal: el .ini generado + la cuenta de Windows solo para el grupo Windows
INV=$(mktemp -d); trap 'rm -rf "$INV"' EXIT
cp "$L/$DC.ini" "$INV/hosts.ini"; mkdir -p "$INV/group_vars"
python3 - "$L/secretos-$DC.txt" "$INV" "$DC" <<'P'
import json, os, sys
s = dict(l.rstrip("\n").split("=", 1) for l in open(sys.argv[1]) if "=" in l)
w = next((v for k, v in s.items() if k.endswith("_ansible")), "")
f = os.open(f"{sys.argv[2]}/group_vars/{sys.argv[3]}_windows.yml", os.O_WRONLY | os.O_CREAT, 0o600)
os.write(f, json.dumps({"ansible_user": "ansible", "ansible_password": w}).encode())
f = os.open(f"{sys.argv[2]}/secretos.json", os.O_WRONLY | os.O_CREAT, 0o600)
os.write(f, json.dumps({"dc_secretos": json.dumps(s)}).encode())
P
ansible-playbook -i "$INV/hosts.ini" "$D/$PB" -e "@$INV/secretos.json" "$@"
