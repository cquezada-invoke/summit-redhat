"""Catálogo de automatizaciones (desde AAP + ficha común de FaceOps), ejecución y tickets."""
import json
import os
import re
import threading
import time
from datetime import datetime

import requests
import yaml

from .aap import AAP, ErrorAAP
from .bd import ahora, auditar, ej, j, q, q1
from .inventario import normal
from .permisos import equipos_de_segmento, puede_ejecutar, puede_ver_segmento

AQUI = os.path.dirname(os.path.abspath(__file__))
FICHAS = os.path.join(AQUI, "..", "datos", "catalogo.yml")
PALABRAS_LECTURA = ("verificar", "estado", "reporte", "recolectar", "consultar", "listar", "auditar", "revisar", "prueba de", "probar")


GUIAS = os.path.join(AQUI, "..", "datos", "guias.yml")
GUIAS_DEMOS = os.path.join(AQUI, "..", "datos", "guias-demos.yml")
DEMOS = os.path.join(AQUI, "..", "datos", "demos.yml")
SNOW = os.path.join(AQUI, "..", "datos", "servicenow.yml")
FORMULARIOS = os.path.join(AQUI, "..", "datos", "formularios.yml")
SKILLS = os.path.join(AQUI, "..", "datos", "skills.yml")
COMPUESTAS = os.path.join(AQUI, "..", "datos", "compuestas.yml")
TIPOS_AMIGABLES = ("equipo", "equipos", "fecha", "ip")


def combinar_campos(encuesta, formulario):
    """La encuesta de AAP manda (es lo que AAP valida); el formulario de FaceOps la hace amigable.
    Sin encuesta, el formulario define los campos."""
    if not encuesta:
        return formulario
    por_var = {c["var"]: c for c in formulario}
    out = []
    for c in encuesta:
        f = por_var.get(c["var"], {})
        n = dict(c)
        for k in ("etiqueta", "ayuda"):
            if f.get(k):
                n[k] = f[k]
        if f.get("tipo") in TIPOS_AMIGABLES and c["tipo"] in ("text", "multiplechoice", "multiselect"):
            n["tipo"] = f["tipo"]
        out.append(n)
    return out


def _yaml(ruta, defecto):
    try:
        return yaml.safe_load(open(ruta)) or defecto
    except FileNotFoundError:
        return defecto


def orden_demos():
    """nombre técnico → (demo, orden) según datos/demos.yml; lo demás queda en «Otras automatizaciones»."""
    out = {}
    for i, d in enumerate(_yaml(DEMOS, [])):
        for k, n in enumerate(d.get("automatizaciones", [])):
            out[n] = (d["demo"], i * 100 + k)
    return out


def resumen_demos():
    return {d["demo"]: d.get("resumen", "") for d in _yaml(DEMOS, [])}


def conf_servicenow(nombre_aap):
    return _yaml(SNOW, {}).get(nombre_aap, {})


def fichas_base():
    try:
        return yaml.safe_load(open(FICHAS)) or {}
    except FileNotFoundError:
        return {}


# ------------------------------------------------------------------ catálogo
def sincronizar_catalogo(cliente, usuario="sistema"):
    aap = AAP.de_cliente(cliente)
    base = fichas_base()
    defaults_org = base.get("_por_organizacion", {})
    fichas = base.get("fichas", {})
    proyectos, n = {}, 0
    guias, demos, formularios = {**_yaml(GUIAS, {}), **_yaml(GUIAS_DEMOS, {})}, orden_demos(), _yaml(FORMULARIOS, {})
    skills_yml = _yaml(SKILLS, {})
    for nombre_aap, sk in skills_yml.items():  # cada skill del cliente sabe qué automatización la habilita
        ej("INSERT INTO skills(cliente_id, nombre, descripcion, regla, automatizacion) VALUES (?,?,?,?,?) "
           "ON CONFLICT(cliente_id, nombre) DO UPDATE SET automatizacion=excluded.automatizacion, "
           "descripcion=CASE WHEN skills.descripcion='' THEN excluded.descripcion ELSE skills.descripcion END",
           cliente["id"], sk["skill"], sk.get("descripcion", ""), json.dumps(sk.get("deteccion", {}), ensure_ascii=False), nombre_aap)
    items = [("job", t) for t in aap.plantillas()] + [("workflow", w) for w in aap.workflows()]
    for tipo, t in items:
        sf = t.get("summary_fields", {})
        org = (sf.get("organization") or {}).get("name", "General")
        if org in base.get("_ignorar_organizaciones", []) or t["name"] in base.get("_ignorar", []) \
                or t["name"].startswith("FaceOps · Aprobar ›"):
            continue
        version = ""
        pid = (sf.get("project") or {}).get("id")
        if pid:
            if pid not in proyectos:
                try:
                    pr = aap.proyecto(pid)
                    proyectos[pid] = (pr["name"], (pr.get("scm_revision") or "")[:8], pr.get("scm_url", ""), pr.get("scm_branch", ""))
                except ErrorAAP:
                    proyectos[pid] = ("", "", "", "")
            version = f"{proyectos[pid][0]} @ {proyectos[pid][1]}" if proyectos[pid][1] else proyectos[pid][0]
        else:
            version = "workflow · " + (t.get("modified") or "")[:10]
        campos = []
        for c in aap.encuesta(tipo, t["id"]) if t.get("survey_enabled") else []:
            campos.append({"var": c["variable"], "etiqueta": c.get("question_name", c["variable"]), "tipo": c.get("type", "text"),
                           "requerido": bool(c.get("required")), "defecto": c.get("default", ""),
                           "opciones": c.get("choices") if isinstance(c.get("choices"), list) else
                           [x for x in str(c.get("choices") or "").split("\n") if x], "ayuda": c.get("question_description", "")})
        f = dict(defaults_org.get(org, {}))
        f.update(fichas.get(t["name"], {}))
        nombre_simple = f.get("nombre") or re.sub(r"^[^·]+·\s*", "", t["name"])
        tipo_rw = f.get("tipo") or ("lectura" if any(p in normal(t["name"]) for p in PALABRAS_LECTURA) else "escritura")
        inv = (sf.get("inventory") or {}).get("name", "")
        if tipo == "job":
            pr = proyectos.get(pid, ("", "", "", ""))
            tecnico = {"playbook": t.get("playbook", ""), "proyecto": pr[0], "repositorio": pr[2], "rama": pr[3] or "por defecto",
                       "commit": pr[1], "inventario": inv, "organizacion": org,
                       "credenciales": [c["name"] for c in sf.get("credentials", [])], "descripcion_aap": t.get("description", "")}
            aprob_aap = ""
        else:
            try:
                nodos = aap.nodos_workflow(t["id"])
            except ErrorAAP:
                nodos = []
            tecnico = {"nodos": nodos, "organizacion": org, "inventario": inv, "descripcion_aap": t.get("description", "")}
            aprob_aap = " · ".join(x["nombre"] for x in nodos if x["tipo"] == "aprobacion")
        demo, orden = demos.get(t["name"], ("", 999))
        existe = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND aap_tipo=? AND aap_id=?", cliente["id"], tipo, t["id"])
        form = formularios.get(t["name"], {})
        campos_ficha = combinar_campos(campos, form.get("campos") or f.get("campos") or [])
        datos = {"nombre_aap": t["name"], "version": version, "proyecto": proyectos.get(pid, ("", ""))[0] if pid else "",
                 "inventario": inv, "campos": json.dumps(campos_ficha or [], ensure_ascii=False), "actualizado": ahora(),
                 "tecnico": json.dumps(tecnico, ensure_ascii=False), "demo": demo, "orden": orden, "aprobacion_aap": aprob_aap}
        datos["skills"] = json.dumps(form.get("skills", []))
        datos["por_inventario"] = int(bool(form.get("por_inventario")))
        sk = skills_yml.get(t["name"])
        datos["habilita_skill"] = sk["skill"] if sk else ""
        datos["compatibles"] = json.dumps(sk.get("sistemas", []) if sk else [])
        if sk:
            datos["categoria"] = "Skills"
        datos["aprobacion_ambientes"] = json.dumps(form.get("aprobacion_ambientes", []))  # p. ej. [produccion, qa]  # solo equipos con esas skills (p. ej. jboss)
        if "usa_limite" in form:
            datos["usa_limite"] = int(bool(form["usa_limite"]))
        if aprob_aap:
            datos["requiere_aprobacion"] = 1  # la aprobación ya vive en el workflow de AAP
        # La guía viene de datos/guias*.yml mientras nadie la edite en FaceOps-admin (al editarla pierde la marca _origen)
        g_yml = dict(guias.get(t["name"], {}))
        g_bd = j(existe.get("guia"), {}) if existe else {}
        if g_yml and (not g_bd or "_origen" in g_bd):
            g_yml["_origen"] = "datos"
            datos["guia"] = json.dumps(g_yml, ensure_ascii=False)
            if g_yml.get("nombre"):
                datos["nombre"] = g_yml["nombre"]
        if existe:
            # La ficha editada en FaceOps manda; desde AAP solo se refrescan nombre técnico, versión, inventario y formulario
            ej("UPDATE automatizaciones SET " + ", ".join(f"{k}=?" for k in datos) + " WHERE id=?", *datos.values(), existe["id"])
        else:
            datos.update({"nombre": datos.get("nombre") or nombre_simple, "descripcion": f.get("descripcion") or t.get("description") or "",
                          "categoria": f.get("categoria") or org, "tipo": tipo_rw, "ambito": f.get("ambito", inv),
                          "usa_limite": int(datos.get("usa_limite", f.get("usa_limite", tipo == "job" and bool(inv)))),
                          "requiere_aprobacion": int(datos.get("requiere_aprobacion", f.get("requiere_aprobacion", 0))),
                          "minutos_manual": int(f.get("minutos_manual", 30)), "tarifa": float(f.get("tarifa", 0)),
                          "etiquetas": json.dumps(f.get("etiquetas", [])), "info": f.get("info", ""),
                          "visible": int(f.get("visible", 1))})
            ej("INSERT INTO automatizaciones(cliente_id, aap_tipo, aap_id, " + ", ".join(datos) + ") VALUES (?,?,?," +
               ",".join("?" * len(datos)) + ")", cliente["id"], tipo, t["id"], *datos.values())
        n += 1
    n += sincronizar_compuestas(cliente, demos)
    from .economia import sincronizar_minutos
    sincronizar_minutos()   # esfuerzo manual = suma de sus tareas
    auditar(usuario, cliente["id"], "sincronizar catálogo", "catálogo", {"automatizaciones": n})
    return n


