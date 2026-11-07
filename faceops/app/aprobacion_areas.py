"""Áreas en las aprobaciones y en lo que ve cada usuario. Las áreas son las del catálogo (app/areas.py: Infraestructura,
Redes, Seguridad y cumplimiento, DevSecOps…): cada automatización está hecha para un área.

· Cada persona pertenece a un área (en su perfil del cliente). Sin área = ve todas (dirección, administradores).
· Cada automatización tiene su área y una cadena de aprobación por áreas (etapa 1, etapa 2…). Sin cadena: aprueba su área.
· Las aprobaciones no vencen: el ticket queda pendiente hasta que alguien de esa área lo aprueba o lo rechaza.
· El tiempo de cada etapa se acumula al área de esa etapa (ticket_etapas).
· Un usuario con área ve solo los tickets de su área: los de automatizaciones de su área, los que pasan por su área en la
  cadena de aprobación y los que pidió él."""
from datetime import datetime, timezone

from .areas import NOMBRES_SECCION as NOMBRES, SECCIONES as AREAS, seccion_de as _seccion
from .bd import ahora, con, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ticket_etapas (
  id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL, etapa INTEGER NOT NULL, total INTEGER DEFAULT 1, area TEXT DEFAULT '',
  inicio TEXT, fin TEXT, estado TEXT DEFAULT 'pendiente', quien TEXT DEFAULT '', aap_aprobacion_id INTEGER, grupo INTEGER DEFAULT 1,
  UNIQUE(ticket_id, etapa));
