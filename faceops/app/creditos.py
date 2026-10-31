"""Bolsa de créditos del cliente. Todo se mide en créditos (no en dinero):
  · el cliente compra una bolsa (compra, +)
  · cada mes se descuentan los cargos fijos: la base del producto y el equipo de Managed Service asignado (−)
  · cada ejecución exitosa descuenta su factor de beneficio × las máquinas a las que llegó (−)
Sin créditos disponibles, FaceOps no lanza automatizaciones."""
from datetime import datetime, timezone

from .bd import ahora, con, ej, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS creditos_mov (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, fecha TEXT, tipo TEXT, creditos REAL, detalle TEXT DEFAULT '',
  ticket_id INTEGER, periodo TEXT DEFAULT '', usuario TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS creditos_mov_cliente ON creditos_mov(cliente_id, fecha);
CREATE TABLE IF NOT EXISTS tareas_ajuste (codigo TEXT PRIMARY KEY, manual REAL, usuario TEXT, fecha TEXT);
"""
TIPOS = {"compra": "Compra de créditos", "base": "Base del producto", "managed_service": "Equipo de Managed Service",
         "ejecucion": "Ejecución", "ajuste": "Ajuste"}


class SinCreditos(PermissionError):
    pass


def iniciar():
    con().executescript(ESQUEMA)


def periodo():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def cobrar_mensual(cliente):
    """Cargos fijos del mes (una vez por mes): base del producto y equipo de Managed Service."""
    p = periodo()
    for tipo, campo in (("base", "cr_base"), ("managed_service", "cr_ms")):
        monto = float(cliente.get(campo) or 0)
        if monto and not q1("SELECT 1 FROM creditos_mov WHERE cliente_id=? AND tipo=? AND periodo=?", cliente["id"], tipo, p):
            ej("INSERT INTO creditos_mov(cliente_id, fecha, tipo, creditos, detalle, periodo, usuario) VALUES (?,?,?,?,?,?,?)",
               cliente["id"], ahora(), tipo, -monto, f"{TIPOS[tipo]} · {p}", p, "sistema")


def saldo(cliente_id):
    return round((q1("SELECT SUM(creditos) s FROM creditos_mov WHERE cliente_id=?", cliente_id) or {}).get("s") or 0, 2)


def reservado(cliente_id):
    """Créditos estimados de los tickets que aún no terminan (para no lanzar más de lo que alcanza)."""
    from .economia import estimar
    total = 0.0
    for t in q("SELECT t.*, a.nombre_aap, a.tipo, a.usa_limite, a.minutos_manual, a.categoria, a.campos FROM tickets t "
               "JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.estado IN "
               "('en_cola','en_ejecucion','pendiente_aprobacion')", cliente_id):
        import json
        try:
            p = json.loads(t["parametros"] or "{}")
        except ValueError:
            p = {}
        total += estimar(t, p, t.get("limite") or "")["total"]
    return round(total, 2)


def estado(cliente):
    cobrar_mensual(cliente)
    s, r = saldo(cliente["id"]), reservado(cliente["id"])
    return {"saldo": s, "reservado": r, "disponible": round(s - r, 2), "cr_base": float(cliente.get("cr_base") or 0),
            "cr_ms": float(cliente.get("cr_ms") or 0)}


def validar(cliente, necesarios):
    """Antes de lanzar: alcanza el saldo disponible para esta ejecución."""
    e = estado(cliente)
    if e["disponible"] <= 0 or necesarios > e["disponible"]:
        raise SinCreditos(f"Sin créditos suficientes: esta ejecución usa {necesarios:g} y quedan {max(e['disponible'], 0):g} disponibles. "
                          "Pide una recarga de créditos a Invoke.")


def registrar_ejecucion(t):
    """Al liquidar el ticket: descuenta sus créditos (idempotente; si se re-liquida, se ajusta el monto)."""
    monto = float(t.get("creditos") or 0) if t.get("estado") == "exitoso" else 0.0
    ya = q1("SELECT id FROM creditos_mov WHERE ticket_id=? AND tipo='ejecucion'", t["id"])
    if ya:
        ej("UPDATE creditos_mov SET creditos=? WHERE id=?", -monto, ya["id"])
    elif monto:
        ej("INSERT INTO creditos_mov(cliente_id, fecha, tipo, creditos, detalle, ticket_id, periodo, usuario) VALUES (?,?,?,?,?,?,?,?)",
           t["cliente_id"], t.get("fin") or ahora(), "ejecucion", -monto, f"{t['numero']}", t["id"], (t.get("creado") or "")[:7], "sistema")


def cargar(cliente_id, creditos, detalle, usuario, tipo="compra"):
    ej("INSERT INTO creditos_mov(cliente_id, fecha, tipo, creditos, detalle, periodo, usuario) VALUES (?,?,?,?,?,?,?)",
       cliente_id, ahora(), tipo, float(creditos), detalle, periodo(), usuario)


def movimientos(cliente_id, limite=200, mes=None):
    sql = "SELECT m.*, t.numero, a.nombre auto FROM creditos_mov m LEFT JOIN tickets t ON t.id=m.ticket_id " \
          "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE m.cliente_id=?"
    args = [cliente_id]
    if mes:
        sql += " AND m.fecha LIKE ?"
        args.append(mes + "%")
    return q(sql + " ORDER BY m.fecha DESC, m.id DESC LIMIT ?", *args, limite)
