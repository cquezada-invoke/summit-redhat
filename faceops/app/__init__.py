"""FaceOps · consola de automatización del Managed Service de Invoke, sobre Ansible Automation Platform."""
import os
import secrets
from functools import wraps

from flask import Flask, abort, flash, g, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

# YAML: usa el cargador en C (libyaml) cuando existe; es ~10 veces más rápido y se usa en muchas páginas
import yaml as _yaml
if hasattr(_yaml, "CSafeLoader"):
    def _safe_load(stream, _L=_yaml.CSafeLoader):
        return _yaml.load(stream, Loader=_L)
    _yaml.safe_load = _safe_load

from . import bd  # noqa: E402
from .permisos import clientes_visibles, perfil  # noqa: E402



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


class PrefijoCliente:
    """Cada URL lleva el código del cliente: /faceops/<código>/inventario. El código sale de PATH_INFO y pasa a SCRIPT_NAME,
    así url_for genera todos los enlaces con el cliente y la sesión no decide por su cuenta en qué cliente se está."""
    def __init__(self, wsgi, app):
        self.wsgi, self.app, self._c = wsgi, app, (0.0, set())

    def codigos(self):
        import time
        if time.time() - self._c[0] > 30:
            try:
                rutas = {r.rule.strip("/").split("/")[0] for r in self.app.url_map.iter_rules()}
                self._c = (time.time(), {(c["codigo"] or "").lower() for c in bd.q("SELECT codigo FROM clientes")} - rutas - {""})
            except Exception:  # noqa: BLE001
                return self._c[1]
        return self._c[1]

    def __call__(self, environ, start_response):
        partes = (environ.get("PATH_INFO") or "/").split("/", 2)
        if len(partes) > 1 and partes[1].lower() in self.codigos():
            environ["faceops.raiz"] = environ.get("SCRIPT_NAME", "")
            environ["faceops.cliente"] = partes[1].lower()
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + "/" + partes[1]
            environ["PATH_INFO"] = "/" + (partes[2] if len(partes) > 2 else "")
        return self.wsgi(environ, start_response)


