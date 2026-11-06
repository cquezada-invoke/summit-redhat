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
        for k in ("etiqueta", "ayuda", "enlace", "acepta"):
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
                          "visible": int(f.get("visible", 1)), "por_inventario": int(f.get("por_inventario", 0))})
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
                   and (not auto.get("por_inventario") or e.get("estado") == "con datos" or auto["nombre_aap"] == INVENTARIAR)
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


INVENTARIAR = "Sistema · Inventariar máquina"


def aplica_a_equipo(usuario, cliente_id, auto, e):
    """¿La automatización puede correr sobre este equipo puntual? (mismas reglas que el formulario por segmento)."""
    return motivo_no_aplica(usuario, cliente_id, auto, e) is None


def motivo_no_aplica(usuario, cliente_id, auto, e):
    """None si la automatización puede correr sobre el equipo; si no, por qué (en palabras del usuario)."""
    from .inventario import skills_de
    from .permisos import ambiente_permitido, perfil
    if not auto["usa_limite"]:
        return "esta automatización elige sus equipos en el formulario, no se lanza desde un equipo"
    if not auto["visible"]:
        return "la automatización no está publicada en el catálogo"
    if e["estado"] == "retirado":
        return "el equipo está retirado"
    if not ambiente_permitido(perfil(usuario, cliente_id), e.get("ambiente")):
        return f"tu perfil no puede operar el ambiente {e.get('ambiente')}"
    en_su_inv = auto.get("habilita_skill") and e.get("ip")   # instalar una plataforma: corre con el inventario y la credencial de la máquina
    if not (auto.get("por_inventario") or en_su_inv or not auto["inventario"] or auto["inventario"] in j(e["inventarios"], [])):
        return "el equipo no está en el inventario de AAP de esta automatización"
    if auto.get("por_inventario") and e["estado"] != "con datos" and auto["nombre_aap"] not in (INVENTARIAR, "FaceOps · Vincular máquina a la bóveda"):   # inventariar trae los datos; vincular no los necesita
        return "el equipo aún no está inventariado: lanza primero «Sistema · Inventariar máquina»"
    from .compatibilidad import compatible
    sk = set(skills_de(e))
    ok, por_que = compatible(auto, dict(e, skills=list(sk)))
    if not ok:
        return por_que or "no es compatible con el sistema, el tipo o los productos del equipo"
    if auto.get("habilita_skill") and auto["habilita_skill"] in sk:
        return f"el equipo ya tiene {auto['habilita_skill']}"
    return None


def solicitar_en_equipo(usuario, cliente, auto, e, parametros, motivo, secretos=None, prueba=False):
    """Ejecuta la automatización directamente sobre un equipo (desde su detalle en el inventario)."""
    no_ = motivo_no_aplica(usuario, cliente["id"], auto, e)
    if no_:
        raise PermissionError(f"Esta automatización no aplica a {e['nombre']}: {no_}")
    if (auto.get("habilita_skill") and not auto.get("por_inventario") and auto["inventario"]
            and auto["inventario"] not in j(e["inventarios"], [])):
        parametros = dict(parametros, _en_su_inventario=True)   # la máquina no está en el inventario de la plataforma
    return solicitar(usuario, cliente, auto, None, parametros, motivo, secretos, {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {},
                     equipo=e, prueba=prueba)


JENKINS = "Jenkins · Preparar servidor"


def preparar_jenkins(equipo, parametros):
    """Jenkins: FaceOps le dice bajo qué ruta se publica (su consola en FaceOps). La clave de «admin» la genera el equipo y
    AAP la guarda en la bóveda (web/<máquina>/jenkins); el usuario nunca la ve: FaceOps entra por él al abrir la consola."""
    import os
    if not equipo:
        raise ValueError("Jenkins se instala sobre un equipo: lánzalo desde la ficha del servidor")
    pre = os.environ.get("FACEOPS_PREFIJO", "/faceops").rstrip("/")
    return dict(parametros, jk_ruta=f"{pre}/inventario/{equipo['id']}/consola-web/jenkins")


def solicitar(usuario, cliente, auto, segmento, parametros, motivo, secretos=None, filtros=None, equipo=None, equipos=None, prueba=False):
    """FaceOps orquesta: crea el ticket (propio y, si corresponde, en la herramienta del cliente) y lanza en AAP.
    Si requiere aprobación, la aprobación vive en AAP (nodo de aprobación del workflow): FaceOps la muestra y
    permite responderla, y quien entre a AAP la ve igual en su bandeja de aprobaciones."""
    ok, por_que = puede_ejecutar(usuario, cliente["id"], auto)
    if not ok:
        raise PermissionError(por_que)
    from .pruebas import estado as estado_prueba
    ep_ = estado_prueba(dict(auto, cliente_id=cliente["id"]))
    if ep_["estado"] != "certificada" and not prueba:   # regla de prueba: sin su plan de prueba aprobado no se usa
        raise PermissionError(f"«{auto['nombre']}» está bloqueada: " + ("cambió desde su última prueba y debe probarse de nuevo" if ep_["estado"] == "cambiada"
                                                                        else "aún no tiene su plan de prueba ejecutado y aprobado"))
    if auto["nombre_aap"] == JENKINS:
        parametros = preparar_jenkins(equipo, parametros)
    if auto["nombre_aap"] == "FaceOps · Habilitar servidor" and str(parametros.get("hab_so", "")) in ("rhel10", "rhel9", "rhel8", "rhel7") \
            and parametros.get("hab_destino", "onpremise") in ("onpremise", "vmware") and not parametros.get("hab_interno"):
        # RHEL on-premise nace registrado en Red Hat (regularizado): la clave de activación es obligatoria y solo viaja a AAP
        if not (secretos or {}).get("hab_rh_llave") or not str(parametros.get("hab_rh_org") or "").strip():
            raise ValueError("Para crear un RHEL indica la organización y la clave de activación de Red Hat: la máquina queda registrada al nacer")
    limite = ""
    if equipo:  # sobre un equipo puntual: el límite es el equipo; el segmento (para visibilidad) es el primero suyo
        from .inventario import segmentos_de
        segs = segmentos_de(equipo, q("SELECT * FROM segmentos WHERE cliente_id=?", cliente["id"]))
        segmento, limite = (segs[0] if segs else ""), equipo["nombre"]
        if parametros.get("mj_destino") and parametros["mj_destino"] != equipo["nombre"]:   # migraciones: también el equipo de destino
            limite += ":" + parametros["mj_destino"]
    elif equipos:   # acción de una plataforma sobre varias máquinas elegidas (validadas por quien llama): el límite son ellas
        from .inventario import segmentos_de
        segs = segmentos_de(equipos[0], q("SELECT * FROM segmentos WHERE cliente_id=?", cliente["id"]))
        segmento, limite = (segs[0] if segs else ""), ":".join(e_["nombre"] for e_ in equipos)
    elif parametros.get("mj_origen"):   # migración JBoss: el origen (y el destino, si migra) son el límite
        from .inventario import segmentos_de
        from .permisos import equipo_visible, perfil
        e_o = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], parametros["mj_origen"])
        segs = segmentos_de(e_o, q("SELECT * FROM segmentos WHERE cliente_id=?", cliente["id"])) if e_o else []
        if not e_o or not equipo_visible(perfil(usuario, cliente["id"]), dict(e_o), segs):
            raise PermissionError("Elige un servidor de origen de tu inventario")
        segmento = segs[0] if segs else ""
        limite = e_o["nombre"] + (":" + parametros["mj_destino"] if parametros.get("mj_modo") == "migrar" and parametros.get("mj_destino") else "")
        if parametros.get("mj_modo") != "migrar":
            parametros.pop("mj_destino", None)
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
    if limite and auto["nombre_aap"] not in ("Infra · Encender o apagar equipo", "FaceOps · Eliminar servidor"):   # no se lanza sobre máquinas apagadas
        from .salud import encendido as _enc
        nombres_ = [n for n in limite.split(":") if n]
        apagados = [n for n in nombres_ if (lambda e_: bool(e_) and _enc(dict(e_)) == "apagado")(
            q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], n))]
        if apagados and (equipo or parametros.get("mj_origen") or len(apagados) == len(nombres_)):
            raise PermissionError(f"{', '.join(apagados)}: apagad{'as' if len(apagados) > 1 else 'a'}. "
                                  f"Enciénde{'las' if len(apagados) > 1 else 'la'} desde su detalle en el inventario antes de lanzar la automatización")
        if apagados:   # en un segmento: corre solo en las encendidas
            limite = ":".join(n for n in nombres_ if n not in apagados)
    if auto["nombre_aap"] not in ("Infra · Encender o apagar equipo", "FaceOps · Eliminar servidor"):   # equipos elegidos en el formulario
        from .salud import encendido as _enc2
        elegidos = []
        for k_ in ("servidor", "servidores", "destinos", "ad_equipo", "paquetes_origen", "mj_destino"):
            v_ = parametros.get(k_)
            elegidos += [v_] if isinstance(v_, str) else list(v_ or []) if isinstance(v_, list) else []
        apag_ = [n for n in dict.fromkeys(x for x in elegidos if x) if (lambda e_: bool(e_) and _enc2(dict(e_)) == "apagado")(
            q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], n))]
        if apag_:
            raise PermissionError(f"{', '.join(apag_)}: apagad{'as' if len(apag_) > 1 else 'a'}. "
                                  f"Enciénde{'las' if len(apag_) > 1 else 'la'} desde su detalle en el inventario antes de lanzar la automatización")
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


