"""Ficha de la máquina › botón «Habilidades» (a la altura de la campana de alertas, en el extremo derecho): ventana
centrada con las habilidades que FaceOps puede ejecutar en ESA máquina, cada una marcada «Quick win» o «Diferenciador»
(datos/habilidades.yml). Primero las migraciones y lo que diferencia.

Una habilidad se muestra si su acción del catálogo aplica a la máquina (operacion.motivo_no_aplica: sistema, plataformas
instaladas, inventario de AAP; ámbito de plataforma en datos/skills-ambito.yml). Si aplica pero no está certificada (regla
de prueba, app/pruebas.py) queda deshabilitada con su motivo. FaceOps nunca toca la máquina: cada habilidad crea un
ticket que pasa por la aprobación de AAP; el botón queda «En curso · número» (también al recargar) y al terminar
«Aplicada · número», sin cerrar la ventana."""
import os
import re

import yaml
from flask import g, request, session, url_for

from .bd import j, q, q1
from .vistas import bp, equipos_visibles
from . import requiere_login

RUTA = os.path.join(os.path.dirname(__file__), "..", "datos", "habilidades.yml")
MARCA = "Skill · "   # prefijo del motivo de los tickets creados desde la ventana
ABIERTOS = ("pendiente_aprobacion", "en_cola", "en_ejecucion", "en curso", "ejecutando", "esperando")
_cache = {}


def mapa():
    try:
        mt = os.path.getmtime(RUTA)
    except OSError:
        return []
    if _cache.get("mt") != mt:
        with open(RUTA, encoding="utf-8") as f_:
            _cache.update(mt=mt, h=(yaml.safe_load(f_) or {}).get("habilidades") or [])
    return _cache["h"]


def _motivo_prueba(a):
    from .pruebas import estado, exceptuada
    if exceptuada(a["nombre_aap"]):
        return None
    ep = estado(dict(a, cliente_id=g.cliente["id"]))["estado"]
    if ep == "certificada":
        return None
    return {"cambiada": "cambió desde su última prueba; debe probarse de nuevo",
            "fallida": "su última prueba falló"}.get(ep, "sin plan de prueba aprobado")


def _en_ambito(a, e):
    """Si la acción es de una plataforma (skills-ambito.yml), la máquina debe tenerla instalada."""
    from .skills_maquina import ambito
    from .inventario import skills_de
    from .vistas import familia
    amb = ambito(a["nombre_aap"])
    return amb == "so" or bool({familia(s) for s in skills_de(e)} & set(amb))


_PLAT = {"jboss": "JBoss", "tomcat": "Tomcat", "keycloak": "Keycloak", "weblogic": "WebLogic", "iis": "IIS", "nginx": "Nginx",
         "httpd": "Apache", "redis": "Redis", "postgresql": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB", "openshift": "OpenShift"}


def _ambito_txt(a, e, ui=""):
    """«Sistema operativo», «Máquina» (mover/replicar) o el nombre de la plataforma de la máquina a la que pertenece."""
    from .skills_maquina import ambito
    from .inventario import skills_de
    from .vistas import familia
    if ui:
        return "Máquina"
    amb = ambito(a["nombre_aap"])
    if amb == "so":
        return "Sistema operativo"
    fams = [f for f in amb if f in {familia(s) for s in skills_de(e)}] or list(amb)
    return " · ".join(_PLAT.get(f, f.capitalize()) for f in fams[:2])


def _auto(e, h):
    """(automatización, motivo de bloqueo) de la primera acción de la habilidad que aplica a la máquina; (None, None) si ninguna."""
    from .operacion import motivo_no_aplica
    elegida, bloqueo = None, None
    for n_ in h.get("nombre_aap") or []:
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], n_)
        if not a:
            continue
        a = dict(a)
        if not h.get("ambito") and not _en_ambito(a, e):
            continue
        if h.get("origen") or h.get("sin_limite"):   # elige su servidor en el formulario: basta con que sea compatible y esté en el catálogo
            from .compatibilidad import compatible
            from .inventario import skills_de
            if not a.get("visible") or e.get("estado") == "retirado" or (
                    not h.get("sin_limite") and not compatible(a, dict(e, skills=list(skills_de(e))))[0]):   # sin límite: elige su origen, no corre en la máquina
                continue
        elif not h.get("ui") and motivo_no_aplica(g.usuario, g.cliente["id"], a, e):
            continue
        b = _motivo_prueba(a)
        if not b:
            return a, None
        if not elegida:
            elegida, bloqueo = a, b
    return elegida, bloqueo


