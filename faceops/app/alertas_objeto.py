"""Alertas por objeto: cada objeto del sistema (servidor, storage, equipo de red…) tiene su botón «Alertas».

El catálogo y la configuración viven en Ansible: FaceOps ofrece las alertas que aplican al tipo de objeto (catálogo
aap/files/alertas-objetos.yml, el mismo que usa el playbook) y pide su configuración con un ticket a
«Monitoreo · Configurar alertas del equipo», que la escribe en Prometheus. FaceOps guarda solo el reflejo de lo que Ansible
dejó aplicado (datos.alertas del equipo, desde los artefactos del ticket) para mostrar el estado del botón."""
import json
import os
import re
import time
from urllib.parse import urlencode

import yaml

from .bd import ahora, ej, j, q, q1
from .hora_local import texto_utc

AUTO = "Monitoreo · Configurar alertas del equipo"
RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aap", "files", "alertas-objetos.yml")
RED = ("router", "switch", "firewall", "balanceador")
_cache = {}
# Grafana del monitoreo: tablero «Alertas de <equipo>» que deja el mismo playbook (uno por equipo, solo sus alertas activas). Por el acceso público vive en /grafana/
# detrás de la sesión de FaceOps (proxy con auth_request, solo lectura); FACEOPS_GRAFANA cambia la base si hace falta.
GRAFANA = os.environ.get("FACEOPS_GRAFANA", "/grafana").rstrip("/")
REGLAS = "/opt/summit-monitoreo/prometheus/faceops/reglas"


def panel_id(aid):
    """El mismo id de panel que arma aap/files/alertas_objeto.py (C01 → 6701)."""
    return ord(aid[0].upper()) * 100 + int(re.sub(r"\D", "", aid) or 0)


def uid_tablero(eq):
    """El mismo uid que arma aap/files/alertas_objeto.py para el tablero del equipo (máx. 40 caracteres)."""
    import hashlib
    u = f"faceops-al-{eq}"
    return u if len(u) <= 40 else f"faceops-al-{eq[:20]}-{hashlib.sha1(eq.encode()).hexdigest()[:8]}"


def sufijo(ambito):
    """'' para la máquina; '--jboss' para la plataforma JBoss (grupo, archivo de reglas y tablero propios)."""
    return "" if (ambito or "maquina") == "maquina" else "--" + ambito.split(":", 1)[1]


def ambito_de_plataforma(fam):
    """Familia de la plataforma (jboss, redis…) → ámbito del catálogo, o None si el catálogo no tiene alertas para ella."""
    a = f"plataforma:{fam}"
    return a if any(x.get("ambito") == a for x in catalogo()["alertas"]) else None


def url_grafana(equipo, aid=None, ambito="maquina"):
    """Tablero «Alertas de <equipo>» (solo sus alertas activas del ámbito), últimas 24 h y, si se indica, solo el panel de esa alerta."""
    par = {"orgId": 1, "from": "now-24h", "to": "now"}
    if aid:
        par["viewPanel"] = panel_id(aid)
    return f"{GRAFANA}/d/{uid_tablero(equipo + sufijo(ambito))}?" + urlencode(par)


def nombre_regla(a):
    """Nombre de la regla en Prometheus (igual que el playbook): «CPU alta» C01 → FaceOpsCpuAltaC01 (+Aviso/Critico)."""
    return "FaceOps" + re.sub(r"[^A-Za-z0-9]", "", a["nombre"].title()) + a["id"]


def catalogo():
    m = os.path.getmtime(RUTA)
    if _cache.get("m") != m:
        _cache.update(m=m, d=yaml.safe_load(open(RUTA, encoding="utf-8")))
    return _cache["d"]