GRUPO_SKILL = {"jboss": "jboss", "keycloak": "keycloak", "red-hat-idm": "idm", "midpoint": "midpoint", "satellite": "satellite",
               "389-directory-server": "directory", "active-directory": "ad", "iis": "iis", "windows-exporter": "windows"}


def preparar_en_su_inventario(aap, auto, inv_id, nombres):
    """Para instalar una plataforma en una máquina que no está en el inventario de esa plataforma: la plantilla acepta
    inventario y credencial al lanzar, y la máquina queda en el grupo que espera el playbook (jboss, keycloak, idm…)."""
    if auto["aap_tipo"] == "job":
        aap.patch(f"job_templates/{auto['aap_id']}/", {"ask_inventory_on_launch": True, "ask_credential_on_launch": True})
    grupo = GRUPO_SKILL.get(auto.get("habilita_skill") or "", auto.get("habilita_skill") or "")
    if not grupo or not inv_id:
        return
    gs = aap.get("groups/", inventory=inv_id, name=grupo).get("results") or []
    gid = gs[0]["id"] if gs else aap.post("groups/", {"name": grupo, "inventory": inv_id, "description": "Creado por FaceOps al instalar la plataforma"})["id"]
    for n in nombres:
        hs = aap.get("hosts/", inventory=inv_id, name=n).get("results") or []
        if hs:
            try:
                aap.post(f"groups/{gid}/hosts/", {"id": hs[0]["id"]})
            except ErrorAAP:
                pass


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
    """Todo ticket requiere una o más aprobaciones (la cadena de áreas de su automatización). Solo se lanza sin pedirla
    cuando FaceOps ya registró todas sus etapas aprobadas (ejecutar(..., aprobado=True))."""
    return not auto.get("_aprobado")


def objetivo_aap(aap, auto, ambiente=None):
    """Qué se lanza en AAP: la plantilla tal cual, o (si FaceOps exige aprobación —siempre o en ese ambiente— y AAP
    no la tiene) su workflow con aprobación previa."""
    if requiere_aprobacion(auto, ambiente) and not auto.get("aprobacion_aap"):
        wf = aap.asegurar_flujo_aprobacion(auto)   # su nombre lleva la cadena de áreas: si cambia la cadena, se crea otro
        if wf != auto.get("aprobacion_wf"):
            ej("UPDATE automatizaciones SET aprobacion_wf=? WHERE id=?", wf, auto["id"])
        return "workflow", wf
    return auto["aap_tipo"], auto["aap_id"]


OPENSHIFT = "OpenShift · Validar e instalar IPI VMware"
OCP_PREPARAR = "OpenShift · Preparar instalación IPI VMware"
OCP_INSTALACION = (OCP_PREPARAR, OPENSHIFT)
INCORPORAR = "FaceOps · Incorporar máquinas"


def _simular(ticket_id):
    """Maqueta (demo cáscara): no hay AAP ni máquinas detrás; el ticket termina bien con un resultado simulado."""
    import random
    from datetime import datetime, timedelta, timezone
    d = random.randint(20, 240)
    fin = datetime.now(timezone.utc)
    ej("UPDATE tickets SET estado='exitoso', inicio=?, fin=?, duracion_s=?, resultado=? WHERE id=?",
       (fin - timedelta(seconds=d)).isoformat(timespec="seconds"), fin.isoformat(timespec="seconds"), d,
       f"Simulado en la maqueta: en la consola real lo ejecuta AAP (aprobación y ejecución, ~{d} s).", ticket_id)


def ejecutar(ticket_id, secretos=None, aprobado=False):
    if os.environ.get("FACEOPS_MAQUETA") == "1":
        return _simular(ticket_id)
    from .confianza import CONTEXTO
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    tok = CONTEXTO.set({"ticket": t, "automatizacion": a["nombre_aap"],
                        "usuario": (q1("SELECT usuario FROM usuarios WHERE id=?", t["usuario_id"]) or {}).get("usuario", "")})
    try:
        _ejecutar(ticket_id, secretos, aprobado)
    finally:
        CONTEXTO.reset(tok)


def _ejecutar(ticket_id, secretos=None, aprobado=False):
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    if aprobado:   # todas sus etapas ya se aprobaron en FaceOps: se lanza sin pedir otra aprobación
        a = dict(a, requiere_aprobacion=0, aprobacion_ambientes="[]", aprobacion_aap=0, _aprobado=True)
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
            tipo, id_ = "workflow", aap.asegurar_flujo_aprobacion(a, credenciales=[cr["id"]], sufijo=inf["nombre"])
            job = aap.lanzar(tipo, id_, extra_vars=params)
            ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, aap_job_tipo=?, inicio=? WHERE id=?", job, tipo, ahora(), ticket_id)
            return
        if a["nombre_aap"] == HABILITAR and params.get("hab_destino") == "vmware" and not params.get("hab_vc_puerto"):
            inf = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre=?", c["id"], params.get("hab_infraestructura", ""))
            if inf:   # el puerto del vCenter vive en la infraestructura (la credencial de AAP solo trae la dirección)
                from .infraestructuras import _vc
                params["hab_vc_puerto"] = str(_vc(j(inf["detalle"], {}).get("host", ""))[1])
        if a["nombre_aap"] == HABILITAR and params.get("hab_destino") == "gcp":   # crear en una nube GCP: su credencial y su proyecto
            inf = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre=?", c["id"], params.get("hab_infraestructura", ""))
            if not inf or not inf["credencial"]:
                raise ErrorAAP("Esa infraestructura GCP no tiene credencial en AAP: usa «Conectar» en Infraestructuras")
            cr = (aap.get("credentials/", name=inf["credencial"]).get("results") or [None])[0]
            if not cr:
                raise ErrorAAP(f"No existe en AAP la credencial «{inf['credencial']}» de {inf['nombre']}")
            det = j(inf["detalle"], {})
            params.update({k: v for k, v in {"hab_gcp_proyecto": det.get("proyecto"), "hab_gcp_zona": det.get("zona")}.items() if v})
            creds = [x["id"] for x in aap.todos(f"job_templates/{a['aap_id']}/credentials/") if x.get("kind") != "gce"] + [cr["id"]]
            tipo, id_ = "workflow", aap.asegurar_flujo_aprobacion(a, credenciales=creds, sufijo=inf["nombre"])
            job = aap.lanzar(tipo, id_, extra_vars=params)
            ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, aap_job_tipo=?, inicio=? WHERE id=?", job, tipo, ahora(), ticket_id)
            return
        if a["nombre_aap"] in OCP_INSTALACION:  # la infraestructura elegida define la plataforma y la credencial (vCenter)
            inf = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre=?", c["id"], params.get("iv_infraestructura") or params.get("ocp_infraestructura", ""))
            if not inf:
                raise ErrorAAP("Elige la infraestructura donde se instala OpenShift")
            det = j(inf["detalle"], {})
            params["ocp_plataforma"] = {"vmware": "vsphere"}.get(inf["tipo"], inf["tipo"])
            if inf["tipo"] == "vmware":   # el puerto del vCenter vive en la infraestructura (la credencial de AAP solo trae la dirección)
                from .infraestructuras import _vc
                params["iv_vc_puerto"] = str(_vc(det.get("host", ""))[1])
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
            tipo, id_ = "workflow", aap.asegurar_flujo_aprobacion(a, credenciales=creds, sufijo=inf["nombre"])
            job = aap.lanzar(tipo, id_, extra_vars=params)
            ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, aap_job_tipo=?, inicio=? WHERE id=?", job, tipo, ahora(), ticket_id)
            return
        if a["nombre_aap"] == "OpenShift · Versión del clúster":   # orden entre ambientes: UAT/SIT antes que producción
            import re as _re
            objetivo = q1("SELECT ambiente FROM equipos WHERE cliente_id=? AND nombre=?", c["id"], (t["limite"] or "").split(":")[0]) or {}
            params["ov_ambiente"] = objetivo.get("ambiente") or ""
            params["ov_otros"] = [{"nombre": x["nombre"], "ambiente": x["ambiente"] or "", "version": (_re.search(r"4\.\d+\.\d+", x["so"] or "") or [""])[0]}
                                  for x in q("SELECT nombre, ambiente, so FROM equipos WHERE cliente_id=? AND estado!='retirado' AND lower(categoria) LIKE 'clúster%'", c["id"])
                                  if x["nombre"] != (t["limite"] or "").split(":")[0]]
        en_su_inv = bool(params.pop("_en_su_inventario", False))
        if (a.get("por_inventario") or en_su_inv) and a["nombre_aap"] != APAGAR:
            grupos = {}
            for n in (t["limite"] or "").split(":"):
                e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", c["id"], n)
                if e:
                    try:
                        acc = acceso_equipo(aap, c, e)
                    except ErrorAAP:
                        if j(t.get("parametros"), {}).get("vm_tipo") not in ("red", "openshift"):
                            raise
                        # vincular un equipo de red o un clúster OpenShift: el playbook no entra por SSH (usa lo entregado o la API)
                        acc = ({i["name"]: i["id"] for i in aap.inventarios()}.get(e["inventario"]), e["inventario"], [])
                    grupos.setdefault((acc[0], acc[1], tuple(acc[2])), []).append(n)
            if len(grupos) != 1:
                raise ErrorAAP("Los equipos elegidos están en inventarios de AAP con credenciales distintas: "
                               + " · ".join(f"{k[1]}: {', '.join(v)}" for k, v in grupos.items()) + ". Elige equipos de uno solo.")
            (inv_id, inv_n, creds), = grupos.keys()
            if en_su_inv:   # la plataforma se instala en una máquina de otro inventario: la suma al grupo que espera el playbook
                preparar_en_su_inventario(aap, a, inv_id, (t["limite"] or "").split(":"))
            if requiere_aprobacion(a, amb):
                # el flujo lleva fijas la credencial del inventario: uno por inventario y credencial (Linux y Windows del mismo centro no se mezclan)
                suf = inv_n + (f" · credencial {'-'.join(str(x) for x in creds)}" if creds else "")
                tipo, id_ = "workflow", aap.asegurar_flujo_aprobacion(a, inventario=inv_id, credenciales=list(creds), sufijo=suf)
                job = aap.lanzar(tipo, id_, extra_vars=params, limite=t["limite"] or None)
            else:
                tipo, id_ = "job", a["aap_id"]
                job = aap.lanzar(tipo, id_, extra_vars=params, limite=t["limite"] or None, inventario=inv_id, credenciales=list(creds))
            ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, aap_job_tipo=?, inicio=? WHERE id=?", job, tipo, ahora(), ticket_id)
            return
        if a["nombre_aap"] == APAGAR:  # siempre con aprobación; inventario y credencial del equipo, fijos en el nodo
            e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", c["id"], t["limite"])
            inv_id, inv_n, creds = acceso_equipo(aap, c, e)
            tipo, id_ = "workflow", aap.asegurar_flujo_aprobacion(a, inventario=inv_id, credenciales=creds, sufijo=inv_n + (f" · credencial {'-'.join(str(x) for x in creds)}" if creds else ""))
        else:
            tipo, id_ = objetivo_aap(aap, a, amb)
        job = aap.lanzar(tipo, id_, extra_vars=params, limite=t["limite"] or None)
        ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, aap_job_tipo=?, inicio=? WHERE id=?", job, tipo, ahora(), ticket_id)
    except ErrorAAP as e:
        ej("UPDATE tickets SET estado='fallido', resultado=?, fin=? WHERE id=?", str(e)[:1000], ahora(), ticket_id)
        conector(c).cerrar(q1("SELECT * FROM tickets WHERE id=?", ticket_id), a)


