"""Columna «Credencial» de los usuarios del sistema operativo en la ficha de máquina: quién administra la credencial de
cada usuario. Cruza los usuarios del inventario con las credenciales de AAP que se usan sobre esa máquina o su inventario
(plan de recolección del cliente) y con la bóveda (nodo-vault). Solo metadatos: tipo, nombre y origen; nunca valores."""
from .bd import j

ENC = "$encrypted$"


def _origen_externo(tipo_fuente):
    t = (tipo_fuente or "").lower()
    if "hashicorp" in t or "vault" in t:
        return "HashiCorp Vault"
    if "cyberark" in t:
        return "CyberArk"
    if "azure key vault" in t:
        return "Azure Key Vault"
    if "thycotic" in t or "secret server" in t:
        return "Thycotic Secret Server"
    return tipo_fuente or "externo"


def _norm(u):
    u = (u or "").strip().lower()
    dom = ""
    if "\\" in u:
        dom, u = u.split("\\", 1)
    elif "@" in u:
        u, dom = u.split("@", 1)
    return u, dom.split(".")[0]


def _calza(usuario, cred_user):
    (a, da), (b, db) = _norm(usuario), _norm(cred_user)
    return bool(a) and a == b and da == db


def _creds_maquina(aap, cliente, nombre):
    """Credenciales de AAP (dict completo de la API, del que solo se usan metadatos) que FaceOps usa sobre esta máquina:
    las del plan de recolección para el inventario y grupo (límite) donde está el host."""
    hs = aap.get("hosts/", name=nombre).get("results", [])
    if not hs:
        return []
    plan = j(cliente.get("recoleccion"), {})
    nombres = []
    for h in hs:
        sf = h.get("summary_fields") or {}
        inv = (sf.get("inventory") or {}).get("name", "")
        grupos = {x.get("name") for x in ((sf.get("groups") or {}).get("results") or [])}
        for p in plan.get(inv) or []:
            lim = p.get("limite") or ""
            if p.get("credencial") and (not lim or lim == nombre or lim in grupos):
                nombres.append(p["credencial"])
    return list(dict.fromkeys(nombres))


def por_usuario(cliente, e, usuarios, usuario_faceops=None):
    """{usuario: [{tipo, nombre, origen}]} · None si no se pudo consultar AAP (la ficha muestra «—»)."""
    if not usuarios:
        return {}
    from .aap import AAP
    try:
        aap = AAP.de_cliente(cliente)
        todas = aap.todos("credentials/")
        fuentes = aap.todos("credential_input_sources/")
        usadas = set(_creds_maquina(aap, cliente, e["nombre"]))
    except Exception:  # noqa: BLE001 - sin AAP (o aún sin el dato en caché) la columna queda en «—»
        return None
    por_id = {c["id"]: c for c in todas}
    tipo_de = lambda c: ((c.get("summary_fields") or {}).get("credential_type") or {}).get("name", "")
    ext = {}   # credencial destino → {campo: origen externo}
    for s in fuentes:
        src = por_id.get(s.get("source_credential")) or {}
        ext.setdefault(s.get("target_credential"), {})[s.get("input_field_name")] = (_origen_externo(tipo_de(src)), src.get("name", ""))
    out = {u["n"]: [] for u in usuarios}
    for c in todas:
        if c["name"] not in usadas:
            continue
        ins = c.get("inputs") or {}
        cu = ins.get("username") if ins.get("username") not in (None, "", ENC) else ""
        ex = ext.get(c["id"]) or {}
        if not cu and "username" in ex:   # el username lo resuelve el lookup: es el usuario con que FaceOps entra
            cu = usuario_faceops or ""
        o = ex.get("username") or ex.get("password") or ex.get("ssh_key_data") or next(iter(ex.values()), None)
        reg = {"tipo": tipo_de(c), "nombre": c["name"], "origen": f"{o[0]} (lookup de AAP: {o[1]})" if o else "AAP"}
        for u in usuarios:
            if cu and _calza(u["n"], cu):
                out[u["n"]].append(reg)
    try:   # bóveda del lab (nodo-vault): faceops/maquinas/<máquina> · solo se mira quién es el usuario
        from .boveda import acceso
        u_b = (acceso(e["nombre"], esperar=False) or {}).get("usuario")
    except Exception:  # noqa: BLE001
        u_b = None
    if u_b:
        for u in usuarios:
            if _calza(u["n"], u_b):
                out[u["n"]].append({"tipo": "Secreto KV", "nombre": f"faceops/maquinas/{e['nombre']}", "origen": "HashiCorp Vault (nodo-vault)"})
    return out