def _ticket(e, aid, origen=None):
    """Último ticket de la acción sobre la máquina: abierto (por cualquier vía) o exitoso pedido desde la ventana.
    origen: parámetro que nombra la máquina cuando la acción no la lleva en el límite."""
    for t in q("SELECT id, numero, estado, limite, motivo, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 40",
               g.cliente["id"], aid):
        desde_aqui = (t["motivo"] or "").startswith(MARCA) and (t["motivo"] or "").endswith(f" en {e['nombre']}")
        if origen and (j(t["parametros"], {}) or {}).get(origen) == e["nombre"]:
            pass
        elif e["nombre"] not in (t["limite"] or "").split(":") and not desde_aqui:
            continue
        if t["estado"] in ABIERTOS:
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": True}
        if t["estado"] == "exitoso" and desde_aqui:
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": False}
        if desde_aqui:
            return None   # el último pedido desde aquí falló o se canceló: se puede volver a pedir
    return None


def _resolver(h, e):
    """La habilidad para esta máquina: con «por_so» toma la acción y los campos del sistema que calce; con
    «solo_sin_registro» solo si FaceOps sabe que la máquina NO está registrada o licenciada. None si no aplica."""
    so = e.get("so") or ""
    if h.get("so") and not re.search(h["so"], so):
        return None
    if h.get("sin_skill"):
        from .inventario import skills_de
        if h["sin_skill"] in set(skills_de(e)):
            return None
    if h.get("categoria") and not re.search(h["categoria"], e.get("categoria") or "servidor"):
        return None
    if not h.get("categoria") and (e.get("categoria") or "") == "clúster openshift":
        return None   # las skills de máquina (SO, plataformas) no se ofrecen en un clúster: tiene las suyas
    if h.get("solo_sin_registro"):
        d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
        if d.get("registrado") is not False and d.get("licenciado") is not False:
            return None
    if h.get("por_so"):
        v = next((x for x in h["por_so"] if re.search(x["so"], so)), None)
        if not v:
            return None
        h = dict(h, nombre_aap=v["nombre_aap"], campos=v.get("campos") or [])
    return h


def _faltan_datos(a, h):
    """Datos obligatorios de la acción que la skill no completa (ni por defecto, ni fijos, ni pidiéndolos, ni la máquina)."""
    tiene = {h.get("origen")} | set(h.get("parametros") or {}) | {c["var"] for c in h.get("campos") or []}
    return [c["etiqueta"] for c in j(a.get("campos"), []) if c.get("requerido") and c.get("tipo") not in ("equipo", "equipos")
            and c.get("defecto") in (None, "") and c["var"] not in tiene]


