#!/usr/bin/python3
# Maqueta · registra los servidores "físicos" en el inventario «Servidores habilitados» de AAP (forma físico, marca y BMC)
import base64, glob, json, os, re, ssl, sys, urllib.parse, urllib.request
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CL = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
A = "Basic " + base64.b64encode(f"admin:{CL}".encode()).decode(); CTX = ssl._create_unverified_context()
def api(m, p, d=None):
    r = urllib.request.Request("https://localhost/api/controller/v2/" + p, method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Authorization": A, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, context=CTX, timeout=60) as x:
        b = x.read(); return json.loads(b) if b else {}
ips = json.load(open("/tmp/maqueta-fisicos.json"))
BMC = {"dell": "iDRAC9", "hpe": "iLO 6", "lenovo": "XClarity Controller 2", "cisco": "CIMC", "supermicro": "Supermicro BMC"}
inv = api("GET", "inventories/?name=" + urllib.parse.quote("Servidores habilitados"))["results"][0]["id"]
for nombre, ip in ips.items():
    marca = nombre.split("-")[1]
    vars_ = {"ansible_host": ip, "faceops_forma": "físico", "faceops_categoria": "servidor", "faceops_ubicacion": "Centro de datos Santiago",
             "faceops_ambiente": "produccion", "faceops_bmc": BMC.get(marca, "BMC"), "faceops_maqueta": "servidor físico emulado (VM con SMBIOS de la marca)"}
    h = api("GET", f"hosts/?name={nombre}&inventory={inv}")["results"]
    if h:
        api("PATCH", f"hosts/{h[0]['id']}/", {"variables": json.dumps(vars_)})
    else:
        api("POST", f"inventories/{inv}/hosts/", {"name": nombre, "variables": json.dumps(vars_)})
    print("registrado", nombre, ip)