def equipos_aplicables(usuario, cliente_id, auto, segmento):
    """Equipos del segmento de negocio que están en el inventario de la automatización y en los ambientes del perfil."""
    from .inventario import sistema_eq, skills_de
    from .permisos import ambiente_permitido, perfil
    p = perfil(usuario, cliente_id)
    req = set(j(auto.get("skills"), []))
    compat = set(j(auto.get("compatibles"), []))
    habilita = auto.get("habilita_skill")
    return sorted([{"nombre": e["nombre"], "ambiente": e.get("ambiente") or "sin ambiente", "infra": e.get("ubicacion") or "sin ubicación"}
                   for e in equipos_de_segmento(cliente_id, segmento)
                   if e["estado"] != "retirado" and ambiente_permitido(p, e.get("ambiente"))
                   and e.get("energia") != "apagado"                            # apagados: AAP no llegaría (fallaría el ticket)
                   and (not req or req & set(skills_de(e)))
                   and (auto.get("por_inventario") or not auto["inventario"] or auto["inventario"] in j(e["inventarios"], []))
                   and (not auto.get("por_inventario") or e.get("estado") == "con datos")
                   and (not compat or sistema_eq(e) in compat)                 # skill: solo equipos compatibles…
                   and (not habilita or habilita not in skills_de(e))], key=lambda x: x["nombre"])  # …que aún no la tienen


def sincronizar_compuestas(cliente, demos):
    """Fichas de FaceOps que reúnen varias plantillas de AAP (datos/compuestas.yml). Las plantillas reunidas se ocultan."""
    n = 0
    for nombre_aap, d in _yaml(COMPUESTAS, {}).items():
        datos = {"nombre_aap": nombre_aap, "campos": json.dumps(d.get("campos", []), ensure_ascii=False),
                 "version": "FaceOps · " + ", ".join(x["automatizacion"] for x in d["destinos"].values()),
                 "tecnico": json.dumps({"compuesta": d["destinos"], "accion": d["accion"]}, ensure_ascii=False),
                 "demo": demos.get(nombre_aap, ("", 999))[0], "orden": demos.get(nombre_aap, ("", 999))[1], "actualizado": ahora()}
        existe = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], nombre_aap)
        if not existe or "_origen" in j(existe.get("guia"), {"_origen": 1}):
            datos["guia"] = json.dumps(dict(d.get("guia", {}), _origen="datos"), ensure_ascii=False)
        if existe:
            ej("UPDATE automatizaciones SET " + ", ".join(f"{k}=?" for k in datos) + " WHERE id=?", *datos.values(), existe["id"])
        else:
            datos.update({"nombre": d["nombre"], "descripcion": d.get("descripcion", ""), "categoria": d.get("categoria", "General"),
                          "tipo": d.get("tipo", "escritura"), "usa_limite": 0, "requiere_aprobacion": 1, "visible": 1,
                          "minutos_manual": d.get("minutos_manual", 30), "tarifa": d.get("tarifa", 0),
                          "etiquetas": json.dumps(d.get("etiquetas", [])), "aprobacion_aap": "en el workflow de cada acción"})
            ej("INSERT INTO automatizaciones(cliente_id, aap_tipo, aap_id, " + ", ".join(datos) + ") VALUES (?,?,?," +
               ",".join("?" * len(datos)) + ")", cliente["id"], "faceops", 0, *datos.values())
        for o in d.get("oculta", []):
            ej("UPDATE automatizaciones SET visible=0 WHERE cliente_id=? AND nombre_aap=?", cliente["id"], o)
        n += 1
    return n


def resolver_compuesta(auto, params):
    """(automatización de AAP que corresponde a la acción elegida, variables con los nombres que espera esa plantilla)."""
    d = _yaml(COMPUESTAS, {}).get(auto["nombre_aap"])
    if not d:
        return auto, params
    accion = params.get(d["accion"]) or next(iter(d["destinos"]))
    dest = d["destinos"].get(accion)
    if not dest:
        raise ErrorAAP(f"Acción no válida: {accion}")
    destino = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", auto["cliente_id"], dest["automatizacion"])
    if not destino:
        raise ErrorAAP(f"No está en el catálogo «{dest['automatizacion']}» (sincroniza el catálogo)")
    nuevos = {k: v for k, v in params.items() if k != d["accion"] and k not in dest["variables"] and
              not any(k in x["variables"] for x in d["destinos"].values())}
    for de, a in dest["variables"].items():
        if params.get(de) not in (None, ""):
            nuevos[a] = params[de]
    return destino, nuevos


def limite_para(usuario, cliente_id, auto, segmento, ambientes=None, infras=None):
    """Patrón --limit de AAP: equipos del segmento, opcionalmente filtrados por ambiente e infraestructura."""
    if not auto["usa_limite"] or not segmento:
        return ""
    eqs = [e for e in equipos_aplicables(usuario, cliente_id, auto, segmento)
           if (not ambientes or e["ambiente"] in ambientes) and (not infras or e["infra"] in infras)]
    return ":".join(sorted({e["nombre"] for e in eqs}))


def _segmentos_con_todo(usuario, cliente_id):
    from .permisos import perfil, segmentos_visibles
    segs = [{"nombre": s["nombre"], "etiqueta": s["nombre"]} for s in segmentos_visibles(usuario, cliente_id)]
    if "*" in (perfil(usuario, cliente_id) or {}).get("segmentos", []):
        segs.append({"nombre": "*", "etiqueta": "Todo el parque"})
    return segs


