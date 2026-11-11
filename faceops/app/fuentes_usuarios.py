"""Usuarios de FaceOps y sus fuentes.

- Locales: se crean y administran en FaceOps (Administración › Usuarios y perfiles). Se pueden cargar en bloque desde un
  archivo HTPasswd (bcrypt, APR1-MD5 o SHA): se guardan con su hash original y quedan como locales.
- Externos: los entrega una fuente (Active Directory, LDAP o el IGA midPoint). Esa fuente los administra: identidad, clave,
  estado y grupos/roles. FaceOps solo los muestra; su perfil (modo, aprobador, segmentos) sale del mapeo de grupos o roles de
  la fuente. Puede haber varias fuentes por cliente.

Las fuentes se integran desde Ansible y se conectan en Integraciones: su credencial vive en AAP («FaceOps · Fuente de
identidad»), la plantilla «Identidad · Fuente de usuarios de FaceOps» lee usuarios y grupos y configura el autenticador
del Gateway de AAP, y la clave de un usuario externo la valida ese Gateway. FaceOps nunca se conecta a la fuente."""
import base64
import hashlib
import json
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from .bd import ahora, auditar, ej, j, q, q1

TIPOS = {
    "ad": {"nombre": "Active Directory", "url": "ldaps://dc01.empresa.local:636",
           "filtro": "(&(objectCategory=person)(objectClass=user)(sAMAccountName={usuario}))",
           "filtro_lista": "(&(objectCategory=person)(objectClass=user)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
           "attr_usuario": "sAMAccountName", "attr_nombre": "displayName", "attr_correo": "mail", "attr_grupos": "memberOf",
           "bind_dn": "CN=svc-faceops,OU=Servicios,DC=empresa,DC=local", "base_dn": "DC=empresa,DC=local", "mapeo_ayuda": "DN o nombre (CN) del grupo"},
    "ldap": {"nombre": "LDAP", "url": "ldaps://ldap.empresa.local:636",
             "filtro": "(&(objectClass=inetOrgPerson)(uid={usuario}))", "filtro_lista": "(objectClass=inetOrgPerson)",
             "attr_usuario": "uid", "attr_nombre": "cn", "attr_correo": "mail", "attr_grupos": "memberOf",
             "bind_dn": "uid=svc-faceops,cn=users,cn=accounts,dc=empresa,dc=local", "base_dn": "cn=users,cn=accounts,dc=empresa,dc=local",
             "mapeo_ayuda": "DN o nombre (cn) del grupo"},
    "midpoint": {"nombre": "IGA · midPoint", "url": "https://midpoint.empresa.local/midpoint",
                 "bind_dn": "faceops-svc", "mapeo_ayuda": "Nombre del rol u organización en midPoint"},
}
MODOS = ("lectura", "escritura")
CLAVE_EXTERNA = "externo:"   # prefijo de clave_hash de un usuario externo: nunca valida una clave local
CLAVE_HTPASSWD = "htpasswd:"


# ------------------------------------------------------------------ claves locales (incluye las cargadas desde HTPasswd)
def _apr1(clave, sal):
    """APR1-MD5 de Apache (htpasswd -m)."""
    pw, mag = clave.encode(), b"$apr1$"
    s = sal.encode()[:8]
    ctx = hashlib.md5(pw + mag + s)
    fin = hashlib.md5(pw + s + pw).digest()
    for i in range(len(pw), 0, -16):
        ctx.update(fin[:min(16, i)])
    i = len(pw)
    while i:
        ctx.update(b"\0" if i & 1 else pw[:1])
        i >>= 1
    fin = ctx.digest()
    for i in range(1000):
        c = hashlib.md5()
        c.update(pw if i & 1 else fin)
        if i % 3:
            c.update(s)
        if i % 7:
            c.update(pw)
        c.update(fin if i & 1 else pw)
        fin = c.digest()
    it = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    def _b64(v, n):
        r = ""
        for _ in range(n):
            r += it[v & 0x3f]
            v >>= 6
        return r
    f = fin
    r = (_b64((f[0] << 16) | (f[6] << 8) | f[12], 4) + _b64((f[1] << 16) | (f[7] << 8) | f[13], 4) + _b64((f[2] << 16) | (f[8] << 8) | f[14], 4)
         + _b64((f[3] << 16) | (f[9] << 8) | f[15], 4) + _b64((f[4] << 16) | (f[10] << 8) | f[5], 4) + _b64(f[11], 2))
    return f"$apr1${sal[:8]}${r}"


