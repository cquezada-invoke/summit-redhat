"""Flotante de salud de la máquina › «Para llegar al 100 %»: por cada sugerencia del radar, la acción de FaceOps que la
resuelve (automatización del catálogo compatible con la máquina, con ticket y aprobación, o un flotante de la ficha), con
cuánto sube el pilar. El mapa vive en datos/salud-acciones.yml (no en la plantilla).

FaceOps nunca toca la máquina: cada botón crea un ticket que pasa por la aprobación de AAP. El estado del botón se
reconstruye desde los tickets («En curso · número» mientras el ticket está abierto, «Aplicada · número» al terminar bien),
así se mantiene al recargar."""
import os
import re

import yaml
from flask import g, request, url_for

from .bd import j, q, q1
from .vistas import bp, equipos_visibles
from . import requiere_login

RUTA = os.path.join(os.path.dirname(__file__), "..", "datos", "salud-acciones.yml")
MARCA = "Salud · "   # prefijo del motivo de los tickets creados desde el flotante
ABIERTOS = ("pendiente_aprobacion", "en_cola", "en_ejecucion", "en curso", "ejecutando", "esperando")
_cache = {}


def mapa():
    try:
        mt = os.path.getmtime(RUTA)
    except OSError:
        return []
    if _cache.get("mt") != mt:
        with open(RUTA, encoding="utf-8") as f_:
            _cache.update(mt=mt, s=(yaml.safe_load(f_) or {}).get("sugerencias") or [])
    return _cache["s"]


def _sugerencia(pilar, texto):
    for s in mapa():
        if s.get("pilar") in (None, pilar) and re.search(s["patron"], texto):
            return s
    return None


def _auto(cliente_id, e, ac):
    """(automatización, motivo de bloqueo o None) para una acción «automatizacion», o (None, None) si no hay ninguna."""
    from .compatibilidad import compatible
    from .inventario import skills_de
    from .pruebas import estado as estado_prueba
    e = dict(e, skills=list(skills_de(e)))
    cands = [x for n_ in ac.get("nombre_aap") or [] for x in [q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, n_)] if x]
    if ac.get("buscar"):
        rx = re.compile(ac["buscar"])
        cands += [x for x in q("SELECT * FROM automatizaciones WHERE cliente_id=? ORDER BY nombre_aap", cliente_id)
                  if rx.search(x["nombre_aap"]) and x["id"] not in {c["id"] for c in cands}]
    elegida, bloqueo = None, None
    for a in cands:
        a = dict(a)
        try:
            ok, _ = compatible(a, e)
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            continue
        ep = estado_prueba(a)["estado"]
        if ep == "certificada":
            return a, None
        if not elegida:   # compatible pero bloqueada por la regla de prueba: se muestra deshabilitada si no hay otra
            elegida, bloqueo = a, ("cambió desde su última prueba y debe probarse de nuevo" if ep == "cambiada" else
                                   "su última prueba falló y debe pasar de nuevo" if ep == "fallida" else
                                   "aún no tiene su plan de prueba ejecutado y aprobado")
    return elegida, bloqueo


def _ticket(cliente_id, e, aid):
    """Último ticket de esa automatización sobre la máquina pedido desde el flotante (o abierto por cualquier vía)."""
    for t in q("SELECT id, numero, estado, limite, motivo, fin FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 40",
               cliente_id, aid):
        if e["nombre"] not in (t["limite"] or "").split(":"):
            continue
        if t["estado"] in ABIERTOS:
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": True}
        if t["estado"] == "exitoso" and (t["motivo"] or "").startswith(MARCA):
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": False}
        if (t["motivo"] or "").startswith(MARCA):
            return None   # el último pedido desde aquí falló o se canceló: se puede volver a pedir
    return None


def _sube(s, texto, p, pilar_falta):
    m = re.search(r"\(\+(\d+) ?%\)", texto)
    if m:
        return int(m.group(1))
    v = s.get("sube") if s else None
    if isinstance(v, (int, float)):
        return int(v)
    pct = p.get("pct") or 0
    if v == "frescura":   # recuperación sin descontar la antigüedad del inventario
        base = max([d.get("v") or 0 for d in p.get("detalle") or []] or [0])
        return max(0, base - pct) or None
    if v == "rendimiento":
        n = len(p.get("detalle") or []) or 1
        d = next((d for d in p.get("detalle") or [] if texto.startswith(d["t"].split(" (")[0])), None)
        return round((100 - d["v"]) / n) if d else None
    if v == "replica":
        return 85 - pct if pct < 85 and "tercera" not in texto else 100 - pct
    return None


@bp.app_template_global("salud_acciones")
def salud_acciones(e, pilar):
    """[{texto, sube, acciones:[{tipo, boton, ui|aid,url, bloqueo, ticket}]}] para lo que falta en un pilar."""
    escritura = getattr(g, "perfil", None) and g.perfil["modo"] == "escritura"
    out = []
    for texto in pilar.get("falta") or []:
        s = _sugerencia(pilar["nombre"], texto)
        accs = []
        for ac in (s or {}).get("acciones") or []:
            if ac["tipo"] == "ui":
                accs.append({"tipo": "ui", "ui": ac["ui"], "boton": ac["boton"]})
                continue
            a, bloqueo = _auto(g.cliente["id"], e, ac)
            if not a:
                continue
            accs.append({"tipo": "auto", "boton": ac.get("boton") or a["nombre"], "auto": a["nombre"], "aid": a["id"], "bloqueo": bloqueo,
                         "escritura": escritura, "ticket": _ticket(g.cliente["id"], e, a["id"]),
                         "url": url_for("consola.salud_accion", eid=e["id"]), "sid": s["id"]})
        out.append({"texto": texto, "sube": _sube(s, texto, pilar, pilar.get("falta")), "id": (s or {}).get("id"), "acciones": accs})
    return out


@bp.route("/inventario/<int:eid>/salud/accion", methods=["POST"])
@requiere_login
def salud_accion(eid):
    """Crea el ticket (con aprobación en AAP) de la automatización que resuelve una sugerencia de salud; si ya hay uno
    abierto de esa automatización para la máquina, devuelve ese."""
    from .aap import ErrorAAP
    from .operacion import solicitar
    from flask import session
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    sid, aid = request.form.get("sid") or "", int(request.form.get("aid") or 0)
    s = next((x for x in mapa() if x.get("id") == sid), None)
    if not e or not s:
        return {"error": "Sugerencia o máquina no válidas"}, 400
    ac = next((ac for ac in s.get("acciones") or [] if ac["tipo"] == "automatizacion"
               for a in [_auto(g.cliente["id"], e, ac)[0]] if a and a["id"] == aid), None)
    if not ac:
        return {"error": "Esa automatización no resuelve esta sugerencia o no es compatible con la máquina"}, 400
    a, bloqueo = _auto(g.cliente["id"], e, ac)
    if bloqueo:
        return {"error": f"«{a['nombre']}» está bloqueada: {bloqueo}"}, 400
    t0 = _ticket(g.cliente["id"], e, a["id"])
    if t0 and t0["abierto"]:
        return dict(t0, url=url_for("consola.ticket", tid=t0["id"]), existente=True)
    try:
        t = solicitar(g.usuario, g.cliente, a, None, dict(ac.get("parametros") or {}),
                      f"{MARCA}{a['nombre']} en {e['nombre']}", None,
                      {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]),
            "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}
