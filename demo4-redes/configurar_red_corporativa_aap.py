#!/usr/bin/python3
# Red corporativa: da de alta sus equipos en AAP (inventario «Red · laboratorio», grupo «red-corporativa») con la categoría,
# la zona y el rol que FaceOps usa para su inventario y su diagrama. Los VyOS van en «firewall» (network_cli, credencial
# del firewall); los FRR y los servidores, por SSH con la llave del laboratorio. Idempotente. No imprime claves.
import base64, glob, json, os, re, ssl, sys, urllib.error, urllib.parse, urllib.request

INV_F = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV_F).read(), re.M).group(1).strip()
C = "https://localhost/api/controller/v2"
CTX = ssl._create_unverified_context()
H = {"Authorization": "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode(), "Content-Type": "application/json"}


def aap(m, p, d=None, t=False):
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{C}/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                                                           headers=H), context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if t:
            return None
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


inv = aap("GET", "inventories/?" + urllib.parse.urlencode({"name": "Red · laboratorio"}))["results"][0]["id"]
UB, AMB = "Red corporativa · Centro de datos Santiago", "produccion"
VYOS = "ansible_connection: ansible.netcommon.network_cli\nansible_network_os: vyos.vyos.vyos\nansible_user: '{{ fw_usuario }}'\nansible_password: '{{ fw_clave }}'\n"
EQ = [  # nombre, ip de gestión, categoría, zona, rol, grupos, extra (en orden de prioridad, por si la licencia de AAP no alcanza)
    ("fw-perimetral", "172.20.20.53", "firewall", "perimetro", "Firewall perimetral · Internet, DMZ y hacia adentro", ["firewall"], VYOS),
    ("fw-interno", "172.20.20.57", "firewall", "interno", "Firewall interno · segmentación entre zonas", ["firewall"], VYOS),
    ("borde", "172.20.20.52", "router", "borde", "Router de borde · BGP con ISP A e ISP B", ["router"], ""),
    ("core", "172.20.20.58", "switch", "core", "Core L3 · una interfaz (VLAN) por zona", ["switch"], ""),
    ("lb-dmz", "172.20.20.54", "balanceador", "dmz", "Balanceador de la DMZ (www 198.51.100.10)", ["balanceador"], ""),
    ("srv-app-01", "172.20.20.59", "servidor", "aplicaciones", "Aplicación (10.20.0.10:8080)", ["servidores-corp"], "faceops_skills: [app]\n"),
    ("srv-bd-01", "172.20.20.60", "servidor", "datos", "Base de datos (10.30.0.10:5432)", ["servidores-corp"], "faceops_skills: [postgres]\n"),
    ("dns-dmz", "172.20.20.56", "servidor", "dmz", "DNS de la zona corp.invoke.lab", ["dns"], "faceops_skills: [dns]\n"),
    ("proxy-dmz", "172.20.20.55", "servidor", "dmz", "Proxy inverso", ["proxy"], "faceops_skills: [proxy]\n"),
    ("nas-01", "172.20.20.62", "almacenamiento", "storage", "Storage NAS (10.50.0.10:2049)", ["storage"], ""),
    ("pc-campus-01", "172.20.20.61", "estación de trabajo", "campus", "Usuario del campus (10.40.0.10)", ["campus"], ""),
]
grupos = {}
for g in ["red-corporativa"] + sorted({x for e in EQ for x in e[5]}):
    o = aap("GET", "groups/?" + urllib.parse.urlencode({"name": g, "inventory": inv}))["results"]
    grupos[g] = o[0]["id"] if o else aap("POST", f"inventories/{inv}/groups/", {"name": g, "description": f"Red corporativa · {g}"})["id"]
for nombre, ip, cat, zona, rol, gs, extra in EQ:
    v = (f"ansible_host: {ip}\nfaceops_categoria: {cat}\nfaceops_forma: virtual\nfaceops_ubicacion: {UB}\nfaceops_ambiente: {AMB}\n"
         f"faceops_zona: {zona}\ndescripcion: {rol}\n" + extra)
    h = aap("GET", "hosts/?" + urllib.parse.urlencode({"name": nombre, "inventory": inv}))["results"]
    if h:
        hid = aap("PATCH", f"hosts/{h[0]['id']}/", {"variables": v, "enabled": True})["id"]
    else:
        r_ = aap("POST", f"inventories/{inv}/hosts/", {"name": nombre, "variables": v}, t=True)
        if not r_:
            print(f"   ! {nombre}: AAP no lo acepta (la licencia llegó a su máximo de equipos administrados)")
            continue
        hid = r_["id"]
    for g in ["red-corporativa"] + gs:
        aap("POST", f"groups/{grupos[g]}/hosts/", {"id": hid}, t=True)
    print(f"   · {nombre:14} {ip:14} {cat:20} zona {zona}")
print("LISTO")
