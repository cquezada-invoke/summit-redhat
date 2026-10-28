#!/usr/bin/python3
# AAP 2.7 · login con las cuentas del Active Directory del data center (autenticador LDAP del gateway).
#  · Entran las personas del grupo Empleados (las que midPoint aprovisiona desde RRHH).
#  · El grupo TI queda como administrador de AAP.
# Idempotente. Uso: python3 configurar_ldap_aap.py [dc1] [--probar usuario]   (la clave de prueba se lee de stdin)
import base64, glob, json, os, re, ssl, sys, urllib.error, urllib.parse, urllib.request

import yaml

AQUI = os.path.dirname(os.path.abspath(__file__))
DC = next((a for a in sys.argv[1:] if not a.startswith("--") and a.startswith("dc")), "dc1")
D = yaml.safe_load(open(f"{AQUI}/inventario/{DC}.yml"))
SEC = dict(l.rstrip("\n").split("=", 1) for l in open(os.path.expanduser(f"~/datacenter/secretos-{DC}.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
G = "https://localhost/api/gateway/v1"
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()

ad = next(e for e in D["equipos"] if "active-directory" in e["roles"])
IP_AD = f"{D['dc']['red_base']}.{D['zonas'][ad['zona']]['octeto']}.{ad['ip']}"
BASE = ",".join("DC=" + p for p in D["dc"]["dominio_ad"].split("."))
NOMBRE = f"Active Directory · {D['dc']['dominio_ad']}"


def gw(m, p, d=None, auth=BASIC, tolerar=False):
    req = urllib.request.Request(f"{G}/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": auth})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return {"_error": e.code, "_body": e.read()[:300].decode(errors="replace")}
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:600].decode(errors='replace')}")


conf = {
    "SERVER_URI": [f"ldap://{IP_AD}:389"],
    "BIND_DN": f"CN=svc-aap,OU=Servicios,OU=Invoke,{BASE}",
    "BIND_PASSWORD": SEC["ad_svc_aap"],
    "START_TLS": False,
    "CONNECTION_OPTIONS": {"OPT_REFERRALS": 0, "OPT_NETWORK_TIMEOUT": 30},
    "USER_SEARCH": [f"OU=Personas,OU=Invoke,{BASE}", "SCOPE_SUBTREE", "(sAMAccountName=%(user)s)"],
    "USER_ATTR_MAP": {"first_name": "givenName", "last_name": "sn", "email": "mail"},
    "GROUP_SEARCH": [f"OU=Grupos,OU=Invoke,{BASE}", "SCOPE_SUBTREE", "(objectClass=group)"],
    "GROUP_TYPE": "MemberDNGroupType",
    "GROUP_TYPE_PARAMS": {"name_attr": "cn", "member_attr": "member"},
}
datos = {"name": NOMBRE, "enabled": True, "create_objects": True, "remove_users": False,
         "type": "ansible_base.authentication.authenticator_plugins.ldap", "configuration": conf}
ya = gw("GET", "authenticators/?name=" + urllib.parse.quote(NOMBRE))["results"]
aut = gw("PATCH", f"authenticators/{ya[0]['id']}/", datos) if ya else gw("POST", "authenticators/", datos)
print(f"   · Autenticador '{NOMBRE}' (id {aut['id']}) → ldap://{IP_AD}:389")

grupo = lambda cn: f"CN={cn},OU=Grupos,OU=Invoke,{BASE}"
mapas = [
    {"name": "Empleados pueden entrar", "map_type": "allow", "order": 10,
     "triggers": {"groups": {"has_or": [grupo("Empleados")]}}},
    {"name": "TI administra AAP", "map_type": "is_superuser", "order": 20,
     "triggers": {"groups": {"has_or": [grupo("TI")]}}},
]
existentes = {m["name"]: m for m in gw("GET", f"authenticator_maps/?authenticator={aut['id']}&page_size=50")["results"]}
for m in mapas:
    m["authenticator"] = aut["id"]
    if m["name"] in existentes:
        gw("PATCH", f"authenticator_maps/{existentes[m['name']]['id']}/", m)
    else:
        gw("POST", "authenticator_maps/", m)
print("   · Reglas: Empleados entran · TI es administrador")

if "--probar" in sys.argv:
    usuario = sys.argv[sys.argv.index("--probar") + 1]
    clave = sys.stdin.readline().strip()
    r = gw("GET", "me/", auth="Basic " + base64.b64encode(f"{usuario}:{clave}".encode()).decode(), tolerar=True)
    if "_error" in r:
        print(f"   · Prueba de login de {usuario}: FALLA ({r['_error']}) {r['_body']}")
        sys.exit(1)
    u = (r.get("results") or [r])[0]
    print(f"   · Prueba de login de {usuario}: OK · superusuario={u.get('is_superuser')}")
print("LISTO")
