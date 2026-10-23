#!/usr/bin/env bash
# Instala panel de cumplimiento, Mailpit y prepara los nodos. Ejecutar en la VM como cquezada.
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
for u in jperez mgonzalez; do [ -f ~/.ssh/id_$u ] || ssh-keygen -q -t ed25519 -N '' -C "$u" -f ~/.ssh/id_$u; done

echo "== Bandeja de correo (Mailpit)"
podman container exists mailpit || podman run -d --name mailpit --restart always -p 1025:1025 -p 8025:8025 docker.io/axllent/mailpit:v1.21 >/dev/null
podman start mailpit >/dev/null

echo "== Panel de cumplimiento"
sudo tee /etc/systemd/system/panel-cumplimiento.service >/dev/null <<UNIT
[Unit]
Description=Panel de cumplimiento de configuración
After=network-online.target

[Service]
User=$USER
Environment=HOME=$HOME
WorkingDirectory=$DIR/panel
ExecStart=/usr/bin/python3 $DIR/panel/panel.py
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now panel-cumplimiento >/dev/null 2>&1
sudo systemctl restart panel-cumplimiento
if systemctl is-active -q firewalld; then
  sudo firewall-cmd -q --add-port=8088/tcp --add-port=8025/tcp --permanent
  sudo firewall-cmd -q --zone=libvirt --add-port=8088/tcp --permanent
  sudo firewall-cmd -q --reload
fi

echo "== Nodos: personas, auditoría y vigía"
ansible-playbook -i inventario.ini preparar-nodos.yml
echo "== Estado inicial según el estándar"
ansible-playbook -i inventario.ini so-base.yml
echo
echo "Panel:  http://$(curl -s -m 5 -H Metadata-Flavor:Google http://metadata/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip):8088"
echo "Correo: mismo host, puerto 8025"
