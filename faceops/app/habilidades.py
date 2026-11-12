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
        elif h.get("ui") in (None, "", "parches") and motivo_no_aplica(g.usuario, g.cliente["id"], a, e):
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


SUB_RED = ("router", "switch", "firewall", "balanceador", "waf", "vpn", "wifi")
_SUB_RED_SO = (("waf", r"(?i)fortiweb|\bwaf\b|modsecurity|imperva"), ("balanceador", r"(?i)big-?ip|\bf5\b|haproxy|netscaler|citrix adc|nginx|avi"),
               ("firewall", r"(?i)fortios|fortigate|pan-?os|palo alto|\basa\b|checkpoint|gaia|sophos|pfsense|opnsense|vyos"),
               ("wifi", r"(?i)aironet|meraki mr|aruba ?os|wlc|unifi"), ("vpn", r"(?i)\bvpn\b|openvpn|wireguard|ipsec"),
               ("switch", r"(?i)nx-?os|cumulus|sonic|eos\b|comware|switch"))


def subcategoria_red(e):
    """router | switch | firewall | balanceador | waf | vpn | wifi si es un equipo de red; "" si no. Los de tipo «red»
    (sin subcategoría) se clasifican por su sistema y, si no alcanza, por su nombre (sw-, fw-, lb-, rtr-)."""
    c = (e.get("categoria") or "").lower()
    if c in SUB_RED:
        return c
    if c != "red":
        return ""
    so, n = e.get("so") or "", (e.get("nombre") or "").lower()
    for sub, rx in _SUB_RED_SO:
        if re.search(rx, so):
            return sub
    for sub, rx in (("switch", r"(^|[-_])sw\d*([-_]|$)"), ("firewall", r"(^|[-_])fw\d*([-_]|$)"), ("balanceador", r"(^|[-_])lb\d*([-_]|$)"),
                    ("vpn", r"(^|[-_])vpn"), ("wifi", r"(^|[-_])(ap|wlc)\d*([-_]|$)")):
        if re.search(rx, n):
            return sub
    return "router"   # IOS/IOS XE, Junos, MikroTik… sin otra pista: enrutamiento


def _red_auto(e, h):
    """(acción, motivo de bloqueo) de una skill de red; (None, motivo) si la acción aún no existe o no es para su sistema."""
    so = e.get("so") or ""
    if h.get("so_accion") and not re.search(h["so_accion"], so):
        return None, f"acción aún no disponible para {so.split(' ')[0] if so else 'este sistema'}"
    if h.get("equipos") and not re.search(h["equipos"], e.get("nombre") or ""):
        return None, "acción aún no disponible para este equipo"
    for n_ in h.get("nombre_aap") or []:
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], n_)
        if not a:
            continue
        a = dict(a)
        if e.get("estado") == "retirado":
            return a, "el equipo está retirado"
        if a["usa_limite"] and a["inventario"] and a["inventario"] not in j(e.get("inventarios"), []):
            return a, "el equipo no está en el inventario de AAP de esta acción"
        return a, _motivo_prueba(a)
    return None, "acción aún no disponible"


