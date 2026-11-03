"""Asistente de FaceOps (chat): se le pide en palabras lo que se necesita ("crea la VLAN 40 en fw con 10.50.40.1/24 llamada
Campus"), encuentra la automatización con el buscador por intención, completa sus datos con lo que dijo la persona y pregunta
lo que falte, muestra un resumen y, al confirmar, abre el ticket (con su aprobación, como cualquier otro). Responde con el
número del ticket y su enlace. También informa el estado de un ticket ("estado de FO-LAB-00680").
Mismas reglas que la consola: permisos del perfil, compatibilidad con el equipo y cliente suspendido."""
import re

from .bd import j, q, q1
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
    if not m:
        return [_msg("¿Qué necesitas? Por ejemplo: «crea la VLAN 40 en fw con 10.50.40.1/24 llamada Campus» o «estado de FO-LAB-00680».")], {}
    if nm in NO:
        return [_msg("Listo, lo dejé. ¿Algo más?")], {}
    # listas de tickets: «últimos 10 tickets», «mis tickets fallidos», «tickets FO-LAB-00678 y FO-LAB-00679»
    codigos = [c.upper() for c in RE_TICKET.findall(m)]
    if est.get("paso") not in ("confirmar", "dato") and (len(codigos) > 1 or (re.search(r"\btickets?\b", nm) and re.search(
            r"\b(ultim|recient|lista|muestra|mostrar|ver|dame|mis|cuales|fallid|pendient|exitos|abiert|hoy)\w*", nm) and not codigos)):
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
    # inventario: «muéstrame 10 máquinas», «equipos de red», «clústeres openshift», «máquinas virtuales apagadas»
    if est.get("paso") not in ("confirmar", "dato") and re.search(
            r"\b(maquinas?|equipos?|servidores?|inventario|red|redes|routers?|switch(es)?|firewalls?|openshift|clusters?|clusteres?|virtuales?|vms?|windows|linux|rhel|ubuntu|storage|balanceador(es)?)\b", nm) \
            and re.search(r"\b(muestra|muestrame|mostrar|lista|listar|busca|buscar|ver|dame|cuales|cuantas|cuantos|hay|que)\b|^\d", nm) \
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
    return [_msg(f"{total} certificado{'s' if total != 1 else ''}" + (f" · muestro los 10 que vencen antes" if total > 10 else "") + ":",
                 certs=[{"serie": f.get("serie"), "cn": f.get("cn") or "", "dias": f.get("dias"), "estado": f.get("estado") or "",
                         "uso": bool(f.get("uso") and f["uso"].get("entrega") is not False)} for f in fs[:10]])]


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


def _detalle(t):
    """Ficha corta de un ticket para el chat."""
    params = j(t.get("parametros"), {})
    res = [x for x in (t.get("resultado") or "").splitlines() if x.strip()]
    dur = t.get("duracion_s")
    filas = [("Estado", (t["estado"] or "").replace("_", " ")),
             ("Equipo / ámbito", t.get("limite") or t.get("segmento") or "—"),
             ("Pedido por", t.get("usuario_nombre") or "—"),
             ("Creado", (t.get("creado") or "")[:16].replace("T", " ") + " UTC"),
             ("Duración", (f"{dur // 60} min {dur % 60} s" if dur and dur >= 60 else (f"{dur} s" if dur else "—"))),
             ("Motivo", (t.get("motivo") or "—")[:160])]
    if params:
        filas.append(("Datos", ", ".join(f"{k}={v}" for k, v in list(params.items())[:8] if not str(k).startswith("_"))[:240]))
    refs = [r for r in ((t.get("externo_ref") or "").split("|")[0], ("Odoo #" + t["odoo_ref"]) if (t.get("odoo_ref") or "").isdigit() else "")
            if r and r not in ("pendiente", "propio")]
    if refs:
        filas.append(("Referencias", " · ".join(refs)))
    return {"filas": filas, "resultado": res[:6]}


def _equipos_lista(cliente, nm, texto, equipos_vis):
    """Lista acotada de equipos del inventario según lo pedido (categoría, forma, sistema, energía, ambiente, nombre)."""
    from .buscador import buscar_equipos
    m = re.search(r"\b(\d{1,2})\b", nm)
    n = min(int(m.group(1)), 30) if m else 10
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
    n = min(int(m.group(1)), 30) if (m and not codigos) else (len(codigos) or 10)
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
    motivo = f"Pedido por el asistente de FaceOps: {est.get('texto', '')[:200]}"
    e = next((x for x in equipos_vis if x["nombre"] == est.get("equipo")), None)
    try:
        if e and a["usa_limite"]:
            t = solicitar_en_equipo(usuario, cliente, a, e, params, motivo)
        else:
            t = solicitar(usuario, cliente, a, "", params, motivo, equipo=e if e else None)
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
