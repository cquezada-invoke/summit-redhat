"""Gobierno › Reportes: reporte de cumplimiento por área responsable (los grupos dueños de FaceOps) sobre los 6 pilares de
salud, con registro de iniciativas de mejora y envío programado al correo de cada grupo.

De dónde sale cada dato (nada se estima):
· Objetos de cada área: los mismos de su sección del menú (vistas.seccion_eq: Servidores, Redes, Storage, Contenedores,
  Hipervisores, Nubes) y, para las áreas transversales, la regla de madurez.dominios (Sistemas operativos, Bases de datos,
  Plataformas, Aplicaciones, Infraestructura = todo el parque). Un área sin objetos propios no tiene reporte.
· % por pilar y total: radar.radar_equipos (el Radar de salud de toda la consola) sobre esos objetos y sus hallazgos.
· Tendencia: cada cálculo guarda la medición del día (reporte_mediciones); se compara con la última medición de hace una
  semana o un mes. Sin medición de ese período se dice así, no se inventa.
· Iniciativas de mejora: cada pilar bajo el umbral (verde, 85 %) abre o actualiza una iniciativa del área (responsable =
  grupo dueño y sus dueños), con fecha compromiso (14 días si está en rojo, 30 si está en amarillo; editable) y beneficio
  esperado = puntos que sube el pilar al cumplir sus criterios pendientes (100 − % actual). Pasa a «en curso» cuando hay un
  ticket de una acción del área desde que se abrió, y se cierra sola cuando una medición deja el pilar en el umbral o sobre
  él, con el último ticket exitoso del área en ese lapso.
· Envío: semanal por defecto (lunes 08:00 en la zona del destinatario), configurable por área; botón «Enviar ahora». Va al
  correo del grupo dueño con el SMTP de FaceOps (correo.enviar). Correo breve; el detalle vive en la página."""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import flash, g, redirect, render_template, request, url_for

from . import requiere_login
from .bd import ahora, auditar, con, ej, j, q, q1
from .salud import SALUD_AMARILLO, SALUD_VERDE
from .vistas import _escritura, bp

UMBRAL = SALUD_VERDE   # bajo esto un pilar es brecha y genera iniciativa
PLAZO = {"mal": 14, "med": 30}   # días para la fecha compromiso según el color del pilar al abrirla
FRECUENCIAS = [("semanal", "Semanal"), ("mensual", "Mensual"), ("pausado", "Pausado")]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ESTADOS = {"abierta": "Abierta", "en_curso": "En curso", "cerrada": "Cerrada"}
TICKET_ABIERTO = ("pendiente_aprobacion", "en_cola", "en_ejecucion")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS reporte_mediciones (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, seccion TEXT NOT NULL, dia TEXT NOT NULL, fecha TEXT,
  pilares TEXT DEFAULT '{}', total INTEGER, objetos INTEGER DEFAULT 0, UNIQUE(cliente_id, seccion, dia));