@bp.app_template_global("habilidades_maquina")
def habilidades_maquina(e, rep=None):
    """[{id, tipo, grupo, nombre, valor, auto, aid, ui, bloqueo, ticket, url}] de la máquina, diferenciadores primero."""
    escritura = getattr(g, "perfil", None) and g.perfil["modo"] == "escritura"
    out = []
    for h in mapa():
        h = _resolver(h, e)
        if not h:
            continue
        a, bloqueo = _auto(e, h)
        if not a and h.get("por_so"):   # la acción de este sistema aún no existe: se muestra deshabilitada con su motivo
            out.append({"id": h["id"], "tipo": h.get("tipo") or "quick win", "grupo": h.get("grupo") or "", "nombre": h["nombre"],
                        "valor": h.get("valor") or "", "ambito": h.get("ambito") or "Sistema operativo", "auto": h["nombre_aap"][0], "aid": None, "ui": "",
                        "bloqueo": "aún no hay una acción del catálogo compatible y certificada para esta máquina",
                        "ticket": None, "escritura": bool(escritura), "url": "", "campos": []})
            continue
        if not a:
            continue
        x = {"id": h["id"], "tipo": h.get("tipo") or "quick win", "grupo": h.get("grupo") or "", "nombre": h["nombre"],
             "valor": h.get("valor") or "", "ambito": h.get("ambito") or _ambito_txt(a, e, h.get("ui") or ""), "auto": a["nombre"], "aid": a["id"], "ui": h.get("ui") or "", "bloqueo": bloqueo or "",
             "ticket": None, "escritura": bool(escritura), "url": url_for("consola.habilidad_ejecutar", eid=e["id"]),
             "campos": h.get("campos") or []}
        if x["ui"] == "replica":   # su flotante propio decide si se puede (instancias, balanceo, regla de prueba)
            if not rep or not rep.get("posible"):
                continue
            x["bloqueo"] = x["bloqueo"] or (rep.get("bloqueo") or "")
            x["en_curso"] = rep.get("en_curso")
        elif not x["ui"]:
            if not bloqueo:
                f_ = _faltan_datos(a, h)
                if f_:
                    x["bloqueo"] = "pide datos (" + ", ".join(f_[:2]) + "): lánzala desde Acciones"
            x["ticket"] = _ticket(e, a["id"], h.get("origen"))
        out.append(x)
    out.sort(key=lambda x: (x["tipo"] != "diferenciador", bool(x["bloqueo"])))
    return out


@bp.route("/inventario/<int:eid>/habilidad", methods=["POST"])
@requiere_login
def habilidad_ejecutar(eid):
    """Crea el ticket (con aprobación en AAP) de la habilidad; si ya hay uno abierto de esa acción para la máquina, devuelve ese."""
    from .aap import ErrorAAP
    from .operacion import solicitar_en_equipo
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    h = next((x for x in mapa() if x.get("id") == request.form.get("hid")), None)
    if not e or not h or h.get("ui"):
        return {"error": "Skill o máquina no válidas"}, 400
    h = _resolver(h, e)
    if not h:
        return {"error": "Este skill no aplica a la máquina"}, 400
    a, bloqueo = _auto(e, h)
    if not a:
        return {"error": "Ninguna acción de este skill aplica a la máquina"}, 400
    if bloqueo:
        return {"error": f"«{a['nombre']}» está bloqueada: {bloqueo}"}, 400
    if _faltan_datos(a, h):
        return {"error": f"«{a['nombre']}» pide datos: lánzala desde Acciones"}, 400
    t0 = _ticket(e, a["id"], h.get("origen"))
    if t0 and t0["abierto"]:
        return dict(t0, url=url_for("consola.ticket", tid=t0["id"]), existente=True)
    params, secretos = {}, {}
    for c in j(a.get("campos"), []):   # valores por defecto de la acción
        if c["var"] == h.get("origen"):
            params[c["var"]] = e["nombre"]
        elif c.get("tipo") == "equipo":
            params[c["var"]] = e["nombre"]
        elif c.get("tipo") == "equipos":
            params[c["var"]] = [e["nombre"]]
        elif c.get("defecto") not in (None, ""):
            params[c["var"]] = int(c["defecto"]) if c.get("tipo") == "integer" and str(c["defecto"]).lstrip("-").isdigit() else c["defecto"]
    params.update(h.get("parametros") or {})
    for c in h.get("campos") or []:   # datos que pide la skill: las claves (password) solo viajan a AAP; en FaceOps quedan enmascaradas
        v = (request.form.get("v_" + c["var"]) or "").strip()
        if not v:
            if c.get("requerido"):
                return {"error": f"Falta «{c['etiqueta']}»"}, 400
            continue
        if c.get("tipo") == "password":
            secretos[c["var"]], params[c["var"]] = v, "••••••"
        else:
            params[c["var"]] = v
    try:
        if h.get("origen") or h.get("sin_limite"):   # la acción elige su origen por parámetro (no por límite)
            from .operacion import solicitar
            t = solicitar(g.usuario, g.cliente, a, None, params, f"{MARCA}{h['nombre']} en {e['nombre']}", secretos or None,
                          {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {})
        else:
            t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params, f"{MARCA}{h['nombre']} en {e['nombre']}", secretos or None)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"]}
