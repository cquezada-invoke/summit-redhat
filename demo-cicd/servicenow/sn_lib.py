# Configuración de ServiceNow para la Demo 3 (grupo, campos de change_task y prueba de ciclo de vida).
# Se ejecuta en la VM (lee la clave desde Vault secret/servicenow). Idempotente.
import json,ssl,urllib.request,base64,urllib.parse,sys
ctx=ssl._create_unverified_context(); _t=json.load(open("/home/cquezada/demo-drift/vault-init.json"))["root_token"]
_d=json.load(urllib.request.urlopen(urllib.request.Request("https://127.0.0.1:8200/v1/secret/data/servicenow",headers={"X-Vault-Token":_t}),context=ctx))["data"]["data"]
U=_d["url"]; AUTH="Basic "+base64.b64encode((_d["usuario"]+":"+_d["clave"]).encode()).decode()
def sn(method,path,body=None):
    req=urllib.request.Request(U+urllib.parse.quote(path,safe="/?=&^,:._-@!*%"),method=method,data=json.dumps(body).encode() if body is not None else None,headers={"Accept":"application/json","Content-Type":"application/json","Authorization":AUTH})
    try:
        r=urllib.request.urlopen(req,timeout=60); b=r.read(); return r.status,(json.loads(b) if b else {})
    except urllib.error.HTTPError as e:
        b=e.read(); 
        try: return e.code,json.loads(b)
        except Exception: return e.code,b[:300]
