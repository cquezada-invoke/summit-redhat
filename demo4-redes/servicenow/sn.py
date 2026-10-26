# Ayudante común para hablar con ServiceNow desde la VM (credenciales desde Vault secret/servicenow). No imprime claves.
import base64, json, os, ssl, urllib.error, urllib.parse, urllib.request

_CTX = ssl._create_unverified_context()


def credenciales():
    root = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"]
    req = urllib.request.Request("https://127.0.0.1:8200/v1/secret/data/servicenow", headers={"X-Vault-Token": root})
    d = json.load(urllib.request.urlopen(req, context=_CTX, timeout=20))["data"]["data"]
    return d["url"].rstrip("/"), d["usuario"], d["clave"]


URL, USUARIO, _CLAVE = credenciales()
_AUTH = "Basic " + base64.b64encode(f"{USUARIO}:{_CLAVE}".encode()).decode()


def api(metodo, ruta, datos=None, tolerar=False):
    req = urllib.request.Request(f"{URL}{ruta}", method=metodo,
                                 data=json.dumps(datos).encode() if datos is not None else None,
                                 headers={"Accept": "application/json", "Content-Type": "application/json",
                                          "Authorization": _AUTH})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        raise SystemExit(f"ERROR ServiceNow {metodo} {ruta.split('?')[0]} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


def tabla(t, consulta="", campos="", limite=50):
    q = urllib.parse.urlencode({"sysparm_query": consulta, "sysparm_fields": campos, "sysparm_limit": limite})
    return api("GET", f"/api/now/table/{t}?{q}")["result"]


def uno(t, consulta, campos="sys_id"):
    r = tabla(t, consulta, campos, 1)
    return r[0] if r else None


def asegurar(t, consulta, datos):
    """Crea o actualiza un registro (idempotente). Devuelve el registro."""
    o = uno(t, consulta, "sys_id")
    if o:
        return api("PATCH", f"/api/now/table/{t}/{o['sys_id']}", datos)["result"]
    return api("POST", f"/api/now/table/{t}", datos)["result"]