def equipos_elegibles(usuario, cliente_id, auto):
    """Equipos que el usuario puede elegir en un campo «equipo/equipos»."""
    nombres = {}
    for s in _segmentos_con_todo(usuario, cliente_id):
        for e in equipos_aplicables(usuario, cliente_id, auto, s["nombre"]):
            nombres.setdefault(e["nombre"], f"{e['ambiente']} · {e['infra']}")
    return [{"nombre": n, "detalle": d} for n, d in sorted(nombres.items())]


def segmentos_aplicables(usuario, cliente_id, auto):
    """Segmentos de negocio donde la automatización tiene equipos (con la skill que exige, si exige una)."""
    from .permisos import segmentos_visibles
    out = []
    for s in [{"nombre": x["nombre"], "etiqueta": x["nombre"]} for x in segmentos_visibles(usuario, cliente_id)]:
        eqs = equipos_aplicables(usuario, cliente_id, auto, s["nombre"])
        if eqs:
            out.append(dict(s, equipos=eqs))
    return out


# ------------------------------------------------------------------ tickets
def numero_ticket(cliente):
    n = (q1("SELECT COUNT(*) n FROM tickets WHERE cliente_id=?", cliente["id"])["n"] or 0) + 1
    return f"FO-{cliente['codigo'].upper()}-{n:05d}"


def aplica_a_equipo(usuario, cliente_id, auto, e):
    """¿La automatización puede correr sobre este equipo puntual? (mismas reglas que el formulario por segmento)."""
    from .inventario import sistema_eq, skills_de
    from .permisos import ambiente_permitido, perfil
    if not auto["usa_limite"] or not auto["visible"] or e["estado"] == "retirado":
        return False
    if not ambiente_permitido(perfil(usuario, cliente_id), e.get("ambiente")):
        return False
    if not (auto.get("por_inventario") or not auto["inventario"] or auto["inventario"] in j(e["inventarios"], [])):
        return False
    if auto.get("por_inventario") and e["estado"] != "con datos":
        return False
    from .compatibilidad import compatible
    sk = set(skills_de(e))
    if not compatible(auto, dict(e, skills=list(sk)))[0]:   # sistema, tipo de periférico, componente de red, producto
        return False
    return not (auto.get("habilita_skill") and auto["habilita_skill"] in sk)


def solicitar_en_equipo(usuario, cliente, auto, e, parametros, motivo, secretos=None):
    """Ejecuta la automatización directamente sobre un equipo (desde su detalle en el inventario)."""
    if not aplica_a_equipo(usuario, cliente["id"], auto, e):
        raise PermissionError("Esta automatización no aplica a este equipo")
    return solicitar(usuario, cliente, auto, None, parametros, motivo, secretos, {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {},
                     equipo=e)


def solicitar(usuario, cliente, auto, segmento, parametros, motivo, secretos=None, filtros=None, equipo=None):
    """FaceOps orquesta: crea el ticket (propio y, si corresponde, en la herramienta del cliente) y lanza en AAP.
    Si requiere aprobación, la aprobación vive en AAP (nodo de aprobación del workflow): FaceOps la muestra y
    permite responderla, y quien entre a AAP la ve igual en su bandeja de aprobaciones."""
    ok, por_que = puede_ejecutar(usuario, cliente["id"], auto)
    if not ok:
        raise PermissionError(por_que)
    limite = ""
    if equipo:  # sobre un equipo puntual: el límite es el equipo; el segmento (para visibilidad) es el primero suyo
        from .inventario import segmentos_de
        segs = segmentos_de(equipo, q("SELECT * FROM segmentos WHERE cliente_id=?", cliente["id"]))
        segmento, limite = (segs[0] if segs else ""), equipo["nombre"]
    elif auto["usa_limite"]:
        if not segmento or not puede_ver_segmento(usuario, cliente["id"], segmento):
            raise PermissionError("Elige un segmento al que tengas acceso")
        filtros = {k: v for k, v in (filtros or {}).items() if v}
        if len(filtros.get("ambientes", [])) != 1:
            raise PermissionError("Elige un ambiente: cada ejecución va a un único ambiente")
        limite = limite_para(usuario, cliente["id"], auto, segmento, filtros.get("ambientes"), filtros.get("infraestructura"))
        if not limite:
            raise PermissionError("No quedan equipos con ese segmento de negocio, ambiente y centro de datos")
    from .creditos import validar as validar_creditos
    from .economia import estimar
    validar_creditos(cliente, estimar(auto, parametros, limite)["total"])   # sin créditos no se lanza
    tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, "
             "motivo, creado, valor_min, tarifa, aap_job_tipo, filtros) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             cliente["id"], numero_ticket(cliente), auto["id"], usuario["id"], "en_cola", segmento or "", limite,
             json.dumps(parametros, ensure_ascii=False), motivo, ahora(), auto["minutos_manual"], auto["tarifa"], auto["aap_tipo"],
             json.dumps(filtros or {}, ensure_ascii=False))
    t = q1("SELECT * FROM tickets WHERE id=?", tid)
    auditar(usuario["usuario"], cliente["id"], "solicitar", t["numero"],
            {"automatizacion": auto["nombre"], "version": auto["version"], "segmento": segmento, "filtros": filtros,
             "limite": limite, "parametros": parametros})
    if auto["aap_tipo"] == "faceops":
        destino, mapeados = resolver_compuesta(auto, parametros)
        extra = conector(cliente).crear(dict(t, parametros=json.dumps(mapeados, ensure_ascii=False)), destino, usuario) or {}
    else:
        extra = conector(cliente).crear(t, auto, usuario) or {}
    if extra:
        ej("UPDATE tickets SET parametros=? WHERE id=?", json.dumps(dict(parametros, **extra), ensure_ascii=False), tid)
    ejecutar(tid, secretos)  # las claves van solo a AAP: en el ticket y la auditoría quedan enmascaradas
    return q1("SELECT * FROM tickets WHERE id=?", tid)


def relanzar(usuario, cliente, t0, motivo=None):
    """Vuelve a solicitar una automatización que quedó sin ejecutar (deuda de aprobación): mismos parámetros y equipos."""
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t0["automatizacion_id"])
    ok, por_que = puede_ejecutar(usuario, cliente["id"], a)
    if not ok:
        raise PermissionError(por_que)
    from .creditos import validar as validar_creditos
    from .economia import estimar
    parametros = j(t0["parametros"], {})
    validar_creditos(cliente, estimar(a, parametros, t0["limite"])["total"])
    tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, "
             "motivo, creado, valor_min, tarifa, aap_job_tipo, filtros) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             cliente["id"], numero_ticket(cliente), a["id"], usuario["id"], "en_cola", t0["segmento"], t0["limite"], t0["parametros"],
             motivo or t0["motivo"], ahora(), a["minutos_manual"], a["tarifa"], a["aap_tipo"], t0.get("filtros") or "{}")
    t = q1("SELECT * FROM tickets WHERE id=?", tid)
    auditar(usuario["usuario"], cliente["id"], "volver a solicitar", t["numero"], {"origen": t0["numero"], "automatizacion": a["nombre"]})
    conector(cliente).crear(t, a, usuario)
    ejecutar(tid)
    return q1("SELECT * FROM tickets WHERE id=?", tid)


APAGAR = "FaceOps · Apagar equipo"


def acceso_equipo(aap, cliente, equipo):
    """(inventario_id, nombre_inventario, [credencial_id]) para llegar a un equipo: el plan de recolección del cliente."""
    plan = j(cliente["recoleccion"], {})
    grupos = set(j(equipo["grupos"], []))
    invs = {i["name"]: i["id"] for i in aap.inventarios()}
    for inv in [equipo["inventario"]] + [x for x in j(equipo["inventarios"], []) if x != equipo["inventario"]]:
        for p in plan.get(inv, []):
            if not p.get("limite") or p["limite"] in grupos:
                cred = aap.credencial(p["credencial"]) if p.get("credencial") else None
                return invs.get(inv), inv, ([cred] if cred else [])
    raise ErrorAAP(f"No sé con qué credencial llegar a {equipo['nombre']} (agrégalo al plan de recolección del cliente)")


