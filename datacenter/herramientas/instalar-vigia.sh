#!/bin/bash
# Deja el vigía de construcción del DC como servicio permanente: al encender la VM espera el registro en Red Hat
# y construye la identidad (sitio-linux.yml). Si falla, reintenta cada 15 minutos. Cuando termina, no vuelve a correr.
DC=${1:-dc1}
sudo tee /etc/systemd/system/dc-construir@.service >/dev/null <<U
[Unit]
Description=Data center %i · construir identidad cuando los equipos estén registrados
After=network-online.target libvirtd.service
[Service]
User=$USER
Environment=HOME=$HOME PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
WorkingDirectory=$HOME/summit-redhat
ExecStart=/bin/bash -c 'bash datacenter/herramientas/vigia-instalacion.sh %i >> $HOME/datacenter/logs/vigia.log 2>&1'
Restart=on-failure
RestartSec=900
[Install]
WantedBy=multi-user.target
U
sudo systemctl stop dc-vigia 2>/dev/null || true
sudo systemctl daemon-reload
sudo systemctl enable -q --now "dc-construir@$DC"
systemctl is-active "dc-construir@$DC"
