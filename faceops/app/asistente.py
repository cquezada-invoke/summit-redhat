"""Asistente de FaceOps (chat): se le pide en palabras lo que se necesita ("crea la VLAN 40 en fw con 10.50.40.1/24 llamada
Campus"), encuentra la automatización con el buscador por intención, completa sus datos con lo que dijo la persona y pregunta
lo que falte, muestra un resumen y, al confirmar, abre el ticket (con su aprobación, como cualquier otro). Responde con el
número del ticket y su enlace. También informa el estado de un ticket ("estado de FO-LAB-00680").
Mismas reglas que la consola: permisos del perfil, compatibilidad con el equipo y cliente suspendido."""
import re

from .bd import j, q, q1
from .hora_local import texto_utc
from .inventario import normal

RE_IP = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?)\b")
RE_TICKET = re.compile(r"\bFO-[A-Z0-9]+-\d+\b", re.I)
SI = {"si", "sí", "dale", "ok", "confirmo", "confirmar", "crear", "crealo", "créalo", "adelante", "hazlo", "s"}
NO = {"no", "cancelar", "cancela", "olvidalo", "olvídalo", "salir"}


# Pedidos frecuentes con su automatización (antes que el buscador por intención, que pesa palabras sueltas como «disco»)
INTENCIONES = [
    (r"\b(crea|crear|nueva|nuevo|necesito|levanta|levantar|habilita|habilitar|quiero)\b.*\b(maquinas? virtual(es)?|vms?|servidor(es)?|maquinas?)\b", "FaceOps · Habilitar servidor"),
    (r"\bvlan\b", "Red · Configurar VLAN"),
    (r"\b(amplia|ampliar|agranda|agrandar|crecer)\b.*\bdisco\b", "FaceOps · Ampliar disco"),
    (r"\b(agrega|agregar|nuevo|anadir)\b.*\bdisco\b", "FaceOps · Agregar disco"),
    (r"\b(elimina|eliminar|borra|borrar|dar de baja)\b.*\b(servidor|maquina|vm)\b", "FaceOps · Eliminar servidor"),
]


def _intencion(texto, autos):
    n = normal(texto)
    for patron, nombre in INTENCIONES:
        if re.search(patron, n):
            a = next((x for x in autos if x["nombre_aap"] == nombre), None)
            if a:
                return a
    return None


def _msg(texto, **kw):
    return dict({"texto": texto}, **kw)


def _equipos(usuario_equipos, texto):
    t = " " + normal(texto) + " "
    return [e for e in usuario_equipos if re.search(r"(?<![a-z0-9-])" + re.escape(normal(e["nombre"])) + r"(?![a-z0-9-])", t)]


CONTEXTO = {"ram": r"(?:ram|memoria)", "disco": r"(?:disco|almacenamiento|storage)", "cpu": r"(?:v?cpus?|cores?|nucleos|procesador(?:es)?)",
            "replicas": r"(?:replicas?|servidores|maquinas|copias|instancias)"}


def _numero_ctx(n, clave):
    """Número dicho junto a la palabra: «4 GB de RAM», «RAM de 4», «2 vCPU», «disco 40»."""
    k = CONTEXTO[clave]
    m = re.search(r"(\d{1,4})\s*(?:gb|g|gib)?\s*(?:de\s+)?" + k + r"\b", n) or re.search(r"\b" + k + r"\s*(?:de\s+|:\s*)?(\d{1,4})", n)
    return m.group(1) if m else None


def _opcion(ops, valor):
    """La opción igual o, si no existe, la menor que lo cubre (pidió 3 GB → 4)."""
    if valor in ops:
        return valor
    nums = sorted((int(o), o) for o in ops if str(o).isdigit())
    return next((o for i, o in nums if i >= int(valor)), nums[-1][1] if nums else None)


def extraer(campos, texto, previos=None, infras=None):
    """Valores de los campos del formulario que se pueden leer del texto."""
    v, t = dict(previos or {}), texto
    n = normal(texto)
    n2 = re.sub(r"\b([a-z]+)\s+(\d)", r"\1\2", n.replace("24.04", "2404").replace("server ", ""))   # «rhel 9» → rhel9
    ips = RE_IP.findall(t)
    ints = [x for x in re.findall(r"(?<![\d./])(\d{1,5})(?![\d./])", t)]
    citado = re.findall(r"[«\"“']([^»\"”']{2,60})[»\"”']", t)
    for c in campos:
        var, tipo, et = c["var"], c.get("tipo", "text"), normal(c.get("etiqueta", ""))
        if var in v:
            continue
        clave = next((k for k in CONTEXTO if k in var), None)
        ops = [str(o) for o in (c.get("opciones") or [])]
        if clave:
            x = _numero_ctx(n, clave)
            if x:
                v[var] = _opcion(ops, x) if ops else x
            continue
        if tipo == "infraestructura" and infras:
            hit = [i for i in infras if any(w in n for w in normal(i).replace("·", " ").split() if len(w) > 2)]
            v[var] = hit[0] if hit else infras[0]   # sin indicarla: la primera del cliente (on-premise)
            continue
        if ops:
            hit = [o for o in ops if re.search(r"\b" + re.escape(normal(o)) + r"\b", n2) or re.search(r"\b" + re.escape(normal(o)) + r"\b", n)]
            if len(hit) == 1:
                v[var] = hit[0]
            continue
        if "ip" in var.split("_") or var.endswith("_ip") or " ip" in " " + et:
            if ips:
                v[var] = ips.pop(0)
            continue
        if any(k in var for k in ("interfaz", "puerto_padre")):
            m = re.search(r"\b(eth\d+|port\d+|ens\d+|enp\d+s\d+)\b", n)
            if m:
                v[var] = m.group(1)
            continue
        if var.endswith("_id") or tipo in ("number", "entero") or any(k in var for k in ("_gb", "_cpu", "replicas", "puerto")):
            clave = var.split("_")[-1] if var.endswith("_id") else var.split("_", 1)[-1]
            m = re.search(r"\b" + re.escape(clave.replace("id", "").strip() or "x") + r"\w*\s+(\d{1,5})\b", n) if clave else None
            if not m and var.endswith("_id"):
                m = re.search(r"\b" + re.escape(var.split("_")[0]) + r"\s+(\d{1,5})\b", n)
            if m:
                v[var] = m.group(1)
            elif len(ints) == 1 and var.endswith("_id"):
                v[var] = ints[0]
            continue
        if any(k in var for k in ("nombre", "descripcion", "motivo")):
            m = re.search(r"\b(?:llamad[ao]|nombre|descripci[oó]n|como)\s+[«\"“']?([^,;«»\"”']{2,60}?)(?=\s+(?:en|para|con|de|y|que)\b|[,;.»\"”']|$)", t, re.I)
            val = citado.pop(0) if citado else (m.group(1).strip() if m else "")
            if val and "minúscula" in (c.get("ayuda") or ""):   # nombres técnicos: una sola palabra en minúsculas
                val = normal(val.split()[0])
            if val:
                v[var] = val
    return v


