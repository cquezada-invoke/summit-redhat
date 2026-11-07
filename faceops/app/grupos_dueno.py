"""Grupos dueños por sección del menú (Servidores, Storage, Hipervisores, Redes, Bases de datos…). Reemplazan a las «áreas».

· Cada sección tiene un grupo dueño por cliente: su rol «Dueño de <sección>» (personas responsables, usuarios del sistema),
  sus miembros (funcionarios que participan) y un correo de grupo (lista de distribución).
· Las aprobaciones de un ticket van al grupo dueño de la sección de la automatización: el aviso se envía al correo del grupo,
  no a una persona. Cualquier miembro puede TOMAR la aprobación y responderla; queda registrado quién la tomó, cuándo y qué
  respondió, y con eso se mide la participación de cada persona en cada grupo.
· Una persona puede estar en varios grupos. Sin grupo = dirección o administración: ve todo y no aprueba por grupo."""
from .areas import NOMBRES_SECCION, SECCIONES, _DE_AREA
from .bd import ahora, auditar, con, ej, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS grupos_dueno (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, seccion TEXT NOT NULL, correo TEXT DEFAULT '', descripcion TEXT DEFAULT '',
  actualizado TEXT, UNIQUE(cliente_id, seccion));
CREATE TABLE IF NOT EXISTS grupo_miembros (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, seccion TEXT NOT NULL, usuario_id INTEGER NOT NULL, rol TEXT DEFAULT 'miembro',
  desde TEXT, UNIQUE(cliente_id, seccion, usuario_id));
CREATE INDEX IF NOT EXISTS grupo_miembros_u ON grupo_miembros(usuario_id);
CREATE TABLE IF NOT EXISTS avisos_aprobacion (
  id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL, etapa INTEGER, seccion TEXT, para TEXT, resultado TEXT, fecha TEXT);