def resolver_aprobacion(usuario, ticket_id, aprobar, comentario=""):
    """Responde la etapa pendiente. Si vive en AAP, se responde allí (queda registrada en AAP); si el ticket quedó sin
    aprobación viva en AAP (p. ej. una que antes venció), FaceOps lleva las etapas y, aprobada la última, lo lanza."""
    from .aprobacion_areas import actual, area_usuario, avanzar, cerrar
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    if t["estado"] != "pendiente_aprobacion":
        return
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"]) or {}
    et = actual(t, a, area_usuario(usuario, t["cliente_id"]))
    if et:
        cerrar(t["id"], et["etapa"], "aprobada" if aprobar else "rechazada", usuario["usuario"])
    ap_id = (et or {}).get("aap_aprobacion_id") or t["aap_aprobacion_id"]
    if ap_id:
        c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
        try:
            AAP.de_cliente(c).responder_aprobacion(ap_id, aprobar)
        except ErrorAAP as x:   # ya respondida (otro clic, o en AAP): no es un error para quien aprueba
            if "already been approved or denied" not in str(x):
                raise
        ej("UPDATE tickets SET aprobado_por=?, respuesta=?, aprobado_en=? WHERE id=?", usuario["id"], comentario,
           f"FaceOps ({usuario['usuario']}) → AAP", ticket_id)
        # sin etapas pendientes, el ticket sale de «esperando aprobación» de inmediato (no espera la próxima lectura de AAP)
        quedan = q1("SELECT COUNT(*) n FROM ticket_etapas WHERE ticket_id=? AND estado='pendiente'", ticket_id)["n"]
        if not aprobar:
            ej("UPDATE tickets SET estado='rechazado', fin=? WHERE id=? AND estado='pendiente_aprobacion'", ahora(), ticket_id)
        elif not quedan:
            ej("UPDATE tickets SET estado='en_ejecucion' WHERE id=? AND estado='pendiente_aprobacion'", ticket_id)
    elif not aprobar:
        ej("UPDATE tickets SET aprobado_por=?, respuesta=?, aprobado_en=?, estado='rechazado', fin=? WHERE id=?", usuario["id"], comentario,
           f"FaceOps ({usuario['usuario']})", ahora(), ticket_id)
        from .deuda import cerrar_ticket as cerrar_deuda
        cerrar_deuda(t, "rechazado")
    elif avanzar(t, a):   # aprobadas todas las etapas: se lanza sin pedir otra aprobación
        ej("UPDATE tickets SET aprobado_por=?, respuesta=?, aprobado_en=?, estado='en_cola' WHERE id=?", usuario["id"], comentario,
           f"FaceOps ({usuario['usuario']})", ticket_id)
        from .deuda import cerrar_ticket as cerrar_deuda
        cerrar_deuda(t, "aprobado")
        threading.Thread(target=ejecutar, args=(ticket_id, None, True), daemon=True).start()
    auditar(usuario["usuario"], t["cliente_id"], "aprobar" if aprobar else "rechazar", t["numero"],
            {"comentario": comentario, "etapa": (et or {}).get("etapa"), "area": (et or {}).get("area"), "aprobacion_aap": t["aap_aprobacion_id"]})


ESTADOS_AAP = {"successful": "exitoso", "failed": "fallido", "error": "fallido", "canceled": "cancelado"}


def _revisar_aprobaciones(aap, t):
    """Lee en AAP los nodos de aprobación del workflow (una etapa por área). Registra el inicio y el fin de cada etapa para
    acumular su tiempo al área que la tuvo. Devuelve 'rechazado' | 'sin_aap' (una aprobación antigua que venció en AAP:
    el ticket sigue pendiente en FaceOps) | None."""
    from .aprobacion_areas import NOMBRES, cerrar, etapas_de, registrar
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"]) or {}
    cad = etapas_de(a)
    por_nombre = {v.lower(): k for k, v in NOMBRES.items()}
    nodos = [jb for jb in aap.nodos_trabajo_workflow(t["aap_job_id"]) if jb.get("type") == "workflow_approval" and jb.get("id")]

    def orden(jb):
        m = re.match(r"Etapa (\d+)/", jb.get("name") or "")
        return (int(m.group(1)) if m else 999, jb["id"])
    nodos.sort(key=orden)
    total = max(len(cad), len(nodos)) or 1
    ya = {x["etapa"]: x for x in q("SELECT * FROM ticket_etapas WHERE ticket_id=?", t["id"])}
    final = None
    pendiente = None
    for k, job in enumerate(nodos, 1):
        m = re.match(r"Etapa (\d+)/\d+ · ([^·]+) ·", job.get("name") or "")
        gr = int(m.group(1)) if m else k
        area = por_nombre.get((m.group(2).strip().lower() if m else ""), cad[min(k, len(cad)) - 1] if cad else "")
        if job.get("status") == "pending":
            ini = None
            if k not in ya:
                try:
                    ini = aap.aprobacion(job["id"]).get("created")
                except ErrorAAP:
                    ini = None
            registrar(t["id"], k, total, area, ini, job["id"], gr)
            pendiente = pendiente or job
            continue
        try:
            ap = aap.aprobacion(job["id"])
        except ErrorAAP:
            continue
        quien = ((ap.get("summary_fields") or {}).get("approved_or_denied_by") or {}).get("username", "")
        if k not in ya:
            registrar(t["id"], k, total, area, ap.get("created"), job["id"], gr)
        if ap.get("timed_out"):
            final = "sin_aap"
            break
        cerrar(t["id"], k, "rechazada" if job.get("status") == "failed" else "aprobada", quien, ap.get("finished"))
        if job.get("status") == "failed":
            final = "rechazado"
    if pendiente and not final:
        if t["estado"] != "pendiente_aprobacion" or t["aap_aprobacion_id"] != pendiente["id"]:
            ej("UPDATE tickets SET estado='pendiente_aprobacion', aap_aprobacion_id=? WHERE id=?", pendiente["id"], t["id"])
            auditar("AAP", t["cliente_id"], "espera aprobación en AAP", t["numero"], {"aprobacion": pendiente.get("name"), "id": pendiente["id"]})
            from .deuda import abrir as abrir_deuda
            abrir_deuda(t, "operación")
        return None
    if t["estado"] == "pendiente_aprobacion" and not final and nodos:
        ej("UPDATE tickets SET estado='en_ejecucion' WHERE id=?", t["id"])
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