def faltantes(campos, valores):
    return [c for c in campos if c.get("requerido") and str(valores.get(c["var"], "")).strip() == "" and not c.get("defecto")]


def responder(usuario, cliente, perfil, estado, mensaje, equipos_vis, autos_vis):
    """(mensajes, estado nuevo). estado: {paso, auto_id, valores, equipo, falta}"""
    from .buscador import buscar
    est = dict(estado or {})
    m = (mensaje or "").strip()
    nm = normal(m)
    if est.get("paso") not in ("dato", "equipo"):   # las respuestas a una pregunta (nombres, IPs…) se dejan tal cual
        nm = entender(nm)
    if not m:
        return [_msg("¿Qué necesitas? Por ejemplo: «crea la VLAN 40 en fw con 10.50.40.1/24 llamada Campus» o «estado de FO-LAB-00680».")], {}
    if nm in NO:
        return [_msg("Listo, lo dejé. ¿Algo más?")], {}
    # cortesía: saludos, agradecimientos y despedidas
    cortesia = _cortesia(nm, (usuario.get("nombre") or "").split(" ")[0])
    if cortesia and est.get("paso") not in ("confirmar", "dato"):
        return [_msg(cortesia)], est
    # automatizaciones: «aut», «automatizaciones», «aut infra» (lista), detalle y ejecutar desde el chat
    if est.get("paso") not in ("confirmar", "dato"):
        ma = re.fullmatch(r"(?:(?:lista|listar|muestra|muestrame|ver)\s+)?(?:las\s+|los\s+|mis\s+)?(?:aut|auts|automatizacion(?:es)?)(?:\s+(?:de\s+|del\s+|la\s+|las\s+)?(.+))?", nm)
        if ma:
            return _autos_lista(autos_vis, (ma.group(1) or "").strip()), {}
        md_ = re.fullmatch(r"detalle de la automatizacion (\d+)", nm)
        if md_:
            return _auto_detalle(autos_vis, int(md_.group(1))), {}
    mx = re.fullmatch(r"ejecutar (?:la )?automatizacion (\d+)", nm)
    if mx:
        a0 = next((x for x in autos_vis if x["id"] == int(mx.group(1))), None)
        if not a0:
            return [_msg("No encuentro esa automatización.")], {}
        est = {"auto_id": a0["id"], "valores": {}, "texto": ""}
        m = nm = ""
    # «último ticket» / «mi último ticket»: el detalle del último que pidió esta persona por el chat (si no hay, su último ticket)
    if est.get("paso") not in ("confirmar", "dato") and re.search(r"\bultimo\s+ticket\b", nm) and not re.search(r"\d", nm):
        base = ("SELECT t.*, a.nombre auto_nombre, u.nombre usuario_nombre FROM tickets t LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id "
                "LEFT JOIN usuarios u ON u.id=t.usuario_id WHERE t.cliente_id=? AND t.usuario_id=? ")
        t = q1(base + "AND t.motivo LIKE 'Pedido por el asistente de FaceOps%' ORDER BY t.id DESC LIMIT 1", cliente["id"], usuario["id"]) \
            or q1(base + "ORDER BY t.id DESC LIMIT 1", cliente["id"], usuario["id"])
        if not t:
            return [_msg("Todavía no tienes tickets en este cliente.")], {}
        return [_msg(f"{t['numero']} · {t['auto_nombre'] or ''}", ticket=t["id"], numero=t["numero"], detalle=_detalle(t))], {}
    # listas de tickets: «últimos 10 tickets», «mis tickets fallidos», «tickets FO-LAB-00678 y FO-LAB-00679»
    codigos = [c.upper() for c in RE_TICKET.findall(m)]
    if est.get("paso") not in ("confirmar", "dato") and (len(codigos) > 1 or (re.search(r"\btickets?\b", nm) and re.search(
            r"\b(ultim|recient|lista|muestra|mostrar|ver|dame|mis|cuales|fallid|pendient|exitos|abiert|hoy)\w*", nm) and not codigos)
            or (re.fullmatch(r"(los |las |mis |el |la )?tickets?( \w+)?", nm) and not codigos)):   # solo la palabra: la lista
        return _lista(usuario, cliente, nm, codigos), {}
    # detalle de un certificado: «detalle del certificado <serie o nombre>»
    mc = re.search(r"\bdetalle (?:del )?certificado\s+(.+)$", nm)
    if mc and est.get("paso") not in ("confirmar", "dato"):
        return _certificado(cliente, mc.group(1).strip(), equipos_vis), {}
    # certificados: «certificados», «certificados por vencer» (máximo 10, los que vencen antes primero)
    if est.get("paso") not in ("confirmar", "dato") and re.search(r"\bcertificados?\b", nm) and not re.search(r"\b(emite|emitir|renueva|renovar|crea|crear|genera|generar)\b", nm):
        return _certificados(cliente, nm, equipos_vis), {}
    # detalle de un equipo: «detalle del equipo fw», «equipo nodo-jboss»
    me = re.search(r"\b(?:detalle (?:del |de la )?(?:equipo|maquina|servidor)|equipo|maquina|servidor)\s+([a-z0-9][a-z0-9._-]+)", nm)
    if me and est.get("paso") not in ("confirmar", "dato"):
        e = next((x for x in equipos_vis if normal(x["nombre"]) == me.group(1)), None)
        if e:
            return [_msg(e["nombre"], equipo_det={"id": e["id"], "filas": [
                ("Categoría", e.get("categoria") or "servidor"), ("Sistema", e.get("so") or "—"), ("Ambiente", e.get("ambiente") or "—"),
                ("Ubicación", e.get("ubicacion") or "—"), ("Energía", e.get("energia") or "sin dato"),
                ("Estado del inventario", e.get("estado") or "—")]})], {}
    # segmentos de negocio: lista y detalle
    if est.get("paso") not in ("confirmar", "dato"):
        if re.fullmatch(r"(?:(?:lista|listar|muestra|muestrame|mostrar|ver|dame|todos)\s+)*(?:los\s+|mis\s+)?segmentos?(?:\s+de\s+negocio)?", nm):
            return _segmentos(usuario, cliente, equipos_vis), {}
        ms_ = re.fullmatch(r"detalle del segmento (.+)", nm)
        if ms_:
            return _segmento(usuario, cliente, ms_.group(1), equipos_vis), {}
        if re.fullmatch(r"(?:(?:lista|listar|muestra|muestrame|mostrar|ver|dame|todos)\s+)*(?:los\s+)?namespaces?(?:\s+(?:de\s+|en\s+)?(?:openshift|ocp|kubernetes))?|(?:openshift|ocp)\s+namespaces?", nm):
            return _namespaces(equipos_vis), {}
        mn_ = re.fullmatch(r"detalle del namespace (\S+) en (\S+)", nm)
        if mn_:
            return _namespace(equipos_vis, mn_.group(1), mn_.group(2)), {}
    # «infra», «infraestructura»: las infraestructuras (VMware, OpenShift, KVM, nubes…) y su detalle
    if est.get("paso") not in ("confirmar", "dato") and re.fullmatch(
            r"(?:(?:lista|listar|muestra|muestrame|mostrar|ver|dame|toda|todo)\s+)*(?:la\s+|mi\s+|nuestra\s+)?infra(?:estructura)?s?", nm):
        return _infras(cliente, equipos_vis), {}
    mi_ = re.fullmatch(r"detalle de la infraestructura (.+)", nm)
    if mi_ and est.get("paso") not in ("confirmar", "dato"):
        return _infra(cliente, mi_.group(1), equipos_vis), {}
    # inventario: «muéstrame 10 máquinas», «equipos de red», «clústeres openshift», «máquinas virtuales apagadas»
    if est.get("paso") not in ("confirmar", "dato") and re.search(
            r"\b(maquinas?|equipos?|servidores?|inventario|red|redes|routers?|switch(es)?|firewalls?|openshift|clusters?|clusteres?|virtuales?|vms?|windows|linux|rhel|ubuntu|storage|balanceador(es)?)\b", nm) \
            and (re.search(r"\b(muestra|muestrame|mostrar|lista|listar|busca|buscar|ver|dame|cuales|cuantas|cuantos|hay|que)\b|^\d", nm) or len(nm.split()) <= 3) \
            and not re.search(r"\b(crea|crear|nueva|nuevo|elimina|eliminar|agrega|agregar|amplia|ampliar|reinicia|reiniciar)\b", nm):
        return _equipos_lista(cliente, nm, m, equipos_vis), {}
    # estado de un ticket
    tk = RE_TICKET.search(m)   # «detalle de FO-…», «estado de FO-…» o solo el código
    if tk and est.get("paso") not in ("confirmar", "dato"):
        t = q1("SELECT t.*, a.nombre auto_nombre, u.nombre usuario_nombre FROM tickets t LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id "
               "LEFT JOIN usuarios u ON u.id=t.usuario_id WHERE t.cliente_id=? AND t.numero=?", cliente["id"], tk.group(0).upper())
        if not t:
            return [_msg(f"No encuentro el ticket {tk.group(0).upper()} en este cliente.")], {}
        return [_msg(f"{t['numero']} · {t['auto_nombre'] or ''}", ticket=t["id"], numero=t["numero"], detalle=_detalle(t))], {}
    # confirmación
    if est.get("paso") == "confirmar":
        if nm in SI or nm.startswith("si"):
            return _crear(usuario, cliente, est, equipos_vis)
        est.pop("paso")
    elif nm in SI and not est.get("paso"):
        return [_msg("¿Qué quieres que haga? Cuéntame la tarea y el equipo.")], {}
    a = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", est.get("auto_id"), cliente["id"]) if est.get("auto_id") else None
    # elegir automatización (nueva conversación o eligió una opción)
    if est.get("paso") == "elegir":
        ops = est.get("opciones") or []
        ele = next((o for o in ops if str(o) == m or nm == normal(str(o))), None)
        if ele is None and m.isdigit() and 1 <= int(m) <= len(ops):
            ele = ops[int(m) - 1]
        a = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", ele, cliente["id"]) if ele else None
        if not a:
            return [_msg("Elige una de las opciones.", opciones=est.get("botones") or [])], est
        est = {"auto_id": a["id"], "valores": {}, "texto": est.get("texto", "")}
        m = est["texto"]
    elif not a:
        _, res = buscar(m, cliente["id"], autos_vis, [], [], [])
        cand = [x for x in res["automatizaciones"] if x.get("visible", 1)]
        fija = _intencion(m, autos_vis)
        if fija:
            cand = [fija]
        eqs = _equipos(equipos_vis, m)
        if eqs:
            from .compatibilidad import compatible
            cand = [x for x in cand if compatible(x, eqs[0])[0]] or cand
        if not cand:
            return [_msg("No encontré una automatización para eso. Prueba con otras palabras: qué quieres hacer y sobre qué equipo.")], {}
        if len(cand) > 1 and (len(eqs) == 0 or len(cand) > 3):
            top = cand[:4]
            botones = [{"texto": x["nombre"], "valor": x["id"]} for x in top]
            return [_msg("Encontré varias automatizaciones. ¿Cuál quieres?", opciones=botones)], \
                {"paso": "elegir", "opciones": [x["id"] for x in top], "botones": botones, "texto": m}
        a = cand[0]
        est = {"auto_id": a["id"], "valores": {}, "texto": m}
    campos = j(a["campos"], [])
    # equipo
    eqs = _equipos(equipos_vis, m)
    if eqs and not est.get("equipo"):
        est["equipo"] = eqs[0]["nombre"]
    # respuesta a la pregunta pendiente
    if est.get("falta"):
        c = next((x for x in campos if x["var"] == est["falta"]), None)
        if c is not None:
            v1 = extraer([c], m, None, [r["nombre"] for r in q("SELECT nombre FROM infraestructuras WHERE cliente_id=?", cliente["id"])])
            est.setdefault("valores", {})[c["var"]] = v1.get(c["var"], m)
        est.pop("falta", None)
    infras = [r["nombre"] for r in q("SELECT nombre FROM infraestructuras WHERE cliente_id=? ORDER BY id", cliente["id"])]
    est["valores"] = extraer([c for c in campos if c.get("tipo") not in ("equipo", "equipos")], m, est.get("valores"), infras)
    # el equipo lo exige la automatización (por inventario o por su formulario)
    pide_equipo = not any(c.get("tipo") == "infraestructura" for c in campos) and \
        (bool(a["usa_limite"]) or any(c.get("tipo") in ("equipo", "equipos") for c in campos))
    if pide_equipo and not est.get("equipo"):
        est["paso"] = "equipo"
        if est.get("pregunta") == "equipo" and m:
            return [_msg("No reconozco ese equipo. Escribe su nombre tal como aparece en el inventario (p. ej. fw, nodo-jboss).")], est
        est["pregunta"] = "equipo"
        return [_msg(f"«{a['nombre']}»: ¿sobre qué equipo? Escribe su nombre del inventario.")], est
    est.pop("pregunta", None)
    for c in campos:
        if c.get("tipo") in ("equipo", "equipos") and est.get("equipo"):
            est["valores"][c["var"]] = est["equipo"] if c["tipo"] == "equipo" else [est["equipo"]]
    falta = faltantes(campos, est["valores"])
    if falta:
        c = falta[0]
        est["falta"], est["paso"] = c["var"], "dato"
        ops = [{"texto": str(o), "valor": str(o)} for o in (c.get("opciones") or [])]
        return [_msg(f"{c.get('etiqueta', c['var'])}?" + (f" ({c['ayuda'][:140]})" if c.get("ayuda") else ""), opciones=ops)], est
    # resumen
    for c in campos:
        if c["var"] not in est["valores"] and c.get("defecto") not in (None, ""):
            est["valores"][c["var"]] = c["defecto"]
    lineas = [f"• {c.get('etiqueta', c['var'])}: **{est['valores'][c['var']]}**" for c in campos if str(est["valores"].get(c["var"], "")) != ""]
    est["paso"] = "confirmar"
    return [_msg(f"Voy a abrir un ticket de **{a['nombre']}**" + (f" en **{est['equipo']}**" if est.get("equipo") else "") + ":\n"
                 + ("\n".join(lineas) if lineas else "(sin datos adicionales)")
                 + ("\nRequiere aprobación antes de ejecutarse." if a.get("requiere_aprobacion") else "") + "\n¿Lo creo?",
                 opciones=[{"texto": "Sí, crear el ticket", "valor": "si"}, {"texto": "Cancelar", "valor": "cancelar"}])], est


