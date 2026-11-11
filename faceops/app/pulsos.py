"""Pulsos de uso: cada request (y algunos eventos de negocio) de FaceOps deja un «pulso» que viaja a FaceAdmin, al data lake
s3://faceops-pulsos (ver faceadmin/docs/pulsos.md). Sirve para mirar cómo se usa la herramienta, sus errores y su latencia.

Reglas:
  · nunca viajan cuerpos de formularios, contraseñas, tokens, cookies ni cabeceras Authorization; los parámetros con nombres
    sensibles y los segmentos de ruta con pinta de token se redactan; el correo del usuario no viaja (solo id y rol).
  · el pulso se arma en memoria; un hilo lo pasa a una cola en disco (SQLite junto a la BD) y la envía por lotes a FaceAdmin
    cada FACEOPS_PULSOS_CADA segundos (45). Si FaceAdmin no responde, se reintenta después; la app nunca espera por esto.
  · la cola tiene tope (FACEOPS_PULSOS_MAX_MB, 50 MB): al pasarlo se descarta lo más antiguo.
  · FACEOPS_PULSOS=0 lo apaga (por defecto activo).
"""
import gzip
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timezone

VERSION_ESQUEMA = 1
SENSIBLE = re.compile(r"(clave|pass|pwd|token|secret|key|llave|auth|cookie|session|sesion|csrf|otp|totp|codigo_2fa|firma|signature|credential|bearer)", re.I)
TOKEN_RUTA = re.compile(r"^[A-Za-z0-9_\-\.=]{24,}$")
SECRETO_TXT = [
    (re.compile(r"(?i)(bearer|basic)\s+[A-Za-z0-9\-\._~\+/=]+"), r"\1 [redactado]"),
    (re.compile(r"(?i)((?:clave|password|passwd|pwd|token|secret|api[_-]?key|llave|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;}]+"), r"\1[redactado]"),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[redactado]@"),
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "[redactado]"),
    (re.compile(r"[\w\.\-+]+@[\w\-]+\.[\w\.\-]+"), "[correo]"),
]
EXCLUIR_EP = {"static", "version_publica"}
EXCLUIR_RUTA = ("/static/", "/favicon", "/healthz", "/readyz", "/livez", "/ping", "/salud.json")

_buf, _lock, _hilo = [], threading.Lock(), {"ok": False}
_dedupe = {}


def activo():
    return os.environ.get("FACEOPS_PULSOS", "1") != "0"


def _ruta_cola():
    from .bd import RUTA
    d = os.path.join(os.path.dirname(RUTA), "pulsos")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "cola.db")


