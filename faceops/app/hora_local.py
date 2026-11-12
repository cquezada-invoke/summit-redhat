"""Hora local de quien mira. Regla única: el servidor guarda y entrega en UTC (ISO 8601 con Z) y el navegador convierte
a su propia zona con Intl.DateTimeFormat (static/hora-local.js). Sin JavaScript queda la hora en UTC con su etiqueta.

Lectura de lo guardado (sin migrar nada):
- «2026-11-11T22:07:00+00:00» (bd.ahora) y «...Z» (AAP, Alertmanager): ya traen zona.
- Valores sin zona («2026-11-11 22:07:00» de datetime('now') de SQLite o datetime.now() del contenedor, que corre en
  UTC): se leen como UTC.
- «2026-11-11» (solo día: vencimientos, contratos, fechas de calendario): no tienen hora y no se convierten.

Correos y notificaciones no tienen navegador: usan la zona del destinatario (la de su perfil o, si no la definió, la que
reportó su navegador en el último ingreso) y, si no se conoce, UTC con la etiqueta «UTC»."""
import re
from datetime import date, datetime, timedelta, timezone

from markupsafe import Markup, escape

_SOLO_DIA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:[.,]\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$")
# Valores sin zona: UTC (FaceOps: datetime('now') de SQLite y contenedor en UTC). FaceAdmin guarda datetime.now() del
# proceso, así que allí se leen en la zona del sistema (NAIVE_LOCAL = True), que en su contenedor también es UTC.
NAIVE_LOCAL = False
FORMATOS = {"fecha-hora": "%d-%m-%Y %H:%M", "segundos": "%d-%m-%Y %H:%M:%S", "hora": "%H:%M", "hora-seg": "%H:%M:%S",
            "fecha": "%d-%m-%Y", "rel": "%d-%m-%Y %H:%M"}


def a_utc(v):
    """datetime con zona UTC, o None si no es un instante (vacío, solo día o texto que no es fecha)."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        d = v
    elif isinstance(v, date):
        return None
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        d = datetime.fromtimestamp(v / 1000 if v > 10 ** 11 else v, timezone.utc)
    else:
        s = str(v).strip()
        m = _ISO.match(s)
        if not m or _SOLO_DIA.match(s):
            return None
        z = m.group(3) or ""   # sin zona: queda ingenuo y se resuelve abajo (UTC o zona del sistema)
        z = "+00:00" if z == "Z" else (z if not z or ":" in z else z[:3] + ":" + z[3:])
        try:
            d = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}{z}")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.astimezone() if NAIVE_LOCAL else d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def iso_z(v):
    """«2026-11-11T22:07:00Z» (lo que se entrega al navegador y en los JSON)."""
    d = a_utc(v)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else ""


def _plano(v):
    """Lo que no es un instante se muestra tal cual (un día de calendario, «nunca», «ayer»…)."""
    s = str(v)
    return s.replace("T", " ")[:16] if re.match(r"^\d{4}-\d{2}-\d{2}T", s) else s


def relativo(d, ahora=None):
    seg = int(((ahora or datetime.now(timezone.utc)) - d).total_seconds())
    fut, seg = seg < 0, abs(seg)
    for lim, div, uno, varios in ((60, 1, "segundo", "segundos"), (3600, 60, "minuto", "min"), (86400, 3600, "hora", "h"),
                                  (86400 * 30, 86400, "día", "días"), (86400 * 365, 86400 * 30, "mes", "meses"), (10 ** 12, 86400 * 365, "año", "años")):
        if seg < lim:
            n = max(1, seg // div) if div > 1 else seg
            t = f"{n} {uno if n == 1 else varios}"
            return ("en " if fut else "hace ") + t
    return ""


def texto_utc(v, modo="fecha-hora", vacio="—"):
    """Texto plano en UTC con su etiqueta («11-11-2026 22:07 UTC»): para atributos title y textos armados en Python.
    hora-local.js lo convierte también dentro de los title."""
    if v in (None, ""):
        return vacio
    d = a_utc(v)
    if not d:
        return _plano(v)
    return d.strftime(FORMATOS.get(modo, FORMATOS["fecha-hora"])) + " UTC"


def html(v, modo="fecha-hora", vacio="—"):
    """<time datetime="…Z" data-local="modo">11-11-2026 22:07 UTC</time>: el navegador lo pasa a la hora local."""
    if v in (None, ""):
        return vacio
    d = a_utc(v)
    if not d:
        return _plano(v)
    iso = d.strftime("%Y-%m-%dT%H:%M:%SZ")
    txt = relativo(d) if modo == "rel" else d.strftime(FORMATOS.get(modo, FORMATOS["fecha-hora"])) + " UTC"
    return Markup(f'<time datetime="{iso}" data-local="{escape(modo)}" title="Hora UTC">{escape(txt)}</time>')


# ------------------------------------------------------------------ correos y documentos (sin navegador)
def zona_valida(z):
    z = (z or "").strip()
    if not z or len(z) > 64 or not re.match(r"^[A-Za-z0-9_+\-/]+$", z):
        return ""
    if z.upper() == "UTC":
        return "UTC"
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(z)
        return z
    except Exception:  # noqa: BLE001  sin base de zonas en la imagen o zona inexistente
        return ""


def etiqueta_offset(d):
    off = d.utcoffset() or timedelta(0)
    m = int(off.total_seconds() // 60)
    if m == 0:
        return "UTC"
    h, r = divmod(abs(m), 60)
    return f"UTC{'+' if m > 0 else '−'}{h}{f':{r:02d}' if r else ''}"


def en_zona(v, zona="", modo="fecha-hora", vacio="—"):
    """Texto para un correo o un PDF: «11-11-2026 19:07 (America/Santiago, UTC−3)» o «11-11-2026 22:07 UTC»."""
    if v in (None, ""):
        return vacio
    d = a_utc(v)
    if not d:
        return _plano(v)
    fmt = FORMATOS.get(modo, FORMATOS["fecha-hora"])
    zona = zona_valida(zona)
    if zona and zona != "UTC":
        from zoneinfo import ZoneInfo
        dl = d.astimezone(ZoneInfo(zona))
        return f"{dl.strftime(fmt)} ({zona}, {etiqueta_offset(dl)})"
    return d.strftime(fmt) + " UTC"


def ahora_en(zona="", modo="fecha-hora"):
    return en_zona(datetime.now(timezone.utc), zona, modo)


_EN_TEXTO = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}| UTC)?")


def en_texto(v):
    """Texto guardado que trae horas adentro («lanzada 2026-11-11 22:07 en host»): cada hora pasa a <time>."""
    if v in (None, ""):
        return ""
    s, out, i = str(v), [], 0
    for m in _EN_TEXTO.finditer(s):
        out += [escape(s[i:m.start()]), html(m.group(0).replace(" UTC", "Z"))]
        i = m.end()
    out.append(escape(s[i:]))
    return Markup("".join(str(x) for x in out))


def registrar_filtros(app):
    """Filtros Jinja comunes: |fecha (fecha y hora local), |fecha('hora'|'segundos'|'fecha'|'rel'), |fecha_txt (texto UTC
    para atributos title), |iso_z (para data-* y JSON) y |fechas_texto (texto libre con horas adentro)."""
    app.jinja_env.filters["fecha"] = html
    app.jinja_env.filters["fecha_txt"] = texto_utc
    app.jinja_env.filters["iso_z"] = iso_z
    app.jinja_env.filters["hace"] = lambda v, vacio="—": html(v, "rel", vacio)
    app.jinja_env.filters["fechas_texto"] = en_texto