def _certificados(cliente, nm, equipos_vis):
    from .certificados_panel import leer
    try:
        r = leer(cliente, equipos_vis)
    except Exception as x:  # noqa: BLE001
        return [_msg(f"No pude leer los certificados: {str(x)[:160]}")]
    fs = [f for f in r.get("filas") or [] if f.get("estado") not in ("reemplazado", "revocado")]
    if "venc" in nm:
        fs = [f for f in fs if f.get("estado") in ("vencido", "por vencer")]
    fs.sort(key=lambda f: (f.get("dias") is None, f.get("dias") if f.get("dias") is not None else 0))
    total = len(fs)
    if not fs:
        return [_msg("No hay certificados con ese criterio.")]
    return [_msg(f"{total} certificado{'s' if total != 1 else ''}" + (", los que vencen antes primero" if total > 1 else "") + ":",
                 certs=[{"serie": f.get("serie"), "cn": f.get("cn") or "", "dias": f.get("dias"), "estado": f.get("estado") or "",
                         "uso": bool(f.get("uso") and f["uso"].get("entrega") is not False)} for f in fs[:100]])]


def _certificado(cliente, clave, equipos_vis):
    from .certificados_panel import leer
    try:
        fs = leer(cliente, equipos_vis).get("filas") or []
    except Exception as x:  # noqa: BLE001
        return [_msg(f"No pude leer los certificados: {str(x)[:160]}")]
    f = next((f for f in fs if str(f.get("serie")) == clave or normal(str(f.get("serie_txt") or "")) == clave), None) or \
        next((f for f in fs if normal(f.get("cn") or "") == clave and f.get("estado") not in ("reemplazado", "revocado")), None)
    if not f:
        return [_msg("No encontré ese certificado.")]
    fecha = lambda d: d.strftime("%d-%m-%Y") if hasattr(d, "strftime") else (str(d or "—")[:10])  # noqa: E731
    uso = f.get("uso") or {}
    filas = [("Estado", f.get("estado") or "—"), ("Vence", f"{fecha(f.get('fin'))}" + (f" (en {f['dias']} días)" if f.get("dias") is not None and f["dias"] >= 0 else "")),
             ("Desde", fecha(f.get("inicio"))), ("Emisor", f.get("emisor") or "—"), ("Serie", f.get("serie_txt") or f.get("serie") or "—"),
             ("En uso", ("sí" + (f" · puerto {uso.get('puerto')}" if uso.get("puerto") else "")) if uso and uso.get("entrega") is not False else "no"),
             ("Equipo", (f.get("equipo") or {}).get("nombre") or f.get("equipo_nombre") or "—"),
             ("Gestión", "automática · FaceOps" if f.get("auto") else ("manual" if f.get("tipo") == "servidor" else "—"))]
    return [_msg(f.get("cn") or "Certificado", cert_det={"serie": f.get("serie"), "filas": filas})]