def _con():
    c = sqlite3.connect(_ruta_cola(), timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS cola (id INTEGER PRIMARY KEY AUTOINCREMENT, linea TEXT NOT NULL, bytes INTEGER NOT NULL)")
    return c


def redactar(txt, n=300):
    """Quita de un texto libre (mensajes de error) todo lo que parezca secreto o correo."""
    t = str(txt or "")
    for rx, rep in SECRETO_TXT:
        t = rx.sub(rep, t)
    return t[:n]


def _ip(ip):
    ip = (ip or "").split(",")[0].strip()
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::/48"
    p = ip.split(".")
    return ".".join(p[:3]) + ".0/24" if len(p) == 4 else ""


def _ua(ua):
    ua = ua or ""
    if not ua:
        return ""
    for rx, n in ((r"kube-probe", "kube-probe"), (r"curl/", "curl"), (r"python-requests", "python-requests"), (r"Werkzeug", "test"),
                  (r"Ansible|ansible-httpget", "ansible"), (r"bot|crawler|spider", "bot")):
        if re.search(rx, ua, re.I):
            return n
    nav = next((f"{n} {m.group(1)}" for rx, n in ((r"Edg/(\d+)", "Edge"), (r"OPR/(\d+)", "Opera"), (r"Firefox/(\d+)", "Firefox"),
                                                  (r"Chrome/(\d+)", "Chrome"), (r"Version/(\d+).*Safari", "Safari")) if (m := re.search(rx, ua))), "otro")
    so = next((n for rx, n in ((r"Windows", "Windows"), (r"iPhone|iPad", "iOS"), (r"Android", "Android"), (r"Mac OS X", "macOS"),
                               (r"Linux", "Linux")) if re.search(rx, ua)), "")
    return f"{nav} · {so}" if so else nav


def _ruta_real(request):
    """Ruta sin query sensible: valores de parámetros sensibles y segmentos con pinta de token quedan como [redactado]."""
    sens = {str(v) for k, v in (request.view_args or {}).items() if SENSIBLE.search(k)}
    segs = ["[redactado]" if (s in sens or TOKEN_RUTA.match(s)) else s for s in request.path.split("/")]
    ruta = "/".join(segs)[:300]
    q = []
    for k in list(request.args.keys())[:30]:
        v = request.args.get(k, "")
        q.append(f"{k[:40]}=" + ("[redactado]" if SENSIBLE.search(k) or TOKEN_RUTA.match(v or "") else redactar(v, 80)))
    return ruta + ("?" + "&".join(q) if q else "")


def _sesion_anon(app, session):
    s = session.get("csrf") or ""
    if not s:
        return ""
    sal = hashlib.sha256(("pulsos:" + str(app.secret_key)).encode()).hexdigest()
    return hashlib.sha256((sal + s).encode()).hexdigest()[:16]


def _ahora():
    t = datetime.now(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _base():
    return {"v": VERSION_ESQUEMA, "ts": _ahora(), "instancia": os.environ.get("FACEOPS_INSTANCIA", "") or "laboratorio",
            "instancia_id": os.environ.get("FACEOPS_INSTANCIA_ID", "")}


def _contexto_usuario(p):
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return
        u, c = g.get("usuario"), g.get("cliente")
        if c:
            p["cliente"] = (c.get("codigo") or "").lower()
        if u:
            p["usuario_id"] = u.get("id")
            p["rol"] = "admin" if u.get("es_admin") else ((g.get("perfil") or {}).get("modo") or "")
            if g.get("ver_como"):
                p["ver_como"] = True
    except Exception:  # noqa: BLE001
        pass


def encolar(p):
    if not activo():
        return
    with _lock:
        if len(_buf) < 20000:
            _buf.append(json.dumps(p, ensure_ascii=False, default=str, separators=(",", ":")))


def evento(tipo, **datos):
    """Evento de negocio (login, ticket creado, error de AAP…). Los datos se redactan; sirve también fuera de un request."""
    try:
        p = _base()
        p.update(tipo="evento", evento=tipo)
        _contexto_usuario(p)
        limpio = {}
        for k, v in datos.items():
            if SENSIBLE.search(k):
                continue
            limpio[k] = v if isinstance(v, (int, float, bool)) or v is None else redactar(v, 300)
        p["datos"] = limpio
        encolar(p)
    except Exception:  # noqa: BLE001  un pulso nunca rompe la app
        pass


# ----------------------------------------------------------------- envío
def _volcar():
    with _lock:
        lote, _buf[:] = list(_buf), []
    if not lote:
        return
    c = _con()
    try:
        c.executemany("INSERT INTO cola (linea, bytes) VALUES (?,?)", [(x, len(x.encode())) for x in lote])
        tope = float(os.environ.get("FACEOPS_PULSOS_MAX_MB", "50")) * 1024 * 1024
        total = c.execute("SELECT COALESCE(SUM(bytes),0) FROM cola").fetchone()[0]
        while total > tope:   # cola llena: se descarta lo más antiguo
            filas = c.execute("SELECT id, bytes FROM cola ORDER BY id LIMIT 2000").fetchall()
            if not filas:
                break
            quitar, b = [], 0
            for i, n in filas:
                quitar.append(i)
                b += n
                if total - b <= tope * 0.9:
                    break
            c.execute(f"DELETE FROM cola WHERE id <= {int(quitar[-1])}")
            total -= b
        c.commit()
    finally:
        c.close()


def enviar(max_lineas=5000):
    """Manda a FaceAdmin lo que hay en la cola, en lotes; borra lo enviado solo si FaceAdmin lo aceptó. Devuelve líneas enviadas."""
    import requests
    url, tok = os.environ.get("FACEOPS_ADMIN_URL", "").rstrip("/"), os.environ.get("FACEOPS_ADMIN_TOKEN", "")
    if not url or not tok:
        return 0
    import fcntl
    fl = open(_ruta_cola() + ".lock", "w")
    try:
        fcntl.flock(fl, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fl.close()
        return 0   # otro proceso está enviando
    enviadas = 0
    try:
        c = _con()
        try:
            for _ in range(20):
                filas = c.execute("SELECT id, linea FROM cola ORDER BY id LIMIT ?", (max_lineas,)).fetchall()
                if not filas:
                    break
                cuerpo = ("\n".join(f[1] for f in filas) + "\n").encode()
                lote = f"{filas[0][0]}-{filas[-1][0]}-{hashlib.sha256(cuerpo).hexdigest()[:10]}"
                r = requests.post(url + "/api/instancia/pulsos", data=gzip.compress(cuerpo, 6), timeout=20, verify=False,
                                  headers={"X-FaceAdmin-Token": tok, "Content-Type": "application/x-ndjson", "Content-Encoding": "gzip",
                                           "X-Pulsos-Lote": lote})
                if not r.ok:
                    break
                c.execute("DELETE FROM cola WHERE id <= ?", (filas[-1][0],))
                c.commit()
                enviadas += len(filas)
                if len(filas) < max_lineas:
                    break
        finally:
            c.close()
    except Exception:  # noqa: BLE001  FaceAdmin caído o red: se reintenta en el próximo ciclo
        pass
    finally:
        fcntl.flock(fl, fcntl.LOCK_UN)
        fl.close()
    return enviadas


def _ciclo():
    cada = max(10, int(os.environ.get("FACEOPS_PULSOS_CADA", "45")))
    ultimo = time.time()
    while True:
        time.sleep(5)
        try:
            _volcar()
            if time.time() - ultimo >= cada:
                ultimo = time.time()
                enviar()
        except Exception:  # noqa: BLE001
            pass


def _iniciar_hilo():
    if _hilo["ok"]:
        return
    _hilo["ok"] = True
    threading.Thread(target=_ciclo, name="faceops-pulsos", daemon=True).start()
    import atexit
    atexit.register(lambda: (_volcar() if _buf else None))


# ----------------------------------------------------------------- middleware
def _excluir(request):
    p = request.path or ""
    return request.endpoint in EXCLUIR_EP or p.startswith(EXCLUIR_RUTA) or p.endswith((".js", ".css", ".png", ".svg", ".ico", ".woff2", ".map"))


def _modulo(regla, endpoint):
    seg = [s for s in (regla or "").split("/") if s and not s.startswith("<")]
    return {"modulo": seg[0] if seg else "inicio", "seccion": (endpoint or "").split(".")[-1]}


def _traza(exc):
    tb = traceback.extract_tb(exc.__traceback__)[-8:]
    return [f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in tb]


def registrar(app):
    """Engancha los pulsos a la app (before/after_request, excepciones) y los eventos de negocio. Llamar antes de importar vistas."""
    if not activo():
        return
    from flask import g, got_request_exception, request, session

    def _pulso_ini():
        g._pulso_t0 = time.perf_counter()
        g._pulso_uid0 = session.get("uid")
        if request.method != "GET" and not _excluir(request):
            try:
                from .bd import q1
                g._pulso_tk0 = (q1("SELECT MAX(id) m FROM tickets") or {}).get("m") or 0
            except Exception:  # noqa: BLE001
                g._pulso_tk0 = None
    app.before_request_funcs.setdefault(None, []).insert(0, _pulso_ini)   # primero: mide también lo que otros before_request cortan

    def _al_error(sender, exception, **kw):
        try:
            g._pulso_exc = exception
        except Exception:  # noqa: BLE001
            pass
    got_request_exception.connect(_al_error, app, weak=False)

    @app.after_request
    def _pulso_fin(resp):
        try:
            if _excluir(request) or getattr(g, "_pulso_t0", None) is None:
                return resp
            regla = request.url_rule.rule if request.url_rule else ""
            p = _base()
            p.update(tipo="request", metodo=request.method, ruta=regla or "(sin ruta)", ruta_real=_ruta_real(request), endpoint=request.endpoint or "",
                     status=resp.status_code, ms=round((time.perf_counter() - g._pulso_t0) * 1000, 1),
                     bytes_resp=resp.calculate_content_length() if not resp.direct_passthrough else (resp.content_length or 0),
                     bytes_req=request.content_length or 0, ua=_ua(request.headers.get("User-Agent")), ip=_ip(request.remote_addr),
                     sesion=_sesion_anon(app, session), xhr=bool(request.headers.get("X-Requested-With") or "application/json" in (request.headers.get("Accept") or "")))
            p.update(_modulo(regla, request.endpoint))
            _contexto_usuario(p)
            exc = getattr(g, "_pulso_exc", None)
            if exc is not None:
                p["error"] = {"tipo": type(exc).__name__, "mensaje": redactar(exc), "traza": _traza(exc)}
            elif resp.status_code >= 400:
                p["error"] = {"tipo": f"HTTP {resp.status_code}", "mensaje": ""}
            encolar(p)
            _eventos_request(resp)
        except Exception:  # noqa: BLE001  un pulso nunca rompe la respuesta
            pass
        return resp

    def _eventos_request(resp):
        ep = request.endpoint or ""
        if ep in ("consola.ingresar", "consola.ingresar_codigo") and request.method == "POST":
            if session.get("uid") and not g.get("_pulso_uid0"):
                from .bd import q1
                g.usuario = g.get("usuario") or q1("SELECT id, es_admin FROM usuarios WHERE id=?", session.get("uid"))
                evento("login", paso="codigo" if ep.endswith("codigo") else "clave")
            elif session.get("pre_uid") and ep == "consola.ingresar":
                evento("login_doble_factor_pedido")
            elif not session.get("uid"):
                evento("login_fallido", status=resp.status_code)
        elif ep == "consola.salir" and g.get("_pulso_uid0"):
            evento("logout", usuario_id=g.get("_pulso_uid0"))
        tk0 = g.get("_pulso_tk0")
        if tk0 is not None and request.method != "GET":
            from .bd import q
            for t in q("SELECT id, numero, automatizacion_id, estado FROM tickets WHERE id>? ORDER BY id LIMIT 20", tk0):
                evento("ticket_creado", ticket_id=t["id"], numero=t["numero"], automatizacion_id=t["automatizacion_id"], estado=t["estado"],
                       endpoint=ep)

    _ganchos_negocio()
    _iniciar_hilo()


def _ganchos_negocio():
    """Aprobación de tickets y errores de AAP: se envuelven las funciones existentes sin tocar sus módulos."""
    import sys
    from . import aap, operacion
    if not getattr(operacion.resolver_aprobacion, "_pulsos", False):
        orig = operacion.resolver_aprobacion

        def resolver_aprobacion(usuario, ticket_id, aprobar, comentario=""):
            r = orig(usuario, ticket_id, aprobar, comentario)
            evento("ticket_aprobado" if aprobar else "ticket_rechazado", ticket_id=ticket_id, por_usuario_id=(usuario or {}).get("id"))
            return r
        resolver_aprobacion._pulsos = True
        operacion.resolver_aprobacion = resolver_aprobacion
        v = sys.modules.get(__package__ + ".vistas")
        if v is not None and hasattr(v, "resolver_aprobacion"):
            v.resolver_aprobacion = resolver_aprobacion
    if not getattr(aap.ErrorAAP.__init__, "_pulsos", False):
        ini = aap.ErrorAAP.__init__

        def __init__(self, *a, **kw):
            ini(self, *a, **kw)
            try:
                msg = redactar(a[0] if a else "", 300)
                h = hashlib.sha1(msg.encode()).hexdigest()
                if time.time() - _dedupe.get(h, 0) > 300:   # el mismo error repetido (p. ej. el vigía) se informa cada 5 min
                    _dedupe[h] = time.time()
                    if len(_dedupe) > 500:
                        _dedupe.clear()
                    evento("error_aap", mensaje=msg)
            except Exception:  # noqa: BLE001
                pass
        __init__._pulsos = True
        aap.ErrorAAP.__init__ = __init__
