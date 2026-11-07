"""Usuarios de FaceOps y sus fuentes.

- Locales: se crean y administran en FaceOps (Administración › Usuarios y perfiles). Se pueden cargar en bloque desde un
  archivo HTPasswd (bcrypt, APR1-MD5 o SHA): se guardan con su hash original y quedan como locales.
- Externos: los entrega una fuente (Active Directory, LDAP o el IGA midPoint). Esa fuente los administra: identidad, clave,
  estado y grupos/roles. FaceOps solo los muestra; su perfil (modo, aprobador, segmentos) sale del mapeo de grupos o roles de
  la fuente. Puede haber varias fuentes por cliente.

La credencial de servicio de cada fuente vive en la bóveda (faceops/fuentes/<cliente>/<id>), nunca en la base."""
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
    return (u.get("origen") or "local").startswith("fuente:")


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


def _ruta_secreto(f):
    return f"fuentes/{f['cliente_id']}/{f['id']}"


def guardar_secreto(f, clave):
    from .boveda import escribir
    return escribir(_ruta_secreto(f), {"clave": clave})


def _secreto(f):
    if f.get("_clave") is not None:   # pruebas
        return f["_clave"]
    from .boveda import leer
    return (leer(_ruta_secreto(f)) or {}).get("clave") or ""


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


# LDAP / AD
_fabrica_ldap = None   # pruebas: función (fuente) → ldap3.Server con estrategia simulada


def _ldap_con(f, usuario=None, clave=None):
    import ssl
    from ldap3 import Connection, Server, Tls
    c_ = f["conf_d"]
    if _fabrica_ldap:
        srv, kw = _fabrica_ldap(f), {"client_strategy": "MOCK_SYNC"}
    else:
        url = c_.get("url") or ""
        srv = Server(url, use_ssl=url.startswith("ldaps://"), connect_timeout=5,
                     tls=Tls(validate=ssl.CERT_REQUIRED if c_.get("verificar_tls", True) else ssl.CERT_NONE))
        kw = {}
    con = Connection(srv, user=usuario or c_.get("bind_dn"), password=clave if usuario else _secreto(f), receive_timeout=10,
                     raise_exceptions=False, **kw)
    if c_.get("starttls") and not _fabrica_ldap and not (c_.get("url") or "").startswith("ldaps://"):
        con.open()
        con.start_tls()
    return con if con.bind() else None


def _attr(e, n):
    if not n:
        return None
    v = e["attributes"].get(n) if isinstance(e, dict) else None
    if isinstance(v, list):
        return v
    return v


def _primero(v):
    return (v[0] if v else "") if isinstance(v, list) else (v or "")


def _info_ldap(f, e):
    c_ = f["conf_d"]
    gr = _attr(e, c_.get("attr_grupos") or "memberOf") or []
    return {"usuario": str(_primero(_attr(e, c_.get("attr_usuario")))).strip().lower(), "dn": e["dn"],
            "nombre": str(_primero(_attr(e, c_.get("attr_nombre")))) or e["dn"], "correo": str(_primero(_attr(e, c_.get("attr_correo")))),
            "grupos": gr if isinstance(gr, list) else [gr]}


def _ldap_buscar(con, f, filtro, limite=0):
    c_ = f["conf_d"]
    attrs = [a for a in (c_.get("attr_usuario"), c_.get("attr_nombre"), c_.get("attr_correo"), c_.get("attr_grupos") or "memberOf") if a]
    con.search(c_.get("base_dn") or "", filtro, attributes=attrs, size_limit=limite)
    return [x for x in con.response or [] if x.get("type") == "searchResEntry"]


def _ldap_autenticar(f, usuario, clave):
    from ldap3.utils.conv import escape_filter_chars
    con = _ldap_con(f)
    if not con:
        return None
    es = _ldap_buscar(con, f, (f["conf_d"].get("filtro") or "").replace("{usuario}", escape_filter_chars(usuario)), 2)
    con.unbind()
    if len(es) != 1:
        return None
    info = _info_ldap(f, es[0])
    c2 = _ldap_con(f, info["dn"], clave)   # la clave la valida el directorio (bind con el DN del usuario)
    if not c2:
        return None
    c2.unbind()
    return info


def _ldap_listar(f):
    con = _ldap_con(f)
    if not con:
        raise ConnectionError("no se pudo conectar ni autenticar con la cuenta de servicio")
    es = _ldap_buscar(con, f, f["conf_d"].get("filtro_lista") or "(objectClass=*)", 5000)
    con.unbind()
    return [_info_ldap(f, e) for e in es]


# midPoint (IGA): REST. La clave del usuario la valida midPoint (GET /ws/rest/self con sus credenciales).
_http = None   # pruebas: función (método, url, auth, verify) → (código, json)


def _mp(f, ruta, auth):
    import requests
    url = (f["conf_d"].get("url") or "").rstrip("/") + "/ws/rest/" + ruta.lstrip("/")
    if _http:
        return _http("GET", url, auth, f["conf_d"].get("verificar_tls", True))
    r = requests.get(url, auth=auth, headers={"Accept": "application/json"}, timeout=15, verify=f["conf_d"].get("verificar_tls", True))
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