def formato_htpasswd(h):
    if h.startswith(("$2y$", "$2b$", "$2a$")):
        return "bcrypt"
    if h.startswith("$apr1$"):
        return "apr1"
    if h.startswith("{SHA}"):
        return "sha1"
    return None   # crypt(3) DES o texto plano: inseguros, no se aceptan


def _ok_htpasswd(h, clave):
    f = formato_htpasswd(h)
    if f == "bcrypt":
        import bcrypt
        return bcrypt.checkpw(clave.encode(), ("$2b$" + h[4:]).encode())
    if f == "apr1":
        return secrets.compare_digest(_apr1(clave, h.split("$")[2]), h)
    if f == "sha1":
        return secrets.compare_digest("{SHA}" + base64.b64encode(hashlib.sha1(clave.encode()).digest()).decode(), h)
    return False


def verificar_clave(u, clave):
    """Clave de un usuario local (hash de FaceOps o importado desde HTPasswd). Un usuario externo nunca valida aquí."""
    h = (u or {}).get("clave_hash") or ""
    if not clave or not h or h.startswith(CLAVE_EXTERNA):
        return False
    if h.startswith(CLAVE_HTPASSWD):
        try:
            return _ok_htpasswd(h[len(CLAVE_HTPASSWD):], clave)
        except Exception:  # noqa: BLE001
            return False
    return check_password_hash(h, clave)