def tipo_de(e):
    """linux | windows | storage | red | None (sin alertas para este objeto todavía)."""
    c = (e.get("categoria") or "").lower()
    so = (e.get("so") or "").lower()
    if c == "segmento":
        return "segmento"   # segmento de negocio (app/segmento_ops.py): alertas de nivel servicio, sondas blackbox
    if c == "hipervisor":   # ficha del hipervisor (mod_hipervisores.objeto_alertas): alertas propias del anfitrión y su virtualización
        return e.get("hv_tipo") if e.get("hv_tipo") in ("kvm", "vmware") else None
    if c in RED:
        return "red"
    if c == "almacenamiento":
        return "storage"
    if "openshift" in c or c == "openshift":
        return "openshift"   # alertas tentativas del clúster (en revisión)
    if c.startswith("clúster") or c == "kubernetes":
        return None
    if "windows" in so:
        return "windows"
    so = f"{so} {(e.get('so_familia') or '').lower()}"
    if any(x in so for x in ("linux", "red hat", "redhat", "rhel", "rocky", "alma", "centos", "ubuntu", "debian", "oracle", "suse", "fedora")):
        return "linux"
    if c in ("", "servidor", "estación de trabajo") and not so.strip():
        return "linux"   # aún sin inventariar: se ofrece como Linux; el playbook verifica el agente antes de aplicar
    return None


def aplicadas(e, ambito="maquina"):
    """Lo que Ansible dejó configurado para el equipo en ese ámbito (reflejo del último ticket exitoso). Las alertas que hoy
    son de una plataforma pero quedaron aplicadas en el grupo de la máquina siguen contando ahí hasta el próximo ticket."""
    d = j(e.get("datos"), {}) or {}
    if (ambito or "maquina") == "maquina":
        return d.get("alertas") or {}
    return (d.get("alertas_plat") or {}).get(ambito) or {}


def en_curso(cliente_id, e, ambito="maquina"):
    a = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, AUTO)
    if not a:
        return None
    for t in q("SELECT id, numero, estado, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? "
               "AND estado IN ('pendiente_aprobacion','en_cola','en_ejecucion') ORDER BY id DESC LIMIT 50", cliente_id, a["id"]):
        p_ = j(t["parametros"], {}) or {}
        if p_.get("al_equipo") == e["nombre"] and (p_.get("al_ambito") or "maquina") == (ambito or "maquina"):
            return t
    return None


def transito(cliente_id, e, ambito="maquina", cargadas=None):
    """Estado intermedio por alerta mientras su ticket está pendiente o en curso («Activando» / «Desactivando» /
    «Actualizando» · ticket), o «Falló · ticket» si el último ticket del ámbito falló (vuelve a su estado anterior).
    cargadas: ids realmente cargados en el monitoreo (para saber si el ticket activa o desactiva)."""
    a = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, AUTO)
    if not a:
        return {}
    cargadas = set(cargadas or [])
    out = {}
    ts = q("SELECT id, numero, estado, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 80", cliente_id, a["id"])
    mios = [t for t in ts if (j(t["parametros"], {}) or {}).get("al_equipo") == e["nombre"]
            and ((j(t["parametros"], {}) or {}).get("al_ambito") or "maquina") == (ambito or "maquina")]
    for t in mios:   # del más nuevo al más viejo
        p_ = j(t["parametros"], {}) or {}
        on = {x["id"] for x in p_.get("al_alertas") or []}
        off = set(p_.get("al_retiradas") or [])
        if t["estado"] in ("pendiente_aprobacion", "en_cola", "en_ejecucion"):
            for aid in on:
                out.setdefault(aid, {"txt": "Actualizando" if aid in cargadas else "Activando", "numero": t["numero"], "tid": t["id"], "curso": True})
            for aid in off & cargadas:
                out.setdefault(aid, {"txt": "Desactivando", "numero": t["numero"], "tid": t["id"], "curso": True})
            continue
        if t["estado"] in ("fallido", "error", "rechazado", "vencido", "cancelado"):
            for aid in (on - cargadas) | (off & cargadas):
                out.setdefault(aid, {"txt": "Falló" if t["estado"] in ("fallido", "error") else t["estado"].capitalize(), "numero": t["numero"], "tid": t["id"], "curso": False})
        break   # el último ticket terminado manda: lo anterior ya quedó reflejado en el monitoreo
    return out


AUTO_GRAFANA_OCP = "Monitoreo · Grafana de OpenShift"
AUTO_EXPORTADOR = "FaceOps · Exportador de plataforma"


