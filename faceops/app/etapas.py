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
