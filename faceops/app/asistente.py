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


def _msg(texto, **kw):
    return dict({"texto": texto}, **kw)


def _equipos(usuario_equipos, texto):
    t = " " + normal(texto) + " "
    return [e for e in usuario_equipos if re.search(r"(?<![a-z0-9-])" + re.escape(normal(e["nombre"])) + r"(?![a-z0-9-])", t)]


def extraer(campos, texto, previos=None):
    """Valores de los campos del formulario que se pueden leer del texto."""
    v, t, n = dict(previos or {}), texto, normal(texto)
    ips = RE_IP.findall(t)
    ints = [x for x in re.findall(r"(?<![\d./])(\d{1,5})(?![\d./])", t)]
    citado = re.findall(r"[«\"“']([^»\"”']{2,60})[»\"”']", t)
    for c in campos:
        var, tipo, et = c["var"], c.get("tipo", "text"), normal(c.get("etiqueta", ""))
        if var in v:
            continue
        ops = [str(o) for o in (c.get("opciones") or [])]
        if ops:
            hit = [o for o in ops if re.search(r"\b" + re.escape(normal(o)) + r"\b", n)]
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
            m = re.search(r"\b(?:llamad[ao]|nombre|descripci[oó]n|como)\s+[«\"“']?([^,;«»\"”']{2,60})", t, re.I)
            if citado:
                v[var] = citado.pop(0)
            elif m:
                v[var] = m.group(1).strip().rstrip(".")
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
    # estado de un ticket
    tk = RE_TICKET.search(m)
    if tk and not est.get("paso"):
        t = q1("SELECT t.*, a.nombre auto_nombre FROM tickets t LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id "
               "WHERE t.cliente_id=? AND t.numero=?", cliente["id"], tk.group(0).upper())
        if not t:
            return [_msg(f"No encuentro el ticket {tk.group(0).upper()} en este cliente.")], {}
        return [_msg(f"{t['numero']} · {t['auto_nombre'] or ''}: **{t['estado'].replace('_', ' ')}**"
                     + (f" · {(t['resultado'] or '').splitlines()[0][:160]}" if t.get("resultado") else ""), ticket=t["id"], numero=t["numero"])], {}
    # confirmación
    if est.get("paso") == "confirmar":
        if nm in SI or nm.startswith("si"):
            return _crear(usuario, cliente, est, equipos_vis)
        est.pop("paso")
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
            v1 = extraer([c], m)
            est.setdefault("valores", {})[c["var"]] = v1.get(c["var"], m)
        est.pop("falta", None)
    est["valores"] = extraer([c for c in campos if c.get("tipo") not in ("equipo", "equipos")], m, est.get("valores"))
    # el equipo lo exige la automatización (por inventario o por su formulario)
    pide_equipo = bool(a["usa_limite"]) or any(c.get("tipo") in ("equipo", "equipos") for c in campos)
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