def exportador_estado(e, fam):
    """¿Prometheus lee el exportador de la plataforma en este equipo? True / False (objetivo sin respuesta) / None (sin objetivo)."""
    import requests
    try:
        r = requests.get(PROMETHEUS + "/api/v1/query", params={"query": f'up{{job="faceops-exportadores",faceops_equipo="{e["nombre"]}",faceops_plataforma="{fam}"}}'}, timeout=4)
        res = r.json()["data"]["result"]
        return None if not res else res[0]["value"][1] == "1"
    except Exception:  # noqa: BLE001
        return None
PROMETHEUS = os.environ.get("FACEOPS_PROMETHEUS", "http://127.0.0.1:9090")   # Prometheus del anfitrión (FaceOps usa la red del host)
_mon = {}


def grupo_monitoreo(e, ambito="maquina"):
    return f"faceops-{e['nombre']}{sufijo(ambito)}"


def reglas_monitoreo(e, ambito="maquina", tipo=None):
    """Lo que REALMENTE está cargado en el monitoreo para este objeto (no el reflejo del último ticket): por alerta del catálogo
    (etiqueta faceops_alerta), sus reglas con su expresión, duración, salud y estado (firing / pending / inactive). Fuente:
    Prometheus del anfitrión (grupo faceops-<equipo>[--plataforma]) o, para un clúster OpenShift, su Thanos Querier con la
    cuenta de solo lectura que dejó «Monitoreo · Grafana de OpenShift» (token en la bóveda). None si no se pudo consultar."""
    import requests
    grupo = grupo_monitoreo(e, ambito)
    hdr, url = {}, PROMETHEUS
    if tipo == "openshift":
        from .boveda import leer
        cred = leer(f"maquinas/{e['nombre']}/grafana-monitoreo") or {}
        if not cred.get("url") or not cred.get("token"):
            return None
        url, hdr = cred["url"], {"Authorization": f"Bearer {cred['token']}"}
    k = (url, grupo)
    if k in _mon and time.time() - _mon[k][0] < 15:
        return _mon[k][1]
    try:
        r = requests.get(url.rstrip("/") + "/api/v1/rules", params={"type": "alert"}, headers=hdr, timeout=4, verify=False)
        r.raise_for_status()
        gs = [g_ for g_ in r.json()["data"]["groups"] if g_["name"] == grupo]
    except Exception:  # noqa: BLE001
        return None
    out = {}
    orden = {"firing": 2, "pending": 1, "inactive": 0}
    for g_ in gs:
        for x in g_.get("rules") or []:
            aid = (x.get("labels") or {}).get("faceops_alerta")
            if not aid:
                continue
            d = out.setdefault(aid, {"reglas": [], "estado": "inactive", "salud": "ok", "grupo": grupo, "archivo": g_.get("file", ""), "fuente": url})
            d["reglas"].append({k_: x.get(k_) for k_ in ("name", "query", "duration", "labels", "annotations", "health", "lastError", "state", "lastEvaluation")}
                               | {"activas": len(x.get("alerts") or [])})
            if orden.get(x.get("state"), 0) > orden.get(d["estado"], 0):
                d["estado"] = x.get("state")
            if x.get("health") == "err":
                d["salud"] = "err"
    _mon[k] = (time.time(), out)
    return out


def grafana_ocp(cliente_id, e):
    """URL base del tablero «Alertas de OpenShift · <clúster>» si «Monitoreo · Grafana de OpenShift» ya lo dejó (último ticket
    exitoso para este clúster); si no, None."""
    a = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, AUTO_GRAFANA_OCP)
    if not a:
        return None
    for t in q("SELECT parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? AND estado='exitoso' ORDER BY id DESC LIMIT 20", cliente_id, a["id"]):
        if (j(t["parametros"], {}) or {}).get("go_cluster") == e["nombre"]:
            return f"{GRAFANA}/d/" + f"faceops-ocp-{e['nombre']}"[:40]
    return None


def bloqueo_accion(cliente_id, cat, k, _memo={}):
    """Por qué no se puede elegir una acción ("" si se puede): su automatización no está publicada o no pasó su plan de
    prueba (regla de prueba, app/pruebas.py). «Solo avisar» no lanza nada y siempre se puede."""
    auto = ((cat.get("acciones") or {}).get(k) or {}).get("auto")
    if not auto:
        return ""
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, auto)
    if not a:
        return f"sin automatización aún («{auto}» no está en AAP)"
    from .pruebas import estado
    return "" if estado(dict(a, cliente_id=cliente_id))["estado"] == "certificada" else f"en prueba («{auto}» aún no pasa su plan de prueba)"