CREATE TABLE IF NOT EXISTS reporte_iniciativas (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, seccion TEXT NOT NULL, pilar TEXT NOT NULL, estado TEXT DEFAULT 'abierta',
  abierta TEXT, compromiso TEXT, pct_inicial INTEGER, pct_actual INTEGER, beneficio INTEGER, criterios TEXT DEFAULT '[]',
  ticket_id INTEGER, ticket_numero TEXT DEFAULT '', cerrada TEXT, actualizada TEXT, nota TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS reporte_iniciativas_c ON reporte_iniciativas(cliente_id, seccion, pilar, estado);
CREATE TABLE IF NOT EXISTS reporte_programa (
  cliente_id INTEGER NOT NULL, seccion TEXT NOT NULL, frecuencia TEXT DEFAULT 'semanal', dia INTEGER DEFAULT 0, hora TEXT DEFAULT '08:00',
  proximo TEXT, actualizado TEXT, PRIMARY KEY(cliente_id, seccion));
CREATE TABLE IF NOT EXISTS reporte_envios (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, seccion TEXT NOT NULL, fecha TEXT, tipo TEXT, quien TEXT, para TEXT,
  asunto TEXT, total INTEGER, resultado TEXT);
"""

# Acción que resuelve cada criterio del radar (radar.CRITERIOS_EQ) cuando un objeto no lo cumple
ACCION = {
    "D1": "Encender el equipo desde su ficha (ticket «Encender o apagar equipo») o retirarlo del inventario si ya no presta servicio",
    "D2": "Configurar la alerta de disponibilidad desde el botón Alertas de la ficha",
    "H1": "Atender los hallazgos altos con la acción sugerida en la ficha de cada equipo",
    "H2": "Atender los hallazgos medios con la acción sugerida en la ficha de cada equipo",
    "P1": "Instalar el agente de métricas (node_exporter o windows_exporter)",
    "P2": "Configurar las alertas de CPU, memoria y disco desde el botón Alertas",
    "S1": "Cerrar los hallazgos de seguridad: aplicar la política o el parche que indica cada uno",
    "S2": "Planificar la actualización a una versión con soporte del fabricante (Arquitectura › Ciclo de vida)",
    "R1": "Activar el respaldo continuo en Git",
    "C1": "Ajustar o apagar los recursos sin uso",
    "C2": "Asignar el equipo a su segmento de negocio en el mapa",
    "E1": "Vincular el equipo al inventario de AAP para operarlo desde FaceOps",
}
_listo = {"ok": False}


def iniciar():
    if not _listo["ok"]:
        con().executescript(ESQUEMA)
        # una sola iniciativa abierta por área y pilar (el programador y una página pueden medir a la vez)
        ej("DELETE FROM reporte_iniciativas WHERE estado!='cerrada' AND id NOT IN (SELECT MIN(id) FROM reporte_iniciativas "
           "WHERE estado!='cerrada' GROUP BY cliente_id, seccion, pilar)")
        con().executescript("CREATE UNIQUE INDEX IF NOT EXISTS reporte_iniciativas_u ON reporte_iniciativas(cliente_id, seccion, pilar) "
                            "WHERE estado!='cerrada';")
        _listo["ok"] = True


def tono(p):
    if p is None:
        return "nd"
    return "ok" if p >= SALUD_VERDE else ("med" if p >= SALUD_AMARILLO else "mal")


COLOR = {"ok": "verde", "med": "amarillo", "mal": "rojo", "nd": "sin medición"}


# ------------------------------------------------------------------ objetos y salud de cada área
def _equipos(cliente_id):
    """Todo el parque del cliente (no retirado), con segmentos y skills: el reporte es del área, no de quien lo mira."""
    from .inventario import segmentos_de, skills_de
    segs = q("SELECT * FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", cliente_id)
    out = []
    for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' ORDER BY nombre", cliente_id):
        e["segmentos"] = segmentos_de(e, segs)
        e["skills"] = skills_de(e)
        e["_datos"] = j(e.get("datos"), {}) or {}
        out.append(e)
    return out


def objetos_por_area(eqs):
    """{sección del grupo dueño: [equipos]} con la misma regla del menú (y de madurez para las transversales)."""
    from .madurez import BD, PLAT
    from .vistas import clase_eq, seccion_eq
    sec = {e["id"]: seccion_eq(e) for e in eqs}
    reglas = {
        "infraestructura": lambda e: True,
        "servidores": lambda e: sec[e["id"]] == "inventario",
        "redes": lambda e: sec[e["id"]] == "redes",
        "storage": lambda e: sec[e["id"]] == "storage",
        "so": lambda e: clase_eq(e) == "maquinas" and bool(e.get("so")),
        "bd": lambda e: bool(set(e.get("skills") or []) & BD),
        "plataformas": lambda e: bool(set(e.get("skills") or []) & PLAT),
        "contenedores": lambda e: sec[e["id"]] == "contenedores",
        "hipervisores": lambda e: sec[e["id"]] == "hipervisores",
        "nube": lambda e: sec[e["id"]] == "nubes",
        "aplicaciones": lambda e: bool(e.get("segmentos")),
    }
    return {k: [e for e in eqs if f(e)] for k, f in reglas.items()}


def _peores(eqs, por_eq, n=5):
    from .radar import CRITERIOS_EQ, casos_equipo
    nom = {c[0]: c[2] for c in CRITERIOS_EQ}
    filas = []
    for e in eqs:
        cs = {k: v for k, v in casos_equipo(e, por_eq.get(e["id"], [])).items() if v is not None}
        if not cs:
            continue
        ok = sum(1 for v in cs.values() if v)
        filas.append({"id": e["id"], "nombre": e["nombre"], "pct": round(100 * ok / len(cs)), "tono": tono(round(100 * ok / len(cs))), "ok": ok, "n": len(cs),
                      "faltan": [nom[k] for k, v in cs.items() if not v]})
    return sorted(filas, key=lambda f: (f["pct"], f["nombre"]))[:n]


def _remediables(cliente_id, eqs, hs, radar):
    """Ítems pendientes con su acción: cada criterio que algún objeto no cumple y los hallazgos con acción del catálogo."""
    items = []
    for p in radar["principios"]:
        for c in p["criterios"]:
            if c["n_faltan"]:
                items.append({"tipo": "criterio", "pilar": p["nombre"], "texto": c["nombre"], "n": c["n_faltan"],
                              "objetos": c["faltan"][:6], "accion": ACCION.get(c["id"], c["explica"])})
    from .pruebas import no_certificadas
    from .vistas import auto_sugerida
    bloq = no_certificadas(cliente_id)
    autos = [a for a in q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1", cliente_id) if a["id"] not in bloq]
    nombres = {e["id"]: e["nombre"] for e in eqs}
    sev = {"alta": 0, "media": 1, "baja": 2}
    acc = []
    for h in hs:
        a = auto_sugerida(h.get("sugerencia"), autos)
        if a:
            acc.append({"tipo": "hallazgo", "texto": h["titulo"], "severidad": h.get("severidad") or "", "equipo_id": h["equipo_id"],
                        "equipo": nombres.get(h["equipo_id"], ""), "accion": a["nombre"]})
    acc.sort(key=lambda x: (sev.get(x["severidad"], 3), x["equipo"]))
    return sorted(items, key=lambda x: -x["n"]), acc[:15], len(acc)


def _medir(cliente_id, seccion, r):
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pil = {p["nombre"]: p["pct"] for p in r["principios"]}
    ej("INSERT INTO reporte_mediciones(cliente_id, seccion, dia, fecha, pilares, total, objetos) VALUES (?,?,?,?,?,?,?) "
       "ON CONFLICT(cliente_id, seccion, dia) DO UPDATE SET fecha=excluded.fecha, pilares=excluded.pilares, total=excluded.total, "
       "objetos=excluded.objetos", cliente_id, seccion, hoy, ahora(), json.dumps(pil, ensure_ascii=False), r["pct"], r["n"])


def _anterior(cliente_id, seccion, periodo):
    dias = 30 if periodo == "mes" else 7
    lim = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d")
    m = q1("SELECT * FROM reporte_mediciones WHERE cliente_id=? AND seccion=? AND dia<=? ORDER BY dia DESC LIMIT 1", cliente_id, seccion, lim)
    if m:
        m["pilares"] = j(m["pilares"], {}) or {}
    return m


def _tickets_area(cliente_id, seccion, desde):
    """Tickets de acciones del área (sección del menú de la acción) creados desde una fecha, del más nuevo al más viejo."""
    from .areas import seccion_de
    autos = {a["id"] for a in q("SELECT id, nombre, nombre_aap, categoria FROM automatizaciones WHERE cliente_id=?", cliente_id)
             if seccion_de(a) == seccion}
    if not autos:
        return []
    return [t for t in q("SELECT id, numero, estado, automatizacion_id, creado, fin FROM tickets WHERE cliente_id=? AND creado>=? ORDER BY id DESC",
                         cliente_id, desde or "") if t["automatizacion_id"] in autos]


def _sincronizar(cliente_id, seccion, r):
    """Abre, actualiza o cierra las iniciativas de mejora del área según la medición de hoy."""
    abiertas = {i["pilar"]: i for i in q("SELECT * FROM reporte_iniciativas WHERE cliente_id=? AND seccion=? AND estado!='cerrada'", cliente_id, seccion)}
    ts = None
    for p in r["principios"]:
        pct, i = p["pct"], abiertas.get(p["nombre"])
        pend = [c["nombre"] for c in p["criterios"] if c["n_faltan"]]
        if pct is not None and pct < UMBRAL:
            if not i:
                comp = (datetime.now(timezone.utc) + timedelta(days=PLAZO[tono(pct)])).strftime("%Y-%m-%d")
                ej("INSERT OR IGNORE INTO reporte_iniciativas(cliente_id, seccion, pilar, estado, abierta, compromiso, pct_inicial, pct_actual, beneficio, "
                   "criterios, actualizada) VALUES (?,?,?,?,?,?,?,?,?,?,?)", cliente_id, seccion, p["nombre"], "abierta", ahora(), comp, pct, pct,
                   100 - pct, json.dumps(pend, ensure_ascii=False), ahora())
                continue
            estado, tk = i["estado"], (i["ticket_id"], i["ticket_numero"])
            if ts is None:
                ts = _tickets_area(cliente_id, seccion, min(x["abierta"] for x in abiertas.values()))
            t_ = next((t for t in ts if (t["creado"] or "") >= (i["abierta"] or "")), None)
            if t_ and estado == "abierta":
                estado, tk = "en_curso", (t_["id"], t_["numero"])
            ej("UPDATE reporte_iniciativas SET pct_actual=?, beneficio=?, criterios=?, estado=?, ticket_id=?, ticket_numero=?, actualizada=? WHERE id=?",
               pct, 100 - pct, json.dumps(pend, ensure_ascii=False), estado, tk[0], tk[1] or "", ahora(), i["id"])
        elif i:   # el pilar salió de la brecha: se cierra con el último ticket exitoso del área en el lapso
            if ts is None:
                ts = _tickets_area(cliente_id, seccion, min(x["abierta"] for x in abiertas.values()))
            t_ = next((t for t in ts if t["estado"] == "exitoso" and (t["creado"] or "") >= (i["abierta"] or "")), None)
            ej("UPDATE reporte_iniciativas SET estado='cerrada', cerrada=?, pct_actual=?, beneficio=0, ticket_id=?, ticket_numero=?, actualizada=? WHERE id=?",
               ahora(), pct, t_["id"] if t_ else i["ticket_id"], t_["numero"] if t_ else (i["ticket_numero"] or ""), ahora(), i["id"])


def iniciativas(cliente_id, seccion=None):
    iniciar()
    sql = "SELECT * FROM reporte_iniciativas WHERE cliente_id=?" + (" AND seccion=?" if seccion else "") + \
          " ORDER BY CASE estado WHEN 'cerrada' THEN 1 ELSE 0 END, compromiso, pilar"
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = []
    for i in q(sql, *([cliente_id, seccion] if seccion else [cliente_id])):
        i["criterios"] = j(i["criterios"], []) or []
        i["vencida"] = i["estado"] != "cerrada" and (i["compromiso"] or "9999") < hoy
        out.append(i)
    return out


def calcular(cliente, seccion=None, periodo=None, guardar=True):
    """Reporte de todas las áreas con objetos (o de una). Guarda la medición del día y sincroniza las iniciativas."""
    from .grupos_dueno import grupos
    from .radar import radar_equipos
    iniciar()
    g.cliente = g.get("cliente") or cliente
    cid = cliente["id"]
    eqs = _equipos(cid)
    hs_todos = q("SELECT * FROM hallazgos WHERE cliente_id=?", cid)
    por_area = objetos_por_area(eqs)
    prog = {p["seccion"]: p for p in programas(cid)}
    out = []
    for gr in grupos(cid):
        k = gr["clave"]
        es = por_area.get(k) or []
        if not es:
            continue
        ids = {e["id"] for e in es}
        hs = [h for h in hs_todos if h["equipo_id"] in ids]
        r = radar_equipos(es, hs)
        per = periodo or ("mes" if (prog.get(k) or {}).get("frecuencia") == "mensual" else "semana")
        if guardar:
            _medir(cid, k, r)
            _sincronizar(cid, k, r)
        ant = _anterior(cid, k, per)
        pil = []
        for p in r["principios"]:
            a = (ant or {}).get("pilares", {}).get(p["nombre"]) if ant else None
            pil.append({"nombre": p["nombre"], "pct": p["pct"], "tono": tono(p["pct"]), "antes": a,
                        "delta": (p["pct"] - a) if p["pct"] is not None and a is not None else None, "criterios": p["criterios"]})
        fila = {"clave": k, "nombre": gr["nombre"], "correo": gr.get("correo") or "", "duenos": gr["duenos"], "n": r["n"],
                "total": r["pct"], "tono": tono(r["pct"]), "pilares": pil, "periodo": per, "anterior": ant,
                "delta": (r["pct"] - ant["total"]) if ant and ant.get("total") is not None and r["pct"] is not None else None,
                "programa": prog.get(k)}
        if seccion and k == seccion:
            por_eq = {}
            for h in hs:
                por_eq.setdefault(h["equipo_id"], []).append(h)
            fila["peores"] = _peores(es, por_eq)
            fila["remediables"], fila["hallazgos_accion"], fila["n_hallazgos_accion"] = _remediables(cid, es, hs, r)
            fila["iniciativas"] = iniciativas(cid, k)
        out.append(fila)
    return out


# ------------------------------------------------------------------ programación y envío
def _proximo(frecuencia, dia, hora, zona, desde=None):
    """Próximo envío (ISO UTC): el día de la semana elegido (semanal) o el primer día de ese tipo del mes siguiente (mensual),
    a la hora indicada en la zona del destinatario (UTC si no se conoce)."""
    from zoneinfo import ZoneInfo
    if frecuencia == "pausado":
        return None
    tz = ZoneInfo(zona) if zona and zona != "UTC" else timezone.utc
    hh, mm = (int(x) for x in (hora or "08:00").split(":")[:2])
    base = (desde or datetime.now(timezone.utc)).astimezone(tz)
    if frecuencia == "mensual":
        y, m = (base.year + (base.month // 12), base.month % 12 + 1)
        d0 = datetime(y, m, 1, hh, mm, tzinfo=tz)
        d0 += timedelta(days=(int(dia) - d0.weekday()) % 7)
        cand = datetime(base.year, base.month, 1, hh, mm, tzinfo=tz)
        cand += timedelta(days=(int(dia) - cand.weekday()) % 7)
        return (cand if cand > base else d0).astimezone(timezone.utc).isoformat(timespec="seconds")
    cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(days=(int(dia) - base.weekday()) % 7)
    if cand <= base:
        cand += timedelta(days=7)
    return cand.astimezone(timezone.utc).isoformat(timespec="seconds")


def programas(cliente_id):
    """Programa de envío de cada área (semanal, lunes 08:00 por defecto); crea el que falta."""
    from .correo import zona_de
    from .grupos_dueno import grupos
    iniciar()
    ps = {p["seccion"]: p for p in q("SELECT * FROM reporte_programa WHERE cliente_id=?", cliente_id)}
    out = []
    for gr in grupos(cliente_id):
        p = ps.get(gr["clave"])
        if not p:
            p = {"cliente_id": cliente_id, "seccion": gr["clave"], "frecuencia": "semanal", "dia": 0, "hora": "08:00",
                 "proximo": _proximo("semanal", 0, "08:00", zona_de(gr.get("correo") or "")), "actualizado": ahora()}
            ej("INSERT OR IGNORE INTO reporte_programa(cliente_id, seccion, frecuencia, dia, hora, proximo, actualizado) VALUES (?,?,?,?,?,?,?)",
               cliente_id, p["seccion"], p["frecuencia"], p["dia"], p["hora"], p["proximo"], p["actualizado"])
        out.append(dict(p))
    return out


def guardar_programa(cliente_id, seccion, frecuencia, dia, hora, quien):
    from .correo import zona_de
    from .grupos_dueno import grupo
    if frecuencia not in dict(FRECUENCIAS):
        raise ValueError("Frecuencia no válida")
    dia = int(dia)
    if not 0 <= dia <= 6:
        raise ValueError("Día no válido")
    hh, mm = (hora or "").split(":")[:2] if ":" in (hora or "") else ("x", "x")
    if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
        raise ValueError("Hora no válida (HH:MM)")
    gr = grupo(cliente_id, seccion)
    if not gr:
        raise ValueError("Área desconocida")
    programas(cliente_id)
    prox = _proximo(frecuencia, dia, f"{int(hh):02d}:{int(mm):02d}", zona_de(gr.get("correo") or ""))
    ej("UPDATE reporte_programa SET frecuencia=?, dia=?, hora=?, proximo=?, actualizado=? WHERE cliente_id=? AND seccion=?",
       frecuencia, dia, f"{int(hh):02d}:{int(mm):02d}", prox, ahora(), cliente_id, gr["clave"])
    auditar(quien, cliente_id, "programar reporte de cumplimiento", gr["nombre"], {"frecuencia": frecuencia, "dia": DIAS[dia], "hora": hora})


def url_publica(cliente, seccion):
    pub = os.environ.get("FACEOPS_URL_PUBLICA", "https://faceops.34.176.42.56.sslip.io:8843/faceops").rstrip("/")
    return f"{pub}/{(cliente.get('codigo') or '').lower()}/gobierno/reportes?area={seccion}"


def cuerpo_correo(cliente, a, para):
    """Correo breve: total, los 6 pilares con su color y tendencia, iniciativas y el enlace al detalle en FaceOps."""
    from .correo import hora
    ini = [i for i in iniciativas(cliente["id"], a["clave"]) if i["estado"] != "cerrada"]
    venc = sum(1 for i in ini if i["vencida"])

    def dt(d):
        return "" if d is None else f" ({'+' if d >= 0 else ''}{d})"
    per = "semana" if a["periodo"] == "semana" else "mes"
    lin = [f"Reporte de cumplimiento · {a['nombre']} · {cliente['nombre']}",
           f"Medido el {hora(datetime.now(timezone.utc), para)} sobre {a['n']} objeto{'s' if a['n'] != 1 else ''}.", "",
           f"Cumplimiento total: {a['total']} % ({COLOR[a['tono']]})" + (f" · {'+' if a['delta'] >= 0 else ''}{a['delta']} pts contra la {per} anterior"
                                                                     if a["delta"] is not None else f" · sin medición de la {per} anterior"), ""]
    for p in a["pilares"]:
        lin.append(f"  {p['nombre']}: {'—' if p['pct'] is None else str(p['pct']) + ' %'} {COLOR[p['tono']]}{dt(p['delta'])}")
    lin += ["", f"Iniciativas de mejora abiertas: {len(ini)}" + (f" ({venc} con fecha compromiso vencida)" if venc else "")]
    if a.get("peores"):
        lin.append("Peor evaluados: " + ", ".join(f"{x['nombre']} {x['pct']} %" for x in a["peores"][:3]))
    lin += ["", f"Detalle, ítems pendientes y acciones: {url_publica(cliente, a['clave'])}", "",
            "Correo enviado por FaceOps al grupo responsable del área."]
    return "\n".join(lin)


def enviar(cliente, seccion, tipo="manual", quien="sistema"):
    """Calcula el reporte del área y lo envía al correo de su grupo dueño. Devuelve el registro del envío."""
    from .correo import enviar as smtp
    iniciar()
    a = next((x for x in calcular(cliente, seccion) if x["clave"] == seccion), None)
    if not a:
        raise ValueError("El área no tiene objetos que medir: no hay reporte que enviar")
    para = a["correo"]
    asunto = f"FaceOps · Cumplimiento {a['nombre']}: {a['total']} % ({COLOR[a['tono']]})"
    res = smtp(para, asunto, cuerpo_correo(cliente, a, para)) if para else "sin correo de grupo registrado"
    ej("INSERT INTO reporte_envios(cliente_id, seccion, fecha, tipo, quien, para, asunto, total, resultado) VALUES (?,?,?,?,?,?,?,?,?)",
       cliente["id"], seccion, ahora(), tipo, quien, para, asunto, a["total"], res)
    auditar(quien, cliente["id"], "enviar reporte de cumplimiento", a["nombre"], {"para": para, "tipo": tipo, "resultado": res})
    return {"para": para, "asunto": asunto, "resultado": res, "total": a["total"]}


def historial(cliente_id, seccion=None, n=30):
    iniciar()
    if seccion:
        return q("SELECT * FROM reporte_envios WHERE cliente_id=? AND seccion=? ORDER BY id DESC LIMIT ?", cliente_id, seccion, n)
    return q("SELECT * FROM reporte_envios WHERE cliente_id=? ORDER BY id DESC LIMIT ?", cliente_id, n)


def revisar(cliente):
    """Envíos programados vencidos del cliente y la medición diaria (para que la tendencia exista aunque nadie abra la página)."""
    from .correo import zona_de
    iniciar()
    ahora_ = datetime.now(timezone.utc)
    hoy = ahora_.strftime("%Y-%m-%d")
    if not q1("SELECT 1 FROM reporte_mediciones WHERE cliente_id=? AND dia=?", cliente["id"], hoy):
        calcular(cliente)
    areas = None
    for p in programas(cliente["id"]):
        if p["frecuencia"] == "pausado" or not p["proximo"] or p["proximo"] > ahora_.isoformat(timespec="seconds"):
            continue
        if areas is None:
            from .grupos_dueno import grupos
            areas = {gr["clave"]: gr for gr in grupos(cliente["id"])}
            con_obj = {k for k, v in objetos_por_area(_equipos(cliente["id"])).items() if v}
        gr = areas.get(p["seccion"]) or {}
        if p["seccion"] in con_obj and gr.get("correo"):
            try:
                enviar(cliente, p["seccion"], "programado")
            except Exception as x:  # noqa: BLE001
                auditar("sistema", cliente["id"], "error reporte de cumplimiento", p["seccion"], {"error": str(x)[:300]})
        ej("UPDATE reporte_programa SET proximo=? WHERE cliente_id=? AND seccion=?",
           _proximo(p["frecuencia"], p["dia"], p["hora"], zona_de(gr.get("correo") or ""), ahora_), cliente["id"], p["seccion"])


def iniciar_programador(app, cada=300):
    """Hilo propio: cada 5 minutos revisa los envíos programados de cada cliente activo."""
    def bucle():
        time.sleep(60)
        while True:
            try:
                with app.app_context():
                    clientes = q("SELECT * FROM clientes WHERE activo=1")
                for c in clientes:
                    with app.app_context():   # un contexto por cliente: las cachés de g (p. ej. ubicaciones) no se mezclan
                        g.cliente = c
                        try:
                            revisar(c)
                        except Exception as x:  # noqa: BLE001
                            auditar("sistema", c["id"], "error reporte de cumplimiento", "programador", {"error": str(x)[:300]})
            except Exception:  # noqa: BLE001
                pass
            time.sleep(cada)
    threading.Thread(target=bucle, daemon=True, name="faceops-reportes").start()


# ------------------------------------------------------------------ ITIL
def itil_mejora(cliente_id):
    """Para la evaluación ITIL: iniciativas de mejora registradas con responsable (área con grupo dueño), fecha compromiso y
    beneficio esperado, y envíos del reporte que el SMTP aceptó."""
    iniciar()
    ini = q("SELECT seccion, estado, compromiso, beneficio, pct_inicial FROM reporte_iniciativas WHERE cliente_id=?", cliente_id)
    validas = [i for i in ini if i["seccion"] and i["compromiso"] and i["pct_inicial"] is not None]
    env = q1("SELECT COUNT(*) n FROM reporte_envios WHERE cliente_id=? AND resultado LIKE 'enviado%'", cliente_id)["n"]
    prog = q1("SELECT COUNT(*) n FROM reporte_programa WHERE cliente_id=? AND frecuencia!='pausado'", cliente_id)["n"]
    return {"validas": len(validas), "abiertas": sum(1 for i in validas if i["estado"] != "cerrada"),
            "cerradas": sum(1 for i in validas if i["estado"] == "cerrada"), "envios": env, "programados": prog}


# ------------------------------------------------------------------ pantallas
@bp.route("/gobierno/reportes")
@requiere_login
def reportes():
    """Gobierno › Reportes: cumplimiento por área en los 6 pilares, iniciativas de mejora y envío al grupo responsable."""
    from .grupos_dueno import NOMBRES_SECCION
    sec = request.args.get("area", "")
    periodo = request.args.get("periodo") if request.args.get("periodo") in ("semana", "mes") else None
    areas = calcular(g.cliente, sec or None, periodo)
    if not sec and areas:
        sec = min(areas, key=lambda a: (a["total"] if a["total"] is not None else 101))["clave"]
        areas = calcular(g.cliente, sec, periodo, guardar=False)
    sel = next((a for a in areas if a["clave"] == sec), None)
    return render_template("reportes.html", areas=areas, sel=sel, sec=sec, periodo=periodo, hist=historial(g.cliente["id"]),
                           ESTADOS=ESTADOS, FRECUENCIAS=FRECUENCIAS, DIAS=DIAS, COLOR=COLOR, UMBRAL=UMBRAL, nombres=NOMBRES_SECCION,
                           smtp=bool(os.environ.get("FACEOPS_SMTP_HOST")),
                           escribe=g.perfil["modo"] == "escritura")


@bp.route("/gobierno/reportes/enviar", methods=["POST"])
@requiere_login
def reportes_enviar():
    _escritura()
    sec = request.form.get("area", "")
    try:
        r = enviar(g.cliente, sec, "manual", g.usuario["usuario"])
        flash(f"Reporte de {sec}: {r['resultado']}", "ok" if r["resultado"].startswith("enviado") else "error")
    except ValueError as x:
        flash(str(x), "error")
    return redirect(url_for("consola.reportes", area=sec) + "#envios")


@bp.route("/gobierno/reportes/programa", methods=["POST"])
@requiere_login
def reportes_programa():
    _escritura()
    f = request.form
    try:
        guardar_programa(g.cliente["id"], f.get("area", ""), f.get("frecuencia", ""), f.get("dia", "0"), f.get("hora", ""), g.usuario["usuario"])
        flash("Programa de envío guardado", "ok")
    except ValueError as x:
        flash(str(x), "error")
    return redirect(url_for("consola.reportes", area=f.get("area", "")) + "#envios")


@bp.route("/gobierno/reportes/iniciativa/<int:iid>", methods=["POST"])
@requiere_login
def reportes_iniciativa(iid):
    """Fecha compromiso, estado (abierta / en curso; el cierre lo da la medición) y nota de una iniciativa."""
    _escritura()
    i = q1("SELECT * FROM reporte_iniciativas WHERE id=? AND cliente_id=?", iid, g.cliente["id"])
    if not i:
        flash("Iniciativa no encontrada", "error")
        return redirect(url_for("consola.reportes"))
    f = request.form
    comp = f.get("compromiso") or i["compromiso"]
    try:
        datetime.strptime(comp, "%Y-%m-%d")
    except ValueError:
        flash("Fecha compromiso no válida", "error")
        return redirect(url_for("consola.reportes", area=i["seccion"]) + "#iniciativas")
    est = f.get("estado") if f.get("estado") in ("abierta", "en_curso") and i["estado"] != "cerrada" else i["estado"]
    ej("UPDATE reporte_iniciativas SET compromiso=?, estado=?, nota=?, actualizada=? WHERE id=?", comp, est, (f.get("nota") or "")[:300], ahora(), iid)
    auditar(g.usuario["usuario"], g.cliente["id"], "editar iniciativa de mejora", f"{i['seccion']} · {i['pilar']}", {"compromiso": comp, "estado": est})
    flash("Iniciativa actualizada", "ok")
    return redirect(url_for("consola.reportes", area=i["seccion"]) + "#iniciativas")