def _autos_lista(autos_vis, filtro):
    """«aut»: las áreas (las mismas de la pantalla de automatizaciones) para elegir. «aut <área>» o su abreviación
    («aut infra»): todas las de esa área. Si la palabra no es un área, busca por nombre, categoría o descripción."""
    from .areas import AREAS, area_de
    autos = [a for a in autos_vis if a.get("visible", 1)]
    por = {}
    for a in autos:
        por.setdefault(area_de(dict(a)), []).append(a)
    if not filtro:
        return [_msg(f"{sum(1 for k, _, _ in AREAS if por.get(k))} grupos de automatizaciones:",
                     grupos=[{"cmd": f"aut {n}", "nombre": n, "txt": d_, "n": len(por[k]), "uno": "automatización", "varios": "automatizaciones"}
                             for k, n, d_ in AREAS if por.get(k)])]
    f = normal(filtro)
    sel = [(k, n) for k, n, _ in AREAS if por.get(k) and (normal(n) == f or normal(k) == f)] or \
          [(k, n) for k, n, _ in AREAS if por.get(k) and (normal(n).startswith(f) or any(w.startswith(f) for w in normal(n).split()))]
    if sel:
        lista, titulo = [a for k, _ in sel for a in por[k]], " · ".join(n for _, n in sel)
    else:
        pal = [w for w in f.split() if len(w) > 1]
        lista = [a for a in autos if all(w in normal(" ".join([a["nombre"], a.get("categoria") or "", a.get("descripcion") or ""])) for w in pal)]
        titulo = f"«{filtro}»"
    if not lista:
        return [_msg(f"No hay un grupo ni automatizaciones con «{filtro}». Escribe «aut» para ver los grupos.")]
    lista.sort(key=lambda a: normal(a["nombre"]))
    return [_msg(f"{titulo}: {len(lista)} automatizaciones", autos=[{"id": a["id"], "nombre": a["nombre"], "cat": a.get("categoria") or "General",
                                                                       "aprob": bool(a.get("requiere_aprobacion"))} for a in lista])]