def _informe(cliente_id, e):
    """A quién llega el diagnóstico/propuesta de una alerta y con qué acciones (app/alerta_diagnostico.py)."""
    try:
        from .alerta_diagnostico import acciones_con_informe, destino
        para, quien = destino(cliente_id, e)
        return {"acciones": sorted(acciones_con_informe()), "para": para, "quien": quien}
    except Exception:  # noqa: BLE001
        return {"acciones": [], "para": "", "quien": ""}


def _exportador_ctx(cliente_id, e, ambito, cat):
    """Exportador opcional de la plataforma (si alguna de sus alertas lo necesita): estado y automatización para instalarlo."""
    fam = ambito.split(":", 1)[1] if ambito and ambito != "maquina" else ""
    if not fam or not any(a.get("exportador") == fam for a in cat["alertas"]):
        return None
    a = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, AUTO_EXPORTADOR)
    return {"familia": fam, "lee": exportador_estado(e, fam), "aid": a["id"] if a else None, "bloqueo": bloqueo_auto(cliente_id, AUTO_EXPORTADOR)}


def bloqueo_auto(cliente_id, auto):
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, auto)
    if not a:
        return f"sin automatización aún («{auto}» no está en AAP)"
    from .pruebas import estado, exceptuada
    return "" if estado(dict(a, cliente_id=cliente_id))["estado"] == "certificada" or exceptuada(auto) else f"en prueba («{auto}» aún no pasa su plan de prueba)"


def _ult_prueba(cliente_id, e, ambito, aid):
    try:
        from .alerta_prueba import ultima
        return ultima(cliente_id, e["nombre"], ambito, aid)
    except Exception:  # noqa: BLE001
        return None