def solicitar_apagado(usuario, cliente, equipo, motivo):
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], APAGAR)
    if not a:
        raise ErrorAAP("Falta la plantilla «FaceOps · Apagar equipo» en el catálogo (sincroniza el catálogo)")
    ok, por_que = puede_ejecutar(usuario, cliente["id"], dict(a, tipo="escritura"))
    if not ok:
        raise PermissionError(por_que)
    tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, "
             "motivo, creado, valor_min, tarifa, aap_job_tipo, filtros) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             cliente["id"], numero_ticket(cliente), a["id"], usuario["id"], "en_cola", "", equipo["nombre"],
             json.dumps({"equipo": equipo["nombre"]}), motivo or "Equipo sin skills: encendido sin uso", ahora(),
             a["minutos_manual"], a["tarifa"], "workflow", json.dumps({"ambientes": [equipo["ambiente"]]} if equipo["ambiente"] else {}))
    t = q1("SELECT * FROM tickets WHERE id=?", tid)
    auditar(usuario["usuario"], cliente["id"], "solicitar", t["numero"], {"automatizacion": a["nombre"], "equipo": equipo["nombre"]})
    conector(cliente).crear(t, a, usuario)
    ejecutar(tid)
    return q1("SELECT * FROM tickets WHERE id=?", tid)


def requiere_aprobacion(auto, ambiente=None):
    return bool(auto["requiere_aprobacion"]) or (ambiente or "") in j(auto.get("aprobacion_ambientes"), [])


def objetivo_aap(aap, auto, ambiente=None):
    """Qué se lanza en AAP: la plantilla tal cual, o (si FaceOps exige aprobación —siempre o en ese ambiente— y AAP
    no la tiene) su workflow con aprobación previa."""
    if requiere_aprobacion(auto, ambiente) and not auto.get("aprobacion_aap"):
        wf = auto.get("aprobacion_wf")
        if not wf:
            wf = aap.asegurar_flujo_aprobacion(auto)
            ej("UPDATE automatizaciones SET aprobacion_wf=? WHERE id=?", wf, auto["id"])
        return "workflow", wf
    return auto["aap_tipo"], auto["aap_id"]


OPENSHIFT = "OpenShift · Habilitar clúster"
INCORPORAR = "FaceOps · Incorporar máquinas"


def ejecutar(ticket_id, secretos=None):
    from .confianza import CONTEXTO
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    tok = CONTEXTO.set({"ticket": t, "automatizacion": a["nombre_aap"],
                        "usuario": (q1("SELECT usuario FROM usuarios WHERE id=?", t["usuario_id"]) or {}).get("usuario", "")})
    try:
        _ejecutar(ticket_id, secretos)
    finally:
        CONTEXTO.reset(tok)


def _ejecutar(ticket_id, secretos=None):
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    try:
        aap = AAP.de_cliente(c)
        params = j(t["parametros"], {})
        if a["aap_tipo"] == "faceops":  # ficha compuesta: según la acción, la plantilla de AAP que corresponde
            a, params = resolver_compuesta(a, params)
        for k, v in conf_servicenow(a["nombre_aap"]).get("extra", {}).items():  # variables fijas (p. ej. solicitud_tipo)
            if "{" not in str(v):
                params.setdefault(k, v)
        params.update({"faceops_ticket": t["numero"], "faceops_solicitante": (q1("SELECT usuario FROM usuarios WHERE id=?", t["usuario_id"]) or {}).get("usuario", "")})
        params.setdefault("solicitado_por", params["faceops_solicitante"])
        params.update(secretos or {})
        amb = (j(t.get("filtros"), {}).get("ambientes") or [None])[0]
        if a["nombre_aap"] == INCORPORAR:  # llave nueva: la privada solo a AAP; la pública viaja a las máquinas
            from .infraestructuras import _credencial_maquinas, plan_acceso
            inf = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre=?", c["id"], params.get("inc_infraestructura", ""))
            if not inf or inf["tipo"] not in ("gcp", "azure", "aws"):
                raise ErrorAAP("Incorporar máquinas funciona en infraestructuras GCP, Azure o AWS (en vSphere y on-premise se usa su credencial de acceso)")
            det = j(inf["detalle"], {})
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            k = Ed25519PrivateKey.generate()
            priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()
            pub = k.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode() + f" faceops@{inf['nombre'][:30].replace(' ', '-')}"
            org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
            usuario_srv = params.get("inc_usuario") or "faceops"
            acc = _credencial_maquinas(aap, org, inf["nombre"], usuario_srv, llave=priv)
            plan_acceso(c["id"], inf["inventario"], acc["name"])
            zona = det.get("zona") or ""
            params.update({"inc_plataforma": inf["tipo"], "inc_usuario": usuario_srv, "inc_llave_publica": pub, "inc_proyecto": det.get("proyecto", ""),
                           "inc_zona": zona, "inc_region": det.get("region") or (zona.rsplit("-", 1)[0] if zona.count("-") >= 2 else "")})
            if not params.get("inc_etiqueta") and det.get("etiqueta"):
                params["inc_etiqueta"] = det["etiqueta"]
            cr = (aap.get("credentials/", name=inf["credencial"]).get("results") or [None])[0]
            if not cr:
                raise ErrorAAP(f"{inf['nombre']} no tiene su credencial de plataforma en AAP: usa «Conectar» en Infraestructuras")
            tipo, id_ = "workflow", aap.asegurar_flujo_aprobacion(a, horas=72, credenciales=[cr["id"]], sufijo=inf["nombre"])
            job = aap.lanzar(tipo, id_, extra_vars=params)
            ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, aap_job_tipo=?, inicio=? WHERE id=?", job, tipo, ahora(), ticket_id)
            return
        if a["nombre_aap"] == OPENSHIFT:  # la infraestructura elegida define la plataforma y la credencial de la nube
            inf = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre=?", c["id"], params.get("ocp_infraestructura", ""))
            if not inf:
                raise ErrorAAP("Elige la infraestructura donde se instala OpenShift")
            det = j(inf["detalle"], {})
            params["ocp_plataforma"] = {"vmware": "vsphere"}.get(inf["tipo"], inf["tipo"])
            if inf["tipo"] in ("gcp", "aws", "azure"):
                zona = det.get("zona") or ""
                params.setdefault("ocp_nube", {k: v for k, v in {
                    "region": det.get("region") or (zona.rsplit("-", 1)[0] if zona.count("-") >= 2 else zona),
                    "proyecto": det.get("proyecto"), "zonas": [zona] if zona else None}.items() if v})
            creds = [x["id"] for x in aap.todos(f"job_templates/{a['aap_id']}/credentials/")]
            if inf["tipo"] != "kvm" and inf["credencial"]:
                cr = (aap.get("credentials/", name=inf["credencial"]).get("results") or [None])[0]
                if not cr:
                    raise ErrorAAP(f"No existe en AAP la credencial «{inf['credencial']}» de {inf['nombre']}")
                creds.append(cr["id"])
            tipo, id_ = "workflow", aap.asegurar_flujo_aprobacion(a, horas=72, credenciales=creds, sufijo=inf["nombre"])
            job = aap.lanzar(tipo, id_, extra_vars=params)
            ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, aap_job_tipo=?, inicio=? WHERE id=?", job, tipo, ahora(), ticket_id)
            return
        if a.get("por_inventario") and a["nombre_aap"] != APAGAR:
            grupos = {}
            for n in (t["limite"] or "").split(":"):
                e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", c["id"], n)
                if e:
                    acc = acceso_equipo(aap, c, e)
                    grupos.setdefault((acc[0], acc[1], tuple(acc[2])), []).append(n)
            if len(grupos) != 1:
                raise ErrorAAP("Los equipos elegidos están en inventarios de AAP con credenciales distintas: "
                               + " · ".join(f"{k[1]}: {', '.join(v)}" for k, v in grupos.items()) + ". Elige equipos de uno solo.")
            (inv_id, inv_n, creds), = grupos.keys()
            if requiere_aprobacion(a, amb):
                tipo, id_ = "workflow", aap.asegurar_flujo_aprobacion(a, inventario=inv_id, credenciales=list(creds), sufijo=inv_n)
                job = aap.lanzar(tipo, id_, extra_vars=params, limite=t["limite"] or None)
            else:
                tipo, id_ = "job", a["aap_id"]
                job = aap.lanzar(tipo, id_, extra_vars=params, limite=t["limite"] or None, inventario=inv_id, credenciales=list(creds))
            ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, aap_job_tipo=?, inicio=? WHERE id=?", job, tipo, ahora(), ticket_id)
            return
        if a["nombre_aap"] == APAGAR:  # siempre con aprobación; inventario y credencial del equipo, fijos en el nodo
            e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", c["id"], t["limite"])
            inv_id, inv_n, creds = acceso_equipo(aap, c, e)
            tipo, id_ = "workflow", aap.asegurar_flujo_aprobacion(a, inventario=inv_id, credenciales=creds, sufijo=inv_n)
        else:
            tipo, id_ = objetivo_aap(aap, a, amb)
        job = aap.lanzar(tipo, id_, extra_vars=params, limite=t["limite"] or None)
        ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, aap_job_tipo=?, inicio=? WHERE id=?", job, tipo, ahora(), ticket_id)
    except ErrorAAP as e:
        ej("UPDATE tickets SET estado='fallido', resultado=?, fin=? WHERE id=?", str(e)[:1000], ahora(), ticket_id)
        conector(c).cerrar(q1("SELECT * FROM tickets WHERE id=?", ticket_id), a)