def _auto_detalle(autos_vis, aid):
    a = next((x for x in autos_vis if x["id"] == aid), None)
    if not a:
        return [_msg("No encuentro esa automatización.")]
    campos = j(a.get("campos"), [])
    filas = [("Categoría", a.get("categoria") or "General"), ("Tipo", a.get("tipo") or "—"),
             ("Aprobación", "requiere aprobación" if a.get("requiere_aprobacion") else "no requiere"),
             ("Ahorro estimado", f"{a.get('minutos_manual') or 0} min por ejecución")]
    if a.get("descripcion"):
        filas.insert(0, ("Qué hace", a["descripcion"][:300]))
    if campos:
        filas.append(("Datos que pide", ", ".join(c.get("etiqueta", c["var"]) for c in campos)[:240]))
    pide = not any(c.get("tipo") == "infraestructura" for c in campos) and bool(a.get("usa_limite")) \
        and not any(c.get("tipo") in ("equipo", "equipos") for c in campos)
    form = [{k: c.get(k) for k in ("var", "etiqueta", "tipo", "opciones", "defecto", "ayuda", "requerido")} for c in campos]
    return [_msg(a["nombre"], auto_det={"id": a["id"], "filas": filas, "campos": form, "pide_equipo": pide})]


def ejecutar_formulario(usuario, cliente, aid, equipo, valores, equipos_vis, autos_vis):
    """El formulario de la automatización completado en el chat: valida y abre el ticket."""
    a = next((x for x in autos_vis if x["id"] == aid), None)
    if not a:
        return [_msg("No encuentro esa automatización.")]
    campos = j(a.get("campos"), [])
    nombres = {e["nombre"] for e in equipos_vis}
    vals = {}
    for c in campos:
        v = valores.get(c["var"])
        if v in (None, "", []):
            continue
        et = c.get("etiqueta", c["var"])
        if c.get("tipo") == "integer":
            try:
                v = int(v)
            except (TypeError, ValueError):
                return [_msg(f"«{et}» debe ser un número.")]
        if c.get("tipo") == "equipos":
            v = [x.strip() for x in (v.split(",") if isinstance(v, str) else v) if str(x).strip()]
            malos = [x for x in v if x not in nombres]
            if malos:
                return [_msg(f"«{et}»: no reconozco {', '.join(malos)} en el inventario.")]
        if c.get("tipo") == "equipo" and v not in nombres:
            return [_msg(f"«{et}»: no reconozco {v} en el inventario.")]
        vals[c["var"]] = v
    falta = faltantes(campos, vals)
    if falta:
        return [_msg("Falta completar: " + ", ".join(c.get("etiqueta", c["var"]) for c in falta) + ".")]
    pide = not any(c.get("tipo") == "infraestructura" for c in campos) and bool(a.get("usa_limite")) \
        and not any(c.get("tipo") in ("equipo", "equipos") for c in campos)
    eq = (equipo or "").strip() or next((vals[c["var"]] for c in campos if c.get("tipo") == "equipo" and vals.get(c["var"])), "")
    if pide and not eq:
        return [_msg("Indica el equipo donde se ejecuta.")]
    if eq and eq not in nombres:
        return [_msg(f"No reconozco el equipo {eq} en el inventario.")]
    for c in campos:
        if c["var"] not in vals and c.get("defecto") not in (None, ""):
            vals[c["var"]] = c["defecto"]
    return _crear(usuario, cliente, {"auto_id": a["id"], "valores": vals, "equipo": eq,
                                     "texto": f"formulario del chat · {a['nombre']}"}, equipos_vis)[0]


PLATAFORMAS = [  # código del inventario, nombre, alias que se aceptan en el chat
    ("rhel", "RHEL", r"rhel|red ?hat enterprise linux"), ("so:ubuntu", "Ubuntu", r"ubuntu"), ("windows-server", "Windows Server", r"windows server"),
    ("so:windows", "Windows", r"windows"), ("so:linux", "Linux", r"linux"),
    ("data-lake", "Data lake (S3 + Trino)", r"data ?lake|lago de datos|trino"), ("openshift", "OpenShift", r"openshift|ocp"), ("kubernetes", "Kubernetes", r"kubernetes|k8s"), ("podman", "Podman", r"podman"), ("docker", "Docker", r"docker"),
    ("jboss-eap", "JBoss EAP", r"jboss|eap|wildfly"), ("tomcat", "Tomcat", r"tomcat"), ("httpd", "Apache httpd", r"httpd|apache"), ("apache", "Apache", r"apache"),
    ("nginx", "NGINX", r"nginx"), ("iis", "IIS", r"iis"),
    ("postgresql", "PostgreSQL", r"postgres(ql)?|pg"), ("redis", "Redis", r"redis"), ("mysql", "MySQL", r"mysql"), ("mariadb", "MariaDB", r"mariadb"),
    ("oracle", "Oracle", r"oracle"), ("sqlserver", "SQL Server", r"sql ?server|mssql"), ("mongodb", "MongoDB", r"mongo(db)?"),
    ("satellite", "Red Hat Satellite", r"satellite|foreman"), ("wireguard", "VPN WireGuard", r"vpn|wireguard"),
    ("kafka", "Kafka", r"kafka|amq streams"), ("activemq", "ActiveMQ", r"activemq|amq"), ("rabbitmq", "RabbitMQ", r"rabbit(mq)?"),
]
_VERBOS_PLAT = r"(?:(?:lista|listar|listame|muestra|muestrame|mostrar|ver|dame|busca|buscar|todas|todos|las|los|maquinas|equipos|servidores|nodos|con|de|del|en)\s+)*"


def es_plataformas(nm):
    """¿El mensaje pide plataformas («plataformas», «jboss», «listar redis», «máquinas con postgres»)? Devuelve 'todas', los códigos o None."""
    if re.fullmatch(_VERBOS_PLAT + r"(?:tipos de )?plataformas?", nm):
        return "todas"
    m = re.fullmatch(_VERBOS_PLAT + r"(.+?)", nm)
    if not m:
        return None
    resto = m.group(1)
    cods = [c for c, _, al in PLATAFORMAS if re.fullmatch(r"(?:%s|%s)" % (al, re.escape(c)), resto)]
    return cods or None