def contexto(cliente_id, e, puede_escribir, ambito="maquina", nombre_amb=""):
    """Todo lo que el botón y el flotante necesitan. ambito: maquina (campana de la ficha: la máquina y su SO) o
    plataforma:<familia> (campana de la tarjeta de esa plataforma)."""
    t = tipo_de(e)
    if not t:
        return None
    cat = catalogo()
    ap = aplicadas(e, ambito)
    act = {x["id"]: x for x in ap.get("alertas") or []}
    grupos = {}
    datos = j(e.get("datos"), {}) or {}
    g_ocp = grafana_ocp(cliente_id, e) if t == "openshift" else None
    prom = reglas_monitoreo(e, ambito, t)   # lo realmente cargado en el monitoreo (None: no se pudo consultar → reflejo del último ticket)
    trans = transito(cliente_id, e, ambito, (prom or {}).keys() if prom is not None else act.keys()) if t != "openshift" else {}
    for a in cat["alertas"]:
        if t in a["tipos"] and a.get("ambito", "maquina") == ambito and (not a.get("requiere") or datos.get(a["requiere"]) or a["id"] in act):
            x = act.get(a["id"])
            pend_ = a.get("pendiente") or (datos.get("sin_medicion") or {}).get(a["id"], "")   # catálogo, o este objeto no tiene cómo medirla
            if pend_ or t == "openshift":   # pendiente de métrica, o alerta del clúster (se cargan juntas en su monitoreo)
                accs_ = [(cat.get("acciones") or {}).get(k, {}).get("texto", k) for k in (a.get("acciones") or ["avisar"])]
                grupos.setdefault(a.get("grupo", "Otras"), []).append(dict(a, activa=False, acciones_l=[], pendiente=pend_ or "",
                                                                            en_prueba=False, en_revision=t == "openshift",
                                                                            acciones_txt=accs_, mon=(prom or {}).get(a["id"]), prueba=_ult_prueba(cliente_id, e, ambito, a["id"]) if prom and a["id"] in prom else None, grafana_ocp=(f"{g_ocp}?" + urlencode({"orgId": 1, "from": "now-24h", "to": "now", "viewPanel": panel_id(a["id"])})) if g_ocp else "", expr_txt=(a.get("expr") or "").replace('{faceops_equipo="__EQ__"}', "").replace('faceops_equipo="__EQ__",', "")))
                continue
            accs = [(k, (cat.get("acciones") or {}).get(k, {}).get("texto", k), bloqueo_accion(cliente_id, cat, k)) for k in (a.get("acciones") or ["avisar"])]
            grupos.setdefault(a.get("grupo", "Otras"), []).append(dict(a, activa=bool(x) if prom is None else a["id"] in prom, mon=(prom or {}).get(a["id"]),
                                                                         en_reflejo=bool(x), transito=trans.get(a["id"]), acciones_l=accs, prueba=_ult_prueba(cliente_id, e, ambito, a["id"]) if prom and a["id"] in prom else None, grafana=url_grafana(e["nombre"], a["id"], ambito),
                                                                         regla=nombre_regla(a), valor_accion=(x or {}).get("accion", "avisar"),
                                                                         valor_aviso=(x or {}).get("aviso", a.get("aviso")),
                                                                         valor_critico=(x or {}).get("critico", a.get("critico")),
                                                                         valor_sev=(x or {}).get("severidad", a.get("severidad", "aviso")),
                                                                         valor_para=_minutos((x or {}).get("para") or a.get("para"))))
    auto_n = AUTO_GRAFANA_OCP if t == "openshift" else AUTO   # el clúster carga sus alertas con «Monitoreo · Grafana de OpenShift»
    auto = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, auto_n)
    disponible, por_que = False, f"La automatización «{auto_n}» aún no está publicada en AAP"
    if auto:
        from .pruebas import estado
        est = estado(dict(auto, cliente_id=cliente_id))["estado"]
        from .pruebas import exceptuada
        disponible = est == "certificada" or bool(exceptuada(auto_n))   # excepción autorizada por el usuario (planes-prueba.yml)
        por_que = "" if disponible else "La automatización que configura las alertas aún no tiene su plan de prueba aprobado: se podrá activar cuando lo pase"
    if not puede_escribir:
        disponible, por_que = False, "Tu perfil es de solo lectura"
    if not e.get("ip"):
        disponible, por_que = False, e.get("sin_ip") or "El equipo no tiene IP registrada"
    return {"url_probar": e.get("url_probar") or "", "url_regla": e.get("url_regla") or "","tipo": t, "tipo_nombre": cat["tipos"][t]["nombre"], "fuente": cat["tipos"][t]["fuente"], "grupos": grupos,
            "ambito": ambito, "sufijo": sufijo(ambito), "nombre_amb": nombre_amb,
            "n": (len(prom) if prom is not None else
                  (sum(1 for k in act if any(a["id"] == k and a.get("ambito", "maquina") == ambito for a in cat["alertas"])) if ambito != "maquina" else len(act))),
            "eid": e["id"], "monitoreo": prom is not None, "exportador": _exportador_ctx(cliente_id, e, ambito, cat), "disparadas": sum(1 for v in (prom or {}).values() if v["estado"] == "firing"),
            "aplicado": ap.get("actualizado"), "ticket": ap.get("ticket"), "resumen": ap.get("resumen") or [],
            "grafana": url_grafana(e["nombre"], None, ambito), "archivo": f"{REGLAS}/{e['nombre']}{sufijo(ambito)}.yml", "grupo_prom": f"faceops-{e['nombre']}{sufijo(ambito)}",
            "informe": _informe(cliente_id, e), "grafana_ocp": (g_ocp + "?orgId=1&from=now-24h&to=now") if g_ocp else "",
            "puede": puede_escribir, "en_curso": en_curso(cliente_id, e, ambito), "disponible": disponible, "por_que": por_que, "total": sum(len(v) for v in grupos.values()),
            "activables": sum(1 for v in grupos.values() for x in v if not x.get("pendiente"))}


def _minutos(p):
    """«10m» → 10 · «1h» → 60 · «0m»/vacío → 0 (el flotante pide la duración en minutos)."""
    p = str(p or "0m").strip()
    try:
        return int(float(p[:-1]) * (60 if p.endswith("h") else (1 / 60 if p.endswith("s") else 1))) if p[-1:] in "hms" else int(p)
    except ValueError:
        return 0


def requiere_acceso(par):
    """¿Alguna alerta del pedido tiene una acción que entra a la máquina (acceso: equipo)? Entonces el ticket lleva el
    inventario y la credencial de AAP del equipo para que la regla de la alerta los entregue a la respuesta."""
    acc = catalogo().get("acciones") or {}
    return any((acc.get(x.get("accion")) or {}).get("acceso") == "equipo" for x in par.get("al_alertas") or [])


