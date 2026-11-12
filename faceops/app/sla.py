"""Gobierno › SLA: acuerdo de nivel de servicio por servicio de negocio (segmento) y su reporte periódico de cumplimiento
(Gobierno › Reportes › SLA). Cubre el punto ITIL «SLA acordado por servicio y su informe periódico de cumplimiento».

Cada SLA define: disponibilidad objetivo con su ventana de medición (mensual por defecto) y ventanas de mantenimiento que no
cuentan; latencia objetivo (p95); tiempos de respuesta y resolución por prioridad (P1–P4); tiempo máximo de aprobación de
cambios; dueño del servicio (grupo dueño), cliente o área consumidora, vigencia y versión.

Crear o cambiar un SLA es un cambio con aprobación: se abre un ticket «Cambio de SLA» (acción interna de FaceOps, sin AAP)
que espera la aprobación del grupo dueño de Aplicaciones; mientras tanto la versión queda «propuesto». Al aprobarse pasa a
«vigente» y la anterior a «reemplazado» (historial de versiones en sla_versiones).

Medición (nada se estima):
· Disponibilidad y latencia: Prometheus, la sonda blackbox del VIP del segmento (job faceops-segmentos, objeto seg-<código>,
  la misma de las alertas G01/G02 del segmento). Muestra cada 5 min dentro del período, sin las ventanas de mantenimiento.
  Sin muestras = «sin medición» con su motivo, nunca un valor inventado.
· Respuesta, resolución y aprobación: tickets reales de FaceOps del servicio (segmento del ticket o máquinas del segmento),
  con sus marcas de creación, toma o respuesta de la aprobación (ticket_etapas), inicio y cierre. Se excluyen los tickets de
  demostración (DEMO-…, FO-HIS-…, «[demo]»).
· Prioridad: los tickets de FaceOps aún no traen prioridad propia; se asigna con la regla PRIORIDAD_REGLA (a la vista en la
  página).
· Error budget: minutos fuera de servicio del período sobre los minutos permitidos ((100 − objetivo) % del período sin las
  ventanas de mantenimiento). Sobre el umbral del SLA (50 % por defecto) se abre la alerta «SLA en riesgo» (correo breve al
  dueño y al consumidor) y la salud del segmento lo muestra.
· Violaciones: cada una con su enlace (ticket o alerta del segmento). Cada objetivo incumplido abre o actualiza una iniciativa
  de mejora en el registro de Gobierno › Reportes (tabla reporte_iniciativas, mismo modelo), con responsable = grupo dueño.

Reporte: mensual por defecto (día 1, 08:00 en la hora local del destinatario), botón «Enviar ahora», al dueño del servicio y al
consumidor (en el laboratorio de Invoke todo va a LAB_PARA). Correo breve con enlace; el detalle vive en la página. Historial
de envíos en sla_envios."""
import calendar
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for

from . import requiere_login
from .bd import ahora, auditar, con, ej, j, q, q1
from .vistas import _escritura, bp

AUTO = "Gobierno · Cambio de SLA"
APRUEBA = "aplicaciones"   # grupo dueño que aprueba los cambios de SLA (servicios de negocio)
LAB_PARA = os.environ.get("FACEOPS_SLA_PARA_LAB", "cquezada@redhat.com")
ZONA = "America/Santiago"
PASO = 300   # segundos entre muestras de Prometheus
VENTANAS = [("mensual", "Mensual"), ("trimestral", "Trimestral")]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
PRIORIDADES = ["P1", "P2", "P3", "P4"]
DEFECTO = {"disponibilidad": 99.5, "ventana": "mensual", "zona": ZONA, "mantenimiento": [{"dia": 6, "desde": "02:00", "hasta": "04:00"}],
           "latencia_p95_ms": 800, "aprobacion_max_h": 24, "umbral_riesgo": 50,
           "prioridades": {"P1": {"respuesta_min": 15, "resolucion_min": 240}, "P2": {"respuesta_min": 60, "resolucion_min": 480},
                           "P3": {"respuesta_min": 240, "resolucion_min": 4320}, "P4": {"respuesta_min": 1440, "resolucion_min": 7200}}}
PRIORIDAD_REGLA = [("P1", "Respuesta a una alerta o remediación en producción"),
                   ("P2", "Cambio (escritura) en producción"),
                   ("P3", "Cambio fuera de producción o sin ambiente"),
                   ("P4", "Consulta o informe (solo lectura)")]