def plataformas(pedido, parque):
    """parque: [(equipo, [códigos de plataforma])]. «todas»: los tipos con su cantidad; si no, las máquinas de esos tipos."""
    nombre = {c: n for c, n, _ in PLATAFORMAS}
    if pedido == "todas":
        cuenta = {}
        for _, ps in parque:
            for p_ in set(ps):
                cuenta[p_] = cuenta.get(p_, 0) + 1
        orden = [c for c, _, _ in PLATAFORMAS if cuenta.get(c)] + sorted(c for c in cuenta if c not in nombre)
        if not orden:
            return [_msg("Aún no tengo plataformas detectadas: falta inventariar las máquinas.")]
        return [_msg(f"{len(orden)} plataformas en el inventario:", grupos=[
            {"cmd": f"listar {c}", "nombre": nombre.get(c, c), "txt": "", "n": cuenta[c], "uno": "máquina", "varios": "máquinas"} for c in orden])]
    eqs = sorted({e["id"]: e for e, ps in parque if set(ps) & set(pedido)}.values(), key=lambda e: e["nombre"])
    titulo = " · ".join(nombre.get(c, c) for c in pedido)
    if not eqs:
        return [_msg(f"No hay máquinas con {titulo} en el inventario.")]
    return [_msg(f"{titulo}: {len(eqs)} máquina{'s' if len(eqs) != 1 else ''}",
                 equipos=[{"id": e["id"], "nombre": e["nombre"], "cat": titulo, "energia": e.get("energia") or ""} for e in eqs])]


def _infras(cliente, equipos_vis):
    from .infraestructuras import lista
    xs = sorted(lista(cliente["id"], equipos_vis), key=lambda x: (x["centro"], normal(x["nombre"])))
    if not xs:
        return [_msg("No hay infraestructuras incorporadas.")]
    return [_msg(f"{len(xs)} infraestructuras:", infras=[
        {"id": x["id"], "nombre": x["nombre"], "txt": f"{x['prov'].get('nombre', x['tipo'])} · {x['centro']}",
         "estado": x.get("estado") or "sin probar", "n": x.get("equipos") or 0, "unidad": x.get("unidad") or "equipo"} for x in xs])]


def _infra(cliente, clave, equipos_vis):
    from .infraestructuras import lista
    x = next((x for x in lista(cliente["id"], equipos_vis) if normal(x["nombre"]) == clave), None)
    if not x:
        return [_msg("No encuentro esa infraestructura.")]
    filas = [("Tipo", x["prov"].get("nombre", x["tipo"])), ("Centro de datos", x["centro"]), ("Estado de la conexión", x.get("estado") or "—"),
             (("Clústeres" if x.get("unidad") == "clúster" else "Equipos"), x.get("equipos") or 0)]
    if x.get("region"):
        filas.append(("Región", x["region"]))
    if x.get("verificado"):
        filas.append(("Verificada", texto_utc(x["verificado"])))
    if x.get("mensaje"):
        filas.append(("Último resultado", x["mensaje"][:200]))
    return [_msg(x["nombre"], infra_det={"id": x["id"], "nombre": x["nombre"], "filas": filas})]


def _segmentos(usuario, cliente, equipos_vis):
    from .permisos import segmentos_visibles
    segs = sorted(segmentos_visibles(usuario, cliente["id"]), key=lambda x: normal(x["nombre"]))
    if not segs:
        return [_msg("No tienes segmentos de negocio visibles.")]
    return [_msg(f"{len(segs)} segmentos de negocio:", segs=[
        {"nombre": x["nombre"], "txt": (x.get("descripcion") or "")[:90],
         "n": sum(1 for e in equipos_vis if x["nombre"] in (e.get("segmentos") or []))} for x in segs])]


def _segmento(usuario, cliente, clave, equipos_vis):
    from .permisos import segmentos_visibles
    x = next((x for x in segmentos_visibles(usuario, cliente["id"]) if normal(x["nombre"]) == clave), None)
    if not x:
        return [_msg("No encuentro ese segmento.")]
    f = j(x.get("ficha"), {})
    eqs = sorted(e["nombre"] for e in equipos_vis if x["nombre"] in (e.get("segmentos") or []))
    filas = [("Qué es", (x.get("descripcion") or "—")[:300])]
    for k, et in (("dueno", "Dueño"), ("responsable_tecnico", "Responsable técnico"), ("criticidad", "Criticidad"), ("horario_servicio", "Horario")):
        if f.get(k):
            filas.append((et, str(f[k])))
    filas.append(("Equipos", f"{len(eqs)}: " + ", ".join(eqs[:12]) + ("…" if len(eqs) > 12 else "") if eqs else "—"))
    return [_msg(x["nombre"], seg_det={"nombre": x["nombre"], "filas": filas})]


def _namespaces(equipos_vis):
    out = []
    for e in equipos_vis:
        if (e.get("categoria") or "") != "clúster openshift":
            continue
        for n in ((j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})).get("ocp") or {}).get("namespaces_app") or []:
            out.append({"eid": e["id"], "cluster": e["nombre"], "ns": n.get("ns"), "pods": n.get("pods") or 0, "ok": n.get("pods_ok") or 0})
    if not out:
        return [_msg("No hay namespaces de aplicaciones en los clústeres OpenShift del inventario.")]
    out.sort(key=lambda x: (x["cluster"], x["ns"]))
    return [_msg(f"{len(out)} namespaces de aplicaciones en OpenShift:", nss=out)]


def _namespace(equipos_vis, ns, cluster):
    e = next((x for x in equipos_vis if normal(x["nombre"]) == cluster and (x.get("categoria") or "") == "clúster openshift"), None)
    ocp = ((j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})).get("ocp") or {}) if e else {}
    n = next((x for x in ocp.get("namespaces_app") or [] if x.get("ns") == ns), None)
    if not n:
        return [_msg("No encuentro ese namespace.")]
    filas = [("Clúster", e["nombre"]), ("Pods", f"{n.get('pods_ok', 0)} de {n.get('pods', 0)} listos"),
             ("Deployments", n.get("deployments", 0)), ("StatefulSets", n.get("statefulsets", 0)),
             ("Volúmenes", n.get("pvcs", 0)), ("Rutas", len(n.get("rutas") or [])),
             ("Creado", (n.get("creado") or "")[:10] or "—")]
    if n.get("descripcion"):
        filas.insert(0, ("Qué es", n["descripcion"][:200]))
    if n.get("solicitante"):
        filas.append(("Solicitante", n["solicitante"]))
    return [_msg(f"{ns} · {e['nombre']}", ns_det={"eid": e["id"], "ns": ns, "filas": filas})]


# ---- intérprete: corrige errores de tipeo, sinónimos y relleno antes de buscar la intención
_RELLENO = r"\b(por favor|porfa|porfis|plis|please|podrias|puedes|podes|pudieras|me puedes|me podrias|quisiera|quiero|necesito|me gustaria|" \
           r"oye|ya|ahora|rapido|un poco|el favor de|seria posible|es posible|ayudame a|ayudame con|me ayudas a|me ayudas con)\b"