def _resolver(h, e):
    """La habilidad para esta máquina: con «por_so» toma la acción y los campos del sistema que calce; con
    «solo_sin_registro» solo si FaceOps sabe que la máquina NO está registrada o licenciada. None si no aplica.
    Un equipo de red solo recibe las skills «red» de su subcategoría."""
    if h.get("plataforma"):
        return None   # las de una plataforma viven en su ficha (habilidades_plataforma), no en la lista de la máquina
    sub = subcategoria_red(e)
    if h.get("red"):
        return h if sub and (h["red"] == "todos" or sub in h["red"]) else None
    if sub:
        return None
    so = e.get("so") or ""
    if h.get("so") and not re.search(h["so"], so):
        return None
    if h.get("sin_skill"):
        from .inventario import skills_de
        if h["sin_skill"] in set(skills_de(e)):
            return None
    if h.get("categoria") == "hipervisor" or (e.get("categoria") or "") == "hipervisor":
        return None   # las del hipervisor viven en habilidades_hipervisor (ficha del hipervisor), no en una máquina
    if h.get("categoria") and not re.search(h["categoria"], e.get("categoria") or "servidor"):
        return None
    if not h.get("categoria") and (e.get("categoria") or "") in ("clúster openshift", "estación de trabajo"):
        return None   # las skills de servidor (SO, plataformas) no se ofrecen en un clúster ni en una estación: tienen las suyas
    if h.get("solo_sin_registro"):
        d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
        if d.get("registrado") is not False and d.get("licenciado") is not False:
            return None
    if h.get("por_so"):
        v = next((x for x in h["por_so"] if re.search(x["so"], so)), None)
        if not v:
            return None
        h = dict(h, nombre_aap=v["nombre_aap"], campos=v.get("campos") or [], ui=v.get("ui", h.get("ui")),
                 parametros=v.get("parametros", h.get("parametros")))
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
    if (e.get("categoria") or "") == "hipervisor":   # ficha del hipervisor: sus propias skills (Ámbito = Hipervisor)
        return habilidades_hipervisor(e, bool(escritura))
    out = []
    for h in mapa():
        h = _resolver(h, e)
        if not h:
            continue
        if h.get("red"):   # equipo de red: la skill siempre se muestra; deshabilitada con su motivo si aún no se puede
            a, bloqueo = _red_auto(e, h)
            x = {"id": h["id"], "tipo": h.get("tipo") or "quick win", "grupo": h.get("grupo") or "", "nombre": h["nombre"],
                 "valor": h.get("valor") or "", "ambito": h.get("ambito") or "Red", "auto": a["nombre"] if a else h["nombre_aap"][0],
                 "aid": a["id"] if a else None, "ui": "", "bloqueo": bloqueo or "", "ticket": None, "escritura": bool(escritura),
                 "url": url_for("consola.habilidad_ejecutar", eid=e["id"]) if a else "", "campos": h.get("campos") or []}
            if a and not bloqueo:
                f_ = _faltan_datos(a, h)
                if f_:
                    x["bloqueo"] = "pide datos (" + ", ".join(f_[:2]) + "): lánzala desde Acciones"
            if a:
                x["ticket"] = _ticket(e, a["id"], h.get("origen"))
            out.append(x)
            continue
        a, bloqueo = _auto(e, h)
        if not a and h.get("por_so"):   # la acción de este sistema aún no existe o no aplica: se muestra deshabilitada con su motivo
            existe = any(q1("SELECT 1 FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], n_) for n_ in h["nombre_aap"])
            out.append({"id": h["id"], "tipo": h.get("tipo") or "quick win", "grupo": h.get("grupo") or "", "nombre": h["nombre"],
                        "valor": h.get("valor") or "", "ambito": h.get("ambito") or "Sistema operativo", "auto": (h["nombre_aap"] or [h["nombre"]])[0], "aid": None, "ui": "",
                        "bloqueo": "aún no hay una acción del catálogo compatible y certificada para esta máquina" if existe else "acción aún no disponible",
                        "ticket": None, "escritura": bool(escritura), "url": "", "campos": []})
            continue
        if not a:
            continue
        x = {"id": h["id"], "tipo": h.get("tipo") or "quick win", "grupo": h.get("grupo") or "", "nombre": h["nombre"],
             "valor": h.get("valor") or "", "ambito": h.get("ambito") or _ambito_txt(a, e, h.get("ui") or ""), "auto": a["nombre"], "aid": a["id"], "ui": h.get("ui") or "", "bloqueo": bloqueo or "",
             "ticket": None, "escritura": bool(escritura), "url": url_for("consola.habilidad_ejecutar", eid=e["id"]),
             "campos": h.get("campos") or []}
        if x["ui"] == "parches":   # su formulario propio (qué se aplica, reinicio y límite): nunca se lanza sin parámetros
            from .cves import pendientes_parche
            revisado, pend = pendientes_parche(g.cliente["id"], e)
            if not x["bloqueo"] and not pend:
                x["bloqueo"] = "sin parches de seguridad pendientes" if revisado else "sus CVE aún no están revisados («Revisar CVEs»)"
            x["n_pendientes"] = len(pend)
            x["url"] = url_for("consola.equipo_cve_parche", eid=e["id"])
            x["url_datos"] = url_for("consola.equipo_parches", eid=e["id"])
            x["ticket"] = _ticket(e, a["id"], h.get("origen"))
        elif x["ui"] == "replica":   # su flotante propio decide si se puede (instancias, balanceo, regla de prueba)
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
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    return ejecutar(e, request.form.get("hid"), request.form)


def ejecutar(e, hid, form):
    """(respuesta JSON, código) de pedir una skill sobre la máquina e: ticket con aprobación en AAP (o el abierto que ya hay)."""
    from .aap import ErrorAAP
    from .operacion import solicitar_en_equipo
    h = next((x for x in mapa() if x.get("id") == hid), None)
    if not e or not h or h.get("ui"):
        return {"error": "Esta skill se pide desde su formulario (abre la skill de nuevo)" if h and h.get("ui") else "Skill o máquina no válidas"}, 400
    if h.get("plataforma"):
        if not set(_lista(h["plataforma"])) & set(plataformas_de(e)):
            return {"error": "Este skill no aplica a la plataforma de esta máquina"}, 400
    else:
        h = _resolver(h, e)
    if not h:
        return {"error": "Este skill no aplica a la máquina"}, 400
    a, bloqueo = _red_auto(e, h) if h.get("red") else (_plat_auto(e, h) if h.get("plataforma") else _auto(e, h))
    if not a:
        return {"error": f"Ninguna acción de este skill aplica a la máquina{': ' + bloqueo if bloqueo else ''}"}, 400
    if bloqueo:
        return {"error": f"«{a['nombre']}» está bloqueada: {bloqueo}"}, 400
    if _faltan_datos(a, h):
        return {"error": f"«{a['nombre']}» pide datos: lánzala desde Acciones"}, 400
    t0 = _ticket_plat(e, a["id"], h) if h.get("plataforma") else _ticket(e, a["id"], h.get("origen"))
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
        v = (form.get("v_" + c["var"]) or "").strip()
        if not v:
            if c.get("requerido"):
                return {"error": f"Falta «{c['etiqueta']}»"}, 400
            continue
        if c.get("opciones") and v not in [str(o_) for o_ in c["opciones"]]:
            return {"error": f"«{c['etiqueta']}»: elige {' o '.join(str(o_) for o_ in c['opciones'])}"}, 400
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


# ------------------------------------------------------------------ skills del hipervisor (ficha /hipervisores/<id>)
def _skill_hv(e, h):
    """(automatización, motivo) de una skill del hipervisor: motivo vacío si se puede pedir desde su ficha."""
    from .pruebas import estado, exceptuada
    a = next((x for n_ in h.get("nombre_aap") or [] for x in [q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], n_)] if x), None)
    if h.get("solo") and not re.search(h["solo"], e.get("hv_tipo") or ""):
        return a, f"aplica a hipervisores {h['solo'].replace('|', ' o ')}; este es {e.get('so') or e.get('hv_tipo')}"
    if not a:
        return None, "aún no disponible: la acción no está publicada en AAP"
    ep = estado(dict(a, cliente_id=g.cliente["id"]))["estado"]
    if ep != "certificada" and not exceptuada(a["nombre_aap"]):
        return a, {"cambiada": "cambió desde su última prueba; debe probarse de nuevo", "fallida": "su última prueba falló"}.get(ep, "sin plan de prueba aprobado")
    if h.get("por_maquina"):
        return a, "se pide desde la ficha de cada máquina (elige la máquina y su destino)"
    f_ = _faltan_datos(a, h)
    if f_:   # la ventana del hipervisor no pide datos: sin ellos no se crea el ticket
        return a, "pide datos (" + ", ".join(f_[:2]) + "): lánzala desde Acciones"
    if e.get("hv_estado") != "conectada":
        return a, "el hipervisor no está conectado: verifica su conexión"
    return a, ""