def marcar_registradas(cliente_id, nombres):
    """Tras registrar en Red Hat: la ficha deja de pedir el registro sin esperar la próxima recolección."""
    for n in [x for x in nombres if x]:
        e = q1("SELECT id, datos, so FROM equipos WHERE cliente_id=? AND nombre=?", cliente_id, n)
        if e and (e["so"] or "").lower().startswith(("redhat", "red hat")):
            d = j(e["datos"], {})
            d["registrado"] = True
            ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), e["id"])


def _motivo_falla(aap, tipo, id_):
    """«Falló en la tarea X de <equipo>: <mensaje>» + las últimas líneas de la salida del trabajo que falló."""
    import re as _re
    try:
        ids = [id_] if tipo == "job" else [((n.get("summary_fields") or {}).get("job") or {}).get("id")
                                           for n in aap.todos(f"workflow_jobs/{id_}/workflow_nodes/")
                                           if ((n.get("summary_fields") or {}).get("job") or {}).get("type") == "job"
                                           and ((n.get("summary_fields") or {}).get("job") or {}).get("status") in ("failed", "error")]
        for jid in [x for x in ids if x]:
            txt = aap.salida(jid, 400)
            lineas = txt.splitlines()
            for k, l in enumerate(lineas):
                m = _re.match(r"(?:fatal|failed): \[([^\]]+)\].*?=> (\{.*)", l)
                if m:
                    tarea = next((_re.sub(r"^TASK \[(.*)\].*", r"\1", x) for x in reversed(lineas[:k]) if x.startswith("TASK [")), "")
                    try:
                        d = json.loads(m.group(2))
                        msg = "; ".join(d.get("failures") or []) or d.get("msg") or ""
                    except ValueError:
                        msg = m.group(2)[:300]
                    if "UNREACHABLE" in l or "unreachable" in m.group(2):
                        msg = "no se pudo conectar con el equipo (apagado o sin red): " + msg
                    return f"\nFalló en la tarea «{tarea}» de {m.group(1)}: {str(msg)[:400]}\n" + "\n".join(lineas[-15:])
            return "\n" + "\n".join(lineas[-15:])
    except Exception:  # noqa: BLE001
        pass
    return ""