def _mp_objetos(d):
    """Lista de usuarios de una respuesta REST de midPoint (cambia algo entre versiones)."""
    o = d.get("object", d) if isinstance(d, dict) else d
    if isinstance(o, dict):
        o = o.get("object", o.get("user", [o]))
    return o if isinstance(o, list) else [o]


def _mp_info(u):
    u = u.get("user", u)
    refs = u.get("roleMembershipRef") or []
    refs = refs if isinstance(refs, list) else [refs]
    roles = [str(r.get("targetName") or r.get("oid") or "") for r in refs if isinstance(r, dict)]
    pol = u.get("parentOrgRef") or []
    pol = pol if isinstance(pol, list) else [pol]
    roles += [str(r.get("targetName") or "") for r in pol if isinstance(r, dict) and r.get("targetName")]
    act = (u.get("activation") or {}).get("effectiveStatus", "enabled")
    nom = u.get("fullName") or u.get("name")
    nom = nom.get("orig") if isinstance(nom, dict) else nom
    us = u.get("name")
    us = us.get("orig") if isinstance(us, dict) else us
    return {"usuario": str(us or "").strip().lower(), "dn": f"midpoint:{u.get('oid', '')}", "nombre": str(nom or us or ""),
            "correo": str(u.get("emailAddress") or ""), "grupos": roles, "habilitado": act == "enabled"}


def _mp_autenticar(f, usuario, clave):
    cod, d = _mp(f, "self?options=resolveNames", (usuario, clave))
    if cod != 200:
        return None
    i = _mp_info(d)
    return i if i["usuario"] == usuario and i["habilitado"] else None


def _mp_listar(f):
    cod, d = _mp(f, "users?options=resolveNames", (f["conf_d"].get("bind_dn") or "", _secreto(f)))
    if cod != 200:
        raise ConnectionError(f"midPoint respondió {cod}")
    return [i for i in (_mp_info(x) for x in _mp_objetos(d)) if i["usuario"] and i["habilitado"]]


def autenticar_en(f, usuario, clave):
    if not clave or not usuario:   # sin clave, LDAP aceptaría un bind anónimo
        return None
    try:
        return _mp_autenticar(f, usuario, clave) if f["tipo"] == "midpoint" else _ldap_autenticar(f, usuario, clave)
    except Exception:  # noqa: BLE001
        return None


def listar_en(f):
    return _mp_listar(f) if f["tipo"] == "midpoint" else _ldap_listar(f)


def probar(f):
    """(ok, mensaje): conecta con la cuenta de servicio y cuenta cuántos usuarios entregaría (con perfil según el mapeo)."""
    try:
        us = listar_en(f)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"[:300]
    con_ = sum(1 for i in us if perfil_por_mapeo(f, i["grupos"]))
    return True, f"Conexión correcta: {len(us)} usuario{'s' if len(us) != 1 else ''} en la fuente, {con_} con acceso a FaceOps según el mapeo"


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


def sincronizar(f, por="sistema"):
    """Trae de la fuente a quienes tienen acceso según el mapeo; quienes ya no lo tienen (o no están) pierden el acceso."""
    us = listar_en(f)
    vistos, n_alta, n_conf = set(), 0, 0
    for i in us:
        p = perfil_por_mapeo(f, i["grupos"])
        if not p or not i["usuario"]:
            continue
        previo = q1("SELECT id FROM usuarios WHERE usuario=? AND origen=?", i["usuario"], f"fuente:{f['id']}")
        u = _asegurar(f, i, p)
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
    est = f"{len(vistos)} con acceso · {n_alta} nuevos · {n_baja} sin acceso" + (f" · {n_conf} omitidos (nombre ya usado por otro origen)" if n_conf else "")
    ej("UPDATE fuentes_usuarios SET ultima_sync=?, estado=? WHERE id=?", ahora(), est, f["id"])
    auditar(por, f["cliente_id"], "sincronizar fuente de usuarios", f["nombre"], {"resultado": est})
    return est


def autenticar_ingreso(nombre, clave):
    """Usuario que entra (fila de usuarios) o None. Locales: su clave en FaceOps. Externos: los valida su fuente, que también
    decide si sigue con acceso (grupos/roles). Un nombre nuevo se busca en las fuentes activas (alta al primer ingreso)."""
    _tabla()
    u = q1("SELECT * FROM usuarios WHERE usuario=?", nombre)
    if u and not es_externo(u):
        return u if u["activo"] and verificar_clave(u, clave) else None
    cand = [fuente(int(u["origen"].split(":")[1]))] if u else [fuente(x["id"]) for x in q("SELECT id FROM fuentes_usuarios WHERE activo=1 ORDER BY id")]
    for f in cand:
        if not f or not f["activo"]:
            continue
        info = autenticar_en(f, nombre, clave)
        if not info:
            continue
        p = perfil_por_mapeo(f, info["grupos"])
        if not p:
            if u:
                _sin_acceso(u["id"], f)
            return None
        return _asegurar(f, dict(info, usuario=nombre), p)
    return None


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