ESTADOS = {"propuesto": "Propuesto", "vigente": "Vigente", "reemplazado": "Reemplazado", "rechazado": "Rechazado"}
COLOR = {"cumple": "verde", "riesgo": "amarillo", "incumple": "rojo", "sin_medicion": "gris"}
TEXTO = {"cumple": "Cumple", "riesgo": "En riesgo", "incumple": "Incumple", "sin_medicion": "Sin medición"}
CERRADOS = ("exitoso", "fallido")
FUERA = ("rechazado", "cancelado", "vencido")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS sla_versiones (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, segmento_id INTEGER, segmento TEXT NOT NULL, version INTEGER NOT NULL,
  estado TEXT DEFAULT 'propuesto', datos TEXT DEFAULT '{}', motivo TEXT DEFAULT '', ticket_id INTEGER, ticket_numero TEXT DEFAULT '',
  autor TEXT DEFAULT '', creado TEXT, aprobado_por TEXT DEFAULT '', aprobado TEXT, comentario TEXT DEFAULT '', demo INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS sla_versiones_c ON sla_versiones(cliente_id, segmento, estado);
CREATE TABLE IF NOT EXISTS sla_mediciones (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, segmento TEXT NOT NULL, periodo TEXT NOT NULL, sla_id INTEGER,
  calculado TEXT, estado TEXT, cumplimiento INTEGER, consumo REAL, resultado TEXT DEFAULT '{}', UNIQUE(cliente_id, segmento, periodo));
CREATE TABLE IF NOT EXISTS sla_alertas (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, segmento TEXT NOT NULL, sla_id INTEGER, abierta TEXT, cerrada TEXT,
  consumo REAL, umbral REAL, para TEXT DEFAULT '', resultado TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS sla_programa (
  cliente_id INTEGER PRIMARY KEY, frecuencia TEXT DEFAULT 'mensual', dia INTEGER DEFAULT 1, hora TEXT DEFAULT '08:00',
  proximo TEXT, actualizado TEXT);
CREATE TABLE IF NOT EXISTS sla_envios (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, fecha TEXT, periodo TEXT, tipo TEXT, quien TEXT, para TEXT, asunto TEXT,
  resumen TEXT DEFAULT '{}', resultado TEXT);
CREATE TABLE IF NOT EXISTS reporte_iniciativas (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, seccion TEXT NOT NULL, pilar TEXT NOT NULL, estado TEXT DEFAULT 'abierta',
  abierta TEXT, compromiso TEXT, pct_inicial INTEGER, pct_actual INTEGER, beneficio INTEGER, criterios TEXT DEFAULT '[]',
  ticket_id INTEGER, ticket_numero TEXT DEFAULT '', cerrada TEXT, actualizada TEXT, nota TEXT DEFAULT '');
"""
_listo = {"ok": False}


def iniciar():
    if not _listo["ok"]:
        con().executescript(ESQUEMA)
        _listo["ok"] = True


def _dt(s):
    try:
        d = datetime.fromisoformat(str(s or "").replace("Z", "+00:00").replace(" ", "T"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _ahora():
    return datetime.now(timezone.utc)


def dur(m):
    """Minutos → texto corto («15 min», «4 h», «3 d»)."""
    if m is None:
        return "—"
    m = float(m)
    if m < 90:
        return f"{m:.0f} min"
    if m < 48 * 60:
        h = m / 60
        return f"{h:.1f} h".replace(".0 h", " h").replace(".", ",")
    d = m / 1440
    return f"{d:.1f} d".replace(".0 d", " d").replace(".", ",")


def pct(x, dec=2):
    return "—" if x is None else (f"{x:.{dec}f}".rstrip("0").rstrip(".").replace(".", ",") + " %")


# ------------------------------------------------------------------ acción interna «Cambio de SLA» y su aprobación
def auto(cliente_id):
    """La acción interna con la que se abren los tickets de cambio de SLA (no visible en Acciones, sin AAP)."""
    iniciar()
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, AUTO)
    if a:
        return a
    ej("INSERT INTO automatizaciones(cliente_id, aap_tipo, aap_id, nombre_aap, nombre, descripcion, categoria, tipo, usa_limite, "
       "requiere_aprobacion, visible, minutos_manual, area, aprobacion_etapas, aprobacion_aap) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
       cliente_id, "faceops-sla", 0, AUTO, "Cambio de SLA", "Crear o cambiar el SLA de un servicio de negocio: queda propuesto hasta "
       "que el grupo dueño de Aplicaciones lo aprueba", "Gobierno", "escritura", 0, 1, 0, 30, "arquitectura", json.dumps([APRUEBA]),
       "en FaceOps (cambio de gobierno, sin AAP)")
    return q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, AUTO)


def es_ticket_sla(t):
    a = q1("SELECT nombre_aap FROM automatizaciones WHERE id=?", (t or {}).get("automatizacion_id")) or {}
    return a.get("nombre_aap") == AUTO


def _validar(d):
    """Normaliza y valida el SLA del formulario. ValueError con el motivo."""
    out = {}
    try:
        out["disponibilidad"] = float(str(d.get("disponibilidad", "")).replace(",", "."))
    except ValueError:
        raise ValueError("Disponibilidad objetivo no válida") from None
    if not 90 <= out["disponibilidad"] < 100:
        raise ValueError("La disponibilidad objetivo va entre 90 % y 99,999 %")
    out["ventana"] = d.get("ventana") if d.get("ventana") in dict(VENTANAS) else "mensual"
    try:
        ZoneInfo(d.get("zona") or ZONA)
        out["zona"] = d.get("zona") or ZONA
    except Exception:  # noqa: BLE001
        raise ValueError("Zona horaria no válida") from None
    mant = []
    for m in d.get("mantenimiento") or []:
        if m.get("dia") in (None, "") or not m.get("desde") or not m.get("hasta"):
            continue
        dia = int(m["dia"])
        for h in (m["desde"], m["hasta"]):
            hh, _, mm = str(h).partition(":")
            if not (hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60):
                raise ValueError("Hora de mantenimiento no válida (HH:MM)")
        if not 0 <= dia <= 6:
            raise ValueError("Día de mantenimiento no válido")
        mant.append({"dia": dia, "desde": m["desde"], "hasta": m["hasta"]})
    out["mantenimiento"] = mant
    for k, lo in (("latencia_p95_ms", 1), ("aprobacion_max_h", 0.1), ("umbral_riesgo", 1)):
        try:
            out[k] = float(str(d.get(k, "")).replace(",", "."))
        except ValueError:
            raise ValueError(f"Valor no válido en {k}") from None
        if out[k] < lo:
            raise ValueError(f"Valor no válido en {k}")
    out["latencia_p95_ms"] = int(out["latencia_p95_ms"])
    out["umbral_riesgo"] = min(100, out["umbral_riesgo"])
    pr = {}
    for p in PRIORIDADES:
        x = (d.get("prioridades") or {}).get(p) or {}
        try:
            r_, s_ = int(float(x.get("respuesta_min"))), int(float(x.get("resolucion_min")))
        except (TypeError, ValueError):
            raise ValueError(f"Tiempos de {p} no válidos") from None
        if r_ <= 0 or s_ <= 0 or s_ < r_:
            raise ValueError(f"En {p} la resolución debe ser mayor o igual que la respuesta, ambas mayores que cero")
        pr[p] = {"respuesta_min": r_, "resolucion_min": s_}
    out["prioridades"] = pr
    from .grupos_dueno import grupo
    gr = grupo(g.cliente["id"] if g.get("cliente") else d.get("_cliente_id"), d.get("dueno") or "")
    if not gr:
        raise ValueError("Elige el grupo dueño del servicio")
    out["dueno"] = gr["clave"]
    out["consumidor"] = (d.get("consumidor") or "").strip()[:120]
    if not out["consumidor"]:
        raise ValueError("Indica el cliente o área consumidora")
    out["correo_consumidor"] = (d.get("correo_consumidor") or "").strip()[:200]
    for k in ("vigencia_desde", "vigencia_hasta"):
        try:
            out[k] = datetime.strptime(d.get(k) or "", "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise ValueError("Vigencia no válida (AAAA-MM-DD)") from None
    if out["vigencia_hasta"] <= out["vigencia_desde"]:
        raise ValueError("La vigencia debe terminar después de empezar")
    if d.get("demo"):
        out["demo"] = True
    return out


def de_formulario(f):
    d = {k: f.get(k) for k in ("disponibilidad", "ventana", "zona", "latencia_p95_ms", "aprobacion_max_h", "umbral_riesgo", "dueno",
                               "consumidor", "correo_consumidor", "vigencia_desde", "vigencia_hasta")}
    d["mantenimiento"] = [{"dia": f.get(f"m{i}_dia"), "desde": f.get(f"m{i}_desde"), "hasta": f.get(f"m{i}_hasta")} for i in range(3)]
    d["prioridades"] = {p: {"respuesta_min": f.get(f"{p}_resp"), "resolucion_min": f.get(f"{p}_res")} for p in PRIORIDADES}
    return d


def proponer(cliente, usuario, seg, datos, motivo="", demo=False):
    """Nueva versión «propuesto» del SLA del servicio y su ticket «Cambio de SLA» esperando aprobación."""
    from .aprobacion_areas import abrir_grupo
    from .operacion import numero_ticket
    iniciar()
    d = _validar(dict(datos, demo=demo, _cliente_id=cliente["id"]))
    if q1("SELECT 1 FROM sla_versiones WHERE cliente_id=? AND segmento=? AND estado='propuesto'", cliente["id"], seg["nombre"]):
        raise ValueError("Ese servicio ya tiene un cambio de SLA esperando aprobación: apruébalo o recházalo primero")
    a = auto(cliente["id"])
    v = (q1("SELECT MAX(version) v FROM sla_versiones WHERE cliente_id=? AND segmento=?", cliente["id"], seg["nombre"])["v"] or 0) + 1
    vig = vigente(cliente["id"], seg["nombre"])
    mot = (motivo or "").strip() or (f"Cambio del SLA de {seg['nombre']} (v{vig['version']} → v{v})" if vig else f"SLA inicial de {seg['nombre']}")
    mot = ("[demo] " if demo else "") + f"Cambio de SLA · {seg['nombre']} · v{v}: " + mot
    num = numero_ticket(cliente)
    tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, motivo, creado, valor_min) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?)", cliente["id"], num, a["id"], usuario["id"], "pendiente_aprobacion", seg["nombre"], "",
             json.dumps({"sla_version": v, "segmento": seg["nombre"]}, ensure_ascii=False), mot[:500], ahora(), a.get("minutos_manual") or 30)
    sid = ej("INSERT INTO sla_versiones(cliente_id, segmento_id, segmento, version, estado, datos, motivo, ticket_id, ticket_numero, autor, creado, demo) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", cliente["id"], seg["id"], seg["nombre"], v, "propuesto", json.dumps(d, ensure_ascii=False), mot[:500],
             tid, num, usuario["usuario"], ahora(), int(bool(demo)))
    ej("UPDATE tickets SET parametros=? WHERE id=?", json.dumps({"sla_id": sid, "sla_version": v, "segmento": seg["nombre"]}, ensure_ascii=False), tid)
    t = q1("SELECT * FROM tickets WHERE id=?", tid)
    abrir_grupo(t, a, 1)   # etapa del grupo dueño de Aplicaciones (aviso al correo del grupo)
    auditar(usuario["usuario"], cliente["id"], "proponer SLA", num, {"segmento": seg["nombre"], "version": v, "demo": bool(demo)})
    return {"id": sid, "version": v, "ticket_id": tid, "numero": num}


def decidir(usuario, t, aprobar, comentario=""):
    """Respuesta a la aprobación de un ticket «Cambio de SLA»: lo lleva FaceOps (no hay trabajo en AAP)."""
    from .aprobacion_areas import actual, avanzar, cerrar, grupos_usuario
    a = auto(t["cliente_id"])
    s = q1("SELECT * FROM sla_versiones WHERE ticket_id=?", t["id"])
    et = actual(t, a, grupos_usuario(usuario, t["cliente_id"]))
    if et and not et.get("tomado_por"):
        ej("UPDATE ticket_etapas SET tomado_por=?, tomado=? WHERE ticket_id=? AND etapa=?", usuario["usuario"], ahora(), t["id"], et["etapa"])
    if et:
        cerrar(t["id"], et["etapa"], "aprobada" if aprobar else "rechazada", usuario["usuario"])
    quien = f"FaceOps ({usuario['usuario']})"
    dur_ap = int((_ahora() - (_dt(t["creado"]) or _ahora())).total_seconds())
    if not aprobar:
        ej("UPDATE tickets SET aprobado_por=?, respuesta=?, aprobado_en=?, estado='rechazado', fin=?, dur_aprobacion_s=? WHERE id=?",
           usuario["id"], comentario, quien, ahora(), dur_ap, t["id"])
        if s:
            ej("UPDATE sla_versiones SET estado='rechazado', aprobado_por=?, aprobado=?, comentario=? WHERE id=?", usuario["usuario"], ahora(), comentario, s["id"])
    elif avanzar(t, a):
        ini = ahora()
        if s:
            ej("UPDATE sla_versiones SET estado='reemplazado' WHERE cliente_id=? AND segmento=? AND estado='vigente'", s["cliente_id"], s["segmento"])
            ej("UPDATE sla_versiones SET estado='vigente', aprobado_por=?, aprobado=?, comentario=? WHERE id=?", usuario["usuario"], ini, comentario, s["id"])
        res = (f"SLA v{s['version']} de {s['segmento']} vigente desde {ini[:16].replace('T', ' ')} UTC (aprobado por {usuario['usuario']})"
               if s else "Sin versión de SLA asociada")
        ej("UPDATE tickets SET aprobado_por=?, respuesta=?, aprobado_en=?, estado='exitoso', inicio=?, fin=?, duracion_s=?, dur_aprobacion_s=?, "
           "dur_ejecucion_s=0, resultado=? WHERE id=?", usuario["id"], comentario, quien, ini, ahora(), dur_ap, dur_ap, res, t["id"])
        if s:
            try:
                c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
                evaluar(c, s["segmento"], forzar=True)
            except Exception as x:  # noqa: BLE001  la medición no detiene la aprobación
                auditar("sistema", t["cliente_id"], "error medición SLA", s["segmento"], {"error": str(x)[:300]})
    auditar(usuario["usuario"], t["cliente_id"], "aprobar" if aprobar else "rechazar", t["numero"],
            {"comentario": comentario, "etapa": (et or {}).get("etapa"), "area": (et or {}).get("area"), "sla": (s or {}).get("id")})


def _instalar():
    """Envuelve la respuesta a aprobaciones: los tickets «Cambio de SLA» los resuelve este módulo; el resto sigue igual."""
    import sys
    from . import operacion
    orig = operacion.resolver_aprobacion
    if getattr(orig, "_sla", False):
        return

    def resolver_aprobacion(usuario, ticket_id, aprobar, comentario=""):
        t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
        if t and t["estado"] == "pendiente_aprobacion" and es_ticket_sla(t):
            return decidir(usuario, t, aprobar, comentario)
        return orig(usuario, ticket_id, aprobar, comentario)
    resolver_aprobacion._sla = True
    resolver_aprobacion._pulsos = getattr(orig, "_pulsos", False)   # si pulsos ya la envolvió, no la vuelve a envolver
    operacion.resolver_aprobacion = resolver_aprobacion
    v = sys.modules.get(__package__ + ".vistas")
    if v is not None and hasattr(v, "resolver_aprobacion"):
        v.resolver_aprobacion = resolver_aprobacion


_instalar()


# ------------------------------------------------------------------ versiones
def _con_datos(r):
    if r:
        r["d"] = dict(DEFECTO, **(j(r.get("datos"), {}) or {}))
    return r


def vigente(cliente_id, segmento):
    iniciar()
    return _con_datos(q1("SELECT * FROM sla_versiones WHERE cliente_id=? AND segmento=? AND estado='vigente' ORDER BY version DESC LIMIT 1",
                         cliente_id, segmento))


def versiones(cliente_id, segmento):
    iniciar()
    return [_con_datos(r) for r in q("SELECT * FROM sla_versiones WHERE cliente_id=? AND segmento=? ORDER BY version DESC", cliente_id, segmento)]


# ------------------------------------------------------------------ período y ventanas de mantenimiento
def periodo_de(d, ref=None):
    """(clave «2026-11» o «2026-T4», inicio UTC, fin UTC, etiqueta) del período que contiene ref, en la zona del SLA."""
    tz = ZoneInfo(d.get("zona") or ZONA)
    r = (ref or _ahora()).astimezone(tz)
    if d.get("ventana") == "trimestral":
        m0 = 3 * ((r.month - 1) // 3) + 1
        ini = datetime(r.year, m0, 1, tzinfo=tz)
        fin = datetime(r.year + (m0 + 3 > 12), (m0 + 2) % 12 + 1, 1, tzinfo=tz)
        return f"{r.year}-T{(m0 - 1) // 3 + 1}", ini.astimezone(timezone.utc), fin.astimezone(timezone.utc), f"T{(m0 - 1) // 3 + 1} {r.year}"
    ini = datetime(r.year, r.month, 1, tzinfo=tz)
    fin = datetime(r.year + (r.month == 12), r.month % 12 + 1, 1, tzinfo=tz)
    return f"{r.year}-{r.month:02d}", ini.astimezone(timezone.utc), fin.astimezone(timezone.utc), f"{MESES[r.month - 1]} {r.year}"


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def periodo_clave(d, clave):
    """Período por su clave («2026-10»)."""
    try:
        if "-T" in clave:
            y, t_ = clave.split("-T")
            ref = datetime(int(y), 3 * (int(t_) - 1) + 2, 15, tzinfo=ZoneInfo(d.get("zona") or ZONA))
        else:
            y, m = clave.split("-")
            ref = datetime(int(y), int(m), 15, tzinfo=ZoneInfo(d.get("zona") or ZONA))
    except (ValueError, TypeError):
        return periodo_de(d)
    return periodo_de(d, ref)


def en_mantenimiento(d, ts):
    """¿El instante (UTC) cae en una ventana de mantenimiento del SLA (día de la semana y horas en su zona)?"""
    loc = ts.astimezone(ZoneInfo(d.get("zona") or ZONA))
    hm = loc.strftime("%H:%M")
    for m in d.get("mantenimiento") or []:
        if m["desde"] <= m["hasta"]:
            if loc.weekday() == int(m["dia"]) and m["desde"] <= hm < m["hasta"]:
                return True
        elif (loc.weekday() == int(m["dia"]) and hm >= m["desde"]) or (loc.weekday() == (int(m["dia"]) + 1) % 7 and hm < m["hasta"]):
            return True
    return False


def minutos_utiles(d, ini, fin):
    """Minutos del intervalo sin las ventanas de mantenimiento (resolución de 5 min)."""
    n, t = 0, ini
    while t < fin:
        if not en_mantenimiento(d, t):
            n += 1
        t += timedelta(seconds=PASO)
    return n * PASO / 60


# ------------------------------------------------------------------ Prometheus
def _prom_rango(consulta, ini, fin, paso):
    import requests
    from .alertas_objeto import PROMETHEUS
    r = requests.get(PROMETHEUS.rstrip("/") + "/api/v1/query_range", timeout=20, verify=False,
                     params={"query": consulta, "start": ini.timestamp(), "end": fin.timestamp(), "step": paso})
    r.raise_for_status()
    res = r.json()["data"]["result"]
    return [(datetime.fromtimestamp(float(t), timezone.utc), float(v)) for t, v in (res[0]["values"] if res else []) if v not in ("NaN", "+Inf", "-Inf")]


def monitor_de(cliente_id, seg):
    """(nombre del objeto de monitoreo seg-<código>, motivo de «requiere medición» de la sonda del VIP o '')."""
    from .segmento_ops import objeto
    o = objeto(cliente_id, seg, [])
    sin = (j(o["datos"], {}) or {}).get("sin_medicion") or {}
    return o["nombre"], sin.get("G01") or ""


def medir_servicio(d, mon, motivo_sin, ini, fin):
    """Disponibilidad, latencia p95, error budget y episodios fuera de servicio desde la sonda blackbox del VIP."""
    out = {"monitor": mon, "fuente": f'Prometheus · probe_success / probe_duration_seconds{{job="faceops-segmentos",faceops_equipo="{mon}",faceops_rol="vip"}}'}
    fin = min(fin, _ahora())
    util_total = None
    if ini >= fin:
        return dict(out, disponibilidad=None, motivo="el período aún no empieza")
    paso = max(PASO, math.ceil((fin - ini).total_seconds() / 10000))
    sel = f'job="faceops-segmentos",faceops_equipo="{mon}",faceops_rol="vip"'
    try:
        ok = _prom_rango(f"max(probe_success{{{sel}}})", ini, fin, paso)
        lat = _prom_rango(f"max(probe_duration_seconds{{{sel}}})", ini, fin, paso)
    except Exception as x:  # noqa: BLE001
        return dict(out, disponibilidad=None, latencia_p95_ms=None, motivo=f"Prometheus no respondió ({type(x).__name__})")
    ok = [(t, v) for t, v in ok if not en_mantenimiento(d, t)]
    if not ok:
        return dict(out, disponibilidad=None, latencia_p95_ms=None,
                    motivo=motivo_sin or f"Prometheus no tiene muestras de la sonda del VIP ({mon}) en el período: activa las alertas del segmento "
                                         "(campana del segmento) para que FaceOps lo mida")
    malos = [t for t, v in ok if v < 1]
    medido = len(ok) * paso / 60
    caido = len(malos) * paso / 60
    disp = 100 * (len(ok) - len(malos)) / len(ok)
    util_total = minutos_utiles(d, *_periodo_completo(d, ini))
    presupuesto = (100 - d["disponibilidad"]) / 100 * util_total
    episodios, cur = [], None
    for t, v in ok:
        if v < 1:
            if cur and (t - cur["fin"]).total_seconds() <= paso * 1.5:
                cur["fin"] = t
            else:
                cur = {"inicio": t, "fin": t}
                episodios.append(cur)
    for e in episodios:
        e["minutos"] = round(((e["fin"] - e["inicio"]).total_seconds() + paso) / 60)
        e["inicio"], e["fin"] = e["inicio"].isoformat(timespec="seconds"), (e["fin"] + timedelta(seconds=paso)).isoformat(timespec="seconds")
    lat_ms = sorted(v * 1000 for t, v in lat if not en_mantenimiento(d, t))
    p95 = lat_ms[min(len(lat_ms) - 1, max(0, math.ceil(0.95 * len(lat_ms)) - 1))] if lat_ms else None
    return dict(out, disponibilidad=round(disp, 3), minutos_medidos=round(medido), minutos_caido=round(caido), minutos_periodo=round(util_total),
                cobertura=round(100 * medido / util_total) if util_total else None, presupuesto_min=round(presupuesto, 1),
                consumo=round(100 * caido / presupuesto, 1) if presupuesto else None, episodios=episodios[-20:], n_episodios=len(episodios),
                latencia_p95_ms=round(p95) if p95 is not None else None, muestras=len(ok), paso_s=paso,
                desde=ok[0][0].isoformat(timespec="seconds"), hasta=ok[-1][0].isoformat(timespec="seconds"))


def _periodo_completo(d, ini):
    _, a, b, _ = periodo_de(d, ini + timedelta(hours=1))
    return a, b


# ------------------------------------------------------------------ tickets del servicio
def prioridad(t):
    """Regla P1–P4 (los tickets aún no traen prioridad propia): ver PRIORIDAD_REGLA."""
    n = f"{t.get('nombre_aap') or ''} {t.get('auto_nombre') or ''} {t.get('motivo') or ''}".lower()
    amb = (j(t.get("filtros"), {}) or {}).get("ambientes") or []
    prod = "produccion" in amb or "producción" in n or t.get("_prod")
    if (t.get("auto_tipo") or "") == "lectura":
        return "P4"
    if prod and any(x in n for x in ("remediación", "remediacion", "responder alerta", "alerta", "caída", "incidente")):
        return "P1"
    return "P2" if prod else "P3"


def _maquinas(cliente_id, segmento):
    from .inventario import segmentos_de
    segs = q("SELECT * FROM segmentos WHERE cliente_id=?", cliente_id)
    return {e["nombre"]: (e.get("ambiente") or "") for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cliente_id)
            if segmento in segmentos_de(e, segs)}


def tickets_servicio(cliente_id, segmento, ini, fin):
    maq = _maquinas(cliente_id, segmento)
    ts = q("SELECT t.*, a.nombre_aap, a.nombre auto_nombre, a.tipo auto_tipo FROM tickets t LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id "
           "WHERE t.cliente_id=? AND t.creado>=? AND t.creado<? AND t.numero NOT LIKE 'DEMO-%' AND t.numero NOT LIKE 'FO-HIS-%' "
           "AND COALESCE(t.motivo,'') NOT LIKE '[demo]%' ORDER BY t.id", cliente_id, ini.isoformat(timespec="seconds"), fin.isoformat(timespec="seconds"))
    out = []
    for t in ts:
        hosts = {h.strip() for h in (t.get("limite") or "").replace(";", ",").split(",") if h.strip()}
        if t.get("segmento") == segmento or hosts & set(maq):
            t["_prod"] = any(maq.get(h) == "produccion" for h in hosts)
            out.append(t)
    return out


def medir_tickets(d, ts):
    """Respuesta (creación → primera atención: toma o respuesta de la aprobación, o inicio), resolución (creación → cierre) y
    aprobación (cada etapa: pedida → respondida) contra los objetivos por prioridad."""
    from .aprobacion_areas import etapas as etapas_t
    hoy = _ahora()
    por = {p: {"n": 0, "resp_ok": 0, "resp_n": 0, "res_ok": 0, "res_n": 0, "resp_min": [], "res_min": []} for p in PRIORIDADES}
    viol, apr = [], {"n": 0, "ok": 0, "horas": []}
    max_ap = d["aprobacion_max_h"] * 60
    for t in ts:
        c = _dt(t["creado"])
        if not c:
            continue
        es = etapas_t(t) if t["estado"] == "pendiente_aprobacion" else q("SELECT * FROM ticket_etapas WHERE ticket_id=? ORDER BY etapa", t["id"])
        for x in es:   # aprobación de cambios (incluye los propios cambios de SLA)
            a_, b_ = _dt(x.get("inicio")), _dt(x.get("fin")) if x.get("fin") else None
            if not a_:
                continue
            m = ((b_ or hoy) - a_).total_seconds() / 60
            if b_ is None and m <= max_ap:
                continue   # pendiente y aún dentro del plazo: no se cuenta todavía
            apr["n"] += 1
            apr["horas"].append(m / 60)
            if m <= max_ap:
                apr["ok"] += 1
            else:
                viol.append({"tipo": "aprobacion", "ticket_id": t["id"], "numero": t["numero"], "minutos": round(m), "objetivo": max_ap,
                             "texto": f"Aprobación de {t['numero']} ({x.get('area') or 'grupo'}) tardó {dur(m)}" + (" y sigue pendiente" if b_ is None else "")
                                      + f" · máximo {dur(max_ap)}"})
        if es_ticket_sla(t) or t["estado"] in FUERA:
            continue   # los cambios de SLA y los rechazados no son atención del servicio
        p = prioridad(t)
        o = d["prioridades"][p]
        por[p]["n"] += 1
        marcas = [_dt(x.get("tomado")) for x in es] + [_dt(x.get("fin")) for x in es if x.get("fin")] + [_dt(t.get("inicio"))]
        marcas = [m_ for m_ in marcas if m_]
        at = min(marcas) if marcas else None
        r_ = ((at or hoy) - c).total_seconds() / 60
        if at or r_ > o["respuesta_min"]:
            por[p]["resp_n"] += 1
            por[p]["resp_min"].append(r_)
            if r_ <= o["respuesta_min"]:
                por[p]["resp_ok"] += 1
            else:
                viol.append({"tipo": "respuesta", "prioridad": p, "ticket_id": t["id"], "numero": t["numero"], "minutos": round(r_), "objetivo": o["respuesta_min"],
                             "texto": f"{p} · {t['numero']}: respuesta en {dur(r_)}" + ("" if at else " (aún sin atender)") + f" · objetivo {dur(o['respuesta_min'])}"})
        cerrado = t["estado"] in CERRADOS and _dt(t.get("fin"))
        s_ = ((_dt(t["fin"]) if cerrado else hoy) - c).total_seconds() / 60
        if cerrado or s_ > o["resolucion_min"]:
            por[p]["res_n"] += 1
            por[p]["res_min"].append(s_)
            if s_ <= o["resolucion_min"]:
                por[p]["res_ok"] += 1
            else:
                viol.append({"tipo": "resolucion", "prioridad": p, "ticket_id": t["id"], "numero": t["numero"], "minutos": round(s_), "objetivo": o["resolucion_min"],
                             "texto": f"{p} · {t['numero']}: resolución en {dur(s_)}" + ("" if cerrado else " (aún abierto)") + f" · objetivo {dur(o['resolucion_min'])}"})
    for p, x in por.items():
        x["resp_prom"] = round(sum(x["resp_min"]) / len(x["resp_min"])) if x["resp_min"] else None
        x["res_prom"] = round(sum(x["res_min"]) / len(x["res_min"])) if x["res_min"] else None
        x.pop("resp_min"), x.pop("res_min")
    apr["prom_h"] = round(sum(apr["horas"]) / len(apr["horas"]), 1) if apr["horas"] else None
    apr.pop("horas")
    return por, apr, viol


# ------------------------------------------------------------------ evaluación del período
def calcular(cliente, segmento, clave=None):
    """Cumplimiento del SLA vigente de un servicio en un período (el actual por defecto). No guarda."""
    s = vigente(cliente["id"], segmento)
    seg = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", cliente["id"], segmento)
    if not s or not seg:
        return None
    d = s["d"]
    per, ini, fin, etiqueta = periodo_clave(d, clave) if clave else periodo_de(d)
    mon, motivo = monitor_de(cliente["id"], seg)
    m = medir_servicio(d, mon, motivo, ini, fin)
    ts = tickets_servicio(cliente["id"], segmento, ini, fin)
    por, apr, viol = medir_tickets(d, ts)
    objetivos = []
    disp = m.get("disponibilidad")
    consumo = m.get("consumo")
    if disp is None:
        objetivos.append({"clave": "disponibilidad", "nombre": "Disponibilidad", "estado": "sin_medicion", "objetivo": pct(d["disponibilidad"], 3),
                          "valor": "sin medición", "detalle": m.get("motivo")})
    else:
        est = "incumple" if disp < d["disponibilidad"] else ("riesgo" if consumo is not None and consumo >= d["umbral_riesgo"] else "cumple")
        objetivos.append({"clave": "disponibilidad", "nombre": "Disponibilidad", "estado": est, "objetivo": pct(d["disponibilidad"], 3),
                          "valor": pct(disp, 3), "detalle": f"{m['minutos_caido']} min fuera de servicio en {m['minutos_medidos']} min medidos "
                                                           f"({m['cobertura']} % del período sin mantenimiento) · error budget consumido {pct(consumo, 1)}"})
        for e in m.get("episodios") or []:
            viol.append({"tipo": "disponibilidad", "minutos": e["minutos"], "inicio": e["inicio"], "fin": e["fin"], "alerta": "G01",
                         "texto": f"Servicio no disponible {e['minutos']} min (sonda del VIP sin HTTP 200) · alerta G01 «Servicio no disponible»"})
    p95 = m.get("latencia_p95_ms")
    if p95 is None:
        objetivos.append({"clave": "latencia", "nombre": "Latencia p95", "estado": "sin_medicion", "objetivo": f"{d['latencia_p95_ms']} ms",
                          "valor": "sin medición", "detalle": m.get("motivo")})
    else:
        est = "cumple" if p95 <= d["latencia_p95_ms"] else "incumple"
        objetivos.append({"clave": "latencia", "nombre": "Latencia p95", "estado": est, "objetivo": f"{d['latencia_p95_ms']} ms", "valor": f"{p95} ms",
                          "detalle": f"p95 de {m.get('muestras')} muestras de la sonda del VIP (cada {m.get('paso_s', PASO) // 60} min, sin mantenimiento)"})
        if est == "incumple":
            viol.append({"tipo": "latencia", "alerta": "G02", "texto": f"Latencia p95 del período {p95} ms sobre el objetivo de {d['latencia_p95_ms']} ms · alerta G02 «Latencia p95 alta»"})
    for clave_, nombre_, k_ok, k_n in (("respuesta", "Tiempo de respuesta", "resp_ok", "resp_n"), ("resolucion", "Tiempo de resolución", "res_ok", "res_n")):
        n = sum(x[k_n] for x in por.values())
        ok = sum(x[k_ok] for x in por.values())
        objetivos.append({"clave": clave_, "nombre": nombre_, "estado": "sin_medicion" if not n else ("cumple" if ok == n else "incumple"),
                          "objetivo": "por prioridad (P1–P4)", "valor": f"{ok} de {n} en plazo" if n else "sin tickets en el período",
                          "detalle": "tickets de FaceOps del servicio" if n else "No hubo tickets del servicio en el período: nada que medir"})
    objetivos.append({"clave": "aprobacion", "nombre": "Aprobación de cambios", "estado": "sin_medicion" if not apr["n"] else ("cumple" if apr["ok"] == apr["n"] else "incumple"),
                      "objetivo": f"máximo {dur(d['aprobacion_max_h'] * 60)}", "valor": f"{apr['ok']} de {apr['n']} en plazo" if apr["n"] else "sin aprobaciones en el período",
                      "detalle": (f"promedio {apr['prom_h']} h por etapa" if apr["prom_h"] is not None else "") or "Etapas de aprobación de los tickets del servicio"})
    medibles = [o for o in objetivos if o["estado"] != "sin_medicion"]
    cumplen = [o for o in medibles if o["estado"] in ("cumple", "riesgo")]
    cump = round(100 * len(cumplen) / len(medibles)) if medibles else None
    if not medibles:
        estado = "sin_medicion"
    elif any(o["estado"] == "incumple" for o in medibles):
        estado = "incumple"
    elif any(o["estado"] == "riesgo" for o in medibles):
        estado = "riesgo"
    else:
        estado = "cumple"
    return {"segmento": segmento, "segmento_id": seg["id"], "sla_id": s["id"], "version": s["version"], "demo": bool(s.get("demo")), "d": d,
            "periodo": per, "etiqueta": etiqueta, "inicio": ini.isoformat(timespec="seconds"), "fin": fin.isoformat(timespec="seconds"),
            "en_curso": fin > _ahora(), "estado": estado, "parcial": bool(medibles) and len(medibles) < len(objetivos),
            "sin_medir": [o["nombre"] for o in objetivos if o["estado"] == "sin_medicion"], "cumplimiento": cump, "consumo": consumo, "medicion": m, "objetivos": objetivos,
            "por_prioridad": por, "aprobacion": apr, "violaciones": viol, "n_tickets": len(ts), "calculado": ahora()}


def evaluar(cliente, segmento, forzar=False, clave=None):
    """Calcula, guarda la medición del período, y abre o cierra la alerta «SLA en riesgo» y las iniciativas de mejora."""
    iniciar()
    if not forzar and not clave:
        s = vigente(cliente["id"], segmento)
        if s:
            per = periodo_de(s["d"])[0]
            m = q1("SELECT * FROM sla_mediciones WHERE cliente_id=? AND segmento=? AND periodo=?", cliente["id"], segmento, per)
            if m and m["sla_id"] == s["id"] and (_ahora() - (_dt(m["calculado"]) or _ahora())).total_seconds() < 600:
                return j(m["resultado"], {})
    r = calcular(cliente, segmento, clave)
    if not r:
        return None
    ej("INSERT INTO sla_mediciones(cliente_id, segmento, periodo, sla_id, calculado, estado, cumplimiento, consumo, resultado) VALUES (?,?,?,?,?,?,?,?,?) "
       "ON CONFLICT(cliente_id, segmento, periodo) DO UPDATE SET sla_id=excluded.sla_id, calculado=excluded.calculado, estado=excluded.estado, "
       "cumplimiento=excluded.cumplimiento, consumo=excluded.consumo, resultado=excluded.resultado",
       cliente["id"], segmento, r["periodo"], r["sla_id"], r["calculado"], r["estado"], r["cumplimiento"], r["consumo"], json.dumps(r, ensure_ascii=False, default=str))
    if not clave:
        _alerta(cliente, r)
        _iniciativas(cliente, r)
    return r


def destinatarios(cliente_id, d):
    """Dueño del servicio (correo del grupo) y consumidor; en el laboratorio de Invoke, todo a LAB_PARA."""
    from .grupos_dueno import grupo
    from .semillas import limpia
    if not limpia():
        return LAB_PARA
    gr = grupo(cliente_id, d.get("dueno") or "") or {}
    ps = [gr.get("correo") or "", d.get("correo_consumidor") or ""]
    return ", ".join(dict.fromkeys(p for p in ps if p))


def _zona_para(para):
    from .correo import zona_de
    return zona_de(para) or ZONA


def _alerta(cliente, r):
    """«SLA en riesgo»: el error budget consumido pasa el umbral del SLA. Se abre una vez (correo breve) y se cierra sola."""
    from .correo import enviar
    from .hora_local import en_zona
    ab = q1("SELECT * FROM sla_alertas WHERE cliente_id=? AND segmento=? AND cerrada IS NULL ORDER BY id DESC LIMIT 1", cliente["id"], r["segmento"])
    c_, u_ = r.get("consumo"), r["d"]["umbral_riesgo"]
    if c_ is not None and c_ >= u_:
        if ab:
            ej("UPDATE sla_alertas SET consumo=? WHERE id=?", c_, ab["id"])
            return
        para = destinatarios(cliente["id"], r["d"])
        cuerpo = (f"SLA en riesgo · {r['segmento']} · {cliente['nombre']}\n\n"
                  f"El error budget de {r['etiqueta']} va en {pct(c_, 1)} (umbral {pct(u_, 0)}). Disponibilidad medida "
                  f"{r['objetivos'][0]['valor']} contra {r['objetivos'][0]['objetivo']}.\n"
                  f"Medido el {en_zona(_ahora(), _zona_para(para))}.\n\nDetalle: {url_publica(cliente, r['segmento'])}")
        res = enviar(para, f"FaceOps · SLA en riesgo: {r['segmento']} ({pct(c_, 0)} del error budget)", cuerpo) if para else "sin destinatario"
        ej("INSERT INTO sla_alertas(cliente_id, segmento, sla_id, abierta, consumo, umbral, para, resultado) VALUES (?,?,?,?,?,?,?,?)",
           cliente["id"], r["segmento"], r["sla_id"], ahora(), c_, u_, para, res)
        auditar("sistema", cliente["id"], "alerta SLA en riesgo", r["segmento"], {"consumo": c_, "umbral": u_, "correo": res})
    elif ab and (c_ is None or c_ < u_):
        ej("UPDATE sla_alertas SET cerrada=?, consumo=? WHERE id=?", ahora(), c_, ab["id"])


def alertas(cliente_id, segmento=None, n=20):
    iniciar()
    if segmento:
        return q("SELECT * FROM sla_alertas WHERE cliente_id=? AND segmento=? ORDER BY id DESC LIMIT ?", cliente_id, segmento, n)
    return q("SELECT * FROM sla_alertas WHERE cliente_id=? ORDER BY id DESC LIMIT ?", cliente_id, n)


def _iniciativas(cliente, r):
    """Cada objetivo incumplido abre (o actualiza) una iniciativa de mejora del grupo dueño en el registro de Gobierno ›
    Reportes; se cierra cuando una medición posterior lo deja en cumplimiento."""
    sec = r["d"].get("dueno") or APRUEBA
    for o in r["objetivos"]:
        pilar = f"SLA {r['segmento']} · {o['nombre']}"
        ab = q1("SELECT * FROM reporte_iniciativas WHERE cliente_id=? AND pilar=? AND estado!='cerrada'", cliente["id"], pilar)
        vs = [v["texto"] for v in r["violaciones"] if v["tipo"] == o["clave"] or (o["clave"] == "disponibilidad" and v["tipo"] == "disponibilidad")][:8]
        pct_ = r["cumplimiento"] if r["cumplimiento"] is not None else 0
        if o["estado"] == "incumple":
            if ab:
                ej("UPDATE reporte_iniciativas SET pct_actual=?, beneficio=?, criterios=?, actualizada=? WHERE id=?",
                   pct_, 100 - pct_, json.dumps(vs or [o["detalle"]], ensure_ascii=False), ahora(), ab["id"])
            else:
                comp = (_ahora() + timedelta(days=14)).strftime("%Y-%m-%d")
                tk = next((v for v in r["violaciones"] if v.get("ticket_id") and v["tipo"] == o["clave"]), None)
                ej("INSERT INTO reporte_iniciativas(cliente_id, seccion, pilar, estado, abierta, compromiso, pct_inicial, pct_actual, beneficio, criterios, "
                   "ticket_id, ticket_numero, actualizada, nota) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cliente["id"], sec, pilar, "abierta", ahora(), comp,
                   pct_, pct_, 100 - pct_, json.dumps(vs or [o["detalle"]], ensure_ascii=False), (tk or {}).get("ticket_id"), (tk or {}).get("numero") or "",
                   ahora(), f"Violación del SLA v{r['version']} ({r['etiqueta']}): {o['valor']} contra {o['objetivo']}"[:300])
                auditar("sistema", cliente["id"], "iniciativa de mejora por SLA", pilar, {"periodo": r["periodo"]})
        elif ab and o["estado"] in ("cumple", "riesgo"):
            ej("UPDATE reporte_iniciativas SET estado='cerrada', cerrada=?, pct_actual=?, beneficio=0, actualizada=? WHERE id=?", ahora(), pct_, ahora(), ab["id"])


def iniciativas_sla(cliente_id):
    iniciar()
    return q("SELECT * FROM reporte_iniciativas WHERE cliente_id=? AND pilar LIKE 'SLA %' ORDER BY CASE estado WHEN 'cerrada' THEN 1 ELSE 0 END, id DESC", cliente_id)


def ultima(cliente_id, segmento):
    """Última medición guardada del SLA vigente (sin consultar Prometheus: para la salud del segmento)."""
    iniciar()
    s = vigente(cliente_id, segmento)
    if not s:
        return None
    m = q1("SELECT * FROM sla_mediciones WHERE cliente_id=? AND segmento=? AND sla_id=? ORDER BY periodo DESC LIMIT 1", cliente_id, segmento, s["id"])
    return j(m["resultado"], {}) if m else None


def salud_items(cliente, segmento):
    """Situaciones del SLA para la salud del segmento (topologia.salud_segmento)."""
    r = ultima(cliente["id"], segmento)
    if not r:
        return []
    out, rem = [], "Ver Gobierno › Reportes › SLA: violaciones con su ticket o alerta y la iniciativa de mejora abierta"
    for o in r.get("objetivos") or []:
        area = {"disponibilidad": "Disponibilidad", "latencia": "Rendimiento"}.get(o["clave"], "Excelencia operativa")
        if o["estado"] == "incumple":
            out.append({"nivel": "alta" if o["clave"] == "disponibilidad" else "media", "area": area,
                        "titulo": f"SLA v{r['version']} incumplido · {o['nombre']}: {o['valor']} (objetivo {o['objetivo']})", "detalle": o.get("detalle") or "", "remedio": rem})
        elif o["estado"] == "riesgo":
            out.append({"nivel": "media", "area": area, "titulo": f"SLA en riesgo: error budget consumido {pct(r.get('consumo'), 1)} (umbral {pct(r['d']['umbral_riesgo'], 0)})",
                        "detalle": o.get("detalle") or "", "remedio": rem})
        elif o["estado"] == "sin_medicion" and o["clave"] == "disponibilidad":
            out.append({"nivel": "info", "area": area, "titulo": "SLA sin medición de disponibilidad", "detalle": o.get("detalle") or "",
                        "remedio": "Activar las alertas del segmento (campana) para que Prometheus mida la sonda del VIP"})
    return out


# ------------------------------------------------------------------ servicios y su estado
def servicios(cliente, usuario=None):
    """Servicios de negocio visibles (segmentos) con su SLA vigente, el propuesto y la última medición."""
    from .permisos import segmentos_visibles
    iniciar()
    segs = segmentos_visibles(usuario or g.usuario, cliente["id"])
    out = []
    for s in segs:
        v = vigente(cliente["id"], s["nombre"])
        p = _con_datos(q1("SELECT * FROM sla_versiones WHERE cliente_id=? AND segmento=? AND estado='propuesto' ORDER BY id DESC LIMIT 1", cliente["id"], s["nombre"]))
        out.append({"seg": s, "vigente": v, "propuesto": p, "medicion": ultima(cliente["id"], s["nombre"]) if v else None})
    out.sort(key=lambda x: (0 if x["vigente"] else (1 if x["propuesto"] else 2), x["seg"].get("grupo") != "aplicaciones", x["seg"]["nombre"]))
    return out


def con_sla(cliente_id):
    iniciar()
    return [r["segmento"] for r in q("SELECT DISTINCT segmento FROM sla_versiones WHERE cliente_id=? AND estado='vigente' ORDER BY segmento", cliente_id)]


# ------------------------------------------------------------------ reporte periódico y envío
def url_publica(cliente, segmento=None):
    pub = os.environ.get("FACEOPS_URL_PUBLICA", "https://faceops.34.176.42.56.sslip.io:8843/faceops").rstrip("/")
    from urllib.parse import quote
    return f"{pub}/{(cliente.get('codigo') or '').lower()}/gobierno/reportes/sla" + (f"?segmento={quote(segmento)}" if segmento else "")


def _proximo(frecuencia, dia, hora_, zona, desde=None):
    """Próximo envío (ISO UTC): el día del mes indicado a la hora indicada, en la zona del destinatario."""
    if frecuencia == "pausado":
        return None
    tz = ZoneInfo(zona or ZONA)
    hh, mm = (int(x) for x in (hora_ or "08:00").split(":")[:2])
    base = (desde or _ahora()).astimezone(tz)
    paso = 3 if frecuencia == "trimestral" else 1
    y, m = base.year, base.month
    if frecuencia == "trimestral":
        m = 3 * ((m - 1) // 3) + 1
    for _ in range(6):
        d_ = min(int(dia), calendar.monthrange(y, m)[1])
        cand = datetime(y, m, d_, hh, mm, tzinfo=tz)
        if cand > base:
            return cand.astimezone(timezone.utc).isoformat(timespec="seconds")
        m += paso
        if m > 12:
            y, m = y + 1, m - 12
    return None


def programa(cliente_id):
    iniciar()
    p = q1("SELECT * FROM sla_programa WHERE cliente_id=?", cliente_id)
    if not p:
        c = q1("SELECT * FROM clientes WHERE id=?", cliente_id) or {}
        zona = _zona_para(destinatarios(cliente_id, {})) if c else ZONA
        p = {"cliente_id": cliente_id, "frecuencia": "mensual", "dia": 1, "hora": "08:00", "proximo": _proximo("mensual", 1, "08:00", zona), "actualizado": ahora()}
        ej("INSERT OR IGNORE INTO sla_programa(cliente_id, frecuencia, dia, hora, proximo, actualizado) VALUES (?,?,?,?,?,?)",
           cliente_id, p["frecuencia"], p["dia"], p["hora"], p["proximo"], p["actualizado"])
    return dict(p)


def guardar_programa(cliente_id, frecuencia, dia, hora_, quien):
    if frecuencia not in ("mensual", "trimestral", "pausado"):
        raise ValueError("Frecuencia no válida")
    dia = int(dia or 1)
    if not 1 <= dia <= 28:
        raise ValueError("Día del mes entre 1 y 28")
    hh, _, mm = (hora_ or "").partition(":")
    if not (hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60):
        raise ValueError("Hora no válida (HH:MM)")
    programa(cliente_id)
    h = f"{int(hh):02d}:{int(mm):02d}"
    ej("UPDATE sla_programa SET frecuencia=?, dia=?, hora=?, proximo=?, actualizado=? WHERE cliente_id=?", frecuencia, dia, h,
       _proximo(frecuencia, dia, h, _zona_para(destinatarios(cliente_id, {}))), ahora(), cliente_id)
    auditar(quien, cliente_id, "programar reporte de SLA", "SLA", {"frecuencia": frecuencia, "dia": dia, "hora": h})


def reporte(cliente, clave=None):
    """Medición de todos los servicios con SLA vigente (período actual o el pedido)."""
    out = []
    for seg in con_sla(cliente["id"]):
        try:
            r = evaluar(cliente, seg, clave=clave)
        except Exception as x:  # noqa: BLE001
            current_app.logger.warning("SLA %s: %s", seg, x)
            r = None
        if r:
            out.append(r)
    return out


def cuerpo_correo(cliente, rs, para, etiqueta):
    from .hora_local import en_zona
    ok = sum(1 for r in rs if r["estado"] in ("cumple", "riesgo"))
    med = sum(1 for r in rs if r["estado"] != "sin_medicion")
    lin = [f"Cumplimiento de SLA · {cliente['nombre']} · {etiqueta}", f"Medido el {en_zona(_ahora(), _zona_para(para))}.", "",
           f"Servicios que cumplen: {ok} de {med} con medición" + (f" ({len(rs) - med} sin medición)" if len(rs) > med else ""), ""]
    for r in rs:
        d0 = r["objetivos"][0]
        lin.append(f"  {r['segmento']} (SLA v{r['version']}{', demo' if r.get('demo') else ''}): {TEXTO[r['estado']]}"
                   + (f" en lo medido (sin medición: {', '.join(r['sin_medir']).lower()})" if r.get("parcial") else "") + f" · disponibilidad {d0['valor']} "
                   f"(objetivo {d0['objetivo']})" + (f" · error budget {pct(r['consumo'], 1)}" if r.get("consumo") is not None else "")
                   + f" · {len(r['violaciones'])} violación{'es' if len(r['violaciones']) != 1 else ''}")
    lin += ["", f"Detalle, violaciones e iniciativas: {url_publica(cliente)}", "", "Correo enviado por FaceOps al dueño de cada servicio y a su consumidor."]
    return "\n".join(lin)


def enviar(cliente, tipo="manual", quien="sistema", clave=None):
    from .correo import enviar as smtp
    iniciar()
    rs = reporte(cliente, clave)
    if not rs:
        raise ValueError("No hay servicios con SLA vigente: no hay reporte que enviar")
    para = ", ".join(dict.fromkeys(p.strip() for r in rs for p in destinatarios(cliente["id"], r["d"]).split(",") if p.strip()))
    etiqueta = rs[0]["etiqueta"]
    ok = sum(1 for r in rs if r["estado"] in ("cumple", "riesgo"))
    asunto = (f"FaceOps · SLA {etiqueta}: {ok} de {len(rs)} servicio{'s' if len(rs) != 1 else ''} cumple{'n' if ok != 1 else ''}"
              + (" (medición parcial)" if any(r.get("parcial") or r["estado"] == "sin_medicion" for r in rs) else ""))
    res = smtp(para, asunto, cuerpo_correo(cliente, rs, para, etiqueta)) if para else "sin destinatario registrado"
    resumen = {r["segmento"]: {"estado": r["estado"], "cumplimiento": r["cumplimiento"], "consumo": r["consumo"], "version": r["version"],
                               "violaciones": len(r["violaciones"]), "disponibilidad": r["objetivos"][0]["valor"]} for r in rs}
    ej("INSERT INTO sla_envios(cliente_id, fecha, periodo, tipo, quien, para, asunto, resumen, resultado) VALUES (?,?,?,?,?,?,?,?,?)",
       cliente["id"], ahora(), rs[0]["periodo"], tipo, quien, para, asunto, json.dumps(resumen, ensure_ascii=False), res)
    auditar(quien, cliente["id"], "enviar reporte de SLA", etiqueta, {"para": para, "tipo": tipo, "resultado": res})
    return {"para": para, "asunto": asunto, "resultado": res}


def envios(cliente_id, n=30):
    iniciar()
    out = q("SELECT * FROM sla_envios WHERE cliente_id=? ORDER BY id DESC LIMIT ?", cliente_id, n)
    for e in out:
        e["resumen"] = j(e["resumen"], {}) or {}
    return out


def revisar(cliente):
    """Medición de cada SLA vigente (cada 15 min), su alerta y el envío programado vencido."""
    g.cliente = cliente
    for seg in con_sla(cliente["id"]):
        try:
            evaluar(cliente, seg)
        except Exception as x:  # noqa: BLE001
            auditar("sistema", cliente["id"], "error medición SLA", seg, {"error": str(x)[:300]})
    p = programa(cliente["id"])
    if p["frecuencia"] != "pausado" and p["proximo"] and p["proximo"] <= _ahora().isoformat(timespec="seconds"):
        if con_sla(cliente["id"]):
            try:   # el reporte programado es del período que acaba de cerrar
                s = vigente(cliente["id"], con_sla(cliente["id"])[0])
                clave = periodo_de(s["d"], periodo_de(s["d"])[1] - timedelta(days=1))[0]
                enviar(cliente, "programado", "sistema", clave)
            except Exception as x:  # noqa: BLE001
                auditar("sistema", cliente["id"], "error reporte de SLA", "programador", {"error": str(x)[:300]})
        ej("UPDATE sla_programa SET proximo=? WHERE cliente_id=?", _proximo(p["frecuencia"], p["dia"], p["hora"],
                                                                         _zona_para(destinatarios(cliente["id"], {})), _ahora() + timedelta(minutes=1)), cliente["id"])


def iniciar_programador(app, cada=900):
    def bucle():
        time.sleep(90)
        while True:
            try:
                with app.app_context():
                    iniciar()
                    for c in q("SELECT * FROM clientes WHERE activo=1"):
                        if not q1("SELECT 1 FROM sla_versiones WHERE cliente_id=? AND estado='vigente'", c["id"]):
                            continue
                        try:
                            revisar(c)
                        except Exception as x:  # noqa: BLE001
                            auditar("sistema", c["id"], "error SLA", "programador", {"error": str(x)[:300]})
            except Exception:  # noqa: BLE001
                pass
            time.sleep(cada)
    threading.Thread(target=bucle, daemon=True, name="faceops-sla").start()


# ------------------------------------------------------------------ ITIL
def itil_sla(cliente_id):
    """Para la evaluación ITIL (Gestión de niveles de servicio): SLA vigentes aprobados por ticket, servicios medidos y
    envíos del reporte que el servidor de correo aceptó."""
    iniciar()
    vig = q("SELECT v.segmento, v.version, v.ticket_numero FROM sla_versiones v JOIN tickets t ON t.id=v.ticket_id "
            "WHERE v.cliente_id=? AND v.estado='vigente' AND t.estado='exitoso'", cliente_id)
    med = q1("SELECT COUNT(DISTINCT segmento) n FROM sla_mediciones WHERE cliente_id=? AND estado!='sin_medicion'", cliente_id)["n"]
    env = q1("SELECT COUNT(*) n FROM sla_envios WHERE cliente_id=? AND resultado LIKE 'enviado%'", cliente_id)["n"]
    p = q1("SELECT frecuencia FROM sla_programa WHERE cliente_id=?", cliente_id)
    return {"vigentes": len(vig), "medidos": med, "envios": env, "programado": bool(p and p["frecuencia"] != "pausado"),
            "tickets": [v["ticket_numero"] for v in vig]}


# ------------------------------------------------------------------ pantallas
def _seg(sid):
    from .permisos import puede_ver_segmento
    s = q1("SELECT * FROM segmentos WHERE cliente_id=? AND id=?", g.cliente["id"], sid)
    if not s or not puede_ver_segmento(g.usuario, g.cliente["id"], s["nombre"]):
        abort(404)
    return s


@bp.route("/gobierno/sla")
@requiere_login
def sla():
    """Gobierno › SLA: un SLA por servicio de negocio, con versión, estado y su medición del período."""
    from .grupos_dueno import grupos
    sid = request.args.get("servicio", type=int)
    ss = servicios(g.cliente)
    sel = next((x for x in ss if x["seg"]["id"] == sid), None) if sid else None
    hist = versiones(g.cliente["id"], sel["seg"]["nombre"]) if sel else []
    base = (sel["propuesto"] or sel["vigente"]) if sel else None
    hoy = _ahora().strftime("%Y-%m-%d")
    form = dict(DEFECTO, dueno=APRUEBA, consumidor="", correo_consumidor="", vigencia_desde=hoy,
                vigencia_hasta=(_ahora() + timedelta(days=365)).strftime("%Y-%m-%d"), **({} if not base else {}))
    if base:
        form.update(base["d"])
    return render_template("sla.html", ss=ss, sel=sel, hist=hist, form=form, grupos=grupos(g.cliente["id"]), ESTADOS=ESTADOS, COLOR=COLOR, TEXTO=TEXTO,
                           DIAS=DIAS, VENTANAS=VENTANAS, PRIORIDADES=PRIORIDADES, REGLA=PRIORIDAD_REGLA, dur=dur, pct=pct,
                           escribe=g.perfil["modo"] == "escritura", hay_reportes="consola.reportes" in current_app.view_functions)


@bp.route("/gobierno/sla/<int:sid>/proponer", methods=["POST"])
@requiere_login
def sla_proponer(sid):
    _escritura()
    s = _seg(sid)
    try:
        r = proponer(g.cliente, g.usuario, s, de_formulario(request.form), request.form.get("motivo", ""))
        flash(f"SLA v{r['version']} propuesto: ticket {r['numero']} esperando la aprobación del grupo dueño", "ok")
        return redirect(url_for("consola.ticket", tid=r["ticket_id"]))
    except ValueError as x:
        flash(str(x), "error")
    return redirect(url_for("consola.sla", servicio=sid) + "#proponer")


@bp.route("/gobierno/reportes/sla")
@requiere_login
def sla_reporte():
    """Gobierno › Reportes › SLA: cumplimiento del período por servicio, violaciones con su enlace, iniciativas y envíos."""
    clave = request.args.get("periodo") or None
    rs = reporte(g.cliente, clave)
    segn = request.args.get("segmento") or (min(rs, key=lambda r: ({"incumple": 0, "riesgo": 1, "sin_medicion": 2, "cumple": 3}[r["estado"]], r["segmento"]))["segmento"] if rs else "")
    sel = next((r for r in rs if r["segmento"] == segn), None)
    periodos = []
    if rs:
        d0 = rs[0]["d"]
        ref = _ahora()
        for _ in range(6):
            k, ini, _, et = periodo_de(d0, ref)
            periodos.append((k, et))
            ref = ini - timedelta(days=2)
    prog = programa(g.cliente["id"])
    return render_template("sla_reporte.html", rs=rs, sel=sel, segn=segn, periodos=periodos, clave=clave or (rs[0]["periodo"] if rs else ""),
                           prog=prog, envios=envios(g.cliente["id"]), alertas_sla=alertas(g.cliente["id"]), inis=iniciativas_sla(g.cliente["id"]),
                           COLOR=COLOR, TEXTO=TEXTO, REGLA=PRIORIDAD_REGLA, PRIORIDADES=PRIORIDADES, dur=dur, pct=pct, LAB_PARA=LAB_PARA,
                           lab=not __import__("app.semillas", fromlist=["x"]).limpia(), smtp=bool(os.environ.get("FACEOPS_SMTP_HOST")),
                           escribe=g.perfil["modo"] == "escritura", hay_reportes="consola.reportes" in current_app.view_functions)


@bp.route("/gobierno/reportes/sla/enviar", methods=["POST"])
@requiere_login
def sla_enviar():
    _escritura()
    try:
        r = enviar(g.cliente, "manual", g.usuario["usuario"], request.form.get("periodo") or None)
        flash(f"Reporte de SLA: {r['resultado']}", "ok" if r["resultado"].startswith("enviado") else "error")
    except ValueError as x:
        flash(str(x), "error")
    return redirect(url_for("consola.sla_reporte", periodo=request.form.get("periodo") or None) + "#envios")


@bp.route("/gobierno/reportes/sla/programa", methods=["POST"])
@requiere_login
def sla_programa():
    _escritura()
    f = request.form
    try:
        guardar_programa(g.cliente["id"], f.get("frecuencia", ""), f.get("dia", "1"), f.get("hora", ""), g.usuario["usuario"])
        flash("Programa de envío guardado", "ok")
    except ValueError as x:
        flash(str(x), "error")
    return redirect(url_for("consola.sla_reporte") + "#envios")
