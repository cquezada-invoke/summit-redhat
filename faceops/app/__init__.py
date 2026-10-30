"""FaceOps · consola de automatización del Managed Service de Invoke, sobre Ansible Automation Platform."""
import os
import secrets
from functools import wraps

from flask import Flask, abort, flash, g, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from . import bd
from .permisos import clientes_visibles, perfil

VERSION = "1.0.0"


def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
    clave = os.environ.get("FACEOPS_CLAVE_SESION")
    if not clave:
        ruta = os.path.join(os.path.dirname(bd.RUTA), "clave-sesion")
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        if not os.path.exists(ruta):
            open(ruta, "w").write(secrets.token_hex(32))
        clave = open(ruta).read().strip()
    app.secret_key = clave
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=8 * 3600)
    bd.iniciar()
    from .semillas import sembrar
    sembrar()

    @app.before_request
    def cargar_usuario():
        g.usuario = None
        uid = session.get("uid")
        if uid:
            g.usuario = bd.q1("SELECT * FROM usuarios WHERE id=? AND activo=1", uid)
        g.clientes = clientes_visibles(g.usuario) if g.usuario else []
        g.cliente = None
        if g.clientes:
            cid = session.get("cliente_id")
            g.cliente = next((c for c in g.clientes if c["id"] == cid), g.clientes[0])
            session["cliente_id"] = g.cliente["id"]
            g.perfil = perfil(g.usuario, g.cliente["id"])
        if request.method == "POST" and request.endpoint not in ("consola.ingresar",):
            if request.form.get("_csrf") != session.get("csrf"):
                abort(400, "Formulario vencido: recarga la página")

    @app.context_processor
    def contexto():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"usuario": g.get("usuario"), "clientes": g.get("clientes", []), "cliente": g.get("cliente"),
                "perfil_actual": g.get("perfil"), "csrf": session["csrf"], "version_faceops": VERSION, "j": bd.j,
                "menu_nav": _menu}

    def _menu():
        if request.blueprint != "consola" or not g.get("cliente") or not g.get("usuario"):
            return []
        try:
            from .vistas import menu_nav
            return menu_nav()
        except Exception:  # el menú nunca debe tumbar la página
            app.logger.exception("menú lateral")
            return []

    @app.template_filter("horas")
    def horas(h):
        if h is None:
            return "—"
        t = f"{h:.2f}".rstrip("0").rstrip(".")
        return t.replace(".", ",") + " h"

    @app.template_filter("minutos")
    def minutos(m):
        if m is None:
            return "—"
        return f"{m * 60:.0f} s" if m < 1 else (f"{m:.1f} min".replace(".", ",") if m < 60 else f"{m / 60:.1f} h".replace(".", ","))

    @app.template_filter("factor")
    def factor(f):
        if not f:
            return "—"
        v = 1 / f
        return f"{f:.3f}".replace(".", ",") + f" · {v:.0f}× más rápido" if v >= 10 else f"{f:.2f}".replace(".", ",") + f" · {v:.1f}× más rápido".replace(".", ",")

    @app.template_filter("compat")
    def compat(a):
        from .compatibilidad import etiquetas
        return etiquetas(a)

    @app.template_filter("icono_auto")
    def icono_auto(a):
        from markupsafe import Markup
        from .iconos_auto import svg
        return Markup(svg(a))

    @app.template_filter("fecha")
    def fecha(v):
        if not v:
            return "—"
        return str(v).replace("T", " ")[:16]

    @app.template_filter("dur")
    def dur(s):
        if s is None:
            return "—"
        s = int(s)
        if s >= 3600:
            return f"{s // 3600} h {s % 3600 // 60} min"
        return f"{s // 60} min {s % 60} s" if s >= 60 else f"{s} s"

    @app.template_filter("dinero")
    def dinero(v, moneda="USD"):
        return f"{moneda} {v:,.0f}".replace(",", ".")

    from .vistas import bp as consola
    from .admin import bp as admin
    app.register_blueprint(consola)
    app.register_blueprint(admin, url_prefix="/admin")

    from .operacion import vigia
    if os.environ.get("FACEOPS_VIGIA", "1") == "1":
        vigia()
    return app


def requiere_login(f):
    @wraps(f)
    def envoltura(*a, **kw):
        if not g.get("usuario"):
            return redirect(url_for("consola.ingresar", siguiente=request.full_path))
        if not g.get("cliente") and request.blueprint != "admin":
            if g.usuario["es_admin"]:
                return redirect(url_for("admin.panel"))
            flash("Tu usuario no tiene perfiles asignados. Pide acceso a un administrador.", "error")
            session.clear()
            return redirect(url_for("consola.ingresar"))
        return f(*a, **kw)
    return envoltura


def requiere_admin(f):
    @wraps(f)
    @requiere_login
    def envoltura(*a, **kw):
        if not g.usuario["es_admin"]:
            abort(403)
        return f(*a, **kw)
    return envoltura