def create_app():
    from . import replay
    replay.activar()   # solo la maqueta: grabar o reproducir los servicios externos
    app = Flask(__name__)
    if os.environ.get("FACEOPS_DEV") == "1":   # modo desarrollo del lab (herramientas/lab/faceops-dev.sh): plantillas y estáticos se releen en cada carga
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.jinja_env.auto_reload = True
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.extensions["prefijo_cliente"] = pc_ = PrefijoCliente(app.wsgi_app, app)
    app.wsgi_app = ProxyFix(pc_, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
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
    from .salud import tono_salud, tono_uso, semaforo_salud   # regla única de color: salud 85/50, uso inverso
    app.jinja_env.globals.update(tono_salud=tono_salud, tono_uso=tono_uso, semaforo_salud=semaforo_salud)
    from .aap import salud as aap_salud   # banner global «AAP no disponible» (vigía de fondo, sin consultas en la página)
    app.jinja_env.globals.update(aap_salud=aap_salud)
    from .credenciales_usuarios import por_usuario as credenciales_usuarios
    app.jinja_env.globals["credenciales_usuarios"] = credenciales_usuarios

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
            pref = request.environ.get("faceops.cliente")
            por_url = next((c for c in g.clientes if (c["codigo"] or "").lower() == pref), None) if pref else None
            g.cliente = por_url or next((c for c in g.clientes if c["id"] == cid), g.clientes[0])
            session["cliente_id"] = g.cliente["id"]
            g.perfil = perfil(g.usuario, g.cliente["id"])
            cod = (g.cliente["codigo"] or "").lower()
            if (request.method == "GET" and pref != cod and cod in app.extensions["prefijo_cliente"].codigos() and request.endpoint not in (None, "static", "consola.cambiar_cliente", "consola.salir")
                    and not request.path.startswith("/api/") and "text/html" in (request.headers.get("Accept") or "")
                    and not request.headers.get("X-Requested-With")):   # la URL siempre dice en qué cliente se está
                return redirect(request.environ.get("faceops.raiz", request.script_root) + "/" + cod + request.full_path.rstrip("?"))
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
        # bloqueo decidido en FaceAdmin (facturas impagas o manual): nadie entra y quien estaba dentro sale al instante
        from .bloqueo_admin import estado as _est_admin, reportar_uso as _uso_admin
        g.bloqueo = _est_admin() if request.endpoint not in ("static", "version_publica") else {}
        _uso_admin()
        if g.bloqueo.get("bloqueado") and g.usuario and request.endpoint not in ("consola.ingresar", "consola.recuperar", "consola.salir", "static"):
            session.clear()
            g.usuario = None
            return redirect(url_for("consola.ingresar"))
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.endpoint in ("consola.consola_web_plataforma", "consola.web_equipo"):
            # consolas web publicadas por FaceOps (HAL de JBoss, Vault…): no llevan nuestro token; basta que vengan de este mismo sitio
            ref = request.headers.get("Origin") or request.headers.get("Referer") or ""
            if not ref.startswith(request.host_url.rstrip("/")):
                abort(400, "Origen no permitido")
        elif request.method == "POST" and request.endpoint not in ("consola.ingresar", "consola.api_fabrica_ejecutar", "consola.api_alertas_ocp", "consola.api_siem_alerta", "consola.api_autocuidado_evento"):
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
                "menu_nav": _menu, "cod_eq": _cod_eq, "desc_eq": _desc_eq, "plat_eq": _plat_eq, "atajos_usuario": _atajos, "logo_ocp": _logo_ocp, "marca_auto": _marca_auto,
                "etapa_pantalla": _etapa_pantalla, "etapa_foco": _etapa_foco, "etapas_config": _etapas_config, "etapa_bloqueo": _etapa_bloqueo}

    def _etapa_pantalla():   # distintivo «Etapa N» del título de la pantalla (datos/etapas.yml)
        try:
            from .etapas import chip
            return chip(request.endpoint or "")
        except Exception:  # noqa: BLE001 - el distintivo nunca debe tumbar la página
            return None

    def _etapa_bloqueo():   # reglas para deshabilitar en el navegador los controles de otras etapas (solo en pantallas de la etapa en revisión)
        try:
            from .etapas import config_cliente, foco, chip
            ch = chip(request.endpoint or "")
            if not ch or ch["n"] > foco():
                return None
            cid = (g.get("cliente") or {}).get("id")
            cfg = config_cliente(cid) if cid else None
            aid = (request.view_args or {}).get("aid")
            if cfg and request.endpoint == "consola.automatizacion" and aid in cfg["autos"]:
                cfg = dict(cfg, pagina=cfg["autos"][aid]["g"])   # ficha de una acción de otra etapa: no se puede solicitar
            return cfg
        except Exception:  # noqa: BLE001
            app.logger.exception("bloqueo por etapas")
            return None

    def _etapas_config():
        from .etapas import etapas, liberadas
        from .etapas import bloqueo_activo
        return {"etapas": etapas(), "lib": liberadas(), "bloqueo": bloqueo_activo()}

    def _etapa_foco():   # la etapa del filtro «Ver solo …» del menú: la mayor liberada, o la 1 si no hay restricción
        try:
            from .etapas import etapas, liberadas
            lib = liberadas()
            n = max(lib) if lib else min(etapas() or {1: None})
            return {"n": n, "lib": lib, "nombre": (etapas().get(n) or {}).get("nombre", "")}
        except Exception:  # noqa: BLE001
            return {"n": 1, "lib": [], "nombre": ""}

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

    @app.template_filter("hora_aap")
    def hora_aap(ts):
        import time as _t
        return _t.strftime("%H:%M", _t.localtime(ts)) + ("" if _t.time() - ts < 86400 else _t.strftime(" del %d-%m", _t.localtime(ts)))

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

    from .hora_local import registrar_filtros   # |fecha, |fecha_txt, |iso_z, |hace: UTC en el servidor, hora local en el navegador
    registrar_filtros(app)

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

    @app.before_request
    def _perfil_ini():
        """Diagnóstico de rendimiento: con la cabecera X-FaceOps-Perfil: 1 (solo un administrador con sesión) se perfila la vista."""
        if request.headers.get("X-FaceOps-Perfil") == "1" and session.get("uid"):
            import cProfile
            g._prof = cProfile.Profile()
            g._prof.enable()

    @app.after_request
    def _perfil_fin(resp):
        pr = g.pop("_prof", None)
        if pr is not None:
            pr.disable()
            u = bd.q1("SELECT es_admin FROM usuarios WHERE id=?", session.get("uid")) or {}
            if u.get("es_admin"):
                import pstats
                st = pstats.Stats(pr)
                filas = sorted(((v[3], f"{k[0].rsplit('/', 1)[-1]}:{k[1]}:{k[2]}") for k, v in st.stats.items()
                                if "/app/" in k[0] or "requests" in k[0] or "socket" in k[0] or "sqlite" in k[2]), reverse=True)[:15]
                resp.headers["X-FaceOps-Perfil"] = " | ".join(f"{t:.2f}s {n}" for t, n in filas).encode("ascii", "replace").decode()[:3500]
        return resp

    @app.after_request
    def _comprimir(resp):
        """Rendimiento: comprime con gzip el HTML, CSS, JS y JSON (las páginas pesan ~80 KB y quedan en ~12 KB)."""
        import gzip as _gz
        if (resp.mimetype or "") == "text/html" and "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-cache, private"   # el HTML depende de la sesión y de las etapas: el navegador siempre revalida
        try:
            if resp.direct_passthrough and request.path.startswith(app.static_url_path or "/static") and (resp.mimetype or "") in ("text/css", "text/javascript", "application/javascript"):
                resp.direct_passthrough = False   # la hoja de estilos (145 KB) también va comprimida
                resp.headers["Cache-Control"] = "no-cache" if os.environ.get("FACEOPS_DEV") == "1" else "public, max-age=86400"   # se versiona con ?v=<build>; en modo desarrollo siempre revalida
            if (resp.status_code != 200 or resp.direct_passthrough or resp.headers.get("Content-Encoding")
                    or "gzip" not in (request.headers.get("Accept-Encoding") or "")
                    or not (resp.mimetype or "").startswith(("text/", "application/json", "application/javascript"))):
                return resp
            datos = resp.get_data()
            if len(datos) < 1400:
                return resp
            resp.set_data(_gz.compress(datos, 6))
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Content-Length"] = str(len(resp.get_data()))
            resp.headers.add("Vary", "Accept-Encoding")
        except Exception:  # noqa: BLE001  nunca romper una respuesta por comprimirla
            pass
        return resp

    try:   # pulsos de uso hacia el data lake de FaceAdmin (antes de importar vistas: envuelve la aprobación de tickets)
        from . import pulsos
        pulsos.registrar(app)
    except Exception as e_:  # noqa: BLE001
        app.logger.warning("pulsos: %s", e_)
    from .vistas import bp as consola
    from .admin import bp as admin
    from . import mod_redes, mod_storage, mod_contenedores, mod_estaciones, mod_hipervisores, mod_nubes, mod_edge   # noqa: F401  (módulos con repositorio propio)
    from . import nube_recursos   # noqa: F401  (ficha de nube: crear y operar máquinas, productos adicionales)
    from . import fabrica_api   # noqa: F401  (API del agente de la Fábrica de código)
    from . import demo_datos   # noqa: F401  (datos de demostración marcados, se quitan con un clic)
    from . import recuperar   # noqa: F401  (recuperar clave desde el login)
    from . import multicliente   # noqa: F401  (FaceOps centralizado · demo: registra /clientes en la consola)
    from . import salud_acciones   # noqa: F401  (flotante de salud: acciones para llegar al 100 %)
    from . import ficha_maquina   # noqa: F401  (ficha: acceso SSH/RDP, segmentos de negocio, vínculo y medición agentless)
    from . import habilidades   # noqa: F401  (ficha: botón «Habilidades», quick wins y diferenciadores)
    from . import ficha_plataforma   # noqa: F401  (ficha de plataforma: salud propia medida sin agente, alertas y skills)
    from . import segmento_ops   # noqa: F401  (vista del segmento: campana de alertas y botón Skills)
    from . import ia_usos   # noqa: F401  (Administración › Uso de IA y distintivo «IA»)
    from . import reportes   # noqa: F401  (Gobierno › Reportes: cumplimiento por área e iniciativas de mejora)
    from . import seguridad_incidentes   # noqa: F401  (Seguridad › Incidentes de seguridad con el SIEM)
    from . import sla   # noqa: F401  (Gobierno › SLA por servicio y Gobierno › Reportes › SLA)
    from . import cola_vista   # noqa: F401  (Monitoreo › Cola de acciones y estado de la cola en el ticket)
    from . import autocuidado_vista   # noqa: F401  (Monitoreo › Autocuidado de la plataforma, campana global y eventos de EDA)
    app.register_blueprint(consola)
    app.jinja_env.globals["modo_administracion"] = multicliente.modo
    app.jinja_env.globals["es_demo"] = multicliente.es_demo
    app.jinja_env.globals["multicliente_permitido_f"] = multicliente.permitido
    try:
        with app.app_context():
            multicliente.sembrar_demo()   # solo en Invoke Lab: clientes y máquinas ficticios, sin conexión a nada
    except Exception as e_:  # noqa: BLE001
        app.logger.warning("demo multicliente: %s", e_)
    app.register_blueprint(admin, url_prefix="/admin")

    from .operacion import vigia
    if os.environ.get("FACEOPS_VIGIA", "1") == "1":
        vigia()
        if os.environ.get("FACEOPS_MAQUETA") != "1":
            from .cola import asegurar_proceso
            asegurar_proceso()   # proceso propio que lanza en AAP lo encolado (crear y aprobar no esperan a AAP)
        from .paquetes import precargar
        precargar()   # registro de paquetes en memoria antes de la primera ficha de servidor
        try:   # diagnósticos y propuestas de las alertas por correo al responsable de la máquina
            from .alerta_diagnostico import iniciar as _correo_alertas
            with app.app_context():
                _correo_alertas()
        except Exception as e_:  # noqa: BLE001
            app.logger.warning("correo de alertas: %s", e_)
        reportes.iniciar_programador(app)   # envío programado del reporte de cumplimiento a cada grupo dueño
        sla.iniciar_programador(app)   # medición de cada SLA vigente, alerta «SLA en riesgo» y reporte mensual de SLA
    from .bloqueo_admin import iniciar_latido
    iniciar_latido()   # FaceOps informa a FaceAdmin (solo de adentro hacia afuera)
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