def _ticket_hv(e, aid):
    for t in q("SELECT id, numero, estado, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 40", g.cliente["id"], aid):
        if (j(t["parametros"], {}) or {}).get("hv_infra") != e["hv_nombre"]:
            continue
        if t["estado"] in ABIERTOS:
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": True}
        return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": False} if t["estado"] == "exitoso" else None
    return None


def habilidades_hipervisor(e, escritura):
    """Skills de la ficha del hipervisor (datos/habilidades.yml, categoria: hipervisor), mismas columnas y estados que las de
    la máquina. Las que no tienen acción publicada o certificada se muestran deshabilitadas con su motivo."""
    out = []
    for h in mapa():
        if h.get("categoria") != "hipervisor" or (h.get("hipervisor") and not re.search(h["hipervisor"], e.get("hv_tipo") or "")):
            continue
        a, motivo = _skill_hv(e, h)
        out.append({"id": h["id"], "tipo": h.get("tipo") or "quick win", "grupo": h.get("grupo") or "", "nombre": h["nombre"], "valor": h.get("valor") or "",
                    "ambito": h.get("ambito") or "Hipervisor", "auto": (a or {}).get("nombre") or h["nombre_aap"][0], "aid": (a or {}).get("id"), "ui": "",
                    "bloqueo": motivo, "ticket": _ticket_hv(e, a["id"]) if a else None, "escritura": escritura, "url": e.get("url_skill") or "", "campos": []})
    out.sort(key=lambda x: (x["tipo"] != "diferenciador", bool(x["bloqueo"])))
    return out


