#!/bin/bash
# TLS en nodo-postgres con el certificado firmado por la CA interna (ssl=on; los clientes siguen conectando:
# el JDBC de los JBoss negocia TLS solo si el servidor lo ofrece). Idempotente.
set -euo pipefail
P=~/pki; D=$P/emitidos/nodo-postgres
[ -f $D/servidor.crt ] || bash "$(dirname "$0")/emitir.sh" nodo-postgres 192.168.122.13
scp -q $D/servidor.crt $D/servidor.key $D/ca.crt nodo-postgres:/tmp/
ssh nodo-postgres 'set -e
  DATA=$(sudo -u postgres bash -c "cd /tmp && psql -Atc \"show data_directory\"")
  sudo install -o postgres -g postgres -m 0644 /tmp/servidor.crt $DATA/server.crt
  sudo install -o postgres -g postgres -m 0600 /tmp/servidor.key $DATA/server.key
  sudo install -o root -g root -m 0644 /tmp/ca.crt /etc/pki/ca-trust/source/anchors/invoke-ca-interna.crt && sudo update-ca-trust
  rm -f /tmp/servidor.crt /tmp/servidor.key /tmp/ca.crt
  sudo -u postgres bash -c "cd /tmp && psql -qAtc \"ALTER SYSTEM SET ssl = on\" && psql -qAtc \"select pg_reload_conf()\" >/dev/null && psql -Atc \"show ssl\""'
