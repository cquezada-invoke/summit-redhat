#!/usr/bin/python3
# Al encender la VM, las activaciones de Event-Driven Ansible pueden quedar en "error" porque arrancan antes que
# sus dependencias. Espera a que AAP responda y reinicia las que no estén "running". Lo usa summit-lab.service.
import json, ssl, base64, glob, re, os, time, urllib.request, urllib.error
CL = re.search(r"^gateway_admin_password=(.*)$", open(glob.glob(os.path.expanduser("~/aap/*/inventory-demo"))[0]).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context(); AU = "Basic " + base64.b64encode(f"admin:{CL}".encode()).decode()
def eda(m, p, d=None):
    r = urllib.request.Request("https://localhost/api/eda/v1/" + p, method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Authorization": AU, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, context=CTX, timeout=30) as x:
        b = x.read(); return json.loads(b) if b else {}
for _ in range(60):
    try:
        acts = eda("GET", "activations/?page_size=50")["results"]; break
    except Exception:
        time.sleep(10)
else:
    raise SystemExit("EDA no responde")
time.sleep(60)  # deja que intenten arrancar solas
for a in eda("GET", "activations/?page_size=50")["results"]:
    if a["status"] != "running":
        try:
            eda("POST", f"activations/{a['id']}/restart/", {}); print("reiniciada:", a["name"])
        except urllib.error.HTTPError as e:
            print("no se pudo reiniciar:", a["name"], e.code)
