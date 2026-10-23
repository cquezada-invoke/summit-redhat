#!/usr/bin/env bash
# Puerto 2222 del host -> SSH de nodo-so, parche del panel y estándar.
set -euo pipefail
cd ~/demo-drift
sudo tee /etc/systemd/system/ssh-visitante.socket >/dev/null <<'U'
[Unit]
Description=SSH de visitantes (puerto 2222 hacia nodo-so)
[Socket]
ListenStream=0.0.0.0:2222
[Install]
WantedBy=sockets.target
U
sudo tee /etc/systemd/system/ssh-visitante.service >/dev/null <<'U'
[Unit]
Description=Proxy SSH de visitantes hacia nodo-so
[Service]
ExecStart=/usr/lib/systemd/systemd-socket-proxyd 192.168.122.12:22
U
sudo systemctl daemon-reload
sudo systemctl enable --now ssh-visitante.socket
sudo firewall-cmd -q --permanent --add-port=2222/tcp && sudo firewall-cmd -q --reload
python3 parche_visitante.py
python3 -m py_compile panel/panel.py
ansible-playbook -i inventario.ini visitante.yml -l nodo-so -e '{"usuario":"visita00","estado":"absent"}' | tail -3
ansible-playbook -i inventario.ini so-base.yml -l nodo-so | tail -3
sudo systemctl restart panel-cumplimiento
sleep 2; curl -s -o /dev/null -w 'visitante:%{http_code}\n' localhost:8088/visitante