def pedido_de_formulario(e, form, ambito="maquina"):
    """Del flotante → parámetros del ticket (validados contra el catálogo), solo con las alertas de ese ámbito."""
    t = tipo_de(e)
    cat = catalogo()
    ambito = ambito or "maquina"
    if ambito != "maquina" and not ambito_de_plataforma(ambito.split(":", 1)[-1]):
        raise ValueError("Ámbito de alertas desconocido")
    out, fuera = [], []
    sin_med = (j(e.get("datos"), {}) or {}).get("sin_medicion") or {}
    for a in cat["alertas"]:
        if t not in a["tipos"] or a.get("ambito", "maquina") != ambito:
            continue
        if a.get("pendiente") or sin_med.get(a["id"]):
            if form.get(f"on_{a['id']}"):
                raise ValueError(f"«{a['nombre']}» aún no se puede medir: {a.get('pendiente') or sin_med[a['id']]}")
            continue
        if not form.get(f"on_{a['id']}"):
            fuera.append(a["id"])
            continue
        acc = form.get(f"acc_{a['id']}") or "avisar"
        if acc not in (a.get("acciones") or ["avisar"]):
            raise ValueError(f"«{a['nombre']}»: elige una acción de la lista")
        x = {"id": a["id"], "accion": acc}
        pm = (form.get(f"para_{a['id']}") or "").strip()
        if pm:
            if not pm.isdigit() or not 0 <= int(pm) <= 240:
                raise ValueError(f"«{a['nombre']}»: la duración es un número de minutos entre 0 y 240")
            if int(pm) != _minutos(a.get("para")):
                x["para"] = f"{int(pm)}m"
        if a.get("umbral", True) is False:
            sev = form.get(f"sev_{a['id']}", a.get("severidad", "aviso"))
            x["severidad"] = sev if sev in ("aviso", "critico") else "aviso"
        else:
            for k in ("aviso", "critico"):
                v = (form.get(f"{k}_{a['id']}") or "").strip().replace(",", ".")
                if v:
                    try:
                        x[k] = float(v) if "." in v else int(v)
                    except ValueError:
                        raise ValueError(f"«{a['nombre']}»: el umbral de {k} debe ser un número")
            if "aviso" not in x and "critico" not in x:
                raise ValueError(f"«{a['nombre']}»: indica al menos un umbral (aviso o crítico)")
            if "aviso" in x and "critico" in x and ((x["aviso"] <= x["critico"]) if a.get("menor") else (x["aviso"] >= x["critico"])):
                raise ValueError(f"«{a['nombre']}»: el aviso debe ser {'mayor' if a.get('menor') else 'menor'} que el crítico")
        out.append(x)
    # un solo ticket con el grupo completo: las activas con su configuración y las apagadas (quedan fuera de las reglas)
    par = {"al_equipo": e["nombre"], "al_ip": e.get("ip") or "", "al_tipo": t, "al_ambito": ambito, "al_alertas": out, "al_retiradas": fuera, "al_retirar": not out}
    if t == "segmento":   # lo que miden las sondas del segmento (VIP, nodos del balanceador, componentes)
        par["al_objetivos"] = (j(e.get("datos"), {}) or {}).get("objetivos") or []
    return par


