"""Administración › Uso de IA: dónde interviene la IA en FaceOps, con qué modelo, qué datos envía, si decide o solo sugiere,
si está activa y cuántas llamadas hubo. La lista vive en datos/ia-usos.yml (declarativa); ia_badge('<id>') pone el distintivo
«IA» en la pantalla o botón correspondiente, con enlace a esta página."""
import os
from datetime import datetime, timedelta, timezone

import yaml
from flask import g, render_template, url_for
from markupsafe import Markup, escape

from . import requiere_login
from .vistas import bp

ARCH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "ia-usos.yml")
_cache = {"t": 0, "d": None}


def datos():
    try:
        m = os.path.getmtime(ARCH)
        if _cache["d"] is None or m != _cache["t"]:
            _cache.update(t=m, d=yaml.safe_load(open(ARCH, encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError):
        return {"usos": []}
    return _cache["d"]


def _activo(tipo):
    if tipo == "agente":
        from . import agente
        return agente.disponible()
    if tipo == "fabrica":
        return bool(os.environ.get("FACEOPS_FABRICA_URL") and os.environ.get("FACEOPS_FABRICA_TOKEN"))
    return None


def _conteo(regla):
    if not (regla or "").startswith("auditoria:"):
        return None
    from .bd import q1
    desde = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    cid = (g.get("cliente") or {}).get("id")
    r = q1("SELECT COUNT(*) n FROM auditoria WHERE accion=? AND cuando>=?" + (" AND cliente_id=?" if cid else ""),
           regla.split(":", 1)[1], desde, *([cid] if cid else []))
    return (r or {}).get("n", 0)


def _enlace(e):
    try:
        return url_for(e) if e and not e.startswith(("http", "/")) else (e or "")
    except Exception:  # noqa: BLE001 - un endpoint que no existe no rompe la página
        return ""


@bp.app_template_global("ia_badge")
def ia_badge(clave="", texto="IA"):
    u = next((x for x in datos().get("usos") or [] if x.get("id") == clave), None)
    tip = ("Interviene IA: " + (u.get("que_hace") or "").strip()[:160] + " · " + (u.get("decide") or "")) if u else "Interviene IA"
    try:
        href = url_for("consola.uso_ia") + (f"#ia-{clave}" if clave else "")
    except Exception:  # noqa: BLE001
        href = "#"
    return Markup(f'<a href="{escape(href)}" class="ia-badge" title="{escape(tip + " — ver Administración › Uso de IA")}" '
                  'style="display:inline-block;font:700 10px/1 system-ui,sans-serif;letter-spacing:.04em;padding:3px 5px;border-radius:4px;'
                  'background:#6d28d9;color:#fff;text-decoration:none;vertical-align:middle;margin-left:6px">' + str(escape(texto)) + "</a>")


@bp.route("/configuracion/ia")
@requiere_login
def uso_ia():
    from .vistas import _solo_admin
    _solo_admin()
    d = datos()
    filas = []
    for u in d.get("usos") or []:
        filas.append({**u, "url": _enlace(u.get("enlace")), "esta_activo": _activo(u.get("activo")), "llamadas": _conteo(u.get("conteo"))})
    return render_template("uso_ia.html", filas=filas, regla=d.get("regla", ""), no_ia=d.get("no_ia") or [],
                           otros=d.get("otros_sistemas") or [])
