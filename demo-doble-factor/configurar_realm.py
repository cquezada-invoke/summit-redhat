#!/usr/bin/python3
# Configura el Keycloak de la demo de doble factor (idempotente):
#   realm invoke-2fa · cliente mi-app (OIDC público + PKCE) · usuario demo con correo y teléfono
#   flujo de login "Invoke · doble factor": usuario y contraseña → código por SMS o por correo (a elección)
#   SMTP del realm = Gmail de cquezada@invoke.cl SIN contraseña: la pega Cristian en Realm settings → Email.
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

KC = os.environ.get("KC_INTERNO", "http://192.168.122.1:8095/doble-factor")
APP = os.environ.get("APP_PUBLICA", "http://34.176.42.56:8090/mi-app")
CRED = os.path.expanduser("~/doble-factor/credenciales.txt")
REALM, CLIENTE, FLUJO = "invoke-2fa", "mi-app", "invoke-doble-factor"
CORREO = "cquezada@invoke.cl"

cred = dict(l.strip().split("=", 1) for l in open(CRED) if "=" in l)


def pedir(metodo, ruta, datos=None, token=None, forma=False, ok404=False):
    url = ruta if ruta.startswith("http") else f"{KC}/admin/realms{ruta}"
    cuerpo, cab = None, {}
    if datos is not None:
        cuerpo = urllib.parse.urlencode(datos).encode() if forma else json.dumps(datos).encode()
        cab["Content-Type"] = "application/x-www-form-urlencoded" if forma else "application/json"
    if token:
        cab["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, cuerpo, cab, method=metodo), timeout=30) as r:
            t = r.read()
            return json.loads(t) if t else None
    except urllib.error.HTTPError as e:
        if ok404 and e.code == 404:
            return None
        raise SystemExit(f"ERROR {metodo} {url} -> {e.code}: {e.read().decode()[:400]}")


def token():
    return pedir("POST", f"{KC}/realms/master/protocol/openid-connect/token",
                 {"grant_type": "password", "client_id": "admin-cli", "username": "admin",
                  "password": cred["kc_admin"]}, forma=True)["access_token"]


for i in range(60):
    try:
        T = token()
        break
    except (SystemExit, OSError):
        time.sleep(5)
else:
    sys.exit("Keycloak no responde")

# master por http (la demo no tiene TLS en este puerto)
pedir("PUT", "/master", {"sslRequired": "none"}, T)

realm = {"realm": REALM, "enabled": True, "displayName": "Invoke · Acceso seguro", "sslRequired": "none",
         "internationalizationEnabled": True, "supportedLocales": ["es"], "defaultLocale": "es",
         "bruteForceProtected": True, "failureFactor": 10, "loginTheme": "keycloak.v2",
         "accessCodeLifespanLogin": 900,
         "smtpServer": {"host": "smtp.gmail.com", "port": "587", "starttls": "true", "ssl": "false", "auth": "true",
                        "user": CORREO, "from": CORREO, "fromDisplayName": "Invoke · Acceso seguro"}}
actual = pedir("GET", f"/{REALM}", token=T, ok404=True)
if actual is None:
    pedir("POST", "", realm, T)
    print("· realm creado")
else:
    if (actual.get("smtpServer") or {}).get("host"):
        realm.pop("smtpServer")  # ya configurado: no pisa la contraseña que pegó Cristian
    pedir("PUT", f"/{REALM}", realm, T)
    print("· realm actualizado")

# atributo "telefono" visible y editable en el perfil de usuario
perfil = pedir("GET", f"/{REALM}/users/profile", token=T)
if not any(a["name"] == "telefono" for a in perfil["attributes"]):
    perfil["attributes"].append({"name": "telefono", "displayName": "Teléfono (SMS)",
                                 "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
                                 "validations": {"length": {"max": 30}}, "multivalued": False})
    pedir("PUT", f"/{REALM}/users/profile", perfil, T)

# cliente mi-app
cli = {"clientId": CLIENTE, "name": "Mi App (demo doble factor)", "enabled": True, "publicClient": True,
       "standardFlowEnabled": True, "directAccessGrantsEnabled": False, "protocol": "openid-connect",
       "redirectUris": [f"{APP}/callback"], "webOrigins": ["+"], "rootUrl": APP, "baseUrl": f"{APP}/",
       "attributes": {"pkce.code.challenge.method": "S256", "post.logout.redirect.uris": f"{APP}/"}}
existe = pedir("GET", f"/{REALM}/clients?clientId={CLIENTE}", token=T)
if existe:
    pedir("PUT", f"/{REALM}/clients/{existe[0]['id']}", {**existe[0], **cli}, T)
else:
    pedir("POST", f"/{REALM}/clients", cli, T)

# usuarios de la demo
usuarios = [
    {"username": "demo", "firstName": "Cristian", "lastName": "Demo", "email": CORREO, "telefono": "+56 9 5555 0101"},
    {"username": "solo-sms", "firstName": "Usuario", "lastName": "SMS", "email": None, "telefono": "+56 9 5555 0202"},
]
for u in usuarios:
    datos = {"username": u["username"], "enabled": True, "firstName": u["firstName"], "lastName": u["lastName"],
             "emailVerified": bool(u["email"]), "attributes": {"telefono": [u["telefono"]]}}
    if u["email"]:
        datos["email"] = u["email"]
    hay = pedir("GET", f"/{REALM}/users?exact=true&username={u['username']}", token=T)
    if hay:
        uid = hay[0]["id"]
        pedir("PUT", f"/{REALM}/users/{uid}", datos, T)
    else:
        pedir("POST", f"/{REALM}/users", datos, T)
        uid = pedir("GET", f"/{REALM}/users?exact=true&username={u['username']}", token=T)[0]["id"]
    pedir("PUT", f"/{REALM}/users/{uid}/reset-password",
          {"type": "password", "value": cred["usuario_demo"], "temporary": False}, T)

# flujo de login: cookie | (usuario y contraseña → [SMS | correo])
f = urllib.parse.quote
if not any(x["alias"] == FLUJO for x in pedir("GET", f"/{REALM}/authentication/flows", token=T)):
    pedir("POST", f"/{REALM}/authentication/flows",
          {"alias": FLUJO, "description": "Usuario y contraseña + código por SMS o correo", "providerId": "basic-flow",
           "topLevel": True, "builtIn": False}, T)
    pedir("POST", f"/{REALM}/authentication/flows/{f(FLUJO)}/executions/execution", {"provider": "auth-cookie"}, T)
    pedir("POST", f"/{REALM}/authentication/flows/{f(FLUJO)}/executions/flow",
          {"alias": "invoke-formularios", "type": "basic-flow", "description": "Contraseña y segundo factor"}, T)
    pedir("POST", f"/{REALM}/authentication/flows/invoke-formularios/executions/execution",
          {"provider": "auth-username-password-form"}, T)
    pedir("POST", f"/{REALM}/authentication/flows/invoke-formularios/executions/flow",
          {"alias": "invoke-segundo-factor", "type": "basic-flow", "description": "Código por SMS o por correo"}, T)
    for p in ("invoke-otp-sms", "invoke-otp-correo"):
        pedir("POST", f"/{REALM}/authentication/flows/invoke-segundo-factor/executions/execution", {"provider": p}, T)
requisitos = {"auth-cookie": "ALTERNATIVE", "invoke-formularios": "ALTERNATIVE",
              "auth-username-password-form": "REQUIRED", "invoke-segundo-factor": "REQUIRED",
              "invoke-otp-sms": "ALTERNATIVE", "invoke-otp-correo": "ALTERNATIVE"}
for ex in pedir("GET", f"/{REALM}/authentication/flows/{f(FLUJO)}/executions", token=T):
    clave = ex.get("providerId") if not ex.get("authenticationFlow") else ex.get("displayName")
    req = requisitos.get(clave)
    if req and ex["requirement"] != req:
        ex["requirement"] = req
        pedir("PUT", f"/{REALM}/authentication/flows/{f(FLUJO)}/executions", ex, T)
pedir("PUT", f"/{REALM}", {"browserFlow": FLUJO}, T)

print("LISTO")
print(f"  App:      {APP}/")
print(f"  Teléfono: {APP}/telefono")
print(f"  Consola:  {KC.replace('http://192.168.122.1:8095', 'http://34.176.42.56:8090')}/admin/  (usuario admin)")
print(f"  Usuarios: demo (SMS o correo) · solo-sms (solo SMS) · claves en {CRED}")