_SINONIMOS = [
    (r"\b(ensename|ensenar|mostrarme|muestrame|muestreme|muestra|mostrar|despliega|desplegar|lista(me)?|listar|listado de|listado|ver|dame|trae(me)?|revisa(r)?)\b", "muestra"),
    (r"\b(solicitudes|requerimientos|casos|incidencias|ordenes|pedidos)\b", "tickets"), (r"\b(solicitud|requerimiento|caso|orden)\b", "ticket"),
    (r"\b(vms?|maquinas virtuales|maquina virtual|servidores virtuales|hosts?|nodos)\b", "maquinas"),
    (r"\b(certs?|ssl|tls|certificado digital|certificados digitales)\b", "certificados"),
    (r"\b(playbooks?|plantillas?|job templates?|automatizacion|automatismos?|rutinas?)\b", "automatizaciones"),
    (r"\b(equipos de red|dispositivos de red|networking|red de datos)\b", "red"),
    (r"\b(apps de negocio|aplicaciones de negocio|servicios de negocio)\b", "segmentos"),
    (r"\b(ns|proyectos de openshift|proyectos openshift)\b", "namespaces"),
    (r"\b(infras|infraestructuras)\b", "infraestructura"), (r"\b(tecnologias|productos instalados)\b", "plataformas"),
    (r"\b(postgresql|postgre|pgsql)\b", "postgres"), (r"\b(eap|wildfly)\b", "jboss"),
]
_VOCAB = ["tickets", "ticket", "inventario", "maquinas", "maquina", "servidores", "servidor", "equipos", "equipo", "certificados", "certificado",
          "automatizaciones", "plataformas", "plataforma", "segmentos", "segmento", "namespaces", "namespace", "infraestructura", "openshift",
          "jboss", "redis", "postgres", "tomcat", "nginx", "kafka", "windows", "ubuntu", "rhel", "firewall", "firewalls", "routers", "router",
          "switches", "balanceadores", "storage", "detalle", "ultimo", "ultimos", "muestra", "fallidos", "pendientes", "exitosos", "apagadas",
          "encendidas", "encender", "apagar", "reiniciar", "crea", "crear", "elimina", "eliminar", "vencer", "vencidos", "hoy", "mis"]


