"""FaceOps · consola de automatización del Managed Service de Invoke, sobre Ansible Automation Platform."""
import os
import secrets
from functools import wraps

from flask import Flask, abort, flash, g, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from . import bd
from .permisos import clientes_visibles, perfil

VERSION = "1.0.0"


def version_info():
    """Versión de esta instalación: línea (V1) + número de compilación (commits) + commit y fecha. La escribe el despliegue en
    app/version.json desde el repositorio central; sin ese archivo, solo la línea."""
    import json as _json
    try:
        v = _json.load(open(os.path.join(os.path.dirname(__file__), "version.json")))
    except (OSError, ValueError):
        v = {}
    v.setdefault("linea", VERSION.rsplit(".", 1)[0])
    v["texto"] = f"v{v['linea']}.{v['build']}" if v.get("build") else f"v{VERSION}"
    v["instancia"] = os.environ.get("FACEOPS_INSTANCIA", "") or "laboratorio"
    v["rol"] = os.environ.get("FACEOPS_ROL", "cliente")
    return v


def create_app():
    from . import replay
    replay.activar()   # solo la maqueta: grabar o reproducir los servicios externos
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
    from . import puntos as _puntos
    try:
        _puntos.iniciar()
        from . import manifiesto as _mf
        _mf.iniciar()
    except Exception:  # noqa: BLE001
        pass
    from .semillas import sembrar
    sembrar()
    try:   # grupos y roles al estilo OpenShift (una vez por cliente)
        from . import rbac as _rbac
        with app.app_context():
            for c_ in bd.q("SELECT id FROM clientes"):
                _rbac.migrar(c_["id"])
    except Exception as e_:  # noqa: BLE001
        app.logger.warning("grupos y roles: %s", e_)

    maqueta = os.environ.get("FACEOPS_MAQUETA") == "1"   # demo cáscara: login simple (sin doble factor), datos emulados, acciones simuladas
    app.jinja_env.globals["MAQUETA"] = maqueta

    @app.before_request
    def cargar_usuario():
        g.usuario = None
        uid = session.get("uid")
        if uid:
            g.usuario = bd.q1("SELECT * FROM usuarios WHERE id=? AND activo=1 AND (COALESCE(expira,'')='' OR expira > ?)", uid, bd.ahora())
        g.clientes = clientes_visibles(g.usuario) if g.usuario else []
        g.cliente = None
        if g.clientes:
            cid = session.get("cliente_id")
            g.cliente = next((c for c in g.clientes if c["id"] == cid), g.clientes[0])
            session["cliente_id"] = g.cliente["id"]
            g.perfil = perfil(g.usuario, g.cliente["id"])
        g.acceso, g.ver_como = None, None
        if g.usuario and g.cliente:
            from . import rbac as _rbac
            if _rbac.activo(g.cliente["id"]):
                g.acceso = _rbac.acceso(g.usuario, g.cliente["id"])
                vc = session.get("ver_como")
                if vc and (g.usuario.get("es_admin") or g.acceso["admin_total"]):   # «ver como»: probar lo que ve otro usuario
                    otro = bd.q1("SELECT * FROM usuarios WHERE id=?", vc)
                    if otro:
                        g.ver_como = {"real": g.usuario, "otro": otro}
                        g.usuario = dict(otro, es_admin=0)
                        g.acceso = _rbac.acceso(g.usuario, g.cliente["id"])
                        g.perfil = perfil(g.usuario, g.cliente["id"]) or dict(g.perfil, modo="lectura")
                        if request.method != "GET" and request.endpoint != "consola.ver_como_salir":
                            abort(403, "Estás viendo FaceOps como otro usuario: solo lectura")
        # bloqueo decidido en FaceAdmin (facturas impagas o manual): nadie entra, salvo los administradores de Invoke
        from .bloqueo_admin import estado as _est_admin
        g.bloqueo = _est_admin() if request.endpoint not in ("static", "version_publica") else {}
        if g.bloqueo.get("bloqueado") and g.usuario and not g.usuario.get("es_admin") and request.endpoint not in ("consola.ingresar", "consola.salir", "static"):
            session.clear()
            g.usuario = None
            return redirect(url_for("consola.ingresar"))
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.endpoint in ("consola.consola_web_plataforma", "consola.web_equipo"):
            # consolas web publicadas por FaceOps (HAL de JBoss, Vault…): no llevan nuestro token; basta que vengan de este mismo sitio
            ref = request.headers.get("Origin") or request.headers.get("Referer") or ""
            if not ref.startswith(request.host_url.rstrip("/")):
                abort(400, "Origen no permitido")
        elif request.method == "POST" and request.endpoint not in ("consola.ingresar",):
            tok_ = request.form.get("_csrf") or request.headers.get("X-CSRF") or ((request.get_json(silent=True) or {}).get("_csrf") if request.is_json else None)
            if tok_ != session.get("csrf"):
                abort(400, "Formulario vencido: recarga la página")

    from .identidad import codigo as _cod_eq, descripcion as _desc_eq, plataformas as _plat_eq

    def _marca_auto(a):
        """Marca de color de una automatización (datos/marcas.yml): {texto, color} o None."""
        import os as _os
        import yaml as _yaml
        ruta = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "datos", "marcas.yml")
        try:
            mt = _os.path.getmtime(ruta)
            if _marca_auto.__dict__.get("mt") != mt:
                _marca_auto.d, _marca_auto.mt = _yaml.safe_load(open(ruta, encoding="utf-8")) or {}, mt
        except OSError:
            return None
        n = (a.get("nombre_aap") if hasattr(a, "get") else None) or ""
        return next(({"texto": m.get("texto", k), "color": m.get("color", "#0b7a53")} for k, m in (_marca_auto.d or {}).items()
                     if n in (m.get("automatizaciones") or [])), None)

    def _logo_ocp(e=None, tam=22, forzar=False):
        """Logo de OpenShift junto a un clúster, uno de sus nodos o cualquier cosa de OpenShift (forzar=True)."""
        from markupsafe import Markup
        from flask import url_for as _u
        from .bd import j as _j
        es = forzar
        if e is not None and not es:
            d = e.get("datos") if isinstance(e, dict) else None
            d = _j(d, {}) if isinstance(d, str) else (d or {})
            es = (e.get("categoria") or "") == "clúster openshift" or bool(d.get("nodo_ocp")) or (e.get("inventario") or "") == "OpenShift · nodos"
        if not es:
            return ""
        return Markup(f'<img class="logo-ocp" src="{_u("static", filename="openshift.png")}" alt="OpenShift" title="Red Hat OpenShift" '
                      f'width="{tam}" height="{tam}" style="vertical-align:-{tam // 5}px;margin-right:6px">')

    @app.route("/version")
    def version_publica():
        """Versión de la instancia, sin sesión: la consulta la consola de administración para ver qué cliente quedó atrasado."""
        v = version_info()
        return dict({k: v.get(k) for k in ("texto", "linea", "build", "commit", "fecha", "rama", "instancia", "rol")},
                    instancia_id=os.environ.get("FACEOPS_INSTANCIA_ID", ""))

    @app.context_processor
    def contexto():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"usuario": g.get("usuario"), "clientes": g.get("clientes", []), "cliente": g.get("cliente"),
                "perfil_actual": g.get("perfil"), "csrf": session["csrf"], "acceso": g.get("acceso"), "ver_como": g.get("ver_como"), "bloqueo": g.get("bloqueo") or {},
                "ve_gobierno": bool(g.get("usuario") and (g.usuario.get("es_admin") or (g.get("acceso") or {}).get("gobierno"))), "version_faceops": version_info(), "j": bd.j,
                "menu_nav": _menu, "cod_eq": _cod_eq, "desc_eq": _desc_eq, "plat_eq": _plat_eq, "atajos_usuario": _atajos, "logo_ocp": _logo_ocp, "marca_auto": _marca_auto}

    def _atajos():
        if request.blueprint != "consola" or not g.get("usuario"):
            return []
        from .atajos import del_usuario
        return del_usuario(g.usuario)

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

    @app.template_filter("bytes")
    def bytes_(n):
        n = float(n or 0)
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
            n /= 1024
        return f"{n:.1f} TB"

    @app.template_filter("combine")
    def combine(a, b):
        return {**(a or {}), **(b or {})}

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


EJECUTIVO = {"consola.resumen", "consola.resumen_csv", "consola.salir", "consola.doble_factor", "consola.cambiar_cliente"}


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
        # perfil ejecutivo (administrador del contrato en el cliente): solo el resumen global y sus reportes
        if (g.get("perfil") or {}).get("modo") == "ejecutivo" and request.endpoint not in EJECUTIVO:
            return redirect(url_for("consola.resumen"))
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
