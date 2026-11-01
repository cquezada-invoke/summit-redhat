"""Áreas en las aprobaciones y en lo que ve cada usuario. Las áreas son las del catálogo (app/areas.py: Infraestructura,
Redes, Seguridad y cumplimiento, DevSecOps…): cada automatización está hecha para un área.

· Cada persona pertenece a un área (en su perfil del cliente). Sin área = ve todas (dirección, administradores).
· Cada automatización tiene su área y una cadena de aprobación por áreas (etapa 1, etapa 2…). Sin cadena: aprueba su área.
· Las aprobaciones no vencen: el ticket queda pendiente hasta que alguien de esa área lo aprueba o lo rechaza.
· El tiempo de cada etapa se acumula al área de esa etapa (ticket_etapas).
· Un usuario con área ve solo los tickets de su área: los de automatizaciones de su área, los que pasan por su área en la
  cadena de aprobación y los que pidió él."""
from datetime import datetime, timezone

from .areas import AREAS, NOMBRES, area_de as _area_catalogo
from .bd import ahora, con, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ticket_etapas (
  id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL, etapa INTEGER NOT NULL, total INTEGER DEFAULT 1, area TEXT DEFAULT '',
  inicio TEXT, fin TEXT, estado TEXT DEFAULT 'pendiente', quien TEXT DEFAULT '', aap_aprobacion_id INTEGER,
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
    return NOMBRES.get(k, k or "Sin área")


def area_de(auto):
    """Clave del área para la que está hecha la automatización (la misma del catálogo)."""
    return _area_catalogo(dict(auto or {}))


def etapas_de(auto):
    return [x for x in j((auto or {}).get("aprobacion_etapas"), []) if x] or [area_de(auto)]


def area_usuario(usuario, cliente_id):
    """'' = ve todas las áreas (administradores de Invoke y perfiles sin área)."""
    if usuario.get("es_admin"):
        return ""
    p = q1("SELECT area FROM perfiles WHERE usuario_id=? AND cliente_id=?", usuario["id"], cliente_id)
    return (p or {}).get("area") or ""


# ------------------------------------------------------------------ etapas de cada ticket
def _ahora_dt():
    return datetime.now(timezone.utc)


def _dt(s):
    try:
        d = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def registrar(ticket_id, etapa, total, area, inicio=None, aap_id=None):
    if q1("SELECT 1 FROM ticket_etapas WHERE ticket_id=? AND etapa=?", ticket_id, etapa):
        ej("UPDATE ticket_etapas SET aap_aprobacion_id=COALESCE(?, aap_aprobacion_id), total=? WHERE ticket_id=? AND etapa=?", aap_id, total, ticket_id, etapa)
        return
    ej("INSERT INTO ticket_etapas(ticket_id, etapa, total, area, inicio, aap_aprobacion_id) VALUES (?,?,?,?,?,?)",
       ticket_id, etapa, total, area, inicio or ahora(), aap_id)


def cerrar(ticket_id, etapa, estado, quien="", fin=None):
    ej("UPDATE ticket_etapas SET estado=?, fin=?, quien=? WHERE ticket_id=? AND etapa=? AND estado='pendiente'", estado, fin or ahora(), quien, ticket_id, etapa)


def etapas(t, auto=None):
    """Etapas del ticket; si es anterior a este registro, se arma su etapa 1 desde la creación del ticket."""
    rs = q("SELECT * FROM ticket_etapas WHERE ticket_id=? ORDER BY etapa", t["id"])
    if rs or t["estado"] != "pendiente_aprobacion":
        return rs
    auto = auto or q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"]) or {}
    cad = etapas_de(auto)
    registrar(t["id"], 1, len(cad), cad[0], t["creado"])
    return q("SELECT * FROM ticket_etapas WHERE ticket_id=? ORDER BY etapa", t["id"])


def actual(t, auto=None):
    return next((x for x in etapas(t, auto) if x["estado"] == "pendiente"), None)


def dias(x):
    a, b = _dt(x["inicio"]), _dt(x["fin"]) if x.get("fin") else _ahora_dt()
    return max(0.0, (b - a).total_seconds() / 86400) if a and b else 0.0


def puede_aprobar(usuario, cliente_id, t, auto=None):
    """Aprobador del cliente y de la misma área que la etapa pendiente (los administradores, cualquiera)."""
    if usuario.get("es_admin"):
        return True
    p = q1("SELECT aprobador, area FROM perfiles WHERE usuario_id=? AND cliente_id=?", usuario["id"], cliente_id)
    if not p or not p["aprobador"]:
        return False
    et = actual(t, auto)
    return not p["area"] or (et is not None and et["area"] == p["area"])


# ------------------------------------------------------------------ visibilidad
def mapa_autos(cliente_id):
    """{automatización: (su área, áreas de su cadena)}"""
    return {a["id"]: (area_de(a), etapas_de(a)) for a in q("SELECT * FROM automatizaciones WHERE cliente_id=?", cliente_id)}


def filtrar_tickets(ts, usuario, cliente_id):
    """Los tickets que ve el usuario según su área (sin área: todos)."""
    area = area_usuario(usuario, cliente_id)
    if not area:
        return ts
    m = mapa_autos(cliente_id)
    en_etapas = {r["ticket_id"] for r in q("SELECT DISTINCT ticket_id FROM ticket_etapas WHERE area=?", area)}
    return [t for t in ts if t.get("usuario_id") == usuario["id"] or t["id"] in en_etapas
            or m.get(t["automatizacion_id"], ("", []))[0] == area or area in m.get(t["automatizacion_id"], ("", []))[1]]


def pendientes(cliente_id, ts):
    """Tickets pendientes de aprobación (de los visibles) con su etapa actual y los días acumulados por área."""
    autos = {a["id"]: a for a in q("SELECT * FROM automatizaciones WHERE cliente_id=?", cliente_id)}
    out = []
    for t in ts:
        if t["estado"] != "pendiente_aprobacion":
            continue
        a = autos.get(t["automatizacion_id"]) or {}
        es = etapas(t, a)
        act = next((x for x in es if x["estado"] == "pendiente"), None)
        por_area = {}
        for x in es:
            por_area[x["area"]] = por_area.get(x["area"], 0.0) + dias(x)
        out.append({"t": t, "auto": a.get("nombre") or "—", "area_auto": area_de(a), "etapas": es, "actual": act,
                    "area": act["area"] if act else area_de(a), "etapa_n": act["etapa"] if act else 1, "total": (act or {}).get("total") or len(es) or 1,
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
        if p["actual"]:
            x = ag.setdefault(p["area"], {"area": p["area"], "dias": 0.0, "tickets": 0, "esperando": 0, "d1": 0, "d3": 0, "d7": 0})
            x["esperando"] += 1
            de = p["dias_etapa"]
            if de >= 1:
                x["d1" if de < 3 else ("d3" if de < 7 else "d7")] += 1
    out = sorted(ag.values(), key=lambda x: -x["dias"])
    for x in out:
        x["dias"] = int(x["dias"])
    return out
