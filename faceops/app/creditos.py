"""Bolsa de créditos del cliente. Todo se mide en créditos (no en dinero):
  · el cliente compra una bolsa (compra, +)
  · cada mes se descuenta la base del producto (−)
  · cada ejecución exitosa descuenta su factor de beneficio × las máquinas a las que llegó (−)
Sin créditos disponibles, FaceOps no lanza automatizaciones.
El Managed Service NO consume créditos: se contrata aparte. El primer año del contrato el cliente compra ambos (créditos y
Managed Service); desde el segundo año puede tomar el control y comprar solo créditos."""
from datetime import datetime, timezone

from .bd import ahora, con, ej, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS creditos_mov (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, fecha TEXT, tipo TEXT, creditos REAL, detalle TEXT DEFAULT '',
  ticket_id INTEGER, periodo TEXT DEFAULT '', usuario TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS creditos_mov_cliente ON creditos_mov(cliente_id, fecha);
CREATE TABLE IF NOT EXISTS tareas_ajuste (codigo TEXT PRIMARY KEY, manual REAL, usuario TEXT, fecha TEXT);
"""
TIPOS = {"compra": "Compra de créditos", "base": "Base del producto", "ejecucion": "Ejecución", "ajuste": "Ajuste"}


class SinCreditos(PermissionError):
    pass


def iniciar():
    con().executescript(ESQUEMA)
    from .bd import ajuste
    if not ajuste("ms_fuera_de_creditos"):   # 31/10: el Managed Service se contrata aparte; se anulan sus cargos en créditos
        ej("DELETE FROM creditos_mov WHERE tipo='managed_service'")
        ej("UPDATE clientes SET cr_ms=0")
        for c in q("SELECT c.id, MIN(m.fecha) f FROM clientes c JOIN creditos_mov m ON m.cliente_id=c.id AND m.tipo='compra' "
                   "WHERE COALESCE(c.contrato_inicio, '')='' GROUP BY c.id"):   # laboratorio: contrato desde la primera compra, año 1 con MS
            fin = _mas_un_anio(c["f"])
            if fin:
                ej("UPDATE clientes SET contrato_inicio=?, ms_hasta=? WHERE id=?", c["f"][:10], fin.date().isoformat(), c["id"])
        ajuste("ms_fuera_de_creditos", ahora())


def periodo():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def cobrar_mensual(cliente):
    """Cargo fijo del mes (una vez por mes): base del producto. El Managed Service va por fuera de los créditos."""
    p = periodo()
    for tipo, campo in (("base", "cr_base"),):
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
            "ms": managed_service(cliente)}


def _mas_un_anio(f):
    try:
        d = datetime.fromisoformat(f[:10])
        return d.replace(year=d.year + 1) if not (d.month == 2 and d.day == 29) else d.replace(year=d.year + 1, day=28)
    except (TypeError, ValueError):
        return None


def managed_service(cliente):
    """Contrato de Managed Service (aparte de los créditos): obligatorio el primer año desde el inicio del contrato."""
    ini, hasta = (cliente.get("contrato_inicio") or "")[:10], (cliente.get("ms_hasta") or "")[:10]
    fin_obl = _mas_un_anio(ini)
    hoy = datetime.now(timezone.utc).date().isoformat()
    vigente = bool(hasta) and hasta >= hoy
    return {"inicio": ini, "hasta": hasta, "obligatorio_hasta": fin_obl.date().isoformat() if fin_obl else "",
            "vigente": vigente, "en_anio_1": bool(fin_obl) and hoy < fin_obl.date().isoformat(),
            "estado": ("contratado hasta " + hasta) if vigente else ("solo créditos (desde el segundo año)" if ini and not vigente else "sin contrato registrado")}


def validar_contrato(inicio, ms_hasta):
    """Regla comercial: el primer año el cliente compra créditos y Managed Service; el MS debe cubrir al menos ese año."""
    fin = _mas_un_anio(inicio)
    if not fin:
        raise ValueError("Indica la fecha de inicio del contrato")
    if not ms_hasta or ms_hasta[:10] < fin.date().isoformat():
        raise ValueError(f"El primer año el Managed Service es obligatorio: debe cubrir al menos hasta el {fin.date().isoformat()}")


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