def actualizar_tickets():
    try:
        aprobar_vinculaciones()
    except Exception:  # noqa: BLE001
        pass
    # etapas de aprobación que quedaron abiertas en tickets que ya no esperan aprobación (cancelados, rechazados, terminados)
    ej("UPDATE ticket_etapas SET estado='cancelada', fin=? WHERE estado='pendiente' AND ticket_id IN "
       "(SELECT id FROM tickets WHERE estado NOT IN ('pendiente_aprobacion','en_cola','en_ejecucion'))", ahora())
    # tickets cuyo registro en ServiceNow quedó pendiente (instancia caída o en hibernación): se crean cuando vuelve
    for c_ in q("SELECT * FROM clientes WHERE tickets_tipo='servicenow'"):
        pend = q("SELECT * FROM tickets WHERE cliente_id=? AND externo_ref='pendiente' ORDER BY id LIMIT 20", c_["id"])
        if pend and estado_servicenow(c_).get("ok"):
            con_ = conector(c_)
            for t_ in pend:
                a_ = q1("SELECT * FROM automatizaciones WHERE id=?", t_["automatizacion_id"])
                u_ = q1("SELECT * FROM usuarios WHERE id=?", t_["usuario_id"]) or {"nombre": "FaceOps", "usuario": "faceops"}
                if not a_:
                    continue
                ej("UPDATE tickets SET externo_ref='' WHERE id=?", t_["id"])
                con_.crear(t_, a_, u_)
                t2 = q1("SELECT * FROM tickets WHERE id=?", t_["id"])
                if t2["estado"] not in ("pendiente_aprobacion", "en_cola", "en_ejecucion") and t2["externo_ref"] not in ("", "pendiente"):
                    con_.cerrar(t2, a_)
    for c_ in q("SELECT * FROM clientes WHERE tickets_conf LIKE '%\"odoo\"%'"):   # tickets que Odoo no recibió: se reintentan
        o_ = Odoo(c_)
        if not o_.o.get("url"):
            continue
        if time.time() - _ODOO_VISTO.get(c_["id"], 0) > 30:   # pedidos nuevos en Odoo (etiqueta FaceOps), cada 30 s
            _ODOO_VISTO[c_["id"]] = time.time()
            odoo_entrantes(c_)
        for t_ in q("SELECT * FROM tickets WHERE cliente_id=? AND odoo_ref='pendiente' ORDER BY id LIMIT 10", c_["id"]):
            a_ = q1("SELECT * FROM automatizaciones WHERE id=?", t_["automatizacion_id"])
            u_ = q1("SELECT * FROM usuarios WHERE id=?", t_["usuario_id"]) or {"nombre": "FaceOps", "usuario": "faceops"}
            if a_:
                o_.crear(t_, a_, u_)
                t2 = q1("SELECT * FROM tickets WHERE id=?", t_["id"])
                if t2["estado"] not in ("pendiente_aprobacion", "en_cola", "en_ejecucion"):
                    o_.cerrar(t2, a_)
    from datetime import datetime as _dt, timedelta, timezone as _tz
    tope_ = (_dt.now(_tz.utc) - timedelta(minutes=15)).isoformat(timespec="seconds")
    for t in q("SELECT * FROM tickets WHERE estado IN ('en_cola','en_ejecucion') AND aap_job_id IS NULL AND creado < ?", tope_):
        ej("UPDATE tickets SET estado='fallido', fin=?, resultado=? WHERE id=?", ahora(),
           "No llegó a ejecutarse en AAP (no se pudo lanzar en 15 min): vuelve a solicitarlo; FaceOps no deja tickets pegados", t["id"])
    for t in q("SELECT * FROM tickets WHERE estado IN ('en_ejecucion','pendiente_aprobacion') AND aap_job_id IS NOT NULL"):
        c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
        try:
            aap = AAP.de_cliente(c)
            negado = _revisar_aprobaciones(aap, t) if t["aap_job_tipo"] == "workflow" else None
            r = aap.trabajo(t["aap_job_tipo"], t["aap_job_id"])
        except ErrorAAP:
            continue
        if negado == "sin_aap":   # la aprobación venció en AAP (flujos antiguos): en FaceOps no vence, sigue pendiente
            ej("UPDATE tickets SET estado='pendiente_aprobacion', aap_aprobacion_id=NULL, aap_job_id=NULL, "
               "resultado=? WHERE id=?", f"La aprobación del trabajo #{t['aap_job_id']} venció en AAP; sigue pendiente en FaceOps", t["id"])
            continue
        if t["estado"] == "en_ejecucion" and r.get("status") == "running" and not negado:
            try:   # vigía: un ticket solo puede detenerse por una aprobación; si la ejecución dejó de avanzar, se corta
                from .avance import SIN_AVANCE_CORTE_S, vigilancia
                vg = vigilancia(aap, t)
            except Exception:  # noqa: BLE001
                vg = {}
            if vg.get("cortar"):
                try:
                    aap.post(f"{'workflow_jobs' if t['aap_job_tipo'] == 'workflow' else 'jobs'}/{t['aap_job_id']}/cancel/")
                except ErrorAAP:
                    pass
                motivo_ = (f"FaceOps detuvo la ejecución: sin avance durante más de {SIN_AVANCE_CORTE_S // 60} min en la tarea "
                           f"«{vg.get('tarea') or vg.get('trabajo') or '?'}»" + (f" de {vg['host']}" if vg.get("host") else "")
                           + ". El equipo puede estar colgado o sin respuesta; revisa y vuelve a solicitar.")
                ej("UPDATE tickets SET estado='fallido', fin=?, duracion_s=?, resultado=? WHERE id=?", ahora(), _segundos(t["creado"], ahora()), motivo_, t["id"])
                auditar("FaceOps", t["cliente_id"], "ticket detenido por falta de avance", t["numero"], vg)
                continue
        if r.get("status") in ESTADOS_AAP:
            est = negado or ESTADOS_AAP[r["status"]]
            fin = ahora()
            dur = _segundos(t["creado"], fin) or int(r.get("elapsed") or 0)  # total: desde que se pidió hasta que terminó
            try:
                d_ejec, d_aprob = tiempos(aap, t, r if t["aap_job_tipo"] == "job" else None)
            except ErrorAAP:
                d_ejec, d_aprob = int(r.get("elapsed") or 0), None
            res = f"AAP terminó ({r['status']}): ejecución {d_ejec} s" + (f", espera de aprobación {d_aprob} s" if d_aprob else "") + {"rechazado": " · la aprobación fue rechazada"}.get(negado or "", "")
            if est == "fallido":
                res += _motivo_falla(aap, t["aap_job_tipo"], t["aap_job_id"])
            ej("UPDATE tickets SET estado=?, fin=?, duracion_s=?, dur_ejecucion_s=?, dur_aprobacion_s=?, resultado=? WHERE id=?",
               est, fin, dur, d_ejec, d_aprob, res, t["id"])
            from .deuda import cerrar_ticket as cerrar_deuda
            cerrar_deuda(t, est)
            a_ = q1("SELECT nombre_aap FROM automatizaciones WHERE id=?", t["automatizacion_id"]) or {}
            from .evaluacion_so import AUTOS as AUTOS_SO, guardar as guardar_eval_so
            if est in ("exitoso", "fallido") and a_.get("nombre_aap") == "Seguridad · Escaneo de servidores":
                try:   # escaneo de seguridad: hallazgos por servidor (aunque algún servidor no responda)
                    from .escaneo import registrar as registrar_escaneo
                    registrar_escaneo(t["cliente_id"], t, artefactos_ticket(aap, t), est)
                except Exception as x:  # noqa: BLE001
                    print("Escaneo:", str(x)[:200])
            if est in ("exitoso", "fallido") and a_.get("nombre_aap") == "DevSecOps · Pipeline Tekton":   # también si el pipeline falló
                try:   # FaceOps guarda las ejecuciones del pipeline que leyó AAP (se ven al final del pipeline)
                    from .devsecops import registrar_corridas
                    registrar_corridas(t["cliente_id"], artefactos_ticket(aap, t).get("faceops_pipeline") or {}, t["id"])
                except Exception as x:  # noqa: BLE001
                    print("DevSecOps:", str(x)[:200])
            if est == "exitoso" and a_.get("nombre_aap") == "Infra · Encender o apagar equipo":
                try:   # el inventario refleja el cambio sin esperar el próximo sondeo
                    en = artefactos_ticket(aap, t).get("faceops_energia") or {}
                    p_ = j(t.get("parametros"), {})
                    nombre_ = en.get("equipo") or p_.get("en_equipo")
                    ahora_ = en.get("ahora") or ("shut off" if p_.get("en_accion") == "apagar" else "running")
                    if nombre_:
                        ej("UPDATE equipos SET energia=?, energia_visto=?, energia_origen='ticket' WHERE cliente_id=? AND nombre=?",
                           "apagado" if "shut" in ahora_ else "encendido", ahora(), t["cliente_id"], nombre_)
                except Exception as x:  # noqa: BLE001
                    print("Energía:", str(x)[:200])
            if est == "exitoso" and a_.get("nombre_aap") in ("Sistema · Paquetes · inventario y respaldo", "Sistema · Inventariar máquina"):
                try:   # FaceOps lee desde AAP lo que trajo la automatización (no se conecta a la máquina)
                    from .inventariado import guardar_paquetes, tras_inventariar
                    art_ = artefactos_ticket(aap, t)
                    if a_["nombre_aap"] == "Sistema · Inventariar máquina":
                        n_paq, n_sub = tras_inventariar(aap, c, t, art_)
                    else:
                        n_paq, n_sub = guardar_paquetes(t["cliente_id"], t, art_.get("faceops_paquetes") or {})
                    ej("UPDATE tickets SET resultado=resultado || ? WHERE id=?",
                       f"\nPaquetes: {n_paq} inventariados · {n_sub} RPM subidos al registro propio", t["id"])
                except Exception as x:  # noqa: BLE001
                    print("Inventario de la máquina:", str(x)[:200])
            if est == "exitoso" and a_.get("nombre_aap") == "Certificados · Renovar por vencer":
                try:   # que el ticket diga qué renovó (o que no había nada gestionado por FaceOps en esa máquina)
                    art_ = (artefactos_ticket(aap, t).get("faceops_certificados") or {})
                    ren_ = {eq_: list((d_.get("renovados") or {}).keys()) for eq_, d_ in art_.items()}
                    nota_ = "; ".join(f"{eq_}: {', '.join(v_)}" for eq_, v_ in ren_.items() if v_) or \
                        "no había certificados gestionados por FaceOps en la máquina: no se renovó nada (usa «Emitir para servicio»)"
                    ej("UPDATE tickets SET resultado=resultado || ? WHERE id=?", "\nCertificados renovados: " + nota_, t["id"])
                except Exception as x:  # noqa: BLE001
                    print("Renovar:", str(x)[:200])
            if est == "exitoso" and a_.get("nombre_aap") in ("Certificados · Emitir para servicio", "Certificados · Renovar por vencer"):
                try:   # el certificado nuevo aparece en la lista: lectura nueva del inventario de certificados en AAP
                    from .certificados_panel import INVENTARIO, _cache
                    jt_ = (aap.get("job_templates/", name=INVENTARIO).get("results") or [None])[0]
                    if jt_:
                        aap.post(f"job_templates/{jt_['id']}/launch/", {})
                    _cache.clear()
                except Exception as x:  # noqa: BLE001
                    print("Certificados:", str(x)[:200])
            if est == "exitoso" and a_.get("nombre_aap") == "DC · Registrar en Red Hat":
                marcar_registradas(t["cliente_id"], (t["limite"] or "").split(":"))
            if est == "exitoso" and a_.get("nombre_aap") == "Plataforma · Desinstalar":
                try:   # la plataforma desinstalada deja de figurar en la máquina sin esperar la próxima recolección
                    fam_ = j(t.get("parametros"), {}).get("dp_familia") or ""
                    sk_ = {"postgresql": "postgres"}.get(fam_, fam_)
                    e_ = q1("SELECT id, skills_auto, skills_manual FROM equipos WHERE cliente_id=? AND nombre=?", t["cliente_id"], t["limite"])
                    if e_ and sk_:
                        ej("UPDATE equipos SET skills_auto=?, skills_manual=? WHERE id=?",
                           json.dumps([x for x in j(e_["skills_auto"], []) if x != sk_]), json.dumps([x for x in j(e_["skills_manual"], []) if x != sk_]), e_["id"])
                except Exception as x:  # noqa: BLE001
                    print("Desinstalar:", str(x)[:200])
            if est == "exitoso" and a_.get("nombre_aap") == "Arquitectura · Evaluar stack tecnológico":
                try:   # informe de obsolescencia del stack tecnológico
                    from .stack import guardar as guardar_stack
                    guardar_stack(t, artefactos_ticket(aap, t))
                except Exception as x:  # noqa: BLE001
                    print("Evaluación del stack:", str(x)[:200])
            if est == "exitoso" and a_.get("nombre_aap") in AUTOS_SO:
                try:   # la última evaluación del sistema operativo queda a la mano en cada equipo
                    guardar_eval_so(t, artefactos_ticket(aap, t))
                except ErrorAAP:
                    pass
                if a_.get("nombre_aap") == "Migración · Evaluar sistema operativo":
                    try:   # informe PDF de impacto, a quien la pidió
                        from .informe_so import desde_ticket as informe_desde_ticket
                        informe_desde_ticket(t)
                    except Exception as x:  # noqa: BLE001 - sin informe la evaluación igual queda en la ficha
                        print("Informe de migración:", str(x)[:200])
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
            if est == "exitoso" and a and a["nombre_aap"] in (HABILITAR, REPLICAR, "Satellite · Instalar servidor", "VPN · Preparar servidor"):
                try:
                    alta_ = registrar_servidores(aap, c, t)
                    res += "\n" + alta_
                    ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                    vincular_creados(c, t, alta_)   # los servidores que crea FaceOps quedan vinculados a la bóveda sin otro paso
                    if a["nombre_aap"] == "Satellite · Instalar servidor":   # la plataforma queda marcada en el servidor creado
                        registrar_skill(dict(t, limite=j(t["parametros"], {}).get("hab_nombre_exacto") or "dc1-sat01"),
                                        dict(a, habilita_skill="satellite", campos="[]"), {})
                    if a["nombre_aap"] == "VPN · Preparar servidor":
                        registrar_skill(dict(t, limite=j(t["parametros"], {}).get("hab_nombre_exacto") or "dc1-vpn01"),
                                        dict(a, habilita_skill="vpn", campos="[]"), {})
                except ErrorAAP as x:
                    auditar("sistema", c["id"], "error alta de servidores", t["numero"], {"error": str(x)[:300]})
            if est == "exitoso" and a and a["nombre_aap"] == VINCULAR and (t["motivo"] or "").startswith(MOTIVO_VINCULAR):
                inventariar_vinculado(c, t)
            if est in ("exitoso", "fallido") and a and a["nombre_aap"] in ("VPN · Preparar servidor", "VPN · Gestionar acceso"):
                try:   # estado de la VPN (clientes y usuarios, sin claves privadas) en la ficha del servidor
                    vpn_ = (artefactos_ticket(aap, t) or {}).get("faceops_vpn") or {}
                    e_ = q1("SELECT id, datos FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", c["id"], vpn_.get("equipo") or "")
                    if e_:
                        d_ = j(e_["datos"], {})
                        d_["vpn"] = {k: v for k, v in vpn_.items() if k in ("endpoint", "puerto", "red", "clientes", "usuarios", "publica", "aplicado")}
                        d_["vpn"]["actualizado"] = ahora()
                        ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d_, ensure_ascii=False), e_["id"])
                except ErrorAAP:
                    pass
            if est == "exitoso" and a and a["nombre_aap"] == "Infra · Mover máquina entre centros de datos":
                try:   # la máquina queda en su centro de datos nuevo: IP y ubicación en AAP y en FaceOps
                    mv_ = (artefactos_ticket(aap, t) or {}).get("faceops_traslado") or {}
                    trasladado(c, aap, mv_)
                except Exception as x:  # noqa: BLE001
                    auditar("sistema", c["id"], "error traslado", t["numero"], {"error": str(x)[:300]})
            if est == "exitoso" and a and a["nombre_aap"] in ("Satellite · Incorporar RHEL", "Satellite · Convertir a RHEL"):   # quedan marcadas como gestionadas por ese Satellite
                sat_ = j(t["parametros"], {}).get("sat_equipo") or ""
                for n_ in (t["limite"] or "").split(":"):
                    e_ = q1("SELECT id, datos FROM equipos WHERE cliente_id=? AND nombre=?", c["id"], n_)
                    if e_ and sat_:
                        d_ = j(e_["datos"], {})
                        d_["satellite"] = {"servidor": sat_, "ticket": t["numero"], "fecha": ahora()}
                        ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d_, ensure_ascii=False), e_["id"])
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
            if est == "exitoso" and a and a["nombre_aap"] == "OpenShift · Recolectar clúster":
                try:   # el clúster, sus nodos, operadores y namespaces quedan al día en FaceOps
                    from .inventario import sincronizar
                    sincronizar(c, "FaceOps")
                    res += "\nInventario del clúster actualizado en FaceOps (nodos, operadores, namespaces y respaldos)"
                    ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                except Exception as x:  # noqa: BLE001
                    auditar("sistema", c["id"], "error recolección de clúster", t["numero"], {"error": str(x)[:300]})
            if a and a["nombre_aap"] == "OpenShift · Informe y ficha" and est == "exitoso" and j(t["parametros"], {}).get("oi_programar") == "si":
                try:   # informe diario programado en AAP para ese clúster (07:00 de Chile), sin repetir la programación
                    nom_s = f"Informe diario · {t['limite']}"
                    if not (aap.get(f"job_templates/{a['aap_id']}/schedules/", name=nom_s).get("results") or []):
                        aap.post(f"job_templates/{a['aap_id']}/schedules/", {
                            "name": nom_s, "rrule": "DTSTART;TZID=America/Santiago:20261103T070000 RRULE:FREQ=DAILY;INTERVAL=1",
                            "extra_data": {"oi_tipo": "informe"}, "limit": t["limite"], "description": f"Programado desde {t['numero']}"})
                        ej("UPDATE tickets SET resultado=? WHERE id=?", (t.get("resultado") or "") + f"\nProgramado en AAP: todos los días a las 07:00 (Chile) para {t['limite']}", t["id"])
                except Exception as x:  # noqa: BLE001
                    auditar("sistema", c["id"], "error al programar informe", t["numero"], {"error": str(x)[:300]})
            if a and a["nombre_aap"] == OCP_PREPARAR and est in ("exitoso", "fallido"):
                try:   # el manifiesto quedó en Git (listo o no) con el detalle de cada prerrequisito
                    m = next((x for x in [artefactos_ticket(aap, t).get("faceops_manifiesto_ocp")] if x), None)
                    if m:
                        malos = [f"✗ {x['item']}: {x['detalle']}" for x in m.get("chequeos") or [] if not x.get("ok")]
                        res += (f"\nManifiesto de {m['cluster']} (OpenShift {m.get('version')}, {m.get('arquitectura')}, {m.get('nodos')} nodos): "
                                + ("LISTO para instalar" if str(m.get("listo")) in ("True", "true") else "NO listo") + f" · {m.get('git')}"
                                + ("\n" + "\n".join(malos) if malos else "") + (" · vCenter simulado" if str(m.get("simulado")) in ("True", "true") else ""))
                        ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
                except Exception as x:  # noqa: BLE001
                    auditar("sistema", c["id"], "error manifiesto OpenShift", t["numero"], {"error": str(x)[:300]})
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


