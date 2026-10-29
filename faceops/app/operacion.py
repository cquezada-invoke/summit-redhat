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
        sk = skills_yml.get(t["name"])
        datos["habilita_skill"] = sk["skill"] if sk else ""
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
    auditar(usuario, cliente["id"], "sincronizar catálogo", "catálogo", {"automatizaciones": n})
    return n


def equipos_aplicables(usuario, cliente_id, auto, segmento):
    """Equipos del segmento de negocio que están en el inventario de la automatización y en los ambientes del perfil."""
    from .inventario import skills_de
    from .permisos import ambiente_permitido, perfil
    p = perfil(usuario, cliente_id)
    req = set(j(auto.get("skills"), []))
    return sorted([{"nombre": e["nombre"], "ambiente": e.get("ambiente") or "sin ambiente", "infra": e.get("ubicacion") or "sin ubicación"}
                   for e in equipos_de_segmento(cliente_id, segmento)
                   if e["estado"] != "retirado" and ambiente_permitido(p, e.get("ambiente"))
                   and (not req or req & set(skills_de(e)))
                   and (not auto["inventario"] or auto["inventario"] in j(e["inventarios"], []))], key=lambda x: x["nombre"])


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


def solicitar(usuario, cliente, auto, segmento, parametros, motivo, secretos=None, filtros=None):
    """FaceOps orquesta: crea el ticket (propio y, si corresponde, en la herramienta del cliente) y lanza en AAP.
    Si requiere aprobación, la aprobación vive en AAP (nodo de aprobación del workflow): FaceOps la muestra y
    permite responderla, y quien entre a AAP la ve igual en su bandeja de aprobaciones."""
    ok, por_que = puede_ejecutar(usuario, cliente["id"], auto)
    if not ok:
        raise PermissionError(por_que)
    limite = ""
    if auto["usa_limite"]:
        if not segmento or not puede_ver_segmento(usuario, cliente["id"], segmento):
            raise PermissionError("Elige un segmento al que tengas acceso")
        filtros = {k: v for k, v in (filtros or {}).items() if v}
        if len(filtros.get("ambientes", [])) != 1:
            raise PermissionError("Elige un ambiente: cada ejecución va a un único ambiente")
        limite = limite_para(usuario, cliente["id"], auto, segmento, filtros.get("ambientes"), filtros.get("infraestructura"))
        if not limite:
            raise PermissionError("No quedan equipos con ese segmento de negocio, ambiente e infraestructura")
    tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, "
             "motivo, creado, valor_min, tarifa, aap_job_tipo, filtros) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             cliente["id"], numero_ticket(cliente), auto["id"], usuario["id"], "en_cola", segmento or "", limite,
             json.dumps(parametros, ensure_ascii=False), motivo, ahora(), auto["minutos_manual"], auto["tarifa"], auto["aap_tipo"],
             json.dumps(filtros or {}, ensure_ascii=False))
    t = q1("SELECT * FROM tickets WHERE id=?", tid)
    auditar(usuario["usuario"], cliente["id"], "solicitar", t["numero"],
            {"automatizacion": auto["nombre"], "version": auto["version"], "segmento": segmento, "filtros": filtros,
             "limite": limite, "parametros": parametros})
    extra = conector(cliente).crear(t, auto, usuario) or {}
    if extra:
        ej("UPDATE tickets SET parametros=? WHERE id=?", json.dumps(dict(parametros, **extra), ensure_ascii=False), tid)
    ejecutar(tid, secretos)  # las claves van solo a AAP: en el ticket y la auditoría quedan enmascaradas
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


def ejecutar(ticket_id, secretos=None):
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    try:
        aap = AAP.de_cliente(c)
        params = j(t["parametros"], {})
        for k, v in conf_servicenow(a["nombre_aap"]).get("extra", {}).items():  # variables fijas (p. ej. solicitud_tipo)
            if "{" not in str(v):
                params.setdefault(k, v)
        params.update({"faceops_ticket": t["numero"], "faceops_solicitante": (q1("SELECT usuario FROM usuarios WHERE id=?", t["usuario_id"]) or {}).get("usuario", "")})
        params.setdefault("solicitado_por", params["faceops_solicitante"])
        params.update(secretos or {})
        amb = (j(t.get("filtros"), {}).get("ambientes") or [None])[0]
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
            a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
            if est == "exitoso" and a and a.get("habilita_skill"):
                registrar_skill(t, a, j(t["parametros"], {}))
            conector(c).cerrar(q1("SELECT * FROM tickets WHERE id=?", t["id"]), a)


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
                hoy = datetime.now()
                if hoy.hour == 3 and ultimo_dia != hoy.date():
                    ultimo_dia = hoy.date()
                    for c in q("SELECT * FROM clientes WHERE activo=1 AND aap_url!=''"):
                        try:
                            recolectar(c)
                            time.sleep(600)
                            sincronizar(c)
                            sincronizar_catalogo(c)
                        except Exception as e:  # noqa: BLE001
                            auditar("sistema", c["id"], "error recolección", "inventario", {"error": str(e)[:300]})
            except Exception:  # noqa: BLE001
                pass
            time.sleep(intervalo)
    threading.Thread(target=bucle, daemon=True, name="faceops-vigia").start()


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
