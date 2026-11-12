"""Las páginas no esperan a nada externo. Regla: todo lo que depende de un servicio externo (Gitea, Vault, Prometheus/Thanos,
FaceAdmin, consolas web) o de la máquina en vivo (puerto SSH/RDP) se lee de la caché; si falta, se busca en segundo plano
y la página lo muestra con un marcador («data-pendiente») que base.html rehidrata solo, apenas está.

  valor(clave, fn, ttl, dep)  → lo último conocido al instante (o None si aún no se sabe); renueva en segundo plano.
  cortacircuito por dependencia: si una dependencia falló, no se le vuelve a preguntar en CORTE segundos (ninguna request reintenta).
  pendiente()                 → True si esta request mostró algo sin dato (la ficha agrega el rehidratador)."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

CORTE = 45          # segundos sin reintentar una dependencia que falló
TIMEOUT = 1         # tiempo máximo de cada consulta de fondo a una dependencia «rápida» (puertos, consolas, Prometheus)

_cache = {}         # clave → (t, valor)
_corriendo = set()
_caida = {}         # dependencia → hasta
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="sin-espera")


def caida(dep):
    """¿La dependencia está en corte (falló hace menos de CORTE s)?"""
    return bool(dep) and time.time() < _caida.get(dep, 0)


def fallo(dep, seg=CORTE):
    if dep:
        _caida[dep] = time.time() + seg


def ok(dep):
    if dep:
        _caida.pop(dep, None)


def _marcar_pendiente():
    try:
        from flask import g, has_request_context
        if has_request_context():
            g.sin_espera_pendiente = True
    except Exception:  # noqa: BLE001
        pass


def activar():
    """La request en curso es una página que no espera (la ficha): los lectores con esperar=None responden desde la caché."""
    try:
        from flask import g
        g.sin_esperar = True
    except Exception:  # noqa: BLE001
        pass


def activa():
    try:
        from flask import g, has_request_context
        return has_request_context() and bool(g.get("sin_esperar"))
    except Exception:  # noqa: BLE001
        return False


def pendiente():
    try:
        from flask import g
        return bool(g.get("sin_espera_pendiente"))
    except Exception:  # noqa: BLE001
        return False


class Reintentar(Exception):
    """fn aún no puede dar el dato (depende de otro que viene en camino): no se guarda ni corta la dependencia."""


def _correr(clave, fn, dep):
    try:
        v = fn()
        _cache[clave] = (time.time(), v)
        ok(dep)
    except Reintentar:
        pass
    except Exception:  # noqa: BLE001 - la dependencia no respondió: corte compartido, se conserva lo último conocido
        fallo(dep)
    finally:
        with _lock:
            _corriendo.discard(clave)


def refrescar(clave, fn, dep=None):
    """Lanza la lectura en segundo plano (una sola a la vez por clave; nunca si la dependencia está en corte)."""
    if caida(dep):
        return
    with _lock:
        if clave in _corriendo:
            return
        _corriendo.add(clave)
    try:
        _pool.submit(_correr, clave, fn, dep)
    except RuntimeError:   # intérprete cerrándose
        with _lock:
            _corriendo.discard(clave)


def valor(clave, fn, ttl=60, dep=None, defecto=None):
    """Lo último conocido para la clave, sin esperar. Si no hay o venció, se renueva en segundo plano. Sin dato: defecto y la
    request queda marcada como pendiente (su marcador se rehidrata cuando el dato llega). fn levanta excepción si la dependencia
    falló (activa el corte); devuelve el valor en cualquier otro caso."""
    c = _cache.get(clave)
    if c is None or time.time() - c[0] >= ttl:
        refrescar(clave, fn, dep)
    if c is None:
        if esperando(clave, dep):
            _marcar_pendiente()
        return defecto
    return c[1]


def esperando(clave, dep=None):
    """Sin dato todavía pero en camino (si la dependencia está en corte no se espera: se muestra «no disponible»)."""
    return clave not in _cache and not caida(dep)


def guardar(clave, v):
    _cache[clave] = (time.time(), v)


def olvidar(clave):
    _cache.pop(clave, None)


def conocido(clave):
    return clave in _cache