def registrar_resultado(cliente_id, art, numero):
    """Hook al terminar el ticket: el reflejo de lo aplicado queda en la ficha del equipo."""
    x = (art or {}).get("faceops_alertas") or {}
    e = q1("SELECT id, datos FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente_id, x.get("equipo") or "")
    if not e and x.get("tipo") == "segmento":   # alertas de un segmento de negocio: el reflejo queda en el segmento
        from .segmento_ops import registrar_alertas
        registrar_alertas(cliente_id, x, numero)
        return
    if not e and x.get("tipo") in ("kvm", "vmware"):   # alertas de un hipervisor: el reflejo queda en su infraestructura
        m_ = re.match(r"hv(\d+)-", x.get("equipo") or "")
        i_ = q1("SELECT id, detalle FROM infraestructuras WHERE cliente_id=? AND id=?", cliente_id, int(m_.group(1))) if m_ else None
        if i_:
            d_ = j(i_["detalle"], {})
            d_["alertas"] = {"tipo": x.get("tipo"), "alertas": x.get("alertas") or [], "resumen": x.get("resumen") or [], "actualizado": ahora(), "ticket": numero}
            ej("UPDATE infraestructuras SET detalle=? WHERE id=?", json.dumps(d_, ensure_ascii=False), i_["id"])
        return
    if not e:
        return
    d = j(e["datos"], {})
    r_ = {"tipo": x.get("tipo"), "alertas": x.get("alertas") or [], "resumen": x.get("resumen") or [], "actualizado": ahora(), "ticket": numero}
    amb = x.get("ambito") or "maquina"
    if amb == "maquina":
        d["alertas"] = r_
    else:
        d.setdefault("alertas_plat", {})[amb] = r_
    ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), e["id"])


def _reglas_todas(url, hdr=None):
    """Todos los grupos de reglas de alerta de un Prometheus (None si no responde)."""
    import requests
    try:
        r = requests.get(url.rstrip("/") + "/api/v1/rules", params={"type": "alert"}, headers=hdr or {}, timeout=5, verify=False)
        r.raise_for_status()
        return r.json()["data"]["groups"]
    except Exception:  # noqa: BLE001
        return None


def _indexar(gs, grupo, url):
    out, orden = {}, {"firing": 2, "pending": 1, "inactive": 0}
    for g_ in gs or []:
        if g_["name"] != grupo:
            continue
        for x in g_.get("rules") or []:
            aid = (x.get("labels") or {}).get("faceops_alerta")
            if not aid:
                continue
            d = out.setdefault(aid, {"reglas": [], "estado": "inactive", "salud": "ok", "grupo": grupo, "archivo": g_.get("file", ""), "fuente": url})
            d["reglas"].append({k_: x.get(k_) for k_ in ("name", "query", "duration", "labels", "annotations", "health", "lastError", "state", "lastEvaluation")}
                               | {"activas": len(x.get("alerts") or [])})
            if orden.get(x.get("state"), 0) > orden.get(d["estado"], 0):
                d["estado"] = x.get("state")
            if x.get("health") == "err":
                d["salud"] = "err"
    return out


def _ultimos_disparos(url, hdr=None):
    """(equipo, alerta) → último instante en que estuvo disparada (7 días), leído de ALERTS del Prometheus."""
    import requests
    q_ = 'max by (faceops_equipo, faceops_alerta) (max_over_time(timestamp(ALERTS{alertstate="firing",faceops_alerta!=""})[7d:5m]))'
    try:
        r = requests.get(url.rstrip("/") + "/api/v1/query", params={"query": q_}, headers=hdr or {}, timeout=8, verify=False)
        return {(x["metric"].get("faceops_equipo"), x["metric"].get("faceops_alerta")): float(x["value"][1]) for x in r.json()["data"]["result"]}
    except Exception:  # noqa: BLE001
        return {}


