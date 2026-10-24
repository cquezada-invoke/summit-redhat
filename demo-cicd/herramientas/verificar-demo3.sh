#!/bin/bash
# Verifica la Demo 3 sin mostrar claves
VT=$(python3 -c "import json;print(json.load(open('$HOME/demo-drift/vault-init.json'))['root_token'])")
V=https://127.0.0.1:8200/v1
vlt(){ curl -sk -H "X-Vault-Token: $VT" "$V/$1"; }
adm(){ vlt secret/data/plataforma | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['data']['$1'])"; }
SSH="ssh -i $HOME/.ssh/id_demo -o StrictHostKeyChecking=no -o LogLevel=ERROR ansible@192.168.122.14"

echo "== App"
curl -s http://localhost:8090/app-jboss/ | grep -o 'Base de datos[^<]*' || echo "sin texto de base de datos"

echo "== Keycloak"
KP=$(adm keycloak_admin)
KT=$(curl -s -d grant_type=password -d client_id=admin-cli -d username=admin --data-urlencode "password=$KP" \
  http://192.168.122.14:8080/auth/realms/master/protocol/openid-connect/token | python3 -c 'import sys,json;print(json.load(sys.stdin).get("access_token",""))')
K=http://192.168.122.14:8080/auth/admin/realms/invoke
echo -n "realm: "; curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KT" $K
echo -n "cliente: "; curl -s -H "Authorization: Bearer $KT" "$K/clients?clientId=app-jboss" | python3 -c 'import sys,json;d=json.load(sys.stdin);print([c["clientId"] for c in d])'
echo -n "roles: "; curl -s -H "Authorization: Bearer $KT" "$K/roles" | python3 -c 'import sys,json;print(sorted(r["name"] for r in json.load(sys.stdin)))'

echo "== Artemis (colas pagos.*)"
AP=$(adm artemis_admin)
$SSH "sudo podman exec artemis /var/lib/artemis-instance/bin/artemis queue stat --user admin --password '$AP' --url tcp://localhost:61616 --maxColumnSize 200 2>/dev/null" | grep -o 'pagos\.[a-z]*' | sort -u

echo "== Valkey (usuarios ACL)"
VP=$(adm valkey_admin)
$SSH "sudo podman exec valkey valkey-cli -a '$VP' --no-auth-warning ACL USERS"

echo "== Vault secret/apps/app-jboss (solo nombres)"
vlt secret/data/apps/app-jboss | python3 -c 'import sys,json;print(sorted(json.load(sys.stdin)["data"]["data"].keys()))'
unset VT KP KT AP VP
