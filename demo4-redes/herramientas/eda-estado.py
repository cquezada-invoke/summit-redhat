#!/usr/bin/python3
# Estado de la activación EDA de la Demo 4 y últimas líneas de su registro.
#   python3 eda-estado.py            → estado y registro de "Red · Drift por eventos"
#   python3 eda-estado.py RUTA       → GET a /api/eda/v1/RUTA (diagnóstico)
import base64, glob, json, os, re, ssl, sys, urllib.parse, urllib.request
INV = glob.glob(os.path.expanduser("~/aap/*/inventory-demo"))[0]
C = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
H = {"Authorization": "Basic " + base64.b64encode(("admin:" + C).encode()).decode()}
CTX = ssl._create_unverified_context()
g = lambda p: json.load(urllib.request.urlopen(urllib.request.Request("https://localhost/api/eda/v1/" + p, headers=H), context=CTX))
if len(sys.argv) > 1:
    print(json.dumps(g(sys.argv[1]), indent=1, ensure_ascii=False)[:4000]); sys.exit()
a = g("activations/?name=" + urllib.parse.quote("Red · Drift por eventos"))["results"]
if not a:
    sys.exit("No existe la activación")
a = g(f"activations/{a[0]['id']}/")
print("Activación:", a["status"], "|", (a.get("status_message") or "")[:300])
ins = g(f"activations/{a['id']}/instances/?page_size=1&order_by=-id")["results"]
if ins:
    i = ins[0]
    print("Instancia", i["id"], i["status"])
    logs = g(f"activation-instances/{i['id']}/logs/?page_size=200")["results"]
    for l in logs[-25:]:
        print("  ", l.get("log", "")[:220])