def _alta_vmware(aap, cliente, t, nombres):
    """Sincroniza en AAP el inventario de la infraestructura VMware y trae a FaceOps las VM nuevas (sin vincular, sin segmento)."""
    from .inventario import sincronizar
    inf = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre=?", cliente["id"], j(t["parametros"], {}).get("hab_infraestructura", "")) \
        or q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo='vmware'", cliente["id"])
    inv = (aap.get("inventories/", name=(inf or {}).get("inventario") or "").get("results") or [None])[0] if inf else None
    if inv:
        for fuente in aap.get("inventory_sources/", inventory=inv["id"]).get("results", []):
            u = aap.post(f"inventory_sources/{fuente['id']}/update/")
            for _ in range(30):
                if aap.get(f"inventory_updates/{u.get('inventory_update') or u.get('id')}/").get("status") in ("successful", "failed", "error", "canceled"):
                    break
                time.sleep(4)
    sincronizar(cliente, "FaceOps")
    auditar("FaceOps", cliente["id"], "servidores habilitados", t["numero"], {"servidores": nombres, "infraestructura": (inf or {}).get("nombre")})
    return f"Creadas en VMware vSphere: {', '.join(nombres)} · quedan en el inventario sin vincular y sin segmento"


def artefactos_ticket(aap, t):
    """Artefactos (set_stats) del trabajo del ticket; si va dentro del flujo de aprobación, los de todos sus trabajos."""
    jobs = [t["aap_job_id"]] if t["aap_job_tipo"] == "job" else \
        [n["id"] for n in aap.nodos_trabajo_workflow(t["aap_job_id"]) if n.get("type") == "job" and n.get("id")]
    art = {}
    for jid in jobs:
        try:
            art.update(aap.get(f"jobs/{jid}/").get("artifacts") or {})
        except ErrorAAP:
            pass
    return art


VINCULAR = "FaceOps · Vincular máquina a la bóveda"
MOTIVO_VINCULAR = "Vinculación automática a la bóveda · servidor creado en "


def vincular_creados(cliente, t, texto):
    """Tras crear servidores (ticket ya aprobado): un ticket de vinculación por servidor Linux, que se aprueba solo
    porque la creación ya fue aprobada (queda la referencia al ticket de origen). texto: lo que devolvió registrar_servidores."""
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], VINCULAR)
    u = q1("SELECT * FROM usuarios WHERE id=?", t["usuario_id"])
    if not a or not u or str(j(t["parametros"], {}).get("hab_so", "")).startswith("windows"):
        return
    for nombre in dict.fromkeys(re.findall(r"([A-Za-z0-9][A-Za-z0-9._-]*) · \d+\.\d+\.\d+\.\d+", texto or "")):
        e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], nombre)
        if not e or q1("SELECT 1 FROM tickets WHERE cliente_id=? AND automatizacion_id=? AND limite=? AND estado IN "
                       "('exitoso','en_cola','en_ejecucion','pendiente_aprobacion')", cliente["id"], a["id"], nombre):
            continue
        try:
            solicitar_en_equipo(u, cliente, a, dict(e), {"vm_usuario": "faceops", "vm_tipo": "linux"}, MOTIVO_VINCULAR + t["numero"])
        except Exception as x:  # noqa: BLE001
            auditar("sistema", cliente["id"], "vinculación automática no lanzada", t["numero"], {"equipo": nombre, "error": str(x)[:200]})


