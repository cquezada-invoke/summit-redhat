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
            ok = True if ac.get("compat") is False else compatible(a, e)[0]
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


def _ticket(cliente_id, e, aid, por_param=None):
    """Último ticket de esa automatización sobre la máquina pedido desde el flotante (o abierto por cualquier vía).
    por_param: parámetro que nombra la máquina cuando la automatización no corre en ella (p. ej. ir_maquinas en recursos)."""
    for t in q("SELECT id, numero, estado, limite, motivo, fin, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 40",
               cliente_id, aid):
        if por_param:
            if not str((j(t["parametros"], {}) or {}).get(por_param) or "").startswith(e["nombre"] + ":"):
                continue
        elif e["nombre"] not in (t["limite"] or "").split(":") and not (
                (t["motivo"] or "").startswith(MARCA) and (t["motivo"] or "").endswith(f" en {e['nombre']}")):
            continue   # (las que corren en FaceOps o en otro host no llevan la máquina en el límite: se reconoce por el motivo)
        if t["estado"] in ABIERTOS:
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": True}
        if t["estado"] == "exitoso" and (t["motivo"] or "").startswith(MARCA):
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": False}
        if (t["motivo"] or "").startswith(MARCA):
            return None   # el último pedido desde aquí falló o se canceló: se puede volver a pedir
    return None


def _recurso(e, ac, texto=None, n=None):
    """CPU o memoria (sugerencia de Rendimiento): a cuánto dejarla y los parámetros de «Infra · Ajustar recursos de máquinas».
    Con «texto» propone el valor (uso medio al 60 %, dentro de lo que permite el anfitrión); con «n» valida el pedido."""
    from .vistas import _limites_recursos
    if (e.get("forma") or "") != "virtual":
        return None
    lim = _limites_recursos(e)
    if not lim:
        return None
    k = ac["recurso"]
    cur = int(e.get("cpu") or 0) if k == "cpu" else round((e.get("ram_mb") or 0) / 1024)
    ops = lim["cpus"] if k == "cpu" else lim["rams"]
    if n is None:
        from .radar import sugerido
        m = re.search(r"al (\d+(?:[.,]\d+)?) ?%", texto or "")
        if not m or not cur:
            return None
        from .compatibilidad import requisito_maquina
        rq_ = requisito_maquina(e) or {}
        n = sugerido(k, cur, float(m.group(1).replace(",", ".")), ops, rq_.get("cpu" if k == "cpu" else "ram_gb"))   # entero aplicable, nunca bajo sus plataformas
    if not n or n == cur or not (1 <= n <= (lim["max_cpu"] if k == "cpu" else lim["max_ram"])):
        return None
    cpu, ram = (n, round((e.get("ram_mb") or 0) / 1024) or 1) if k == "cpu" else (int(e.get("cpu") or 1), n)
    u_ = "vCPU" if k == "cpu" else "GB"
    return {"n": n, "boton": f"{'Bajar' if n < cur else 'Subir'} a {n} {u_}", "parametros": {"ir_maquinas": f"{e['nombre']}:{cpu}:{ram * 1024}"},
            "motivo": f"Ajustar {e['nombre']} a {cpu} vCPU y {ram} GB"}


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
            rc = _recurso(e, ac, texto=texto) if ac.get("recurso") else None
            if ac.get("recurso") and not rc:
                continue
            a, bloqueo = _auto(g.cliente["id"], e, ac)
            if not a:
                continue
            accs.append({"tipo": "auto", "boton": (rc or {}).get("boton") or ac.get("boton") or a["nombre"], "auto": a["nombre"], "aid": a["id"],
                         "bloqueo": bloqueo, "escritura": escritura, "n": (rc or {}).get("n") or "",
                         "ticket": _ticket(g.cliente["id"], e, a["id"], "ir_maquinas" if rc else None),
                         "url": url_for("consola.salud_accion", eid=e["id"]), "sid": s["id"]})
        if accs and any(x["tipo"] == "auto" for x in accs):   # «respaldo: true» (abrir un flotante) solo cuando no hay automatización
            accs = [x for x in accs if x["tipo"] == "auto" or not next((ac for ac in s.get("acciones") or [] if ac.get("ui") == x["ui"] and ac.get("respaldo")), None)]
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
    rc = None
    if ac.get("recurso"):
        rc = _recurso(e, ac, n=int(request.form.get("n") or 0))
        if not rc:
            return {"error": "Ese valor no se puede asignar a la máquina en su anfitrión"}, 400
    t0 = _ticket(g.cliente["id"], e, a["id"], "ir_maquinas" if rc else None)
    if t0 and t0["abierto"]:
        return dict(t0, url=url_for("consola.ticket", tid=t0["id"]), existente=True)
    try:
        if rc:   # corre en el anfitrión KVM (como el flotante de CPU/memoria): la máquina va en el parámetro, no en el límite
            t = solicitar(g.usuario, g.cliente, a, None, rc["parametros"], f"{MARCA}{rc['motivo']}")
        else:
            t = solicitar(g.usuario, g.cliente, a, None, dict(ac.get("parametros") or {}),
                          f"{MARCA}{a['nombre']} en {e['nombre']}", None,
                          {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]),
            "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.app_template_global("rec_req")
def rec_req(e):
    """Mínimo de memoria/CPU que exigen las plataformas instaladas (misma fuente que «Encender»)."""
    from .compatibilidad import requisito_maquina
    try:
        return requisito_maquina(e)
    except Exception:  # noqa: BLE001
        return None


@bp.app_template_global("rec_sugerido")
def rec_sugerido(k, cur, u, ops=None, minimo=None):
    """Valor sugerido para el selector de «Ajustar CPU/Memoria» (el mismo cálculo que la sugerencia de Rendimiento)."""
    from .radar import sugerido
    try:
        return sugerido(k, int(cur or 0), u, ops, minimo)
    except (TypeError, ValueError):
        return None
