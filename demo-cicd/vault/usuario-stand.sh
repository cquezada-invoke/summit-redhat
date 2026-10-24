# Crea el usuario "stand" de Vault (solo lectura) para mostrar secretos en el Summit.
# La clave queda en ~/demo-drift/cicd-secretos.txt (vault_stand_clave).
set -e
V=https://127.0.0.1:8200; T=$(python3 -c 'import json;print(json.load(open("/home/cquezada/demo-drift/vault-init.json"))["root_token"])')
c(){ curl -sk -H "X-Vault-Token: $T" "$@"; }
c -X POST $V/v1/sys/auth/userpass -d '{"type":"userpass"}' >/dev/null || true
c -X POST $V/v1/sys/auth/userpass/tune -d '{"listing_visibility":"unauth","description":"Usuarios del stand"}'
c -X PUT $V/v1/sys/policies/acl/stand-lectura -d '{"policy":"path \"secret/data/*\" {capabilities=[\"read\"]}\npath \"secret/metadata/*\" {capabilities=[\"read\",\"list\"]}\npath \"secret/metadata\" {capabilities=[\"list\"]}"}'
P=$(grep -s '^vault_stand_clave=' ~/demo-drift/cicd-secretos.txt | cut -d= -f2-); [ -n "$P" ] || { P=summit-$(openssl rand -hex 3); echo "vault_stand_clave=$P" >> ~/demo-drift/cicd-secretos.txt; }
c -X POST $V/v1/auth/userpass/users/stand -d "{\"password\":\"$P\",\"token_policies\":\"stand-lectura\",\"token_ttl\":\"8h\"}"
R=$(curl -sk -X POST $V/v1/auth/userpass/login/stand -d "{\"password\":\"$P\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["auth"]["client_token"])')
curl -sk -H "X-Vault-Token: $R" "$V/v1/secret/metadata/apps?list=true" | python3 -c 'import sys,json;print("lista apps:",json.load(sys.stdin)["data"]["keys"])'
curl -sk -o /dev/null -w "escritura (debe ser 403): %{http_code}\n" -H "X-Vault-Token: $R" -X POST $V/v1/secret/data/prueba -d '{"data":{"a":"b"}}'
