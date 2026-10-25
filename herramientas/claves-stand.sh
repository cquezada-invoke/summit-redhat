#!/bin/bash
# Muestra en pantalla las claves de las consolas del stand (solo para leerlas en la VM).
# Uso en la VM: cp ~/summit-redhat/herramientas/claves-stand.sh ~/ && bash ~/claves-stand.sh
S=~/demo-drift/cicd-secretos.txt
sed -n 's/^gateway_admin_password=/aap_admin=/p' ~/aap/*/inventory-demo
sed -nE 's/^clave[=:] ?/aap_desarrollador=/p' ~/demo-drift/desarrollador.txt
grep -E '^(auditor_clave|gitea_clave|gitea_cquezada_clave|gitea_jperez_clave|vault_stand_clave)=' $S
T=$(python3 -c 'import json;print(json.load(open("/home/cquezada/demo-drift/vault-init.json"))["root_token"])')
curl -sk -H "X-Vault-Token: $T" https://127.0.0.1:8200/v1/secret/data/plataforma | python3 -c 'import sys,json;d=json.load(sys.stdin)["data"]["data"];[print(k+"="+d[k]) for k in sorted(d)]'
curl -sk -H "X-Vault-Token: $T" https://127.0.0.1:8200/v1/secret/data/red/fortigate | python3 -c 'import sys,json
try: print("fortigate_admin="+json.load(sys.stdin)["data"]["data"]["clave"])
except Exception: pass'
echo "vault_root_token=$T"
