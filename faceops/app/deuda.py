"""Deuda de aprobación: toda automatización que necesita aprobación y no se ejecuta queda registrada como deuda hasta que se
ejecute (o hasta que el responsable acepte formalmente el riesgo). La diferencia con un «pendiente» es la responsabilidad:
el equipo de Managed Service propuso la actividad; quien debía aprobarla (la jefatura) es quien la tiene detenida.

Estados: esperando (aprobación pedida, dentro de su plazo) · vencida (el plazo pasó sin respuesta: no se ejecutó) ·
rechazada · saldada (se aprobó y ejecutó) · riesgo aceptado (el responsable decidió no hacerla, queda registrado).
Tipos de aprobación: operación (AAP) y ticket (herramienta de tickets del cliente)."""
import json
from datetime import datetime, timezone

from .bd import ahora, auditar, con, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS deudas (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, ticket_id INTEGER, ticket_origen_id INTEGER, automatizacion_id INTEGER,
  limite TEXT DEFAULT '', segmento TEXT DEFAULT '', tipo TEXT DEFAULT 'operación', origen TEXT DEFAULT 'solicitud', motivo TEXT DEFAULT '',
  problemas TEXT DEFAULT '[]', responsable TEXT DEFAULT '', aprobadores TEXT DEFAULT '[]', estado TEXT DEFAULT 'esperando',
  creado TEXT, actualizado TEXT, cerrada TEXT, intentos INTEGER DEFAULT 1, nota TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS deudas_cliente ON deudas(cliente_id, estado);
CREATE TABLE IF NOT EXISTS aprobadores (       -- quién aprueba cada automatización o categoría (si no, los aprobadores del cliente)
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, tipo TEXT NOT NULL, clave TEXT NOT NULL, usuario_id INTEGER NOT NULL,
  UNIQUE(cliente_id, tipo, clave, usuario_id));
"""
ABIERTAS = ("esperando", "vencida", "rechazada")


def iniciar():
    con().executescript(ESQUEMA)


def _dias(desde, hasta=None):
    try:
        a = datetime.fromisoformat(desde)
        b = datetime.fromisoformat(hasta) if hasta else datetime.now(timezone.utc)
        return max(0.0, (b - a).total_seconds() / 86400)
    except (TypeError, ValueError):
        return 0.0


def aprobadores(cliente_id):
    """Quién puede aprobar en este cliente (la jefatura responsable de las aprobaciones)."""
    return [f"{r['nombre'] or r['usuario']} ({r['usuario']})" for r in q(
        "SELECT u.usuario, u.nombre FROM perfiles p JOIN usuarios u ON u.id=p.usuario_id WHERE p.cliente_id=? AND p.aprobador=1 AND u.activo=1",
        cliente_id)]


def _persona(u):
    return f"{u['nombre'] or u['usuario']} ({u['usuario']})"


def responsables(cliente_id, automatizacion_id):
    """Quién debe aprobar esta automatización: los asignados a ella; si no, a su categoría; si no, los aprobadores del cliente."""
    a = q1("SELECT id, categoria FROM automatizaciones WHERE id=?", automatizacion_id) or {}
    for tipo, clave in (("automatizacion", str(a.get("id") or "")), ("categoria", a.get("categoria") or "")):
        us = q("SELECT u.usuario, u.nombre FROM aprobadores x JOIN usuarios u ON u.id=x.usuario_id WHERE x.cliente_id=? AND x.tipo=? AND x.clave=? "
               "AND u.activo=1", cliente_id, tipo, clave) if clave else []
        if us:
            return [_persona(u) for u in us]
    return aprobadores(cliente_id)


def errores_que_previene(nombre_aap):
    """Códigos de errores conocidos cuya prevención incluye alguna tarea de esta automatización."""
    try:
        from . import kb
        from .economia import catalogo_tareas
        _, comp = catalogo_tareas()
        tareas = set(comp.get(nombre_aap, []))
        if not tareas or not kb.disponible():
            return []
        return [p["numero"] for p in kb.q("SELECT 'KE-' || lpad(id::text, 4, '0') numero, preventivas FROM problemas")
                if any(x.get("tarea") in tareas for x in (p["preventivas"] or []))]
    except Exception:  # noqa: BLE001 - sin la base de errores conocidos la deuda igual se registra
        return []


def abrir(t, tipo="operación"):
    """El ticket quedó esperando aprobación: nace (o se retoma) su registro de deuda."""
    if q1("SELECT 1 FROM deudas WHERE ticket_id=? AND tipo=?", t["id"], tipo):
        return
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"]) or {}
    motivo = t.get("motivo") or ""
    origen = "prevención" if motivo.startswith("Prevención") else ("remediación" if "remediar" in motivo.lower() else "solicitud")
    ap = responsables(t["cliente_id"], t["automatizacion_id"])
    ej("INSERT INTO deudas(cliente_id, ticket_id, ticket_origen_id, automatizacion_id, limite, segmento, tipo, origen, motivo, problemas, "
       "responsable, aprobadores, estado, creado, actualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
       t["cliente_id"], t["id"], t["id"], t["automatizacion_id"], t.get("limite") or "", t.get("segmento") or "", tipo, origen, motivo,
       json.dumps(errores_que_previene(a.get("nombre_aap", ""))), ", ".join(ap) or "Jefatura del cliente", json.dumps(ap, ensure_ascii=False),
       "esperando", t.get("creado") or ahora(), ahora())


def cerrar_ticket(t, estado_ticket):
    """El ticket terminó: aprobada (saldada), vencida o rechazada (sigue siendo deuda hasta que se ejecute)."""
    for d in q("SELECT * FROM deudas WHERE ticket_id=? AND estado='esperando'", t["id"]):
        nuevo = {"vencido": "vencida", "rechazado": "rechazada"}.get(estado_ticket, "saldada")
        ej("UPDATE deudas SET estado=?, actualizado=?, cerrada=? WHERE id=?", nuevo, ahora(), ahora() if nuevo == "saldada" else None, d["id"])


def relanzada(deuda_id, ticket):
    ej("UPDATE deudas SET ticket_id=?, estado='esperando', intentos=intentos+1, actualizado=? WHERE id=?", ticket["id"], ahora(), deuda_id)


def aceptar_riesgo(cliente_id, deuda_id, quien, nota):
    ej("UPDATE deudas SET estado='riesgo aceptado', nota=?, cerrada=?, actualizado=? WHERE id=? AND cliente_id=?",
       f"{quien}: {nota}".strip(), ahora(), ahora(), deuda_id, cliente_id)


def lista(cliente_id, solo_abiertas=False):
    filas = q("SELECT d.*, a.nombre auto, a.nombre_aap, t.numero ticket, o.numero ticket_origen, t.usuario_id solicitante_id, "
              "su.nombre solicitante, su.usuario solicitante_usuario, su.correo solicitante_correo FROM deudas d "
              "LEFT JOIN usuarios su ON su.id=(SELECT usuario_id FROM tickets WHERE id=d.ticket_id) "
              "LEFT JOIN automatizaciones a ON a.id=d.automatizacion_id LEFT JOIN tickets t ON t.id=d.ticket_id "
              "LEFT JOIN tickets o ON o.id=d.ticket_origen_id WHERE d.cliente_id=? ORDER BY d.creado DESC", cliente_id)
    nombres = {f"{r['nombre']}": f"{r['nombre']} ({r['usuario']})" for r in q("SELECT usuario, nombre FROM usuarios")}
    out = []
    for d in filas:
        if solo_abiertas and d["estado"] not in ABIERTAS:
            continue
        d["dias"] = int(_dias(d["creado"], d["cerrada"]))   # días completos: 24 h = 1 día
        d["problemas_l"] = j(d["problemas"], [])
        d["equipos"] = [x for x in (d["limite"] or "").split(":") if x]
        if d["estado"] in ABIERTAS and not (d["nota"] or "").startswith("devuelta"):   # rige la asignación vigente
            d["responsable"] = ", ".join(responsables(cliente_id, d["automatizacion_id"])) or "Jefatura del cliente"
        else:
            d["responsable"] = ", ".join(nombres.get(x.strip(), x.strip()) for x in (d["responsable"] or "").split(",")) if d["responsable"] else d["responsable"]
        out.append(d)
    _evitables(cliente_id, out)
    return out


def _evitables(cliente_id, deudas):
    """Ocurrencias de errores conocidos que esta deuda habría prevenido: mismo equipo, mientras la deuda estaba abierta."""
    try:
        from . import kb
        if not kb.disponible():
            return
        occ = kb.q("SELECT i.numero, i.equipo, i.creado, 'KE-' || lpad(p.id::text, 4, '0') problema FROM incidentes i "
                   "JOIN problemas p ON p.id=i.problema_id WHERE i.cliente_id=%s", cliente_id)
    except Exception:  # noqa: BLE001
        return
    for d in deudas:
        desde = datetime.fromisoformat(d["creado"]) if d["creado"] else None
        hasta = datetime.fromisoformat(d["cerrada"]) if d["cerrada"] else None
        d["evitables"] = [o["numero"] for o in occ if o["problema"] in d["problemas_l"] and o["equipo"] in d["equipos"]
                          and desde and o["creado"] >= desde and (not hasta or o["creado"] <= hasta)]


def sincronizar(cliente_id):
    """Toda autorización sin respuesta o vencida/rechazada queda en el control de deuda, aunque sea anterior a este registro."""
    for t in q("SELECT t.* FROM tickets t WHERE t.cliente_id=? AND t.estado IN ('pendiente_aprobacion','vencido','rechazado') "
               "AND NOT EXISTS (SELECT 1 FROM deudas d WHERE d.ticket_id=t.id)", cliente_id):
        abrir(t)
        if t["estado"] != "pendiente_aprobacion":
            cerrar_ticket(t, t["estado"])


def resumen(cliente_id):
    sincronizar(cliente_id)
    """Stoppers por responsable: actividades detenidas por aprobación, días acumulados e incidentes que se habrían evitado."""
    ds = lista(cliente_id)
    por = {}
    for d in ds:
        if d["estado"] not in ABIERTAS:
            continue
        x = por.setdefault(d["responsable"], {"responsable": d["responsable"], "abiertas": 0, "dias": 0, "vencidas": 0, "rechazadas": 0,
                                             "evitables": 0, "prevencion": 0})
        x["abiertas"] += 1
        x["dias"] += d["dias"]
        x["vencidas"] += d["estado"] == "vencida"
        x["rechazadas"] += d["estado"] == "rechazada"
        x["evitables"] += len(d.get("evitables") or [])
        x["prevencion"] += d["origen"] == "prevención"
    abiertas = [d for d in ds if d["estado"] in ABIERTAS]
    return {"abiertas": len(abiertas), "dias": int(sum(d["dias"] for d in abiertas)),
            "evitables": sum(len(d.get("evitables") or []) for d in abiertas),
            "por_responsable": sorted(por.values(), key=lambda x: -x["dias"]), "deudas": ds}


def cobertura(cliente_id, equipo, problema):
    """¿El error conocido estaba prevenido en el equipo? cubierto (con fecha) · en deuda (aprobación detenida) · sin prevención."""
    from .economia import catalogo_tareas
    _, comp = catalogo_tareas()
    tareas = {x.get("tarea") for x in (problema.get("preventivas") or [])}
    autos = [n for n, cods in comp.items() if tareas & set(cods)]
    if not autos:
        return {"estado": "sin prevención definida"}
    marcas = ",".join("?" * len(autos))
    ok = q1(f"SELECT t.numero, t.fin, a.nombre FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? "
            f"AND t.estado='exitoso' AND a.nombre_aap IN ({marcas}) AND (':' || t.limite || ':') LIKE ? ORDER BY t.fin DESC LIMIT 1",
            cliente_id, *autos, f"%:{equipo}:%")
    if ok:
        return {"estado": "cubierto", "fecha": (ok["fin"] or "")[:10], "automatizacion": ok["nombre"], "ticket": ok["numero"]}
    dd = q1(f"SELECT d.*, a.nombre auto FROM deudas d JOIN automatizaciones a ON a.id=d.automatizacion_id WHERE d.cliente_id=? "
            f"AND d.estado IN ('esperando','vencida','rechazada') AND a.nombre_aap IN ({marcas}) AND (':' || d.limite || ':') LIKE ? "
            f"ORDER BY d.creado LIMIT 1", cliente_id, *autos, f"%:{equipo}:%")
    if dd:
        return {"estado": "en deuda", "desde": (dd["creado"] or "")[:10], "responsable": dd["responsable"], "automatizacion": dd["auto"],
                "dias": int(_dias(dd["creado"])), "deuda": dd["id"]}
    return {"estado": "sin prevención aplicada", "automatizaciones": autos}


def agrupar(deudas):
    """Una fila por actividad (misma automatización sobre los mismos equipos): cuántos tickets lleva, desde cuándo,
    el estado más grave y quién la tiene en sus manos."""
    grav = {"rechazada": 3, "vencida": 2, "esperando": 1}
    g = {}
    for d in deudas:
        k = (d["automatizacion_id"], d["limite"] or "", d["estado"] in ABIERTAS)
        x = g.setdefault(k, {"auto": d["auto"], "automatizacion_id": d["automatizacion_id"], "equipos": d["equipos"], "tickets": [], "ids": [],
                             "estado": d["estado"], "dias": 0, "responsable": d["responsable"], "origen": d["origen"], "motivo": d["motivo"],
                             "problemas_l": set(), "evitables": [], "abierta": d["estado"] in ABIERTAS, "deuda": d["id"], "nota": d["nota"],
                             "solicitante": d.get("solicitante"), "en_manos": d.get("en_manos")})
        x["tickets"].append(d["ticket"] or d["ticket_origen"])
        x["ids"].append(d["ticket_id"])
        x["dias"] = max(x["dias"], d["dias"])
        if grav.get(d["estado"], 0) > grav.get(x["estado"], 0):
            x["estado"] = d["estado"]
        x["problemas_l"] |= set(d["problemas_l"])
        x["evitables"] += d.get("evitables") or []
    for x in g.values():
        x["problemas_l"] = sorted(x["problemas_l"])
    return sorted(g.values(), key=lambda x: (not x["abierta"], -x["dias"]))


def devolver(cliente_id, deuda_id, quien):
    """«Volver a solicitar»: la actividad vuelve a quien pidió el último ticket para que lo genere de nuevo; desde ahora
    está en sus manos. Se le avisa por correo."""
    from .correo import enviar
    d = q1("SELECT d.*, a.nombre auto FROM deudas d LEFT JOIN automatizaciones a ON a.id=d.automatizacion_id WHERE d.id=? AND d.cliente_id=?",
           deuda_id, cliente_id)
    t = q1("SELECT t.numero, u.nombre, u.usuario, u.correo FROM tickets t LEFT JOIN usuarios u ON u.id=t.usuario_id WHERE t.id=?", d["ticket_id"]) if d else None
    if not d or not t:
        return None
    persona = f"{t['nombre'] or t['usuario']} ({t['usuario']})"
    envio = enviar(t["correo"], f"FaceOps · Vuelve a solicitar: {d['auto']}",
                   f"Hola {t['nombre'] or t['usuario']},\n\nLa actividad «{d['auto']}» ({', '.join(x for x in (d['limite'] or '').split(':') if x) or 'sin equipos'}) "
                   f"quedó {d['estado']} (ticket {t['numero']}) y no se ejecutó. {quien} te la devolvió: desde ahora está en tus manos.\n"
                   f"Vuelve a generar el ticket en FaceOps para que se apruebe y ejecute.\n\nFaceOps")
    ej("UPDATE deudas SET responsable=?, nota=?, actualizado=? WHERE id=?", persona,
       f"devuelta por {quien} a {persona} para volver a solicitar · aviso: {envio}", ahora(), deuda_id)
    return {"persona": persona, "envio": envio}