def resolver_aprobacion(usuario, ticket_id, aprobar, comentario=""):
    """Responde en AAP la aprobación pendiente (queda registrada en AAP) y deja la traza en FaceOps."""
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    if t["estado"] != "pendiente_aprobacion" or not t["aap_aprobacion_id"]:
        return
    c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
    AAP.de_cliente(c).responder_aprobacion(t["aap_aprobacion_id"], aprobar)
    ej("UPDATE tickets SET aprobado_por=?, respuesta=?, aprobado_en=?, estado=? WHERE id=?", usuario["id"], comentario,
       f"FaceOps ({usuario['usuario']}) → AAP", "en_ejecucion" if aprobar else "en_ejecucion", ticket_id)
    auditar(usuario["usuario"], t["cliente_id"], "aprobar en AAP" if aprobar else "rechazar en AAP", t["numero"],
            {"comentario": comentario, "aprobacion_aap": t["aap_aprobacion_id"]})


ESTADOS_AAP = {"successful": "exitoso", "failed": "fallido", "error": "fallido", "canceled": "cancelado"}


def _revisar_aprobaciones(aap, t):
    """Lee en AAP los nodos de aprobación del workflow del ticket. Devuelve 'rechazado' | 'vencido' | None."""
    final = None
    for job in aap.nodos_trabajo_workflow(t["aap_job_id"]):
        if job.get("type") != "workflow_approval" or not job.get("id"):
            continue
        if job.get("status") == "pending":
            if t["estado"] != "pendiente_aprobacion" or t["aap_aprobacion_id"] != job["id"]:
                ej("UPDATE tickets SET estado='pendiente_aprobacion', aap_aprobacion_id=? WHERE id=?", job["id"], t["id"])
                auditar("AAP", t["cliente_id"], "espera aprobación en AAP", t["numero"], {"aprobacion": job.get("name"), "id": job["id"]})
                from .deuda import abrir as abrir_deuda
                abrir_deuda(t, "operación")   # si no se aprueba, queda como deuda de aprobación
            return None
        if t["aap_aprobacion_id"] == job["id"] or job.get("status") in ("successful", "failed"):
            try:
                ap = aap.aprobacion(job["id"])
            except ErrorAAP:
                continue
            quien = ((ap.get("summary_fields") or {}).get("approved_or_denied_by") or {}).get("username", "")
            if ap.get("timed_out"):
                final = "vencido"
            elif job.get("status") == "failed":
                final = "rechazado"
            if t["estado"] == "pendiente_aprobacion" and t["aap_aprobacion_id"] == job["id"]:
                txt = t["aprobado_en"] or ""
                if not txt.startswith("FaceOps"):
                    txt = f"AAP ({quien or 'vencida'})"
                ej("UPDATE tickets SET estado='en_ejecucion', aprobado_en=? WHERE id=?", txt, t["id"])
                auditar("AAP", t["cliente_id"], {"successful": "aprobado en AAP", "failed": "rechazado en AAP"}.get(job.get("status"), "aprobación"),
                        t["numero"], {"por": quien, "vencida": bool(ap.get("timed_out"))})
                t = q1("SELECT * FROM tickets WHERE id=?", t["id"])
    return final


def _segundos(desde, hasta):
    try:
        a = datetime.fromisoformat(desde.replace("Z", "+00:00"))
        b = datetime.fromisoformat(hasta.replace("Z", "+00:00"))
        return max(0, int((b - a).total_seconds()))
    except (AttributeError, ValueError, TypeError):
        return None


def tiempos(aap, t, r=None):
    """(ejecución, aprobación) en segundos. Ejecución = lo que corrió Ansible; aprobación = espera en los nodos de aprobación."""
    if t["aap_job_tipo"] != "workflow":
        r = r or aap.trabajo("job", t["aap_job_id"])
        return int(r.get("elapsed") or 0), 0
    ejec = aprob = 0
    for n in aap.nodos_trabajo_workflow(t["aap_job_id"]):
        if not n.get("id"):
            continue
        if n.get("type") == "workflow_approval":
            aprob += int(float(n.get("elapsed") or 0))
        else:
            ejec += int(float(n.get("elapsed") or 0))
    return ejec, aprob