def pedir_habilidad_hipervisor(e, hid):
    """Ticket (con aprobación en AAP) de una skill del hipervisor; si ya hay uno abierto, devuelve ese."""
    from .operacion import solicitar
    h = next((x for x in mapa() if x.get("id") == hid and x.get("categoria") == "hipervisor"), None)
    if not h:
        raise ValueError("Skill no válida")
    a, motivo = _skill_hv(e, h)
    if motivo:
        raise PermissionError(f"«{h['nombre']}»: {motivo}")
    t0 = _ticket_hv(e, a["id"])
    if t0 and t0["abierto"]:
        return dict(t0, existente=True), a
    params = dict(h.get("parametros") or {}, hv_infra=e["hv_nombre"])
    return solicitar(g.usuario, g.cliente, a, None, params, f"{MARCA}{h['nombre']} en {e['hv_nombre']}"), a


# ------------------------------------------------------------------ skills de una plataforma (ficha /plataformas/<ID>)
def _lista(x):
    return [x] if isinstance(x, str) else list(x or [])


def plataformas_de(e):
    """Códigos de plataforma que corren en la máquina (la misma detección de Plataformas)."""
    from .vistas import _plataformas_de, _riesgo_parque
    r = next((r_ for e_, r_ in _riesgo_parque() if e_["id"] == e["id"]), {}) or {}
    return _plataformas_de(e, r)


def _plat_auto(e, h):
    """(automatización, motivo) de una skill de plataforma: la primera acción del catálogo que exista; motivo vacío si se puede
    pedir. Sin acción publicada: (None, «acción aún no disponible»); sin certificar: el motivo de la regla de prueba."""
    from .operacion import motivo_no_aplica
    for n_ in h.get("nombre_aap") or []:
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], n_)
        if not a:
            continue
        a = dict(a)
        if e.get("energia") == "apagado" and not h.get("apagada_ok"):
            return a, "la máquina que la corre está apagada"
        if not h.get("sin_limite") and not h.get("origen"):
            no_ = motivo_no_aplica(g.usuario, g.cliente["id"], a, e)
            if no_:
                return a, no_
        return a, _motivo_prueba(a) or ""
    return None, "acción aún no disponible"


def _ticket_plat(e, aid, h):
    """Último ticket de la skill de plataforma sobre la máquina (mismos parámetros fijos): abierto o exitoso."""
    fijos = h.get("parametros") or {}
    for t in q("SELECT id, numero, estado, limite, motivo, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 60",
               g.cliente["id"], aid):
        p_ = j(t["parametros"], {}) or {}
        if e["nombre"] not in (t["limite"] or "").split(":") or any(p_.get(k) != v for k, v in fijos.items()):
            continue
        if t["estado"] in ABIERTOS:
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": True}
        return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": False} if t["estado"] == "exitoso" else None
    return None


def skill_plataforma(e, hid, escritura=True):
    """Estado de una skill de plataforma para pintarla (en la ventana Skills o como acción del flotante de salud)."""
    h = next((x for x in mapa() if x.get("id") == hid and x.get("plataforma")), None)
    if not h:
        return None
    a, motivo = _plat_auto(e, h)
    if a and not motivo:
        f_ = _faltan_datos(a, h)
        if f_ and not h.get("campos"):
            motivo = "pide datos (" + ", ".join(f_[:2]) + "): lánzala desde Acciones"
    return {"id": h["id"], "tipo": h.get("tipo") or "quick win", "grupo": h.get("grupo") or "", "nombre": h["nombre"], "valor": h.get("valor") or "",
            "ambito": h.get("ambito") or "", "auto": (a or {}).get("nombre") or (h.get("nombre_aap") or [h["nombre"]])[0], "aid": (a or {}).get("id"), "ui": "",
            "bloqueo": motivo or "", "ticket": _ticket_plat(e, a["id"], h) if a else None, "escritura": bool(escritura),
            "url": url_for("consola.habilidad_ejecutar", eid=e["id"]) if a else "", "campos": h.get("campos") or []}


@bp.app_template_global("habilidades_plataforma")
def habilidades_plataforma(e, cod, ambito=""):
    """Skills de la ficha de una plataforma (habilidades.yml › plataforma: <código>), con Ámbito = la plataforma. Las que no
    tienen acción publicada o no están certificadas se muestran deshabilitadas con su motivo."""
    escritura = bool(getattr(g, "perfil", None) and g.perfil["modo"] == "escritura")
    out = []
    for h in mapa():
        if cod in _lista(h.get("plataforma")):
            x = skill_plataforma(e, h["id"], escritura)
            if x:
                x["ambito"] = x["ambito"] or ambito
                out.append(x)
    out.sort(key=lambda x: (x["tipo"] != "diferenciador", bool(x["bloqueo"])))
    return out

