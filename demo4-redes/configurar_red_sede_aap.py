#!/usr/bin/python3
# Sede Providencia: da de alta sus equipos de red en AAP (inventario «Red · laboratorio», grupo «red-sede» y el de su
# tipo) con lo que FaceOps necesita para su ficha (categoría, fabricante, modelo, plataforma, zona y rol), y deja en la
# bóveda de FaceOps la credencial de consola de cada uno (usuario netadmin) con «FaceOps · Vincular máquina a la bóveda».
# El firewall VyOS se lee por network_cli (credencial «Red · firewall VyOS»); los equipos abiertos (FRR, puente Linux,
# HAProxy) por SSH con la llave del laboratorio («Red · laboratorio (ssh)»). Idempotente. No imprime claves.
import base64, glob, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request

INV_F = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV_F).read(), re.M).group(1).strip()
C = "https://localhost/api/controller/v2"
CTX = ssl._create_unverified_context()
H = {"Authorization": "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode(), "Content-Type": "application/json"}
VAULT = "http://192.168.122.1:8202/v1"


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


def clave_netadmin():
    tok = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"]
    r = urllib.request.urlopen(urllib.request.Request(f"{VAULT}/secret/data/red/sede", headers={"X-Vault-Token": tok}), timeout=10)
    return json.loads(r.read())["data"]["data"]["netadmin"]


inv = aap("GET", "inventories/?" + urllib.parse.urlencode({"name": "Red · laboratorio"}))["results"][0]["id"]
UB, AMB = "Sede Providencia · Sala de comunicaciones", "produccion"
VYOS = {"ansible_connection": "ansible.netcommon.network_cli", "ansible_network_os": "vyos.vyos.vyos",
        "ansible_user": "{{ fw_usuario }}", "ansible_password": "{{ fw_clave }}"}
ABIERTO = {"ansible_connection": "ssh", "ansible_user": "root", "ansible_python_interpreter": "/usr/bin/python3"}
EQ = [  # nombre, ip de gestión, categoría, zona, rol, fabricante, modelo, plataforma, conexión
    ("rtr-wan-01", "172.20.20.71", "router", "wan", "Router WAN · BGP con el proveedor (AS 64600)",
     "FRRouting (código abierto)", "Router virtual · 2 puertos de datos", "frr", ABIERTO),
    ("fw-sede-01", "172.20.20.72", "firewall", "perimetro", "Firewall de la sede · WAN ↔ red interna",
     "VyOS (código abierto)", "Firewall virtual VyOS", "vyos", VYOS),
    ("sw-core-01", "172.20.20.73", "switch", "core", "Switch core L3 · VLAN 10/20/30/99 y uplink al firewall",
     "Switch abierto (FRRouting + puente Linux)", "Switch L3 virtual · 5 puertos", "frr", ABIERTO),
    ("sw-acc-01", "172.20.20.74", "switch", "acceso", "Switch de acceso piso 1 · troncal al core",
     "Switch abierto (FRRouting + puente Linux)", "Switch de acceso virtual · 3 puertos", "frr", ABIERTO),
    ("lb-sede-01", "172.20.20.75", "balanceador", "servidores", "Balanceador · VIP web 10.20.30.100 → srv-web-01/02",
     "HAProxy (código abierto)", "Balanceador virtual HAProxy", "haproxy", ABIERTO),
]
grupos = {}
for g in ["red-sede", "router", "firewall", "switch", "balanceador"]:
    o = aap("GET", "groups/?" + urllib.parse.urlencode({"name": g, "inventory": inv}))["results"]
    grupos[g] = o[0]["id"] if o else aap("POST", f"inventories/{inv}/groups/", {"name": g, "description": f"Equipos de red · {g}"})["id"]
hosts = {}
for nombre, ip, cat, zona, rol, fab, mod, plat, cx in EQ:
    v = dict(cx, ansible_host=ip, faceops_categoria=cat, faceops_forma="virtual", faceops_ubicacion=UB, faceops_ambiente=AMB,
             faceops_zona=zona, descripcion=rol, faceops_fabricante=fab, faceops_modelo=mod, faceops_plataforma=plat,
             faceops_consola="ssh · netadmin (CLI del equipo)")
    h = aap("GET", "hosts/?" + urllib.parse.urlencode({"name": nombre, "inventory": inv}))["results"]
    if h:
        hid = aap("PATCH", f"hosts/{h[0]['id']}/", {"variables": json.dumps(v, ensure_ascii=False), "enabled": True})["id"]
    else:
        r_ = aap("POST", f"inventories/{inv}/hosts/", {"name": nombre, "variables": json.dumps(v, ensure_ascii=False)}, t=True)
        if not r_:
            print(f"   ! {nombre}: AAP no lo acepta (licencia en su máximo de equipos administrados)")
            continue
        hid = r_["id"]
    hosts[nombre] = hid
    for g in ("red-sede", cat):
        aap("POST", f"groups/{grupos[g]}/hosts/", {"id": hid}, t=True)
    print(f"   · {nombre:11} {ip:13} {cat:12} {plat}")

# Credencial de consola en la bóveda de FaceOps (vía AAP): usuario netadmin
if "--sin-boveda" not in sys.argv:
    jt = (aap("GET", "job_templates/?" + urllib.parse.urlencode({"name": "FaceOps · Vincular máquina a la bóveda"}))["results"] or [None])[0]
    cb = (aap("GET", "credentials/?" + urllib.parse.urlencode({"name": "FaceOps · Bóveda (escritura)"}))["results"] or [None])[0]
    if not jt or not cb:
        print("   ! falta la plantilla o la credencial de la bóveda de FaceOps: no se vinculan")
    else:
        clave = clave_netadmin()
        jobs = {}
        for n in hosts:
            j_ = aap("POST", f"job_templates/{jt['id']}/launch/", {"inventory": inv, "limit": n, "credentials": [cb["id"]],
                                                                   "extra_vars": {"vm_tipo": "red", "vm_usuario": "netadmin", "vm_clave": clave}}, t=True)
            jobs[n] = (j_ or {}).get("id")
        del clave
        for n, jid in jobs.items():
            st = "sin lanzar"
            for _ in range(60):
                if not jid:
                    break
                st = aap("GET", f"jobs/{jid}/")["status"]
                if st in ("successful", "failed", "error", "canceled"):
                    break
                time.sleep(3)
            print(f"   · bóveda {n:11} job {jid}: {st}")