"""
ROLES = [("dueño", "Dueño"), ("miembro", "Miembro")]
CLAVES = [k for k, _, _ in SECCIONES]
# laboratorio: de qué grupos es cada persona (rol)
LAB = {"infra.lider": [("servidores", "dueño"), ("hipervisores", "dueño"), ("storage", "dueño"), ("infraestructura", "dueño"), ("so", "dueño")],
       "arq.lider": [("arquitectura", "dueño"), ("aplicaciones", "dueño"), ("contenedores", "dueño")],
       "ms.lider": [("monitoreo", "dueño"), ("disponibilidad", "dueño"), ("respaldos", "dueño"), ("redes", "dueño"), ("seguridad", "dueño"),
                    ("plataformas", "dueño"), ("bd", "dueño"), ("nube", "dueño"), ("integraciones", "dueño")],
       "ms.operador": [("monitoreo", "miembro"), ("servidores", "miembro"), ("storage", "miembro"), ("plataformas", "miembro")],
       "ms.lector": [("monitoreo", "miembro")],
       "proyecto.lider": [("devsecops", "dueño"), ("aplicaciones", "miembro")]}


def clave_de(area):
    """Sección (grupo) para una clave antigua de área o una sección."""
    return area if area in NOMBRES_SECCION else _DE_AREA.get(area or "", area or "")


def nombre(sec):
    return NOMBRES_SECCION.get(clave_de(sec), sec or "Sin grupo")


def iniciar():
    con().executescript(ESQUEMA)
    from .bd import ajuste
    if ajuste("grupos_dueno_v1"):
        return
    for c in q("SELECT id FROM clientes"):
        for k in CLAVES:
            ej("INSERT OR IGNORE INTO grupos_dueno(cliente_id, seccion, actualizado) VALUES (?,?,?)", c["id"], k, ahora())
        # quienes tenían área pasan a ser del grupo de su sección (dueños si eran aprobadores)
        for p in q("SELECT p.usuario_id, p.area, p.aprobador, u.usuario FROM perfiles p JOIN usuarios u ON u.id=p.usuario_id "
                   "WHERE p.cliente_id=? AND COALESCE(p.area, '')!=''", c["id"]):
            ej("INSERT OR IGNORE INTO grupo_miembros(cliente_id, seccion, usuario_id, rol, desde) VALUES (?,?,?,?,?)",
               c["id"], clave_de(p["area"]), p["usuario_id"], "dueño" if p["aprobador"] else "miembro", ahora())
        for usr, gs in LAB.items():
            u = q1("SELECT u.id FROM usuarios u JOIN perfiles p ON p.usuario_id=u.id AND p.cliente_id=? WHERE u.usuario=?", c["id"], usr)
            for sec, rol in gs if u else []:
                ej("INSERT INTO grupo_miembros(cliente_id, seccion, usuario_id, rol, desde) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(cliente_id, seccion, usuario_id) DO UPDATE SET rol=excluded.rol", c["id"], sec, u["id"], rol, ahora())
    # etapas abiertas con clave de área antigua → su grupo
    for x in q("SELECT DISTINCT area FROM ticket_etapas"):
        if x["area"] and x["area"] not in NOMBRES_SECCION:
            ej("UPDATE ticket_etapas SET area=? WHERE area=?", clave_de(x["area"]), x["area"])
    ajuste("grupos_dueno_v1", ahora())


def _cols():
    cs = [r["name"] for r in q("PRAGMA table_info(ticket_etapas)")]
    for c, t in (("tomado_por", "TEXT DEFAULT ''"), ("tomado", "TEXT")):
        if c not in cs:
            ej(f"ALTER TABLE ticket_etapas ADD COLUMN {c} {t}")


# ------------------------------------------------------------------ consulta
def grupos(cliente_id):
    """Todos los grupos del cliente con su rol dueño, miembros y correo, en el orden del menú."""
    iniciar()
    gs = {g["seccion"]: dict(g) for g in q("SELECT * FROM grupos_dueno WHERE cliente_id=?", cliente_id)}
    ms = q("SELECT m.seccion, m.rol, m.desde, u.id, u.usuario, u.nombre, u.correo, u.activo FROM grupo_miembros m JOIN usuarios u ON u.id=m.usuario_id "
           "WHERE m.cliente_id=? ORDER BY m.rol DESC, u.nombre", cliente_id)
    out = []
    for k, n, d in SECCIONES:
        g = gs.get(k) or {"cliente_id": cliente_id, "seccion": k, "correo": "", "descripcion": ""}
        mm = [m for m in ms if m["seccion"] == k]
        out.append(dict(g, clave=k, nombre=n, explica=d, rol=f"Dueño de {n}", duenos=[m for m in mm if m["rol"] == "dueño"],
                        miembros=[m for m in mm if m["rol"] != "dueño"], todos=mm))
    return out


def grupo(cliente_id, sec):
    return next((g for g in grupos(cliente_id) if g["clave"] == clave_de(sec)), None)


def de_usuario(usuario, cliente_id):
    """{sección: rol} de una persona."""
    iniciar()
    return {r["seccion"]: r["rol"] for r in q("SELECT seccion, rol FROM grupo_miembros WHERE cliente_id=? AND usuario_id=?", cliente_id, usuario["id"])}


def es_miembro(usuario, cliente_id, sec):
    return clave_de(sec) in de_usuario(usuario, cliente_id)


# ------------------------------------------------------------------ administración
def guardar_grupo(cliente_id, sec, correo, descripcion, quien):
    iniciar()
    sec = clave_de(sec)
    if sec not in NOMBRES_SECCION:
        raise ValueError("Grupo desconocido")
    correo = (correo or "").strip()[:160]
    for c_ in [x.strip() for x in correo.split(",") if x.strip()]:
        if "@" not in c_ or " " in c_:
            raise ValueError(f"Correo de grupo no válido: {c_}")
    ej("INSERT INTO grupos_dueno(cliente_id, seccion, correo, descripcion, actualizado) VALUES (?,?,?,?,?) ON CONFLICT(cliente_id, seccion) "
       "DO UPDATE SET correo=excluded.correo, descripcion=excluded.descripcion, actualizado=excluded.actualizado", cliente_id, sec, correo,
       (descripcion or "").strip()[:300], ahora())
    auditar(quien, cliente_id, "editar grupo dueño", nombre(sec), {"correo": correo})


def poner_miembro(cliente_id, sec, usuario_id, rol, quien):
    iniciar()
    sec = clave_de(sec)
    if sec not in NOMBRES_SECCION or rol not in dict(ROLES):
        raise ValueError("Grupo o rol no válido")
    u = q1("SELECT u.* FROM usuarios u JOIN perfiles p ON p.usuario_id=u.id AND p.cliente_id=? WHERE u.id=?", cliente_id, usuario_id)
    if not u:
        raise ValueError("La persona no es usuaria de este cliente")
    ej("INSERT INTO grupo_miembros(cliente_id, seccion, usuario_id, rol, desde) VALUES (?,?,?,?,?) ON CONFLICT(cliente_id, seccion, usuario_id) "
       "DO UPDATE SET rol=excluded.rol", cliente_id, sec, usuario_id, rol, ahora())
    auditar(quien, cliente_id, "agregar a grupo dueño", nombre(sec), {"usuario": u["usuario"], "rol": rol})


def quitar_miembro(cliente_id, sec, usuario_id, quien):
    sec = clave_de(sec)
    u = q1("SELECT usuario FROM usuarios WHERE id=?", usuario_id) or {}
    ej("DELETE FROM grupo_miembros WHERE cliente_id=? AND seccion=? AND usuario_id=?", cliente_id, sec, usuario_id)
    auditar(quien, cliente_id, "quitar de grupo dueño", nombre(sec), {"usuario": u.get("usuario")})


def fijar_grupos_usuario(cliente_id, usuario_id, pares, quien):
    """pares = {sección: rol}; reemplaza los grupos de la persona."""
    iniciar()
    act = {r["seccion"] for r in q("SELECT seccion FROM grupo_miembros WHERE cliente_id=? AND usuario_id=?", cliente_id, usuario_id)}
    for sec in act - set(pares):
        quitar_miembro(cliente_id, sec, usuario_id, quien)
    for sec, rol in pares.items():
        poner_miembro(cliente_id, sec, usuario_id, rol, quien)


# ------------------------------------------------------------------ aprobaciones
def avisar(ticket, etapa, sec):
    """Aviso de aprobación al correo del grupo dueño (no a una persona). Si el grupo no tiene correo, a sus miembros."""
    try:
        from .correo import enviar
        g = grupo(ticket["cliente_id"], sec) or {}
        para = g.get("correo") or ", ".join(m["correo"] for m in g.get("todos", []) if m.get("correo") and m.get("activo"))
        auto = q1("SELECT nombre FROM automatizaciones WHERE id=?", ticket["automatizacion_id"]) or {}
        cuerpo = (f"El ticket {ticket['numero']} («{auto.get('nombre', '')}») espera la aprobación del grupo {g.get('rol', nombre(sec))}.\n\n"
                  f"{ticket.get('detalle') or ''}\n\nCualquier miembro del grupo puede tomarla y responderla en FaceOps: "
                  f"Tickets › {ticket['numero']} › Tomar la aprobación. Queda registrado quién la tomó y su respuesta.")
        r = enviar(para, f"[FaceOps] Aprobación pendiente · {ticket['numero']} · {g.get('nombre', nombre(sec))}", cuerpo) if para else "el grupo no tiene correo ni miembros con correo"
        ej("INSERT INTO avisos_aprobacion(ticket_id, etapa, seccion, para, resultado, fecha) VALUES (?,?,?,?,?,?)",
           ticket["id"], etapa, clave_de(sec), para, r, ahora())
    except Exception:  # noqa: BLE001   el aviso nunca detiene el ticket
        pass


def avisos(ticket_id):
    iniciar()
    return q("SELECT * FROM avisos_aprobacion WHERE ticket_id=? ORDER BY id", ticket_id)


def tomar(usuario, ticket_id, etapa=None, forzar=False):
    """Un miembro toma la etapa pendiente de su grupo. Si ya la tomó otro, solo se reasigna con forzar."""
    iniciar(); _cols()
    from .aprobacion_areas import pendientes_de
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    if not t or t["estado"] != "pendiente_aprobacion":
        raise ValueError("El ticket no espera aprobación")
    mios = de_usuario(usuario, t["cliente_id"])
    pend = [x for x in pendientes_de(t) if (etapa is None or x["etapa"] == etapa) and (usuario.get("es_admin") or x["area"] in mios)]
    if not pend:
        raise ValueError("No eres miembro del grupo dueño que debe aprobar este ticket")
    x = pend[0]
    if x.get("tomado_por") and x["tomado_por"] != usuario["usuario"] and not forzar:
        raise ValueError(f"Ya la tomó {x['tomado_por']}")
    ej("UPDATE ticket_etapas SET tomado_por=?, tomado=? WHERE ticket_id=? AND etapa=?", usuario["usuario"], ahora(), ticket_id, x["etapa"])
    auditar(usuario["usuario"], t["cliente_id"], "tomar aprobación", t["numero"], {"grupo": nombre(x["area"]), "etapa": x["etapa"],
                                                                                   "antes": x.get("tomado_por") or ""})
    return x


def participacion(cliente_id, dias=90):
    """Participación en las aprobaciones por grupo y persona: tomadas, aprobadas, rechazadas, horas hasta tomar y hasta responder."""
    iniciar(); _cols()
    from .aprobacion_areas import _dt
    rs = q("SELECT e.* FROM ticket_etapas e JOIN tickets t ON t.id=e.ticket_id WHERE t.cliente_id=? AND e.inicio >= datetime('now', ?)",
           cliente_id, f"-{int(dias)} days")
    gs, per = {}, {}
    for x in rs:
        sec = clave_de(x["area"])
        g = gs.setdefault(sec, {"clave": sec, "nombre": nombre(sec), "total": 0, "pendientes": 0, "resueltas": 0, "sin_tomar": 0, "personas": {}})
        g["total"] += 1
        if x["estado"] == "pendiente":
            g["pendientes"] += 1
            g["sin_tomar"] += 0 if x.get("tomado_por") else 1
            continue
        g["resueltas"] += 1
        quien = x.get("tomado_por") or x.get("quien") or "—"
        p = g["personas"].setdefault(quien, {"usuario": quien, "aprobadas": 0, "rechazadas": 0, "h_tomar": [], "h_resp": []})
        p["aprobadas" if x["estado"] == "aprobada" else "rechazadas"] += 1
        a, b, tm = _dt(x["inicio"]), _dt(x["fin"]), _dt(x.get("tomado"))
        if a and tm:
            p["h_tomar"].append((tm - a).total_seconds() / 3600)
        if a and b:
            p["h_resp"].append((b - a).total_seconds() / 3600)
    nombres = {u["usuario"]: u["nombre"] for u in q("SELECT usuario, nombre FROM usuarios")}
    out = []
    for k, n, _ in SECCIONES:
        g = gs.get(k)
        miembros = (grupo(cliente_id, k) or {}).get("todos", [])
        if not g and not miembros:
            continue
        g = g or {"clave": k, "nombre": n, "total": 0, "pendientes": 0, "resueltas": 0, "sin_tomar": 0, "personas": {}}
        for m in miembros:
            g["personas"].setdefault(m["usuario"], {"usuario": m["usuario"], "aprobadas": 0, "rechazadas": 0, "h_tomar": [], "h_resp": []})
        pl = []
        for p in g["personas"].values():
            n_ = p["aprobadas"] + p["rechazadas"]
            pl.append(dict(p, nombre=nombres.get(p["usuario"], p["usuario"]), total=n_,
                           pct=round(100 * n_ / g["resueltas"]) if g["resueltas"] else 0,
                           h_tomar=round(sum(p["h_tomar"]) / len(p["h_tomar"]), 1) if p["h_tomar"] else None,
                           h_resp=round(sum(p["h_resp"]) / len(p["h_resp"]), 1) if p["h_resp"] else None,
                           rol=next((m["rol"] for m in miembros if m["usuario"] == p["usuario"]), "fuera del grupo")))
        g["personas"] = sorted(pl, key=lambda p: (-p["total"], p["nombre"]))
        g["n_miembros"] = len(miembros)
        g["participan"] = sum(1 for p in pl if p["total"])
        out.append(g)
    return out
