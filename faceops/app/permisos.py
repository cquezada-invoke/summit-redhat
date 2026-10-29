"""Quién puede ver y hacer qué. Un perfil = usuario × cliente × segmentos de negocio × ambientes × modo (lectura | escritura)
× aprobador. Los administradores de Invoke (es_admin) ven todos los clientes, segmentos y ambientes con escritura."""
from .bd import j, q, q1


def perfiles(usuario):
    if usuario["es_admin"]:
        return {c["id"]: {"cliente_id": c["id"], "segmentos": ["*"], "ambientes": ["*"], "modo": "escritura", "aprobador": 1}
                for c in q("SELECT id FROM clientes WHERE activo=1")}
    return {p["cliente_id"]: dict(p, segmentos=j(p["segmentos"], []), ambientes=j(p.get("ambientes"), ["*"]) or ["*"]) for p in
            q("SELECT p.* FROM perfiles p JOIN clientes c ON c.id=p.cliente_id WHERE p.usuario_id=? AND c.activo=1", usuario["id"])}


def clientes_visibles(usuario):
    ids = list(perfiles(usuario))
    if not ids:
        return []
    return q(f"SELECT * FROM clientes WHERE id IN ({','.join('?' * len(ids))}) ORDER BY nombre", *ids)


def perfil(usuario, cliente_id):
    return perfiles(usuario).get(cliente_id)


def segmentos_visibles(usuario, cliente_id):
    p = perfil(usuario, cliente_id)
    if not p:
        return []
    todos = q("SELECT * FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", cliente_id)
    return todos if "*" in p["segmentos"] else [s for s in todos if s["nombre"] in p["segmentos"]]


def puede_ver_segmento(usuario, cliente_id, segmento):
    p = perfil(usuario, cliente_id)
    return bool(p) and ("*" in p["segmentos"] or segmento in p["segmentos"])


def ambiente_permitido(p, ambiente):
    amb = (p or {}).get("ambientes") or ["*"]
    return "*" in amb or (ambiente or "") in amb


def equipo_visible(p, e, segmentos_del_equipo):
    """Visible si el perfil cubre alguno de sus segmentos de negocio (o todos) y su ambiente."""
    if not p or not ambiente_permitido(p, e.get("ambiente")):
        return False
    return "*" in p["segmentos"] or bool(set(segmentos_del_equipo) & set(p["segmentos"]))


def puede_ejecutar(usuario, cliente_id, automatizacion):
    """Lectura: cualquiera con perfil. Escritura: solo perfiles en modo escritura."""
    p = perfil(usuario, cliente_id)
    if not p:
        return False, "No tienes perfil en este cliente"
    if automatizacion["tipo"] == "escritura" and p["modo"] != "escritura":
        return False, "Tu perfil es de solo lectura: esta automatización cambia el entorno"
    return True, ""


def puede_aprobar(usuario, cliente_id):
    p = perfil(usuario, cliente_id)
    return bool(p and p.get("aprobador"))


def equipos_de_segmento(cliente_id, segmento):
    """Equipos de un segmento de negocio; «*» = todo el parque del cliente."""
    from .inventario import cumple_regla
    if segmento == "*":
        return q("SELECT * FROM equipos WHERE cliente_id=?", cliente_id)
    s = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", cliente_id, segmento)
    if not s:
        return []
    regla = j(s["regla"], {})
    return [e for e in q("SELECT * FROM equipos WHERE cliente_id=?", cliente_id) if cumple_regla(e, regla)]