def actualizar_tickets():
    for t in q("SELECT * FROM tickets WHERE estado IN ('en_ejecucion','pendiente_aprobacion') AND aap_job_id IS NOT NULL"):
        c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
        try:
            aap = AAP.de_cliente(c)
            negado = _revisar_aprobaciones(aap, t) if t["aap_job_tipo"] == "workflow" else None
            r = aap.trabajo(t["aap_job_tipo"], t["aap_job_id"])
        except ErrorAAP:
            continue
        if r.get("status") in ESTADOS_AAP:
            est = negado or ESTADOS_AAP[r["status"]]
            fin = ahora()
            dur = _segundos(t["creado"], fin) or int(r.get("elapsed") or 0)  # total: desde que se pidió hasta que terminó
            try:
                d_ejec, d_aprob = tiempos(aap, t, r if t["aap_job_tipo"] == "job" else None)
            except ErrorAAP:
                d_ejec, d_aprob = int(r.get("elapsed") or 0), None
            res = f"AAP terminó ({r['status']}): ejecución {d_ejec} s" + (f", espera de aprobación {d_aprob} s" if d_aprob else "") + {"rechazado": " · la aprobación fue rechazada",
                                                                 "vencido": " · la aprobación venció sin respuesta"}.get(negado or "", "")
            if est == "fallido" and t["aap_job_tipo"] == "job":
                res += "\n" + aap.salida(t["aap_job_id"], 15)
            ej("UPDATE tickets SET estado=?, fin=?, duracion_s=?, dur_ejecucion_s=?, dur_aprobacion_s=?, resultado=? WHERE id=?",
               est, fin, dur, d_ejec, d_aprob, res, t["id"])
            from .deuda import cerrar_ticket as cerrar_deuda
            cerrar_deuda(t, est)
            a_ = q1("SELECT nombre_aap FROM automatizaciones WHERE id=?", t["automatizacion_id"]) or {}
            from .evaluacion_so import AUTOS as AUTOS_SO, guardar as guardar_eval_so
            if est == "exitoso" and a_.get("nombre_aap") in AUTOS_SO and t["aap_job_tipo"] == "job":
                try:   # la última evaluación del sistema operativo queda a la mano en cada equipo
                    guardar_eval_so(t, aap.get(f"jobs/{t['aap_job_id']}/").get("artifacts") or {})
                except ErrorAAP:
                    pass
            try:
                from .confianza import reportar
                reportar(t["id"], est, d_ejec)
            except Exception:  # noqa: BLE001 - el resultado se reintenta en la próxima ejecución; no bloquea
                pass
            a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
            try:
                from .economia import liquidar
                liquidar(t["id"])   # créditos, factor de beneficio y ahorro
            except Exception:  # noqa: BLE001
                pass
            if est == "exitoso" and a and a.get("habilita_skill"):
                registrar_skill(t, a, j(t["parametros"], {}))
            if est in ("exitoso", "fallido") and a and a["nombre_aap"] == "FaceOps · Acceso de red":
                try:
                    from .topologia import verificar_flujos
                    eqs = [dict(e, skills=[], segmentos=[]) for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", c["id"])]
                    verificar_flujos(c, eqs, "FaceOps")
                except ErrorAAP:
                    pass
            if est == "exitoso" and a and a["nombre_aap"] in (HABILITAR, REPLICAR):
                try:
                    res += "\n" + registrar_servidores(aap, c, t)
                    ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                except ErrorAAP as x:
                    auditar("sistema", c["id"], "error alta de servidores", t["numero"], {"error": str(x)[:300]})
            if est == "exitoso" and a and a["nombre_aap"] == "OpenShift · Desplegar segmento":
                try:
                    from .segmentos_ocp import registrar as registrar_ocp
                    res += "\n" + registrar_ocp(aap, c, t)
                    ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                except ErrorAAP as x:
                    auditar("sistema", c["id"], "error registro OpenShift", t["numero"], {"error": str(x)[:300]})
            if est == "exitoso":   # paso del plan de un segmento: la construcción sigue sola con la etapa siguiente
                try:
                    from .arquitectura import construir, segmento_de_motivo
                    seg_c = segmento_de_motivo(t.get("motivo"))
                    if seg_c:
                        cons = construir(c, seg_c, q1("SELECT * FROM usuarios WHERE id=?", t["usuario_id"]))
                        if cons:
                            res += "\n" + cons
                            ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                except Exception as x:  # noqa: BLE001
                    auditar("sistema", c["id"], "error construcción de segmento", t["numero"], {"error": str(x)[:300]})
            if est == "exitoso" and a and a["nombre_aap"] == OPENSHIFT:
                try:
                    res += "\n" + registrar_cluster(aap, c, t)
                    ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                except ErrorAAP as x:
                    auditar("sistema", c["id"], "error alta de clúster", t["numero"], {"error": str(x)[:300]})
            if est == "exitoso" and a and a["nombre_aap"] == "FaceOps · Eliminar servidor":
                try:   # fuera de los inventarios de AAP y retirado en FaceOps (su respaldo en Git queda)
                    n = j(t["parametros"], {}).get("el_equipo", "")
                    for h in aap.get("hosts/", name=n, page_size=50).get("results", []):
                        aap._r("DELETE", f"hosts/{h['id']}/")
                    ej("UPDATE equipos SET estado='retirado' WHERE cliente_id=? AND nombre=?", c["id"], n)
                    res += f"\n{n} eliminado: fuera del balanceo, VM retirada de KVM (disco conservado aparte), fuera de los inventarios de AAP y del inventario de FaceOps. Su historial sigue en Git."
                    ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                    auditar("FaceOps", c["id"], "servidor eliminado", t["numero"], {"equipo": n})
                except ErrorAAP as x:
                    auditar("sistema", c["id"], "error al dar de baja", t["numero"], {"error": str(x)[:300]})
            if est == "exitoso" and a and a["nombre_aap"] == "Seguridad · Revisar CVEs":
                try:
                    from .cves import registrar as registrar_cves
                    res += "\n" + registrar_cves(aap, c, t)
                    ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                except Exception as x:  # noqa: BLE001
                    auditar("sistema", c["id"], "error al registrar CVE", t["numero"], {"error": str(x)[:300]})
            if a and a["nombre_aap"] == "FaceOps · Aplicar política de seguridad":
                try:
                    from .politicas_seg import registrar as registrar_politica
                    res += "\n" + registrar_politica(aap, c, t, est)
                    ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                except Exception as x:  # noqa: BLE001
                    auditar("sistema", c["id"], "error al registrar política", t["numero"], {"error": str(x)[:300]})
            if est == "exitoso" and a and a["nombre_aap"] in ("FaceOps · Recuperar servidor", "FaceOps · Volver a una versión"):
                try:
                    res += "\n" + registrar_recuperacion(aap, c, t)
                    ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                except ErrorAAP as x:
                    auditar("sistema", c["id"], "error al registrar la recuperación", t["numero"], {"error": str(x)[:300]})
            if a and a["nombre_aap"] == "FaceOps · Nuevo segmento de negocio":
                try:
                    from .solicitud_segmento import finalizar
                    txt = finalizar(c, t, est)
                    if txt:
                        res += "\n" + txt
                        ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                except Exception as x:  # noqa: BLE001
                    auditar("sistema", c["id"], "error al activar segmento", t["numero"], {"error": str(x)[:300]})
            conector(c).cerrar(q1("SELECT * FROM tickets WHERE id=?", t["id"]), a)


HABILITAR = "FaceOps · Habilitar servidor"
REPLICAR = "FaceOps · Replicar con alta disponibilidad"
INV_HABILITADOS = "Servidores habilitados"


INV_CLUSTERS = "OpenShift · clústeres"


def alta_cluster(aap, cliente, d, ubicacion="", ticket=""):
    """Da de alta un clúster OpenShift en el inventario de AAP «OpenShift · clústeres» y en el de FaceOps."""
    org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
    inv = (aap.get("inventories/", name=INV_CLUSTERS).get("results") or [None])[0] or aap.post("inventories/", {
        "name": INV_CLUSTERS, "organization": org, "description": "Clústeres OpenShift creados o administrados por FaceOps"})
    nombre = f"ocp-{d['cluster']}"
    ip = (d.get("vips") or {}).get("api") or (d.get("ips") or [""])[0]
    hv = {"ansible_host": ip, "ansible_connection": "local", "faceops_categoria": "clúster openshift", "faceops_forma": "virtual",
          "faceops_so": f"OpenShift {d.get('version', '')}".strip(), "faceops_ambiente": d.get("ambiente") or "produccion",
          "faceops_ubicacion": ubicacion, "faceops_skills": ["openshift"], "faceops_ticket": ticket,
          "openshift": {k: d.get(k) for k in ("cluster", "dominio", "version", "metodo", "plataforma", "topologia", "nodos", "api", "consola")}}
    ya = (aap.get("hosts/", name=nombre, inventory=inv["id"]).get("results") or [None])[0]
    if ya:
        aap.patch(f"hosts/{ya['id']}/", {"variables": json.dumps(hv, ensure_ascii=False)})
    else:
        aap.post("hosts/", {"name": nombre, "inventory": inv["id"], "variables": json.dumps(hv, ensure_ascii=False)})
    cl = j(q1("SELECT clasificacion FROM clientes WHERE id=?", cliente["id"])["clasificacion"], {})
    if INV_CLUSTERS not in cl:
        cl[INV_CLUSTERS] = {"proveedor": "openshift", "ubicacion": ubicacion, "ambiente": "produccion", "sin_recoleccion": True, "forma": "virtual"}
        ej("UPDATE clientes SET clasificacion=? WHERE id=?", json.dumps(cl, ensure_ascii=False), cliente["id"])
    try:
        from .inventario import sincronizar
        sincronizar(q1("SELECT * FROM clientes WHERE id=?", cliente["id"]), "FaceOps")
    except Exception:  # noqa: BLE001
        pass
    return nombre


def registrar_cluster(aap, cliente, t):
    jobs = [t["aap_job_id"]] if t["aap_job_tipo"] == "job" else \
        [n["id"] for n in aap.nodos_trabajo_workflow(t["aap_job_id"]) if n.get("type") == "job" and n.get("id")]
    d = {}
    for jid in jobs:
        d.update((aap.get(f"jobs/{jid}/").get("artifacts") or {}).get("faceops_openshift") or {})
    if not d.get("cluster") or d.get("estado") == "validado":
        return "Manifiesto validado: no se creó ningún clúster" if d.get("estado") == "validado" else "No se recibieron datos del clúster"
    inf = q1("SELECT ubicacion FROM infraestructuras WHERE cliente_id=? AND nombre=?", cliente["id"], j(t["parametros"], {}).get("ocp_infraestructura", ""))
    n = alta_cluster(aap, cliente, d, (inf or {}).get("ubicacion", ""), t["numero"])
    return f"Clúster {d['cluster']} ({d.get('estado')}) en el inventario «{INV_CLUSTERS}» como {n} · consola {d.get('consola')}"


def registrar_servidores(aap, cliente, t):
    """Lee lo que creó «Habilitar servidor» (set_stats) y da de alta cada servidor en el inventario de AAP."""
    jobs = [t["aap_job_id"]] if t["aap_job_tipo"] == "job" else \
        [n["id"] for n in aap.nodos_trabajo_workflow(t["aap_job_id"]) if n.get("type") == "job" and n.get("id")]
    art = {}
    for jid in jobs:
        art.update(aap.get(f"jobs/{jid}/").get("artifacts") or {})
    creados = [x.split("|") for x in art.get("faceops_creados", [])]
    if not creados:
        return "No se recibieron servidores creados"
    inv = (aap.get("inventories/", name=INV_HABILITADOS).get("results") or [None])[0]
    if not inv:
        raise ErrorAAP(f"No existe el inventario «{INV_HABILITADOS}» en AAP")
    so, destino, amb = art.get("faceops_so", ""), art.get("faceops_destino", "onpremise"), art.get("faceops_ambiente", "desarrollo")
    win = so.startswith("windows")
    grupo = "windows" if win else ("linux_gcp" if destino == "gcp" else "linux_onpremise")
    g = (aap.get("groups/", name=grupo, inventory=inv["id"]).get("results") or [None])[0]
    lineas = []
    for nombre, ip in creados:
        hv = {"ansible_host": ip, "faceops_ambiente": amb, "faceops_forma": "virtual", "faceops_so_pedido": so,
              "faceops_segmento": j(t["parametros"], {}).get("hab_segmento", ""),
              "faceops_ubicacion": "Cloud GCP" if destino == "gcp" else "Centro de datos Santiago", "faceops_ticket": t["numero"]}
        if win:
            hv.update({"ansible_connection": "winrm", "ansible_port": 5985, "ansible_winrm_transport": "ntlm"})
        existe = (aap.get("hosts/", name=nombre, inventory=inv["id"]).get("results") or [None])[0]
        h = existe or aap.post("hosts/", {"name": nombre, "inventory": inv["id"], "variables": json.dumps(hv)})
        if g:
            try:
                aap.post(f"groups/{g['id']}/hosts/", {"id": h["id"]})
            except ErrorAAP:
                pass
        lineas.append(f"{nombre} · {ip}")
    auditar("FaceOps", cliente["id"], "servidores habilitados", t["numero"], {"servidores": lineas, "inventario": INV_HABILITADOS})
    try:
        from .inventario import sincronizar
        sincronizar(cliente, "FaceOps")
    except Exception:  # noqa: BLE001 - el alta ya quedó en AAP; la sincronización corre igual a diario
        pass
    txt = "Servidores dados de alta en el inventario «Servidores habilitados» de AAP: " + ", ".join(lineas)
    ha = art.get("faceops_ha")
    if ha:  # Replicar con alta disponibilidad: dónde quedó el balanceo y cómo repartió la prueba
        txt += (f"\nAlta disponibilidad: {ha.get('origen')} y sus réplicas balanceados en round robin por {ha.get('balanceador')}"
                f"{' (balanceo habilitado ahora)' if ha.get('nuevo') else ''} · {ha.get('url')}"
                f"\nPrueba (6 pedidos): {', '.join(str(x) for x in ha.get('respondieron', []))}")
        auditar("FaceOps", cliente["id"], "alta disponibilidad", t["numero"], ha)
    return txt


def registrar_recuperacion(aap, cliente, t):
    """Tras «Recuperar servidor» / «Volver a una versión»: resumen para el ticket y, si la máquina cambió de IP, la nueva IP
    en cada inventario de AAP donde está el equipo y en FaceOps."""
    jobs = [n["id"] for n in aap.nodos_trabajo_workflow(t["aap_job_id"]) if n.get("type") == "job" and n.get("id")]
    art = {}
    for jid in jobs:
        art.update(aap.get(f"jobs/{jid}/").get("artifacts") or {})
    r = art.get("faceops_recuperado") or {}
    if not r:
        return "No llegó el resultado de la restauración"
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], r.get("equipo"))
    if e and r.get("ip") and r["ip"] != e["ip"]:
        for h in aap.get("hosts/", name=e["nombre"], page_size=50).get("results", []):
            try:
                hv = yaml.safe_load(h.get("variables") or "") or {}
            except yaml.YAMLError:
                continue
            if isinstance(hv, dict):
                hv["ansible_host"] = r["ip"]
                aap.patch(f"hosts/{h['id']}/", {"variables": json.dumps(hv)})
        ej("UPDATE equipos SET ip=? WHERE id=?", r["ip"], e["id"])
    auditar("FaceOps", cliente["id"], "equipo restaurado", t["numero"], r)
    return (f"{r.get('equipo')} restaurado en {r.get('ip')} desde la versión {r.get('version')}\n"
            f"{' · '.join(r.get('paquetes', [])[:1])} · {r.get('archivos')}\nServicios: {', '.join(r.get('servicios', [])) or '—'}"
            f"\nCaídos: {', '.join(r.get('caidos', [])) or 'ninguno'}")


