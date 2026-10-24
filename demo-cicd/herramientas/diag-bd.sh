#!/bin/bash
# Compara la clave de la base guardada en Vault con la del datasource y prueba el login (sin mostrar claves)
VT=$(python3 -c "import json;print(json.load(open('$HOME/demo-drift/vault-init.json'))['root_token'])")
DB=$(curl -sk -H "X-Vault-Token: $VT" https://127.0.0.1:8200/v1/secret/data/apps/app-jboss | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["data"]["db"])')
O="-i $HOME/.ssh/id_demo -o LogLevel=ERROR -o StrictHostKeyChecking=no"
DS=$(ssh $O ansible@192.168.122.15 'sudo /opt/rh/eap8/root/usr/share/wildfly/bin/jboss-cli.sh --connect --command="/subsystem=datasources/data-source=app-jboss:read-attribute(name=password)"' | sed -n 's/.*"result" => "\(.*\)".*/\1/p')
[ "$DB" = "$DS" ] && echo "Vault y datasource: IGUALES" || echo "Vault y datasource: DISTINTAS (largo vault ${#DB}, datasource ${#DS})"
ssh $O ansible@192.168.122.15 "PGPASSWORD='$DB' psql -h 192.168.122.13 -U app_jboss -d pagos -tAc 'select count(*) from pedidos' 2>&1 || (command -v psql >/dev/null || echo 'sin psql en nodo-jboss')" | sed 's/^/login con clave de Vault: /'
ssh $O ansible@192.168.122.13 "sudo -u postgres psql -tAc \"select rolname, rolpassword like 'SCRAM%' from pg_authid where rolname='app_jboss'\"; sudo grep -vE '^#|^$' /var/lib/pgsql/data/pg_hba.conf"
unset VT DB DS
