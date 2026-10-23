#!/usr/bin/env bash
# HashiCorp Vault para la demo: contenedor con almacenamiento en disco, TLS para la consola (8200)
# y puerto interno sin TLS para AAP (192.168.122.1:8202). Se abre solo al arrancar la máquina.
set -euo pipefail
V=~/demo-vault
INIT=~/demo-drift/vault-init.json
mkdir -p $V/config $V/tls
podman unshare chown -R 0:0 $V/config $V/tls 2>/dev/null || true
cat > $V/abrir.sh <<'S'
#!/bin/bash
# Abre (unseal) Vault al arrancar: demo con una sola llave guardada en la máquina.
for i in $(seq 1 30); do curl -s -o /dev/null http://192.168.122.1:8202/v1/sys/health && break; sleep 2; done
K=$(python3 -c "import json;print(json.load(open('/home/cquezada/demo-drift/vault-init.json'))['keys_base64'][0])")
curl -s -o /dev/null -X PUT http://192.168.122.1:8202/v1/sys/unseal -d "{\"key\":\"$K\"}"
S
chmod 700 $V/abrir.sh
podman unshare mkdir -p $V/file
cp ~/demo-drift/panel/panel.crt $V/tls/vault.crt
cp ~/demo-drift/panel/panel.key $V/tls/vault.key
chmod 644 $V/tls/*
cat > $V/config/vault.hcl <<'H'
storage "file" { path = "/vault/file" }
listener "tcp" {
  address       = "0.0.0.0:8200"
  tls_cert_file = "/vault/tls/vault.crt"
  tls_key_file  = "/vault/tls/vault.key"
}
listener "tcp" {
  address     = "0.0.0.0:8202"
  tls_disable = true
}
api_addr      = "https://34-176-42-56.sslip.io:8200"
ui            = true
disable_mlock = true
H
mkdir -p ~/.config/containers/systemd
cat > ~/.config/containers/systemd/vault.container <<U
[Unit]
Description=HashiCorp Vault de la demo
[Container]
Image=docker.io/hashicorp/vault:1.17.6
ContainerName=vault
Exec=server
PublishPort=8200:8200
PublishPort=192.168.122.1:8202:8202
Volume=$V/config:/vault/config:Z
Volume=$V/tls:/vault/tls:Z
Volume=$V/file:/vault/file:Z
AddCapability=IPC_LOCK
[Service]
Restart=always
ExecStartPost=/bin/bash /home/cquezada/demo-vault/abrir.sh
[Install]
WantedBy=default.target
U
sudo firewall-cmd -q --permanent --add-port=8200/tcp && sudo firewall-cmd -q --reload
systemctl --user daemon-reload
systemctl --user restart vault
for i in $(seq 1 30); do curl -s -o /dev/null http://192.168.122.1:8202/v1/sys/health && break; sleep 2; done
A=http://192.168.122.1:8202/v1
if [ ! -f "$INIT" ]; then
  curl -s -X PUT $A/sys/init -d '{"secret_shares":1,"secret_threshold":1}' > "$INIT"
  chmod 600 "$INIT"
  K=$(python3 -c "import json;print(json.load(open('$INIT'))['keys_base64'][0])")
  curl -s -o /dev/null -X PUT $A/sys/unseal -d "{\"key\":\"$K\"}"
fi
echo "· Vault iniciado y abierto: $(curl -s $A/sys/seal-status | python3 -c 'import json,sys;print("sellado" if json.load(sys.stdin)["sealed"] else "abierto")')"
python3 ~/demo-cicd/configurar_vault.py