def leer_htpasswd(texto):
    """[(usuario, hash, formato|None)] de un archivo HTPasswd (usuario:hash por línea)."""
    out = []
    for ln in (texto or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or ":" not in ln:
            continue
        us, h = ln.split(":", 1)
        out.append((us.strip().lower(), h.strip(), formato_htpasswd(h.strip())))
    return out


def es_externo(u):
    return (u.get("origen") or "local").startswith(("fuente:", "ldap:"))


def fuente_de(u):
    o = u.get("origen") or "local"
    if o.startswith("fuente:"):
        return q1("SELECT * FROM fuentes_usuarios WHERE id=?", int(o.split(":")[1]))
    return None


def etiqueta_origen(u, fuentes=None):
    o = u.get("origen") or "local"
    if o.startswith("fuente:"):
        f = (fuentes or {}).get(int(o.split(":")[1])) or fuente_de(u)
        return f"{TIPOS.get(f['tipo'], {}).get('nombre', f['tipo'])} · {f['nombre']}" if f else "fuente eliminada"
    if o.startswith("ldap:"):
        return f"LDAP · {o.split(':', 1)[1]} (de prueba, por conectar)"
    return "Local · HTPasswd" if o == "htpasswd" else "Local"


# ------------------------------------------------------------------ fuentes externas
def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS fuentes_usuarios (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, tipo TEXT NOT NULL, conf TEXT DEFAULT '{}',
      mapeo TEXT DEFAULT '[]', activo INTEGER DEFAULT 1, creado TEXT, ultima_sync TEXT DEFAULT '', estado TEXT DEFAULT '');""")


def fuentes(cliente_id):
    _tabla()
    return [dict(f, conf_d=j(f["conf"], {}), mapeo_l=j(f["mapeo"], [])) for f in q("SELECT * FROM fuentes_usuarios WHERE cliente_id=? ORDER BY nombre", cliente_id)]


def fuente(fid, cliente_id=None):
    _tabla()
    f = q1("SELECT * FROM fuentes_usuarios WHERE id=?" + (" AND cliente_id=?" if cliente_id else ""), fid, *([cliente_id] if cliente_id else []))
    return dict(f, conf_d=j(f["conf"], {}), mapeo_l=j(f["mapeo"], [])) if f else None


def perfil_por_mapeo(f, grupos):
    """Perfil para FaceOps según los grupos (AD/LDAP) o roles (midPoint) del usuario en la fuente; None = sin acceso.
    Si calza con varias filas: el modo más alto, aprobador si alguna lo da y la unión de segmentos."""
    norm = {str(g).strip().lower() for g in grupos or []}
    cn = {g.split(",")[0].split("=", 1)[-1].strip().lower() for g in norm}
    perfil = None
    for m in f["mapeo_l"]:
        g = str(m.get("grupo") or "").strip().lower()
        if not g or not (g in norm or g in cn or g.split(",")[0].split("=", 1)[-1] in cn):
            continue
        p = perfil or {"modo": "lectura", "aprobador": 0, "segmentos": []}
        if m.get("modo") == "escritura":
            p["modo"] = "escritura"
        p["aprobador"] = int(p["aprobador"] or bool(m.get("aprobador")))
        segs = m.get("segmentos") or ["*"]
        p["segmentos"] = ["*"] if "*" in segs or "*" in p["segmentos"] else sorted(set(p["segmentos"]) | set(segs))
        perfil = p
    return perfil


# ── Integración desde Ansible: FaceOps nunca se conecta a la fuente ───────────────────────────────────────────────
IDENTIDAD = "Identidad · Fuente de usuarios de FaceOps"
TIPO_CRED = "FaceOps · Fuente de identidad"


def nombre_credencial(f):
    return f"FaceOps · Fuente {f['cliente_id']}-{f['id']} · {f['nombre']}"[:120]


def credencial_aap(aap, f, clave=None):
    """La credencial de la fuente vive en AAP (tipo «FaceOps · Fuente de identidad»): URL, cuenta de servicio y su clave.
    La clave pasa por FaceOps una sola vez, hacia AAP; nunca se guarda en FaceOps."""
    ct = (aap.get("credential_types/", name=TIPO_CRED).get("results") or [None])[0]
    if not ct:
        raise ValueError(f"Falta en AAP el tipo de credencial «{TIPO_CRED}» (ejecuta la configuración de AAP)")
    org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
    nom = nombre_credencial(f)
    ya = (aap.get("credentials/", name=nom).get("results") or [None])[0]
    ins = {"url": f["conf_d"].get("url", ""), "usuario": f["conf_d"].get("bind_dn", ""),
           "verificar_tls": bool(f["conf_d"].get("verificar_tls", True))}
    if clave:
        ins["clave"] = clave
    elif not ya:
        raise ValueError("Indica la clave de la cuenta de servicio: se guarda en la credencial de AAP")
    else:
        ins["clave"] = "$encrypted$"   # AAP conserva la clave guardada
    datos = {"name": nom, "organization": org, "credential_type": ct["id"], "inputs": ins,
             "description": f"Fuente de usuarios de FaceOps ({TIPOS[f['tipo']]['nombre']}); la usa «{IDENTIDAD}»"}
    return aap.patch(f"credentials/{ya['id']}/", datos) if ya else aap.post("credentials/", datos)


def parametros(f, modo):
    c_ = f["conf_d"]
    return {"fu_id": f["id"], "fu_cliente": f["cliente_id"], "fu_nombre": f["nombre"], "fu_tipo": f["tipo"], "fu_modo": modo,
            "fu_credencial": nombre_credencial(f),
            "fu_conf": {k: c_.get(k) or TIPOS[f["tipo"]].get(k, "") for k in ("base_dn", "filtro", "filtro_lista", "attr_usuario", "attr_nombre", "attr_correo", "attr_grupos")}
            | {"starttls": bool(c_.get("starttls"))},
            "fu_grupos": [m["grupo"] for m in f["mapeo_l"] if m.get("grupo")]}


def pedir(usuario, cliente, f, modo, motivo=""):
    """Ticket a «Identidad · Fuente de usuarios de FaceOps» (con aprobación) que ejecuta Ansible con la credencial de la fuente."""
    from .operacion import solicitar
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], IDENTIDAD)
    if not a:
        raise ValueError(f"Falta «{IDENTIDAD}» en el catálogo (sincroniza con AAP)")
    txt = {"probar": "Probar la conexión con", "conectar": "Conectar", "sincronizar": "Sincronizar usuarios de", "retirar": "Retirar"}[modo]
    return solicitar(usuario, cliente, a, "", parametros(f, modo), motivo or f"{txt} la fuente de usuarios «{f['nombre']}»")


def registrar_resultado(cliente_id, art, numero, por="sistema"):
    """Hook al terminar el ticket: lo que Ansible leyó de la fuente se aplica a los usuarios de FaceOps."""
    x = (art or {}).get("faceops_identidad") or {}
    f = fuente(int(x.get("fuente_id") or 0), cliente_id) if x.get("fuente_id") else None
    if not f:
        return
    modo = x.get("modo")
    if modo == "probar":
        est = f"✓ Conexión correcta desde Ansible: {x.get('n_total')} usuarios en la fuente ({numero})"
        ej("UPDATE fuentes_usuarios SET estado=? WHERE id=?", est, f["id"])
        return
    if modo == "retirar":
        for u in q("SELECT id FROM usuarios WHERE origen=?", f"fuente:{f['id']}"):
            _sin_acceso(u["id"], f)
        ej("UPDATE fuentes_usuarios SET estado=?, activo=0 WHERE id=?", f"Retirada por Ansible ({numero})", f["id"])
        return
    sincronizar(f, x.get("usuarios") or [], por, numero, x.get("autenticador") or "")


def _asegurar(f, info, perfil):
    """Crea o actualiza el usuario externo y su perfil en el cliente de la fuente. None si el nombre ya es de otro origen."""
    origen = f"fuente:{f['id']}"
    u = q1("SELECT * FROM usuarios WHERE usuario=?", info["usuario"])
    if u and (u.get("origen") or "local") != origen:
        return None   # un usuario local u otra fuente ya usa ese nombre: no se toma
    if u:
        ej("UPDATE usuarios SET nombre=?, correo=?, activo=1, dn=? WHERE id=?", info["nombre"], info["correo"], info["dn"], u["id"])
        uid = u["id"]
    else:
        uid = ej("INSERT INTO usuarios(usuario, nombre, correo, clave_hash, es_admin, activo, creado, origen, dn) VALUES (?,?,?,?,0,1,?,?,?)",
                 info["usuario"], info["nombre"], info["correo"], CLAVE_EXTERNA + secrets.token_hex(8), ahora(), origen, info["dn"])
    ej("INSERT INTO perfiles(usuario_id, cliente_id, segmentos, modo, aprobador) VALUES (?,?,?,?,?) "
       "ON CONFLICT(usuario_id, cliente_id) DO UPDATE SET segmentos=excluded.segmentos, modo=excluded.modo, aprobador=excluded.aprobador",
       uid, f["cliente_id"], json.dumps(perfil["segmentos"], ensure_ascii=False), perfil["modo"], perfil["aprobador"])
    return q1("SELECT * FROM usuarios WHERE id=?", uid)


def _sin_acceso(uid, f):
    ej("DELETE FROM perfiles WHERE usuario_id=? AND cliente_id=?", uid, f["cliente_id"])
    if not q1("SELECT 1 AS x FROM perfiles WHERE usuario_id=?", uid):
        ej("UPDATE usuarios SET activo=0 WHERE id=?", uid)


def sincronizar(f, usuarios, por="sistema", numero="", autenticador=""):
    """Aplica lo que Ansible leyó de la fuente: alta o actualización de quienes tienen acceso según el mapeo; quienes ya no
    lo tienen (o no están) pierden el acceso."""
    vistos, n_alta, n_conf = set(), 0, 0
    for i in usuarios:
        p = perfil_por_mapeo(f, i.get("grupos") or [])
        if not p or not i.get("usuario"):
            continue
        previo = q1("SELECT id FROM usuarios WHERE usuario=? AND origen=?", i["usuario"], f"fuente:{f['id']}")
        u = _asegurar(f, {"usuario": i["usuario"], "nombre": i.get("nombre") or i["usuario"], "correo": i.get("correo") or "", "dn": i.get("dn") or ""}, p)
        if not u:
            n_conf += 1
            continue
        vistos.add(u["id"])
        n_alta += 0 if previo else 1
    n_baja = 0
    for u in q("SELECT id FROM usuarios WHERE origen=? AND activo=1", f"fuente:{f['id']}"):
        if u["id"] not in vistos:
            _sin_acceso(u["id"], f)
            n_baja += 1
    est = (f"{len(vistos)} con acceso · {n_alta} nuevos · {n_baja} sin acceso" + (f" · {n_conf} omitidos (nombre ya usado por otro origen)" if n_conf else "")
           + (f" · clave validada por el Gateway de AAP ({autenticador})" if autenticador else "") + (f" · {numero}" if numero else ""))
    ej("UPDATE fuentes_usuarios SET ultima_sync=?, estado=? WHERE id=?", ahora(), est, f["id"])
    auditar(por, f["cliente_id"], "sincronizar fuente de usuarios", f["nombre"], {"resultado": est})
    return est


def _gateway_valida(f, usuario, clave):
    """La clave de un usuario externo la valida el Gateway de AAP (su autenticador LDAP lo dejó configurado Ansible).
    FaceOps nunca habla con el directorio."""
    import requests
    c = q1("SELECT * FROM clientes WHERE id=?", f["cliente_id"])
    if not c or not c.get("aap_url"):
        return False
    try:
        from .aap import YO, modo
        r = requests.get(c["aap_url"].rstrip("/") + YO[modo(c)], auth=(usuario, clave), timeout=15, verify=bool(c.get("aap_verificar")))
    except requests.RequestException:
        return False
    if r.status_code != 200:
        return False
    try:
        d = r.json()
        d = (d.get("results") or [d])[0] if isinstance(d, dict) else {}
    except ValueError:
        return False
    return str(d.get("username", "")).lower() == usuario


def autenticar_ingreso(nombre, clave):
    """Usuario que entra (fila de usuarios) o None. Locales: su clave en FaceOps (o la del HTPasswd). Externos: la valida el
    Gateway de AAP; su perfil lo dio la última sincronización de la fuente (desde Ansible)."""
    _tabla()
    if not nombre or not clave:
        return None
    u = q1("SELECT * FROM usuarios WHERE usuario=?", nombre)
    if not u or not u["activo"]:
        return None
    if not es_externo(u):
        return u if verificar_clave(u, clave) else None
    f = fuente_de(u)
    if not f or not f["activo"]:
        return None
    f = fuente(f["id"])
    if f["tipo"] == "midpoint":   # midPoint gobierna; la clave la valida el directorio que provisiona, a través del Gateway
        dir_ = f["conf_d"].get("directorio")
        f = fuente(int(dir_)) if dir_ else None
        if not f:
            return None
    return u if _gateway_valida(f, nombre, clave) else None


def crear_local(cliente_id, datos, clave=None, clave_hash=None, origen="local"):
    if q1("SELECT 1 AS x FROM usuarios WHERE usuario=?", datos["usuario"]):
        raise ValueError(f"Ya existe el usuario {datos['usuario']}")
    uid = ej("INSERT INTO usuarios(usuario, nombre, correo, clave_hash, es_admin, activo, creado, expira, origen) VALUES (?,?,?,?,0,?,?,?,?)",
             datos["usuario"], datos["nombre"], datos.get("correo", ""), clave_hash or generate_password_hash(clave), int(datos.get("activo", 1)),
             ahora(), datos.get("expira", ""), origen)
    return uid


def guardar_perfil(uid, cliente_id, modo, aprobador, segmentos, ambientes=None):
    ej("INSERT INTO perfiles(usuario_id, cliente_id, segmentos, ambientes, modo, aprobador) VALUES (?,?,?,?,?,?) "
       "ON CONFLICT(usuario_id, cliente_id) DO UPDATE SET segmentos=excluded.segmentos, ambientes=excluded.ambientes, modo=excluded.modo, "
       "aprobador=excluded.aprobador", uid, cliente_id, json.dumps(segmentos or ["*"], ensure_ascii=False), json.dumps(ambientes or ["*"]),
       modo if modo in MODOS else "lectura", int(bool(aprobador)))


def cargar_htpasswd(cliente_id, texto, perfil, reemplazar=False):
    """Alta (o actualización de clave) de usuarios locales desde un HTPasswd. Devuelve el resumen por usuario."""
    out = {"creados": [], "actualizados": [], "omitidos": []}
    for us, h, fmt in leer_htpasswd(texto):
        if not fmt:
            out["omitidos"].append((us, "formato inseguro o no soportado (usa bcrypt: htpasswd -B)"))
            continue
        if not us or not all(c.isalnum() or c in "._-@" for c in us):
            out["omitidos"].append((us, "nombre de usuario inválido"))
            continue
        u = q1("SELECT * FROM usuarios WHERE usuario=?", us)
        if u and es_externo(u):
            out["omitidos"].append((us, "ya existe y lo administra su fuente externa"))
            continue
        if u:
            if not reemplazar:
                out["omitidos"].append((us, "ya existe (marca «actualizar claves» para reemplazarla)"))
                continue
            ej("UPDATE usuarios SET clave_hash=? WHERE id=?", CLAVE_HTPASSWD + h, u["id"])
            if not q1("SELECT 1 AS x FROM perfiles WHERE usuario_id=? AND cliente_id=?", u["id"], cliente_id):
                guardar_perfil(u["id"], cliente_id, perfil["modo"], perfil["aprobador"], perfil["segmentos"])
            out["actualizados"].append(us)
            continue
        uid = crear_local(cliente_id, {"usuario": us, "nombre": us}, clave_hash=CLAVE_HTPASSWD + h, origen="htpasswd")
        guardar_perfil(uid, cliente_id, perfil["modo"], perfil["aprobador"], perfil["segmentos"])
        out["creados"].append(us)
    return out
