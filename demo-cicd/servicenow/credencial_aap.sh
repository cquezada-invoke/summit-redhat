# Credencial de AAP 'ServiceNow · ansible.automatizacion' (datos desde Vault secret/servicenow) y política de lectura.
TOK=$(python3 -c 'import json;print(json.load(open("/home/cquezada/demo-drift/vault-init.json"))["root_token"])')
V=https://127.0.0.1:8200
P=$(curl -sk -H "X-Vault-Token: $TOK" $V/v1/sys/policy/aap-lectura | python3 -c 'import sys,json;print(json.load(sys.stdin)["rules"])')
echo "aap-lectura antes:"; echo "$P"
if ! echo "$P" | grep -q 'secret/data/servicenow'; then
  NP="$P
path \"secret/data/servicenow\" { capabilities = [\"read\"] }"
  python3 -c 'import json,sys;print(json.dumps({"policy":sys.argv[1]}))' "$NP" > /tmp/pol.json
  curl -sk -o /dev/null -w "política: %{http_code}\n" -H "X-Vault-Token: $TOK" -X PUT --data @/tmp/pol.json $V/v1/sys/policy/aap-lectura; rm -f /tmp/pol.json
fi
A=$(sed -n 's/^gateway_admin_password=//p' ~/aap/*/inventory-demo | head -1)
python3 - "$A" <<'PY'
import json,ssl,sys,urllib.request,base64
A=sys.argv[1]; ctx=ssl._create_unverified_context(); B="https://localhost/api/controller/v2/"
H={"Authorization":"Basic "+base64.b64encode(("admin:"+A).encode()).decode(),"Content-Type":"application/json"}
def api(m,p,b=None):
    r=urllib.request.Request(B+p,method=m,headers=H,data=json.dumps(b).encode() if b is not None else None)
    try:
        x=urllib.request.urlopen(r,context=ctx); t=x.read(); return x.status,(json.loads(t) if t else {})
    except urllib.error.HTTPError as e: return e.code,e.read()[:400]
q=lambda p:api("GET",p)[1]["results"]
ct=q("credential_types/?name=ServiceNow%20%C2%B7%20instancia")
if not ct:
    s,j=api("POST","credential_types/",{"name":"ServiceNow · instancia","kind":"cloud","description":"Instancia, usuario y clave de ServiceNow para registrar las tareas automatizadas",
      "inputs":{"fields":[{"id":"instancia","type":"string","label":"URL de la instancia"},{"id":"usuario","type":"string","label":"Usuario"},{"id":"clave","type":"string","label":"Clave","secret":True}],"required":["instancia","usuario","clave"]},
      "injectors":{"extra_vars":{"sn_instancia":"{{ instancia }}","sn_usuario":"{{ usuario }}","sn_clave":"{{ clave }}"}}})
    print("tipo",s); ctid=j["id"]
else: ctid=ct[0]["id"]
c=q("credentials/?name=ServiceNow%20%C2%B7%20ansible.automatizacion")
if not c:
    org=api("GET","job_templates/16/")[1]["organization"]
    s,j=api("POST","credentials/",{"name":"ServiceNow · ansible.automatizacion","credential_type":ctid,"organization":org,"description":"Usuario técnico de ServiceNow; datos leídos desde Vault (secret/servicenow)","inputs":{}})
    print("cred",s,j if s!=201 else j["id"]); cid=j["id"]
    for f,k in [("instancia","url"),("usuario","usuario"),("clave","clave")]:
        s,j=api("POST","credential_input_sources/",{"target_credential":cid,"source_credential":6,"input_field_name":f,"metadata":{"secret_backend":"secret","secret_path":"servicenow","secret_key":k}})
        print(" fuente",f,s)
else: cid=c[0]["id"]
s,j=api("POST","job_templates/16/credentials/",{"id":cid}); print("asociar a JT16",s)
s,j=api("POST",f"credentials/{cid}/test/",{}); print("test cred",s,j)
print("cred id",cid,"tipo",ctid)
PY