def entender(nm):
    """Deja el pedido en las palabras que entienden las reglas del asistente: quita relleno («por favor, podrías…»),
    unifica sinónimos («solicitudes» → tickets, «vms» → máquinas) y corrige errores de tipeo («tikets», «certifcados»)."""
    import difflib
    t = re.sub(r"[¡!¿?,;]+", " ", nm)
    t = re.sub(_RELLENO, " ", t)
    pal = []
    for w in t.split():
        if len(w) >= 4 and w.isalpha() and w not in _VOCAB:
            c = difflib.get_close_matches(w, _VOCAB, n=1, cutoff=0.84)
            w = c[0] if c else w
        pal.append(w)
    t = " ".join(pal)
    for pat, rep in _SINONIMOS:
        t = re.sub(pat, rep, t)
    t = re.sub(r"\b(muestra)( muestra)+\b", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or nm


def _cortesia(nm, nombre):
    """Respuesta amable si el mensaje es solo un saludo, un agradecimiento o una despedida."""
    import datetime as _dt
    t = re.sub(r"[!¡?¿.,;:)(]+", " ", nm)
    t = re.sub(r"\b(faceops|asistente|bot|amigo|amiga|crack|equipo)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    h = (_dt.datetime.utcnow() - _dt.timedelta(hours=3)).hour   # sin zona conocida: hora de Chile
    try:   # la hora local de quien escribe (zona del navegador en su ingreso)
        from zoneinfo import ZoneInfo
        from flask import session
        if session.get("tz"):
            h = _dt.datetime.now(ZoneInfo(session["tz"])).hour
    except Exception:  # noqa: BLE001
        pass
    momento = "Buenos días" if 5 <= h < 12 else ("Buenas tardes" if 12 <= h < 20 else "Buenas noches")
    n = f", {nombre}" if nombre else ""
    if re.fullmatch(r"(hola|holi|holaa+|hey|hi|hello|buen(o|a)s?( dias| tardes| noches)?|saludos|que tal|como estas|como te va|como andas|hola que tal|hola como estas|hola buen(o|a)s?( dias| tardes| noches)?)( (que tal|como estas))?", t):
        return (f"¡{momento}{n}! 😊 Aquí estoy para ayudarte. Puedes pedirme un ticket, revisar tus tickets, el inventario, "
                "las automatizaciones («aut»), las plataformas o los certificados. ¿En qué te ayudo?")
    if re.fullmatch(r"(muchas |mil )?gracias( (por todo|totales|igual))?|thanks|te pasaste|excelente|genial|perfecto|buenisimo", t):
        return f"¡Con mucho gusto{n}! Si necesitas algo más, aquí estoy."
    if re.fullmatch(r"(chao|chau|adios|hasta (luego|manana|pronto)|nos vemos|bye)( gracias)?", t):
        return f"¡Hasta pronto{n}! Que te vaya muy bien."
    return None


def _detalle(t):
    """Ficha corta de un ticket para el chat."""
    params = j(t.get("parametros"), {})
    res = [x for x in (t.get("resultado") or "").splitlines() if x.strip()]
    dur = t.get("duracion_s")
    filas = [("Estado", (t["estado"] or "").replace("_", " ")),
             ("Equipo / ámbito", t.get("limite") or t.get("segmento") or "—"),
             ("Pedido por", t.get("usuario_nombre") or "—"),
             ("Creado", texto_utc(t.get("creado"))),
             ("Duración", (f"{dur // 60} min {dur % 60} s" if dur and dur >= 60 else (f"{dur} s" if dur else "—"))),
             ("Motivo", (t.get("motivo") or "—")[:160])]
    return {"filas": filas, "resultado": res[:6]}


def _equipos_lista(cliente, nm, texto, equipos_vis):
    """Lista acotada de equipos del inventario según lo pedido (categoría, forma, sistema, energía, ambiente, nombre)."""
    from .buscador import buscar_equipos
    m = re.search(r"\b(\d{1,2})\b", nm)
    n = min(int(m.group(1)), 100) if m else 100
    limpio = re.sub(r"\b(muestra(me)?|mostrar|lista(r)?|busca(r)?|ver|dame|cuales|cuantas|cuantos|hay|que|del|inventario|las|los|\d{1,2})\b", " ", nm)
    cat = None
    for patron, c in ((r"\bred(es)?\b|\brouters?\b|\bswitch", ("router", "switch", "firewall", "balanceador")), (r"\bfirewalls?\b", ("firewall",)),
                      (r"\bopenshift\b|\bclusters?\b|\bclusteres?\b", ("clúster openshift",)), (r"\bstorage\b|\balmacenamiento\b", ("almacenamiento",)),
                      (r"\bbalanceador", ("balanceador",))):
        if re.search(patron, nm):
            cat = c
            break
    try:
        _, eqs = buscar_equipos(limpio, cliente["id"], equipos_vis, {})
    except Exception:  # noqa: BLE001 - si el buscador no entiende, se filtra solo por categoría
        eqs = equipos_vis
    if cat:   # la categoría manda; encima, solo el filtro de energía si lo pidió
        eqs = [e for e in equipos_vis if (e.get("categoria") or "servidor") in cat]
        if re.search(r"\bapagad", nm):
            eqs = [e for e in eqs if e.get("energia") == "apagado"]
        elif re.search(r"\b(encendid|prendid)", nm):
            eqs = [e for e in eqs if e.get("energia") == "encendido"]
    total = len(eqs)
    eqs = sorted(eqs, key=lambda e: e["nombre"])[:n]
    if not eqs:
        return [_msg("No encontré equipos con ese criterio.")]
    return [_msg(f"{total} equipo{'s' if total != 1 else ''}" + (f" · muestro {len(eqs)}" if total > len(eqs) else "") + ":",
                 equipos=[{"id": e["id"], "nombre": e["nombre"], "cat": e.get("categoria") or "servidor",
                           "energia": e.get("energia") or ""} for e in eqs])]


def _lista(usuario, cliente, nm, codigos):
    """Lista acotada de tickets con su código y estado (enlaces en el chat)."""
    filtros, args = ["t.cliente_id=?"], [cliente["id"]]
    if codigos:
        filtros.append("t.numero IN (" + ",".join("?" * len(codigos)) + ")")
        args += codigos
    else:
        for clave, est in (("fallid", "fallido"), ("pendient", "pendiente_aprobacion"), ("aprob", "pendiente_aprobacion"), ("exitos", "exitoso"),
                           ("ejecuc", "en_ejecucion"), ("curso", "en_ejecucion")):
            if clave in nm:
                filtros.append("t.estado=?")
                args.append(est)
                break
        if re.search(r"\babiert", nm):
            filtros.append("t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion')")
        if re.search(r"\bmis\b", nm):
            filtros.append("t.usuario_id=?")
            args.append(usuario["id"])
        if "hoy" in nm:
            from datetime import datetime, timezone
            filtros.append("t.creado >= ?")
            args.append(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    m = re.search(r"\b(\d{1,2})\b", nm)
    n = min(int(m.group(1)), 100) if (m and not codigos) else (len(codigos) or 50)
    filas = q("SELECT t.id, t.numero, t.estado, a.nombre auto FROM tickets t LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id "
              f"WHERE {' AND '.join(filtros)} ORDER BY t.id DESC LIMIT ?", *args, n)
    if not filas:
        return [_msg("No encontré tickets con ese criterio.")]
    return [_msg(f"{'Estos son' if codigos else 'Los últimos'} {len(filas)} ticket{'s' if len(filas) != 1 else ''}:",
                 lista=[{"id": f["id"], "numero": f["numero"], "estado": f["estado"], "auto": f["auto"] or ""} for f in filas])]


def _crear(usuario, cliente, est, equipos_vis):
    from .aap import ErrorAAP
    from .operacion import solicitar, solicitar_en_equipo
    a = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", est.get("auto_id"), cliente["id"])
    if not a:
        return [_msg("Esa automatización ya no está disponible.")], {}
    params = {k: v for k, v in (est.get("valores") or {}).items() if str(v) != ""}
    claves = {c["var"] for c in j(a.get("campos"), []) if c.get("tipo") == "password"}
    secretos = {k: params.pop(k) for k in list(params) if k in claves}   # claves: solo a AAP, nunca en el ticket
    motivo = f"Pedido por el asistente de FaceOps: {est.get('texto', '')[:200]}"
    e = next((x for x in equipos_vis if x["nombre"] == est.get("equipo")), None)
    try:
        if e and a["usa_limite"]:
            t = solicitar_en_equipo(usuario, cliente, a, e, params, motivo, secretos)
        else:
            t = solicitar(usuario, cliente, a, "", params, motivo, secretos, equipo=e if e else None)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return [_msg(f"No pude crear el ticket: {x}")], {}
    return [_msg(f"Listo: ticket **{t['numero']}** creado ({t['estado'].replace('_', ' ')})."
                 + (" Queda esperando aprobación." if t["estado"] == "pendiente_aprobacion" else ""),
                 ticket=t["id"], numero=t["numero"])], {}


def planificar(cliente, texto, equipos_vis, autos_vis):
    """Sin conversación (p. ej. un ticket que llega desde Odoo): automatización, equipo y datos leídos del texto.
    Devuelve {auto, equipo, valores, faltan: [etiquetas], error}."""
    from .buscador import buscar
    from .compatibilidad import compatible
    _, res = buscar(texto, cliente["id"], autos_vis, [], [], [])
    cand = list(res["automatizaciones"])
    fija = _intencion(texto, autos_vis)
    if fija:
        cand = [fija]
    eqs = _equipos(equipos_vis, texto)
    if eqs and not fija:
        cand = [x for x in cand if compatible(x, eqs[0])[0]] or cand
    if not cand:
        return {"error": "no encontré una automatización para este pedido"}
    a = cand[0]
    campos = j(a["campos"], [])
    infras = [r["nombre"] for r in q("SELECT nombre FROM infraestructuras WHERE cliente_id=? ORDER BY id", cliente["id"])]
    valores = extraer([c for c in campos if c.get("tipo") not in ("equipo", "equipos")], texto, None, infras)
    e = eqs[0] if eqs else None
    for c in campos:
        if c.get("tipo") in ("equipo", "equipos") and e:
            valores[c["var"]] = e["nombre"] if c["tipo"] == "equipo" else [e["nombre"]]
        if c["var"] not in valores and c.get("defecto") not in (None, ""):
            valores[c["var"]] = c["defecto"]
    faltan = [c.get("etiqueta", c["var"]) for c in faltantes(campos, valores)]
    crea_ = any(c.get("tipo") == "infraestructura" for c in campos)   # crea equipos nuevos: no se pide uno existente
    if not crea_ and (a["usa_limite"] or any(c.get("tipo") in ("equipo", "equipos") for c in campos)) and not e:
        faltan.insert(0, "equipo (su nombre del inventario)")
    return {"auto": a, "equipo": e, "valores": valores, "faltan": faltan}