def registrar_skill(t, auto, params):
    """La automatización habilitó su skill: queda en cada equipo donde corrió (límite o campos equipo/equipos)."""
    nombres = set((t["limite"] or "").split(":")) - {""}
    for c in j(auto["campos"], []):
        if c["tipo"] in ("equipo", "equipos") and params.get(c["var"]):
            v = params[c["var"]]
            nombres.update(v if isinstance(v, list) else [v])
    for n in nombres:
        e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", t["cliente_id"], n)
        if not e:
            continue
        hab = j(e.get("skills_habilitadas"), {})
        hab[auto["habilita_skill"]] = {"ticket": t["numero"], "cuando": ahora()}
        ej("UPDATE equipos SET skills_habilitadas=? WHERE id=?", json.dumps(hab, ensure_ascii=False), e["id"])
    if nombres:
        auditar("FaceOps", t["cliente_id"], "skill habilitada", t["numero"], {"skill": auto["habilita_skill"], "equipos": sorted(nombres)})


def vigia(intervalo=10):
    """Hilo de fondo: estado de los trabajos en AAP y recolección diaria del inventario (03:00 hora del servidor)."""
    from .inventario import recolectar, sincronizar

    def bucle():
        ultimo_dia = None
        while True:
            try:
                actualizar_tickets()
                from .topologia import actualizar_flujos
                actualizar_flujos()
                hoy = datetime.now()
                if hoy.hour == 3 and ultimo_dia != hoy.date():
                    ultimo_dia = hoy.date()
                    for c in q("SELECT * FROM clientes WHERE activo=1 AND aap_url!=''"):
                        try:
                            recolectar(c)
                            time.sleep(600)
                            sincronizar(c)
                            sincronizar_catalogo(c)
                            from .topologia import verificar_flujos
                            from .bd import q as _q
                            verificar_flujos(c, [dict(e, skills=[], segmentos=[]) for e in _q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", c["id"])])
                        except Exception as e:  # noqa: BLE001
                            auditar("sistema", c["id"], "error recolección", "inventario", {"error": str(e)[:300]})
            except Exception:  # noqa: BLE001
                pass
            time.sleep(intervalo)
    threading.Thread(target=bucle, daemon=True, name="faceops-vigia").start()

    def energia():
        from .codigo import refrescar
        from .inventario import sondear_energia
        vuelta = 0
        while True:
            try:
                sondear_energia()
            except Exception:  # noqa: BLE001
                pass
            if vuelta % 10 == 0:  # estructura como código: último commit de cada capa, cada 10 minutos
                try:
                    refrescar()
                except Exception:  # noqa: BLE001
                    pass
            vuelta += 1
            time.sleep(60)
    threading.Thread(target=energia, daemon=True, name="faceops-energia").start()


# ------------------------------------------------------------------ conectores de tickets
class Conector:
    def __init__(self, cliente):
        self.c = cliente
        self.conf = j(cliente.get("tickets_conf"), {})

    def crear(self, t, auto, usuario):
        pass

    def cerrar(self, t, auto):
        pass

    def enlace(self, ref):
        return None


class ServiceNow(Conector):
    """ServiceNow orquestado desde FaceOps (modo por automatización en datos/servicenow.yml):
      · ritm   → pide el ítem del catálogo de ServiceNow de la demo, toma el ticket (RITM) y se lo pasa a AAP;
                 los playbooks lo actualizan y lo cierran como siempre (espera de aprobación, completado, rechazado).
      · propio → la automatización abre su propio incidente/cambio; FaceOps lo enlaza al terminar.
      · por defecto → FaceOps abre un incidente al pedir y lo resuelve al terminar."""

    def _api(self, metodo, ruta, datos=None, **params):
        r = requests.request(metodo, self.conf["url"].rstrip("/") + ruta, json=datos, params=params or None, timeout=30,
                             auth=(self.conf["usuario"], self.conf["clave"]), headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json().get("result", {})

    def enlace(self, ref):
        p = (ref or "").split("|")
        if len(p) < 3 or not self.conf.get("url"):
            return None
        return f"{self.conf['url'].rstrip('/')}/nav_to.do?uri={p[2]}.do?sys_id={p[1]}"

    def crear(self, t, auto, usuario):
        sn = conf_servicenow(auto["nombre_aap"])
        modo = sn.get("modo", "incidente")
        try:
            if modo == "propio":
                ej("UPDATE tickets SET externo_ref=? WHERE id=?", "propio", t["id"])
                return {}
            if modo == "ritm":
                return self._pedir_item(t, auto, usuario, sn)
            r = self._api("POST", "/api/now/table/incident", {
                "short_description": f"[FaceOps {t['numero']}] {auto['nombre']}" + (f" · {t['segmento']}" if t["segmento"] else ""),
                "description": f"Solicitado por {usuario['nombre']} en FaceOps.\nMotivo: {t['motivo'] or '—'}\n"
                               f"Automatización: {auto['nombre_aap']} ({auto['version']})\nÁmbito: {t['limite'] or auto['ambito']}",
                "correlation_id": f"faceops:{t['numero']}", "impact": "3", "urgency": "3", "category": "software"})
            ej("UPDATE tickets SET externo_ref=? WHERE id=?", f"{r.get('number')}|{r.get('sys_id')}|incident", t["id"])
        except (requests.RequestException, KeyError, IndexError) as e:
            auditar("sistema", t["cliente_id"], "error conector tickets", t["numero"], {"error": str(e)[:200]})
        return {}

    def _pedir_item(self, t, auto, usuario, sn):
        params = j(t["parametros"], {})
        item = self._api("GET", "/api/now/table/sc_cat_item", sysparm_query=f"name={sn['item']}", sysparm_fields="sys_id", sysparm_limit=1)[0]
        variables = {k: str(params[v]) for k, v in sn.get("variables", {}).items() if params.get(v) not in (None, "")}
        variables["motivo"] = t["motivo"] or f"Solicitado en FaceOps ({t['numero']})"
        orden = self._api("POST", f"/api/sn_sc/servicecatalog/items/{item['sys_id']}/order_now",
                          {"sysparm_quantity": "1", "variables": variables})
        ritm = self._api("GET", "/api/now/table/sc_req_item", sysparm_query=f"request={orden['request_id']}",
                         sysparm_fields="sys_id,number,sys_created_on", sysparm_limit=1)[0]
        # FaceOps lo toma en el acto (el vigía de la demo deja pasar 25 s): así se lanza una sola vez, desde FaceOps
        self._api("PATCH", f"/api/now/table/sc_req_item/{ritm['sys_id']}", {
            "state": "2", "correlation_id": f"faceops:{t['numero']}",
            "comments": f"Recibimos tu solicitud desde FaceOps ({t['numero']}). Te avisaremos aquí mismo.",
            "work_notes": f"Orquestado por FaceOps {t['numero']} · solicitado por {usuario['nombre']} ({usuario['usuario']}). "
                          f"FaceOps lanza en AAP «{auto['nombre_aap']}»."})
        ej("UPDATE tickets SET externo_ref=? WHERE id=?", f"{ritm['number']}|{ritm['sys_id']}|sc_req_item", t["id"])
        extra = {"snow_ritm": ritm["number"], "snow_ritm_sys_id": ritm["sys_id"], "snow_creado": ritm.get("sys_created_on", ""),
                 "snow_motivo": variables["motivo"]}
        for k, v in sn.get("extra", {}).items():
            if "{ritm}" in str(v) and not params.get(k):
                extra[k] = str(v).replace("{ritm}", ritm["number"])
        auditar("FaceOps", t["cliente_id"], "ticket en ServiceNow", t["numero"], {"ritm": ritm["number"], "item": sn["item"]})
        return extra

    def cerrar(self, t, auto):
        ref = t.get("externo_ref") or ""
        try:
            if ref == "propio":
                b = conf_servicenow(auto["nombre_aap"]).get("buscar")
                if b and t["aap_job_id"]:
                    r = self._api("GET", f"/api/now/table/{b['tabla']}", sysparm_query=b["consulta"].replace("{job}", str(t["aap_job_id"])),
                                  sysparm_fields="sys_id,number", sysparm_limit=1)
                    if r:
                        ej("UPDATE tickets SET externo_ref=? WHERE id=?", f"{r[0]['number']}|{r[0]['sys_id']}|{b['tabla']}", t["id"])
                return
            p = ref.split("|")
            if len(p) < 3:
                return
            if p[2] == "sc_req_item":
                # El playbook ya cerró o actualizó el RITM; FaceOps solo deja constancia
                self._api("PATCH", f"/api/now/table/sc_req_item/{p[1]}", {"work_notes": f"FaceOps {t['numero']}: {t['estado']}."})
                return
            notas = f"FaceOps {t['numero']}: {t['estado']}. {(t['resultado'] or '')[:500]}"
            if t["estado"] == "fallido":  # queda abierto para que lo tome una persona
                self._api("PATCH", f"/api/now/table/incident/{p[1]}", {"state": "2", "work_notes": "Ansible no pudo resolverlo: " + notas})
            else:
                self._api("PATCH", f"/api/now/table/incident/{p[1]}", {
                    "state": "6", "close_notes": notas, "comments": notas,
                    "close_code": "Resolved – Permanent Fix Applied" if t["estado"] == "exitoso" else "Resolved – Fixed Without Action"})
        except (requests.RequestException, KeyError) as e:
            auditar("sistema", t["cliente_id"], "error conector tickets", t["numero"], {"error": str(e)[:200]})


def conector(cliente):
    return {"servicenow": ServiceNow}.get(cliente.get("tickets_tipo"), Conector)(cliente)