def tabla_cliente(cliente_id, equipos):
    """Monitoreo › Alertas: una fila por alerta de cada objeto con alertas (máquina, cada plataforma y clúster), con su
    estado REAL en el monitoreo (encendida = regla cargada; disparando = firing ahora; último disparo) y lo que dice
    FaceOps (reflejo del último ticket), marcando si difieren."""
    from .alerta_diagnostico import destino
    cat = catalogo()
    acc_cat = cat.get("acciones") or {}
    gs_host = _reglas_todas(PROMETHEUS)
    ult_host = _ultimos_disparos(PROMETHEUS)
    filas = []
    for e in equipos:
        t = tipo_de(e)
        if not t:
            continue
        d = j(e.get("datos"), {}) or {}
        ult = ult_host
        if t == "openshift":
            prom_by = {"maquina": reglas_monitoreo(e, "maquina", "openshift")}
            from .boveda import leer
            cr = leer(f"maquinas/{e['nombre']}/grafana-monitoreo") or {}
            ult = _ultimos_disparos(cr["url"], {"Authorization": f"Bearer {cr['token']}"}) if cr.get("url") and cr.get("token") else {}
        else:
            ambs = {"maquina"} | set((d.get("alertas_plat") or {}).keys())
            ambs |= {"plataforma:" + g_["name"].split("--", 1)[1] for g_ in gs_host or [] if g_["name"].startswith(f"faceops-{e['nombre']}--")}
            prom_by = {amb: (_indexar(gs_host, grupo_monitoreo(e, amb), PROMETHEUS) if gs_host is not None else None) for amb in ambs}
        resp = None
        for amb, prom in prom_by.items():
            refl = {x["id"]: x for x in aplicadas(e, amb).get("alertas") or []} if t != "openshift" else {}
            trans = transito(cliente_id, e, amb, (prom or {}).keys() if prom is not None else refl.keys()) if t != "openshift" else {}
            if not (prom or refl or trans):
                continue
            resp = resp or destino(cliente_id, e)
            for a in cat["alertas"]:
                if t not in a["tipos"] or a.get("ambito", "maquina") != amb or a.get("pendiente"):
                    continue
                m = (prom or {}).get(a["id"])
                rf = refl.get(a["id"])
                if t == "openshift" and not m:
                    continue
                encendida = bool(m) if prom is not None else bool(rf)
                sevs = sorted({r_["labels"].get("severity", "") for r_ in (m or {}).get("reglas", [])}) or [
                    ("critical" if (rf or {}).get("severidad", a.get("severidad")) == "critico" else "warning") if a.get("umbral", True) is False
                    else ("warning" if (rf or a).get("aviso") is not None else "critical")]
                acc = ((m or {}).get("reglas") or [{}])[0].get("labels", {}).get("faceops_accion") or (rf or {}).get("accion") or ("por definir" if t == "openshift" else "avisar")
                tipo_acc = "avisar" if acc == "avisar" else ("por definir" if acc == "por definir" else
                                                            ("diagnóstico" if acc_cat.get(acc, {}).get("informe") or "diagn" in acc else "remediación"))
                ts = ult.get((e["nombre"], a["id"]))
                filas.append({"eid": e["id"], "objeto": e["nombre"], "tipo": t, "ambito": amb,
                              "objeto_txt": e["nombre"] + ("" if amb == "maquina" else f" · {amb.split(':', 1)[1]}"),
                              "clase": "clúster" if t == "openshift" else ("plataforma" if amb != "maquina" else
                                                                          {"linux": "máquina", "windows": "máquina", "storage": "storage", "red": "red"}.get(t, t)),
                              "aid": a["id"], "nombre": a["nombre"], "grupo": a.get("grupo", ""), "severidad": "/".join(
                                  {"warning": "aviso", "critical": "crítico"}.get(x_, x_) for x_ in sevs if x_),
                              "accion": acc_cat.get(acc, {}).get("texto", acc), "tipo_accion": tipo_acc,
                              "encendida": encendida, "disparando": bool(m and m["estado"] == "firing"), "pendiente": bool(m and m["estado"] == "pending"),
                              "salud": (m or {}).get("salud", ""), "ultimo": texto_utc(ts) if ts else "",
                              "responsable": resp[1] if resp else "", "correo": resp[0] if resp else "",
                              "difiere": (prom is not None and t != "openshift" and bool(m) != bool(rf)),
                              "difiere_txt": ("FaceOps la tiene encendida pero no está cargada en Prometheus" if rf and not m else
                                              "cargada en Prometheus sin reflejo en FaceOps" if m and not rf else ""),
                              "sin_monitoreo": prom is None, "transito": trans.get(a["id"])})
    filas.sort(key=lambda x: (not x["disparando"], not x["encendida"], x["objeto_txt"], x["aid"]))
    return filas


def resumen_cliente(cliente_id):
    """Para Monitoreo › Alertas: cada objeto con alertas configuradas en Ansible."""
    out = []
    for e in q("SELECT id, nombre, descripcion, categoria, so, ip, datos FROM equipos WHERE cliente_id=? AND estado!='retirado' ORDER BY nombre", cliente_id):
        t = tipo_de(e)
        if not t:
            continue
        ap = aplicadas(e)
        out.append({"id": e["id"], "nombre": e["nombre"], "tipo": t, "n": len(ap.get("alertas") or []), "resumen": ap.get("resumen") or [],
                    "actualizado": ap.get("actualizado"), "ticket": ap.get("ticket")})
    return out
