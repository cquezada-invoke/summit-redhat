"""Producto › Uso de IA: dónde interviene la IA en FaceAdmin, con qué modelo, qué datos envía, si decide o solo sugiere, si está
activa (clave configurada) y cuántas llamadas hubo en 30 días. La lista vive en ia_usos.yml (declarativa); ia_badge('<id>') pone
el distintivo «IA» en la pantalla o botón, con enlace a esta página. registrar('<conteo>') cuenta cada llamada a la IA."""
import os
from datetime import datetime, timedelta

import yaml
from flask import render_template, url_for
from markupsafe import Markup, escape

from app import app, ej, q, requiere_login, solo_invoke

ARCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ia_usos.yml")
_cache = {"t": 0, "d": None}


def datos():
    try:
        m = os.path.getmtime(ARCH)
        if _cache["d"] is None or m != _cache["t"]:
            _cache.update(t=m, d=yaml.safe_load(open(ARCH, encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError):
        return {"usos": []}
    return _cache["d"]


def _tabla():
    ej("CREATE TABLE IF NOT EXISTS ia_llamadas (id INTEGER PRIMARY KEY, cuando TEXT, uso TEXT, ok INTEGER)")


def registrar(uso, ok=True):
    """Cuenta una llamada a la IA (sin contenido: solo cuándo, cuál y si respondió). Nunca lanza excepción."""
    try:
        _tabla()
        ej("INSERT INTO ia_llamadas(cuando, uso, ok) VALUES (?,?,?)", datetime.now().isoformat(timespec="seconds"), uso, int(bool(ok)))
    except Exception:  # noqa: BLE001
        pass


with app.app_context():
    _tabla()


@app.template_global("ia_badge")
def ia_badge(clave="", texto="IA"):
    u = next((x for x in datos().get("usos") or [] if x.get("id") == clave), None)
    tip = ("Interviene IA: " + (u.get("que_hace") or "").strip()[:160] + " · " + (u.get("decide") or "")) if u else "Interviene IA"
    try:
        href = url_for("uso_ia") + (f"#ia-{clave}" if clave else "")
    except Exception:  # noqa: BLE001
        href = "#"
    return Markup(f'<a href="{escape(href)}" class="ia-badge" title="{escape(tip + " — ver Producto › Uso de IA")}" '
                  'style="display:inline-block;font:700 10px/1 system-ui,sans-serif;letter-spacing:.04em;padding:3px 5px;border-radius:4px;'
                  'background:#6d28d9;color:#fff;text-decoration:none;vertical-align:middle;margin-left:6px">' + str(escape(texto)) + "</a>")


def _enlace(e):
    try:
        return url_for(e) if e and not e.startswith(("http", "/")) else (e or "")
    except Exception:  # noqa: BLE001
        return ""


@app.route("/producto/uso-ia")
@requiere_login
def uso_ia():
    solo_invoke()
    from alcance import MODELO, ia_config
    d = datos()
    clave = bool(ia_config().get("api_key"))
    desde = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    cuenta = {r["uso"]: r for r in q("SELECT uso, COUNT(*) n, SUM(ok) ok, MAX(cuando) ultima FROM ia_llamadas WHERE cuando>=? GROUP BY uso", desde)}
    filas = [{**u, "url": _enlace(u.get("enlace")), "llamadas": cuenta.get(u.get("conteo")) or {}} for u in d.get("usos") or []]
    return render_template("uso_ia.html", filas=filas, regla=d.get("regla", ""), no_ia=d.get("no_ia") or [], clave=clave, modelo=MODELO)