MOTIVO_INVENTARIO = "Inventario automático · servidor creado en "


def inventariar_vinculado(cliente, t):
    """Vinculación automática terminada: el servidor se inventaría solo (así su plataforma y su ficha quedan completas)."""
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], INVENTARIAR)
    u = q1("SELECT * FROM usuarios WHERE id=?", t["usuario_id"])
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], t["limite"] or "")
    if not a or not u or not e or q1("SELECT 1 FROM tickets WHERE cliente_id=? AND automatizacion_id=? AND limite=? AND estado IN "
                                     "('en_cola','en_ejecucion','pendiente_aprobacion')", cliente["id"], a["id"], e["nombre"]):
        return
    try:
        solicitar_en_equipo(u, cliente, a, dict(e), {}, MOTIVO_INVENTARIO + t["motivo"][len(MOTIVO_VINCULAR):].strip())
    except Exception as x:  # noqa: BLE001
        auditar("sistema", cliente["id"], "inventario automático no lanzado", t["numero"], {"equipo": e["nombre"], "error": str(x)[:200]})


def aprobar_vinculaciones():
    """La vinculación y el inventario automáticos no piden otra aprobación: la creación del servidor ya fue aprobada."""
    for t in q("SELECT * FROM tickets WHERE estado='pendiente_aprobacion' AND (motivo LIKE ? OR motivo LIKE ?)", MOTIVO_VINCULAR + "%", MOTIVO_INVENTARIO + "%"):
        pref_ = MOTIVO_VINCULAR if t["motivo"].startswith(MOTIVO_VINCULAR) else MOTIVO_INVENTARIO
        origen = t["motivo"][len(pref_):].strip()
        o = q1("SELECT * FROM tickets WHERE cliente_id=? AND numero=? AND estado='exitoso'", t["cliente_id"], origen)
        u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 ORDER BY id LIMIT 1")
        if o and u:
            try:
                resolver_aprobacion(u, t["id"], True, f"Aprobado con la creación del servidor ({origen})")
            except Exception:  # noqa: BLE001
                pass


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
    if art.get("faceops_destino") == "vmware":   # las VM del vCenter llegan por la fuente de inventario de su infraestructura
        return _alta_vmware(aap, cliente, t, [c_[0] for c_ in creados])
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
              "faceops_ubicacion": j(t["parametros"], {}).get("hab_ubicacion") or ("Cloud GCP" if destino == "gcp" else "Centro de datos Santiago"),
              "faceops_ticket": t["numero"]}
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
        ultimo_dia, ultima_hora = None, None
        while True:
            try:
                actualizar_tickets()
                from .topologia import actualizar_flujos
                actualizar_flujos()
                hoy = datetime.now()
                if hoy.minute >= 5 and ultima_hora != (hoy.date(), hoy.hour):   # uso por segmento, cada hora
                    ultima_hora = (hoy.date(), hoy.hour)
                    from .uso import lanzar as lanzar_uso
                    for c in q("SELECT * FROM clientes WHERE activo=1 AND aap_url!=''"):
                        try:
                            lanzar_uso(c)
                        except Exception as e:  # noqa: BLE001
                            auditar("sistema", c["id"], "error uso por segmento", "inventario", {"error": str(e)[:300]})
                from .uso import recoger as recoger_uso
                for c in q("SELECT * FROM clientes WHERE activo=1 AND aap_url!=''"):
                    try:
                        recoger_uso(c)
                    except Exception:  # noqa: BLE001
                        pass
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
    from .puntos import hilo as hilo_puntos
    hilo_puntos()


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


_SN_ESTADO = {}


def estado_servicenow(cliente):
    """{'ok': bool, 'detalle': str} de la instancia de ServiceNow del cliente (caché de 3 min)."""
    import time as _t
    k = cliente["id"]
    if k in _SN_ESTADO and _t.time() - _SN_ESTADO[k][0] < 180:
        return _SN_ESTADO[k][1]
    conf = j(cliente.get("tickets_conf"), {})
    if cliente.get("tickets_tipo") != "servicenow" or not conf.get("url"):
        r = {"ok": None, "detalle": "no configurado"}
    else:
        try:
            x = requests.get(conf["url"].rstrip("/") + "/api/now/table/sys_user", params={"sysparm_limit": 1, "sysparm_fields": "sys_id"},
                             auth=(conf.get("usuario"), conf.get("clave")), headers={"Accept": "application/json"}, timeout=6)
            if "Hibernating" in x.text[:2000]:
                r = {"ok": False, "detalle": "instancia en hibernación: despiértala en developer.servicenow.com"}
            elif x.status_code == 401:
                r = {"ok": False, "detalle": "usuario o clave rechazados"}
            elif x.ok and x.headers.get("Content-Type", "").startswith("application/json"):
                r = {"ok": True, "detalle": conf["url"].split("//")[-1].split(".")[0]}
            else:
                r = {"ok": False, "detalle": f"responde {x.status_code}"}
        except requests.RequestException:
            r = {"ok": False, "detalle": "no responde"}
    _SN_ESTADO[k] = (_t.time(), r)
    return r


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
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            est = estado_servicenow(self.c)
            auditar("sistema", t["cliente_id"], "error conector tickets", t["numero"], {"error": str(e)[:200], "servicenow": est.get("detalle", "")})
            if modo != "ritm":   # el incidente se crea cuando ServiceNow vuelva (lo reintenta actualizar_tickets)
                ej("UPDATE tickets SET externo_ref='pendiente' WHERE id=?", t["id"])
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


class Odoo(Conector):
    """Odoo Helpdesk (Enterprise), en paralelo a la herramienta principal del cliente: cada ticket de FaceOps abre un ticket
    en el equipo de Helpdesk configurado y, al terminar, lo resuelve (o lo deja en curso si falló) con el resultado.
    Configuración en el cliente: tickets_conf.odoo = {url, db, usuario, clave (clave de API), equipo}."""

    def __init__(self, cliente):
        super().__init__(cliente)
        self.o = self.conf.get("odoo") or {}
        self._uid = None

    def _rpc(self, servicio, metodo, *args):
        r = requests.post(self.o["url"].rstrip("/") + "/jsonrpc", timeout=30, json={
            "jsonrpc": "2.0", "method": "call", "id": 1, "params": {"service": servicio, "method": metodo, "args": list(args)}})
        r.raise_for_status()
        d = r.json()
        if d.get("error"):
            raise ValueError(((d["error"].get("data") or {}).get("message") or str(d["error"]))[:300])
        return d["result"]

    def _x(self, modelo, metodo, args, kw=None):
        if not self._uid:
            self._uid = self._rpc("common", "authenticate", self.o["db"], self.o["usuario"], self.o["clave"], {})
            if not self._uid:
                raise ValueError("Odoo rechazó el usuario o la clave de API")
        return self._rpc("object", "execute_kw", self.o["db"], self._uid, self.o["clave"], modelo, metodo, args, kw or {})

    def enlace(self, ref):
        if not ref or not str(ref).isdigit() or not self.o.get("url"):
            return None
        return f"{self.o['url'].rstrip('/')}/odoo/helpdesk/{ref}"

    def _etapa(self, nombres):
        for n in nombres:
            r = self._x("helpdesk.stage", "search", [[["name", "ilike", n]]], {"limit": 1})
            if r:
                return r[0]
        return None

    def crear(self, t, auto, usuario):
        from html import escape
        m_ = re.match(r"^Odoo #(\d+)", t.get("motivo") or "")
        if m_:   # el pedido nació en Odoo: se sigue en ese mismo ticket
            ej("UPDATE tickets SET odoo_ref=? WHERE id=?", m_.group(1), t["id"])
            return {}
        try:
            self._x("res.users", "read", [[1]], {"fields": ["id"]})   # autentica y deja self._uid
            tid = self._x("helpdesk.ticket", "create", [{
                "user_id": self._uid,          # asignado al usuario de integración: aparece en «Mis tickets» y en el desempeño
                "name": f"[FaceOps {t['numero']}] {auto['nombre']}" + (f" · {t['segmento']}" if t.get("segmento") else ""),
                "team_id": int(self.o.get("equipo") or 1),
                "priority": "1",
                "description": (f"<p>Solicitado por <b>{escape(usuario['nombre'])}</b> en FaceOps.</p><p>Motivo: {escape(t.get('motivo') or '—')}</p>"
                                f"<p>Automatización: {escape(auto['nombre_aap'])} ({escape(str(auto.get('version') or ''))})<br>"
                                f"Ámbito: {escape(t.get('limite') or auto.get('ambito') or '—')}</p>")}])
            ej("UPDATE tickets SET odoo_ref=? WHERE id=?", str(tid), t["id"])
            auditar("FaceOps", t["cliente_id"], "ticket en Odoo", t["numero"], {"odoo": tid})
        except (requests.RequestException, KeyError, ValueError) as e:
            ej("UPDATE tickets SET odoo_ref='pendiente' WHERE id=?", t["id"])
            auditar("sistema", t["cliente_id"], "error conector Odoo", t["numero"], {"error": str(e)[:200]})
        return {}

    def cerrar(self, t, auto):
        ref = t.get("odoo_ref") or ""
        if not ref.isdigit():
            return
        try:
            notas = f"FaceOps {t['numero']}: {t['estado']}. {(t.get('resultado') or '')[:800]}"
            self._x("helpdesk.ticket", "message_post", [[int(ref)]], {"body": notas.replace("\n", "<br>"), "message_type": "comment",
                                                                      "subtype_xmlid": "mail.mt_note"})
            et = (self._etapa(["Solved", "Resuelto"]) if t["estado"] == "exitoso" else
                  self._etapa(["Cancelled", "Cancelado"]) if t["estado"] in ("rechazado", "cancelado") else self._etapa(["In Progress", "En curso"]))
            if et:
                self._x("helpdesk.ticket", "write", [[int(ref)], {"stage_id": et}])
        except (requests.RequestException, KeyError, ValueError) as e:
            auditar("sistema", t["cliente_id"], "error conector Odoo", t["numero"], {"error": str(e)[:200]})