CREATE INDEX IF NOT EXISTS ticket_etapas_t ON ticket_etapas(ticket_id);
"""
# laboratorio: área de cada persona y cadenas de más de una etapa
AREA_USUARIO = {"infra.lider": "infraestructura", "arq.lider": "arquitectura", "ms.lider": "operacion", "ms.operador": "operacion",
                "ms.lector": "operacion", "proyecto.lider": "devsecops"}
CADENAS = {"FaceOps · Nuevo segmento de negocio": ["arquitectura", "infraestructura"],
           "FaceOps · Acceso de red": ["redes", "seguridad"]}


def iniciar():
    con().executescript(ESQUEMA)
    from . import grupos_dueno as GD
    GD.iniciar()
    GD._cols()
    if "grupo" not in [r["name"] for r in q("PRAGMA table_info(ticket_etapas)")]:
        ej("ALTER TABLE ticket_etapas ADD COLUMN grupo INTEGER DEFAULT 1")
    from .bd import ajuste
    if not ajuste("areas_aprobacion_v1"):
        import json
        for a in q("SELECT id, nombre, nombre_aap FROM automatizaciones"):
            cad = CADENAS.get(a["nombre_aap"]) or (CADENAS["FaceOps · Acceso de red"] if "acceso de red" in (a["nombre"] or "").lower() else None)
            if cad:
                ej("UPDATE automatizaciones SET aprobacion_etapas=? WHERE id=?", json.dumps(cad), a["id"])
        for u, ar in AREA_USUARIO.items():
            ej("UPDATE perfiles SET area=? WHERE usuario_id=(SELECT id FROM usuarios WHERE usuario=?) AND COALESCE(area, '')=''", ar, u)
        # las aprobaciones ya no vencen: lo vencido vuelve a quedar pendiente
        ej("UPDATE tickets SET estado='pendiente_aprobacion', aap_aprobacion_id=NULL, fin=NULL WHERE estado='vencido'")
        ej("UPDATE deudas SET estado='esperando', cerrada=NULL WHERE estado='vencida'")
        ajuste("areas_aprobacion_v1", ahora())
    if not ajuste("areas_aprobacion_v2"):   # laboratorio: los líderes de área aprueban lo de su área
        ej("UPDATE perfiles SET aprobador=1 WHERE usuario_id IN (SELECT id FROM usuarios WHERE usuario IN ('infra.lider','arq.lider'))")
        ajuste("areas_aprobacion_v2", ahora())


def lista(cliente_id=None):
    return [(k, n) for k, n, _ in AREAS]


def nombre(k):
    from .grupos_dueno import clave_de
    return NOMBRES.get(clave_de(k), k or "Sin grupo")


def area_de(auto):
    """Grupo dueño que aprueba la acción: el de la sección del menú en que vive (Servidores, Storage, Redes…)."""
    return _seccion(dict(auto or {}))


UNA_APROBACION = True


def grupos_de(auto):
    """Cadena de aprobación: lista de grupos; las áreas de un grupo aprueban en paralelo y el siguiente espera a todas.
    En la base: ["redes", "seguridad"] (en serie) o [["redes", "seguridad"], "infraestructura"] (las dos primeras en paralelo)."""
    from .grupos_dueno import clave_de
    out = []
    for x in j((auto or {}).get("aprobacion_etapas"), []):
        g_ = [clave_de(y) for y in (x if isinstance(x, list) else [x]) if y]
        if g_:
            out.append(g_)
    # Por ahora todo ticket lleva UNA sola aprobación: la del área dueña de la automatización (o la primera configurada).
    # Más etapas serán parte de la automatización misma cuando se definan; la cadena guardada se conserva.
    return [[out[0][0]]] if UNA_APROBACION and out else (out or [[area_de(auto)]])


def etapas_de(auto):
    """Todas las áreas que aprueban, en orden."""
    return [a for g_ in grupos_de(auto) for a in g_]


def grupos_usuario(usuario, cliente_id):
    """Grupos dueños de la persona (vacío = ve todo: administradores de Invoke y dirección)."""
    if usuario.get("es_admin"):
        return set()
    from .grupos_dueno import de_usuario
    return set(de_usuario(usuario, cliente_id))


def area_usuario(usuario, cliente_id):
    """'' = ve todos los grupos; si no, el primero de sus grupos (para mostrar)."""
    gs = grupos_usuario(usuario, cliente_id)
    return sorted(gs)[0] if gs else ""


# ------------------------------------------------------------------ etapas de cada ticket
def _ahora_dt():
    return datetime.now(timezone.utc)


def _dt(s):
    try:
        d = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def registrar(ticket_id, etapa, total, area, inicio=None, aap_id=None, grupo=1):
    from .grupos_dueno import avisar, clave_de
    area = clave_de(area)
    if q1("SELECT 1 FROM ticket_etapas WHERE ticket_id=? AND etapa=?", ticket_id, etapa):
        ej("UPDATE ticket_etapas SET aap_aprobacion_id=COALESCE(?, aap_aprobacion_id), total=? WHERE ticket_id=? AND etapa=?", aap_id, total, ticket_id, etapa)
        return
    ej("INSERT INTO ticket_etapas(ticket_id, etapa, total, area, inicio, aap_aprobacion_id, grupo) VALUES (?,?,?,?,?,?,?)",
       ticket_id, etapa, total, area, inicio or ahora(), aap_id, grupo)
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    if t and t["estado"] == "pendiente_aprobacion":   # aviso al correo del grupo dueño, no a una persona
        avisar(t, etapa, area)


def abrir_grupo(t, auto, gi, inicio=None):
    """Registra las etapas del grupo gi (1, 2…): una por área, todas pendientes a la vez."""
    gs = grupos_de(auto)
    total = sum(len(x) for x in gs)
    base = sum(len(x) for x in gs[:gi - 1])
    for i, area in enumerate(gs[gi - 1], 1):
        registrar(t["id"], base + i, total, area, inicio, None, gi)


def avanzar(t, auto):
    """Tras aprobar una etapa llevada en FaceOps: si su grupo está completo abre el siguiente. True = no quedan etapas."""
    rs = q("SELECT * FROM ticket_etapas WHERE ticket_id=? ORDER BY etapa", t["id"])
    if any(x["estado"] == "pendiente" for x in rs):
        return False
    gi = max([x["grupo"] or 1 for x in rs] + [0])
    if gi < len(grupos_de(auto)):
        abrir_grupo(t, auto, gi + 1)
        return False
    return True


def cerrar(ticket_id, etapa, estado, quien="", fin=None):
    ej("UPDATE ticket_etapas SET estado=?, fin=?, quien=? WHERE ticket_id=? AND etapa=? AND estado='pendiente'", estado, fin or ahora(), quien, ticket_id, etapa)


def etapas(t, auto=None):
    """Etapas del ticket; si es anterior a este registro, se arma su primer grupo desde la creación del ticket."""
    rs = q("SELECT * FROM ticket_etapas WHERE ticket_id=? ORDER BY etapa", t["id"])
    if rs or t["estado"] != "pendiente_aprobacion":
        return rs
    auto = auto or q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"]) or {}
    abrir_grupo(t, auto, 1, t["creado"])
    return q("SELECT * FROM ticket_etapas WHERE ticket_id=? ORDER BY etapa", t["id"])


def actual(t, auto=None, area=None):
    """La etapa pendiente (la del área pedida, si hay varias en paralelo)."""
    pend = [x for x in etapas(t, auto) if x["estado"] == "pendiente"]
    gs = area if isinstance(area, (set, list, tuple)) else ({area} if area else set())
    return next((x for x in pend if x["area"] in gs), None) or (pend[0] if pend else None)


def pendientes_de(t, auto=None):
    return [x for x in etapas(t, auto) if x["estado"] == "pendiente"]


def dias(x):
    a, b = _dt(x["inicio"]), _dt(x["fin"]) if x.get("fin") else _ahora_dt()
    return max(0.0, (b - a).total_seconds() / 86400) if a and b else 0.0


def puede_aprobar(usuario, cliente_id, t, auto=None):
    """Cualquier miembro del grupo dueño de una etapa pendiente (los administradores, cualquiera). Quien no está en ningún
    grupo aprueba solo si su perfil es aprobador (dirección)."""
    if usuario.get("es_admin"):
        return True
    gs = grupos_usuario(usuario, cliente_id)
    if not gs:
        p = q1("SELECT aprobador FROM perfiles WHERE usuario_id=? AND cliente_id=?", usuario["id"], cliente_id)
        return bool(p and p["aprobador"])
    return any(x["area"] in gs for x in pendientes_de(t, auto))


# ------------------------------------------------------------------ visibilidad
def mapa_autos(cliente_id):
    """{acción: (su área, áreas de su cadena)}"""
    return {a["id"]: (area_de(a), etapas_de(a)) for a in q("SELECT * FROM automatizaciones WHERE cliente_id=?", cliente_id)}


def filtrar_tickets(ts, usuario, cliente_id):
    """Los tickets que ve el usuario según su área (sin área: todos)."""
    gs = grupos_usuario(usuario, cliente_id)
    if not gs:
        return ts
    m = mapa_autos(cliente_id)
    en_etapas = {r["ticket_id"] for r in q("SELECT DISTINCT ticket_id, area FROM ticket_etapas") if r["area"] in gs}
    return [t for t in ts if t.get("usuario_id") == usuario["id"] or t["id"] in en_etapas
            or m.get(t["automatizacion_id"], ("", []))[0] in gs or gs & set(m.get(t["automatizacion_id"], ("", []))[1])]


def pendientes(cliente_id, ts):
    """Tickets pendientes de aprobación (de los visibles) con su etapa actual y los días acumulados por área."""
    autos = {a["id"]: a for a in q("SELECT * FROM automatizaciones WHERE cliente_id=?", cliente_id)}
    out = []
    for t in ts:
        if t["estado"] != "pendiente_aprobacion":
            continue
        a = autos.get(t["automatizacion_id"]) or {}
        es = etapas(t, a)
        pend_ = [x for x in es if x["estado"] == "pendiente"]
        act = pend_[0] if pend_ else None
        por_area = {}
        for x in es:
            por_area[x["area"]] = por_area.get(x["area"], 0.0) + dias(x)
        out.append({"t": t, "auto": a.get("nombre") or "—", "area_auto": area_de(a), "etapas": es, "actual": act,
                    "area": act["area"] if act else area_de(a), "areas_pend": [x["area"] for x in pend_], "etapa_n": act["etapa"] if act else 1, "total": (act or {}).get("total") or len(es) or 1,
                    "dias": dias({"inicio": t["creado"]}), "dias_etapa": dias(act) if act else 0.0, "por_area": por_area})
    out.sort(key=lambda x: -x["dias"])
    return out


def por_area(pend):
    """Días acumulados de pendiente por área (cada etapa suma al área que la tuvo) y tickets que esperan en cada una."""
    ag = {}
    for p in pend:
        for ar, d in p["por_area"].items():
            x = ag.setdefault(ar, {"area": ar, "dias": 0.0, "tickets": 0, "esperando": 0, "d1": 0, "d3": 0, "d7": 0})
            x["dias"] += d
            x["tickets"] += 1
        for x_ in [e_ for e_ in p["etapas"] if e_["estado"] == "pendiente"]:
            x = ag.setdefault(x_["area"], {"area": x_["area"], "dias": 0.0, "tickets": 0, "esperando": 0, "d1": 0, "d3": 0, "d7": 0})
            x["esperando"] += 1
            de = dias(x_)
            if de >= 1:
                x["d1" if de < 3 else ("d3" if de < 7 else "d7")] += 1
    out = sorted(ag.values(), key=lambda x: -x["dias"])
    for x in out:
        x["dias"] = int(x["dias"])
    return out
