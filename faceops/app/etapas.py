"""Etapas de liberación de FaceOps (datos/etapas.yml): a qué etapa pertenece cada pantalla, opción de menú y acción, y qué
etapas están liberadas para los clientes. Un solo lugar: las plantillas solo leen lo que entrega este módulo."""
import json
import os

import yaml

RUTA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "etapas.yml")
_c = {"m": None, "d": {}}


def _datos():
    try:
        m = os.path.getmtime(RUTA)
    except OSError:
        return {}
    if _c["m"] != m:
        _c["m"], _c["d"] = m, yaml.safe_load(open(RUTA, encoding="utf-8")) or {}
    return _c["d"]


def etapas():
    """{n: {nombre, alcance, color, endpoints, menu, acciones}} ordenadas."""
    return {int(k): v or {} for k, v in sorted((_datos().get("etapas") or {}).items(), key=lambda x: int(x[0]))}


def de_endpoint(ep):
    """Etapa de una pantalla (la primera que la declara) o None."""
    for n, e in etapas().items():
        if ep in (e.get("endpoints") or []):
            return n
    return None


def de_menu(seccion, opcion=None):
    clave = f"{seccion} › {opcion}" if opcion else seccion
    for n, e in etapas().items():
        if clave in (e.get("menu") or []):
            return n
    return None


def de_accion(nombre_aap):
    for n, e in etapas().items():
        if nombre_aap in (e.get("acciones") or []):
            return n
    return None


def liberadas():
    """Etapas liberadas a clientes: el ajuste «etapas_liberadas» de la instancia manda sobre el archivo. [] = sin restricción."""
    v = None
    try:
        from .bd import ajuste
        a = ajuste("etapas_liberadas")
        if a not in (None, ""):
            v = json.loads(a)
    except Exception:  # noqa: BLE001 - sin base o sin ajuste: lo que diga el archivo
        v = None
    if v is None:
        v = _datos().get("etapas_liberadas") or []
    return sorted({int(x) for x in v if str(x).strip().isdigit()})


def fijar_liberadas(lista):
    from .bd import ajuste
    ajuste("etapas_liberadas", json.dumps(sorted({int(x) for x in lista if str(x).strip().isdigit()})))


def restringe(usuario):
    """True si a este usuario se le ocultan las etapas no liberadas (clientes; Invoke ve todo)."""
    return bool(liberadas()) and not (usuario or {}).get("es_admin")


def visible(etapa, usuario):
    return not restringe(usuario) or (etapa is not None and etapa in liberadas())


def chip(ep):
    """Lo que la cabecera de la pantalla necesita: {n, nombre, color} o None."""
    n = de_endpoint(ep)
    if n is None:
        return None
    e = etapas().get(n, {})
    return {"n": n, "nombre": e.get("nombre", ""), "color": e.get("color") or "#0066cc"}


# ── Bloqueo temporal de controles de otras etapas (datos/etapas.yml › bloqueo) ──────────────────────────────────────────
def _bloqueo():
    return _datos().get("bloqueo") or {}


def bloqueo_activo():
    try:
        from .bd import ajuste
        a = ajuste("etapas_bloqueo")
        if a not in (None, ""):
            return a == "1"
    except Exception:  # noqa: BLE001
        pass
    return bool(_bloqueo().get("activo"))


def fijar_bloqueo(activo):
    from .bd import ajuste
    ajuste("etapas_bloqueo", "1" if activo else "0")


def foco():
    """Etapa en revisión: la mayor liberada o, sin restricción, la primera definida."""
    lib = liberadas()
    return max(lib) if lib else min(etapas() or {1: None})


def glosa(n):
    b = _bloqueo()
    return (b.get("glosa") or "Etapa {n} · aún no liberada").format(n=n) if n else (b.get("glosa_sin_etapa") or "Otra etapa · aún no liberada")


def etapa_de_auto(a):
    """Etapa de una acción del catálogo: la que la declara en «acciones»; si no, la de su sección del menú (areas_etapa)."""
    n = de_accion(a.get("nombre_aap") or "")
    if n is not None:
        return n
    try:
        from .areas import seccion_de
        return (_bloqueo().get("areas_etapa") or {}).get(seccion_de(a))
    except Exception:  # noqa: BLE001
        return None


def accion_bloqueada(a):
    """Glosa si la acción es de una etapa posterior a la en revisión (o sin etapa) y el bloqueo está activo; si no, None."""
    if not bloqueo_activo():
        return None
    n = etapa_de_auto(a)
    return None if (n is not None and n <= foco()) else glosa(n)


def ruta_bloqueada(ruta):
    """(etapa, glosa) si la ruta (sin el prefijo del cliente) es de otra etapa según las reglas; si no, None."""
    import re
    if not bloqueo_activo():
        return None
    for r in _bloqueo().get("reglas") or []:
        if (r.get("etapa") or 99) <= foco():
            continue
        for rx in r.get("rutas") or []:
            if re.search(rx, ruta):
                return r.get("etapa"), glosa(r.get("etapa"))
    return None


def config_cliente(cliente_id):
    """Lo que necesita el navegador para bloquear controles: reglas y la etapa de cada acción del catálogo."""
    if not bloqueo_activo():
        return None
    import time
    from .bd import q
    f = foco()
    c = _cfg.get(cliente_id)
    if c and time.time() - c[0] < 60 and c[1] == (f, _c["m"]):
        return c[2]
    reglas = [{"n": r.get("etapa"), "g": glosa(r.get("etapa")), "sel": r.get("selectores") or [], "cont": r.get("contenedores") or [],
               "rutas": r.get("rutas") or []} for r in _bloqueo().get("reglas") or [] if (r.get("etapa") or 99) > f]
    autos = {}
    for a in q("SELECT * FROM automatizaciones WHERE cliente_id=?", cliente_id):
        n = etapa_de_auto(dict(a))
        if n is None or n > f:
            autos[a["id"]] = {"n": n, "g": glosa(n)}
    ops = {}   # skills y bloque «Operaciones» (datos/habilidades.yml): su etapa es la de su acción del catálogo
    try:
        for hid, a in acciones_de_habilidades(cliente_id).items():
            n = etapa_de_auto(a)
            if n is None or n > f:
                ops[hid] = {"n": n, "g": glosa(n), "a": a.get("nombre")}
    except Exception:  # noqa: BLE001
        pass
    out = {"reglas": reglas, "autos": autos, "ops": ops}
    _cfg[cliente_id] = (time.time(), (f, _c["m"]), out)
    return out


_cfg = {}


def acciones_de_habilidades(cliente_id):
    """{id de habilidad: acción del catálogo (la primera de sus nombre_aap que exista en el cliente)}."""
    from .bd import q
    from .habilidades import mapa
    por = {a["nombre_aap"]: dict(a) for a in q("SELECT * FROM automatizaciones WHERE cliente_id=?", cliente_id)}
    out = {}
    for h in mapa():
        ns = h.get("nombre_aap") or []
        ns = [ns] if isinstance(ns, str) else ns
        a = next((por[n] for n in ns if n in por), None)
        if a and h.get("id"):
            out[h["id"]] = a
    return out


def habilidad_bloqueada(cliente_id, hid):
    if not bloqueo_activo():
        return None
    a = acciones_de_habilidades(cliente_id).get(hid)
    return (etapa_de_auto(a), accion_bloqueada(a)) if a and accion_bloqueada(a) else None