_ODOO_VISTO = {}


def odoo_entrantes(cliente):
    """Tickets creados en Odoo con la etiqueta «FaceOps»: el asistente lee el pedido (título y descripción), elige la
    automatización y sus datos y abre el ticket de FaceOps (con su aprobación). En Odoo queda una nota con el número y el
    enlace, y el ticket pasa a «En curso»; al terminar, FaceOps lo resuelve ahí mismo. Si falta información, deja una nota
    con lo que falta y lo vuelve a leer cuando cambie la descripción."""
    import hashlib
    from html import unescape
    from .asistente import planificar
    o = Odoo(cliente)
    if not o.o.get("url"):
        return
    ej("CREATE TABLE IF NOT EXISTS odoo_entrantes (cliente_id INTEGER, odoo_id INTEGER, huella TEXT, ticket_id INTEGER, estado TEXT, "
       "cuando TEXT, PRIMARY KEY(cliente_id, odoo_id))")
    try:
        tag = o._x("helpdesk.tag", "search", [[["name", "=ilike", "faceops"]]], {"limit": 1})
        if not tag:
            tag = [o._x("helpdesk.tag", "create", [{"name": "FaceOps"}])]
        cerr = o._x("helpdesk.stage", "search", [[["fold", "=", True]]])
        tks = o._x("helpdesk.ticket", "search_read", [[["tag_ids", "in", tag], ["stage_id", "not in", cerr], ["name", "not ilike", "[FaceOps "]]],
                   {"fields": ["id", "name", "description", "partner_email", "create_uid"], "limit": 20, "order": "id"})
    except (requests.RequestException, ValueError, KeyError) as e:
        auditar("sistema", cliente["id"], "error conector Odoo", "entrantes", {"error": str(e)[:200]})
        return
    if not tks:
        return
    equipos = q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cliente["id"])
    autos = q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1", cliente["id"])
    base = o.o.get("url", "").rstrip("/")
    for tk in tks:
        texto = (tk["name"] or "") + ". " + re.sub(r"<[^>]+>", " ", unescape(tk.get("description") or ""))
        huella = hashlib.sha256(texto.encode()).hexdigest()[:16]
        ya = q1("SELECT * FROM odoo_entrantes WHERE cliente_id=? AND odoo_id=?", cliente["id"], tk["id"])
        if ya and (ya["ticket_id"] or ya["huella"] == huella):
            continue
        plan = planificar(cliente, texto, equipos, autos)
        nota, tid = "", None
        if plan.get("error") or plan.get("faltan"):
            nota = ("FaceOps no pudo abrir la automatización: " + plan["error"] if plan.get("error") else
                    f"FaceOps entendió «{plan['auto']['nombre']}», pero falta: " + ", ".join(plan["faltan"])) + \
                ". Completa la descripción del ticket y FaceOps lo vuelve a leer."
            estado = "incompleto"
        else:
            u = None
            if tk.get("partner_email"):
                u = q1("SELECT * FROM usuarios WHERE lower(correo)=lower(?)", tk["partner_email"])
            u = u or q1("SELECT * FROM usuarios WHERE id=1")
            a, e = plan["auto"], plan["equipo"]
            motivo = f"Odoo #{tk['id']}: {tk['name'][:150]}"
            try:
                t = solicitar_en_equipo(u, cliente, a, e, plan["valores"], motivo) if (e and a["usa_limite"]) else \
                    solicitar(u, cliente, a, "", plan["valores"], motivo, equipo=e)
                tid, estado = t["id"], "creado"
                pub = os.environ.get("FACEOPS_URL_PUBLICA", "https://34.176.42.56:8843/faceops").rstrip("/")
                nota = (f"FaceOps abrió el ticket {t['numero']} «{a['nombre']}»" + (f" en {e['nombre']}" if e else "")
                        + (" (requiere aprobación)" if a.get("requiere_aprobacion") or t["estado"] == "pendiente_aprobacion" else "")
                        + f": {pub}/tickets/{t['id']} · Datos: " + ", ".join(f"{k}={v}" for k, v in plan["valores"].items()))
                try:
                    et = o._etapa(["In Progress", "En curso"])
                    if et:
                        o._x("helpdesk.ticket", "write", [[tk["id"]], {"stage_id": et}])
                except (requests.RequestException, ValueError):
                    pass
            except (PermissionError, ErrorAAP, ValueError) as x:
                nota, estado = f"FaceOps no pudo abrir el ticket: {x}", "error"
        try:
            o._x("helpdesk.ticket", "message_post", [[tk["id"]]], {"body": nota, "message_type": "comment", "subtype_xmlid": "mail.mt_note"})
        except (requests.RequestException, ValueError):
            pass
        ej("INSERT OR REPLACE INTO odoo_entrantes(cliente_id, odoo_id, huella, ticket_id, estado, cuando) VALUES (?,?,?,?,?,?)",
           cliente["id"], tk["id"], huella, tid, estado, ahora())
        auditar("FaceOps", cliente["id"], "pedido desde Odoo", f"Odoo #{tk['id']}", {"estado": estado, "ticket": tid})


class Varios(Conector):
    """La herramienta principal del cliente y además Odoo: crea y cierra en las dos a la vez."""

    def __init__(self, cliente, partes):
        super().__init__(cliente)
        self.partes = partes

    def crear(self, t, auto, usuario):
        extra = {}
        for p in self.partes:
            extra.update(p.crear(t, auto, usuario) or {})
        return extra

    def cerrar(self, t, auto):
        for p in self.partes:
            p.cerrar(q1("SELECT * FROM tickets WHERE id=?", t["id"]) or t, auto)

    def enlace(self, ref):
        return self.partes[0].enlace(ref)


def conector(cliente):
    base = {"servicenow": ServiceNow}.get(cliente.get("tickets_tipo"), Conector)(cliente)
    if (j(cliente.get("tickets_conf"), {}).get("odoo") or {}).get("url"):
        return Varios(cliente, [base, Odoo(cliente)])
    return base


def trasladado(cliente, aap, mv):
    """Después de mover (o revertir) una máquina entre centros de datos: su IP nueva y la ubicación del centro donde quedó
    (la infraestructura KVM cuya red libvirt es la de la máquina) en las variables del host de AAP y en el equipo."""
    import yaml
    if mv.get("modo") not in ("mover", "revertir") or not mv.get("maquina") or not mv.get("red_actual"):
        return
    inf = next((x for x in q("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo='kvm'", cliente["id"])
                if (j(x["detalle"], {}).get("red_libvirt") or "default") == mv["red_actual"]), None)
    ubic, ip = (inf or {}).get("ubicacion") or "", mv.get("ip") or ""
    for h in aap.get("hosts/", name=mv["maquina"], page_size=50).get("results", []):
        try:
            hv = yaml.safe_load(h.get("variables") or "") or {}
        except yaml.YAMLError:
            continue
        if isinstance(hv, dict):
            if ip:
                hv["ansible_host"] = ip
            if ubic:
                hv["faceops_ubicacion"] = ubic
            aap.patch(f"hosts/{h['id']}/", {"variables": json.dumps(hv, ensure_ascii=False)})
    e = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente["id"], mv["maquina"])
    if e:
        ej("UPDATE equipos SET ip=COALESCE(NULLIF(?, ''), ip), ubicacion=COALESCE(NULLIF(?, ''), ubicacion) WHERE id=?", ip, ubic, e["id"])
    auditar("FaceOps", cliente["id"], "máquina trasladada", mv["maquina"], {"centro": ubic, "ip": ip, "modo": mv["modo"]})
