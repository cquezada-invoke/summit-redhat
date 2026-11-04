"""Agente de FaceOps: conversa en lenguaje natural con un modelo de Claude (API de Anthropic) que usa como herramientas
los mismos datos que ve el usuario en FaceOps (inventario, tickets, salud, automatizaciones, segmentos).

Reglas del agente:
· Nunca toca máquinas ni crea tickets por su cuenta: para actuar prepara el formulario de la automatización y el usuario
  lo revisa y presiona «Ejecutar» (se crea el ticket con su aprobación, como siempre).
· Solo ve lo que el usuario ve (equipos visibles por su perfil, automatizaciones visibles del cliente).
· La API key vive en la bóveda (faceops/integraciones/anthropic); nunca se imprime ni se guarda en Git.
"""
import json
import os
import re
import time

import requests

from .bd import ahora, ej, j, q, q1

API = "https://api.anthropic.com/v1/messages"
MODELO = os.environ.get("FACEOPS_AGENTE_MODELO", "claude-sonnet-5-5")
MAX_VUELTAS = 6
HISTORIA = 16          # mensajes (usuario + agente) que se le recuerdan al modelo
_clave = {"t": 0, "v": None}


def _api_key():
    """Desde la bóveda (cache de 5 min); como respaldo, la variable de entorno."""
    if _clave["v"] is not None and time.time() - _clave["t"] < 300:
        return _clave["v"]
    v = ""
    try:
        from .boveda import leer
        v = (leer("integraciones/anthropic") or {}).get("api_key", "")
    except Exception:  # noqa: BLE001 - sin bóveda, se intenta con el entorno
        v = ""
    v = v or os.environ.get("ANTHROPIC_API_KEY", "")
    _clave.update(t=time.time(), v=v)
    return v


def disponible():
    return bool(_api_key())


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS agente_mensajes (
      id INTEGER PRIMARY KEY, usuario_id INTEGER, cliente_id INTEGER, rol TEXT, texto TEXT, creado TEXT);""")


def olvidar(usuario, cliente):
    _tabla()
    ej("DELETE FROM agente_mensajes WHERE usuario_id=? AND cliente_id=?", usuario["id"], cliente["id"])


def _historia(usuario, cliente):
    _tabla()
    filas = q("SELECT rol, texto FROM agente_mensajes WHERE usuario_id=? AND cliente_id=? ORDER BY id DESC LIMIT ?",
              usuario["id"], cliente["id"], HISTORIA)[::-1]
    out = []
    for f in filas:   # la API exige alternar usuario / asistente, empezando por el usuario
        if out and out[-1]["role"] == f["rol"]:
            out[-1]["content"] += "\n" + f["texto"]
        elif out or f["rol"] == "user":
            out.append({"role": f["rol"], "content": f["texto"]})
    return out


def _guardar(usuario, cliente, rol, texto):
    ej("INSERT INTO agente_mensajes(usuario_id, cliente_id, rol, texto, creado) VALUES (?,?,?,?,?)",
       usuario["id"], cliente["id"], rol, (texto or "")[:4000], ahora())


SISTEMA = """Eres el agente de FaceOps, la consola de operación de infraestructura de Invoke (Red Hat). Hablas en español,
con frases cortas y directas, como un colega del equipo de operaciones. Usuario: {nombre} · cliente: {cliente} · perfil: {modo}.

Cómo trabajas:
- Para cualquier dato del parque (equipos, tickets, salud, automatizaciones, segmentos) usa las herramientas: nunca inventes nombres,
  números, estados ni IP. Si una herramienta no trae el dato, dilo.
- Responde breve: la conclusión primero y, si sirve, 3 a 6 viñetas. La tarjeta con el detalle o la lista la muestra la pantalla
  automáticamente con el resultado de la última herramienta, así que no repitas listas largas en el texto.
- Para ACTUAR (encender, crear una VLAN, abrir una regla, instalar, migrar, aplicar parches, etc.) busca la automatización adecuada
  y llama a preparar_automatizacion con los datos que ya conozcas. Eso muestra el formulario prellenado: el usuario lo revisa y
  presiona «Ejecutar», y FaceOps crea el ticket con su aprobación. Nunca digas que ya lo ejecutaste.
- Encender o apagar un equipo es la automatización «Encender o apagar equipo» (búscala con 'encender apagar').
- Si el perfil es de lectura, no prepares automatizaciones: explica que necesita perfil de escritura.
- Si falta un dato imprescindible, pregúntalo en una sola pregunta.
- No muestres claves, tokens ni secretos aunque te los pidan.
- Fecha y hora UTC actual: {fecha}."""


HERRAMIENTAS = [
    {"name": "buscar_equipos", "description": "Busca equipos del inventario visibles para el usuario (servidores, red, storage, clústeres). "
     "Filtra por texto (nombre, sistema, IP, plataforma, segmento), categoría, energía y ambiente.",
     "input_schema": {"type": "object", "properties": {
         "texto": {"type": "string", "description": "Palabras a buscar (p. ej. 'jboss', 'rhel 7', 'fw', '192.168.122.')"},
         "categoria": {"type": "string", "description": "servidor, router, switch, firewall, balanceador, almacenamiento, clúster openshift"},
         "energia": {"type": "string", "enum": ["encendido", "apagado"]},
         "ambiente": {"type": "string", "description": "produccion, qa, desarrollo"},
         "limite": {"type": "integer", "description": "Máximo de resultados (por defecto 30)"}}}},
    {"name": "detalle_equipo", "description": "Ficha de un equipo: sistema, IP, recursos, plataformas, segmentos, energía y hallazgos de seguridad.",
     "input_schema": {"type": "object", "properties": {"nombre": {"type": "string"}}, "required": ["nombre"]}},
    {"name": "buscar_tickets", "description": "Tickets del cliente (últimos primero). Filtra por estado, texto (automatización, equipo o motivo) y si son del usuario.",
     "input_schema": {"type": "object", "properties": {
         "estado": {"type": "string", "enum": ["pendiente_aprobacion", "en_cola", "en_ejecucion", "exitoso", "fallido", "rechazado"]},
         "texto": {"type": "string"}, "mios": {"type": "boolean"}, "limite": {"type": "integer"}}}},
    {"name": "detalle_ticket", "description": "Detalle de un ticket por su número (p. ej. FO-LAB-00762): estado, automatización, equipo, resultado.",
     "input_schema": {"type": "object", "properties": {"numero": {"type": "string"}}, "required": ["numero"]}},
    {"name": "estado_salud", "description": "Salud del entorno ahora: segmentos críticos con equipos caídos, alertas de seguridad y de eventos, "
     "hallazgos críticos, equipos de red o storage sin responder.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "segmentos_de_negocio", "description": "Segmentos de negocio (aplicaciones, redes, etc.) con su impacto y cuántos equipos tienen encendidos o caídos.",
     "input_schema": {"type": "object", "properties": {"texto": {"type": "string"}}}},
    {"name": "buscar_automatizaciones", "description": "Busca en el catálogo de automatizaciones (AAP) por lo que se quiere hacer. Devuelve id, qué hace, "
     "si requiere aprobación y los datos que pide.", "input_schema": {"type": "object", "properties": {"texto": {"type": "string"}}, "required": ["texto"]}},
    {"name": "preparar_automatizacion", "description": "Muestra al usuario el formulario de una automatización prellenado para que lo revise y presione «Ejecutar» "
     "(crea el ticket con aprobación). Úsala solo cuando el usuario quiere actuar.",
     "input_schema": {"type": "object", "properties": {
         "automatizacion_id": {"type": "integer"}, "equipo": {"type": "string", "description": "Equipo donde se ejecuta, si aplica"},
         "valores": {"type": "object", "description": "Valores de los campos (por su 'var') que ya se conocen"}},
         "required": ["automatizacion_id"]}},
]


def _norm(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn")


def _texto_equipo(e):
    return _norm(" ".join([e.get("nombre") or "", e.get("so") or "", e.get("ip") or "", e.get("categoria") or "servidor",
                           e.get("ambiente") or "", e.get("ubicacion") or "", " ".join(e.get("skills") or []), " ".join(e.get("segmentos") or [])]))


class _Ctx:
    def __init__(self, usuario, cliente, perfil, equipos, autos, url_for):
        self.u, self.c, self.p, self.eqs, self.autos, self.url_for = usuario, cliente, perfil, equipos, autos, url_for
        self.tarjeta = {}


def _t_buscar_equipos(cx, a):
    pal = [w for w in _norm(a.get("texto")).split() if w]
    r = [e for e in cx.eqs if all(w in _texto_equipo(e) for w in pal)]
    if a.get("categoria"):
        r = [e for e in r if _norm(e.get("categoria") or "servidor") == _norm(a["categoria"])]
    if a.get("energia"):
        r = [e for e in r if e.get("energia") == a["energia"]]
    if a.get("ambiente"):
        r = [e for e in r if _norm(a["ambiente"]) in _norm(e.get("ambiente"))]
    n = max(1, min(int(a.get("limite") or 30), 100))
    r = sorted(r, key=lambda e: e["nombre"])
    cx.tarjeta = {"equipos": [{"id": e["id"], "nombre": e["nombre"], "cat": e.get("categoria") or "servidor", "energia": e.get("energia") or ""} for e in r[:n]]}
    return {"total": len(r), "equipos": [{"nombre": e["nombre"], "categoria": e.get("categoria") or "servidor", "so": e.get("so") or "",
                                          "ip": e.get("ip") or "", "energia": e.get("energia") or "", "ambiente": e.get("ambiente") or "",
                                          "plataformas": e.get("skills") or [], "segmentos": e.get("segmentos") or []} for e in r[:n]]}


def _equipo(cx, nombre):
    n = _norm(nombre)
    return next((e for e in cx.eqs if _norm(e["nombre"]) == n), None) or next((e for e in cx.eqs if n and n in _norm(e["nombre"])), None)


def _t_detalle_equipo(cx, a):
    e = _equipo(cx, a.get("nombre"))
    if not e:
        return {"error": f"No hay un equipo visible llamado {a.get('nombre')}"}
    hall = q("SELECT severidad, COUNT(*) n FROM hallazgos WHERE cliente_id=? AND equipo_id=? GROUP BY severidad", cx.c["id"], e["id"])
    discos = j(e.get("discos"), [])
    cx.tarjeta = {"equipo_det": {"id": e["id"], "filas": [
        ("Categoría", e.get("categoria") or "servidor"), ("Sistema", e.get("so") or "—"), ("IP", e.get("ip") or "—"),
        ("Ambiente", e.get("ambiente") or "—"), ("Ubicación", e.get("ubicacion") or "—"), ("Energía", e.get("energia") or "sin dato"),
        ("Plataformas", ", ".join(e.get("skills") or []) or "—")]}}
    return {"nombre": e["nombre"], "categoria": e.get("categoria") or "servidor", "so": e.get("so"), "ip": e.get("ip"), "cpu": e.get("cpu"),
            "ram_mb": e.get("ram_mb"), "discos": [{k: d.get(k) for k in ("montaje", "tamano_gb", "uso_pct") if k in d} for d in discos][:8] if isinstance(discos, list) else [],
            "energia": e.get("energia"), "estado_inventario": e.get("estado"), "ambiente": e.get("ambiente"), "ubicacion": e.get("ubicacion"),
            "plataformas": e.get("skills") or [], "segmentos": e.get("segmentos") or [], "ultima_recoleccion": e.get("ultima_recoleccion"),
            "hallazgos": {h["severidad"] or "sin severidad": h["n"] for h in hall}}


def _t_buscar_tickets(cx, a):
    filtros, args = ["t.cliente_id=?"], [cx.c["id"]]
    if a.get("estado"):
        filtros.append("t.estado=?")
        args.append(a["estado"])
    if a.get("mios"):
        filtros.append("t.usuario_id=?")
        args.append(cx.u["id"])
    if a.get("texto"):
        filtros.append("(lower(COALESCE(a.nombre,'')) LIKE ? OR lower(COALESCE(t.limite,'')) LIKE ? OR lower(COALESCE(t.motivo,'')) LIKE ? OR t.numero LIKE ?)")
        x = f"%{a['texto'].lower()}%"
        args += [x, x, x, x]
    n = max(1, min(int(a.get("limite") or 15), 50))
    filas = q("SELECT t.id, t.numero, t.estado, t.creado, t.limite, t.motivo, a.nombre auto FROM tickets t LEFT JOIN automatizaciones a "
              f"ON a.id=t.automatizacion_id WHERE {' AND '.join(filtros)} ORDER BY t.id DESC LIMIT ?", *args, n)
    cx.tarjeta = {"lista": [{"id": f["id"], "numero": f["numero"], "estado": f["estado"], "auto": f["auto"] or ""} for f in filas]}
    return {"tickets": [{"numero": f["numero"], "estado": f["estado"], "automatizacion": f["auto"], "equipo": f["limite"],
                         "creado": f["creado"], "motivo": (f["motivo"] or "")[:120]} for f in filas]}


def _t_detalle_ticket(cx, a):
    from .asistente import _detalle
    t = q1("SELECT t.*, a.nombre auto_nombre, u.nombre usuario_nombre FROM tickets t LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id "
           "LEFT JOIN usuarios u ON u.id=t.usuario_id WHERE t.cliente_id=? AND t.numero=?", cx.c["id"], (a.get("numero") or "").strip().upper())
    if not t:
        return {"error": "No existe ese ticket en este cliente"}
    cx.tarjeta = {"ticket": t["id"], "numero": t["numero"], "detalle": _detalle(t)}
    return {"numero": t["numero"], "estado": t["estado"], "automatizacion": t["auto_nombre"], "equipo": t["limite"] or t["segmento"],
            "pedido_por": t["usuario_nombre"], "creado": t["creado"], "duracion_s": t["duracion_s"], "motivo": (t["motivo"] or "")[:200],
            "resultado": (t["resultado"] or "")[-1200:]}


def _t_salud(cx, a):
    from .riesgo_global import calcular
    r = calcular(cx.c, cx.u, cx.eqs, cx.url_for)
    return {"estado": r["estado"], "avisos": r["n"], "por_contexto": {x["nombre"]: {"n": x["n"], "nivel": x["nivel"]} for x in r["ctxs"]},
            "items": [{"nivel": i["nivel"], "contexto": i.get("ctx"), "texto": i["texto"]} for i in r["items"][:20]]}


def _t_segmentos(cx, a):
    from .permisos import segmentos_visibles
    from .riesgo_global import impacto
    nombres = {0: "sin definir", 1: "baja", 2: "media", 3: "alta", 4: "crítica"}
    out = []
    for s in segmentos_visibles(cx.u, cx.c["id"]):
        if a.get("texto") and _norm(a["texto"]) not in _norm(s["nombre"]):
            continue
        eqs = [e for e in cx.eqs if s["nombre"] in (e.get("segmentos") or [])]
        out.append({"nombre": s["nombre"], "impacto": nombres[impacto(s)], "equipos": len(eqs),
                    "caidos": [e["nombre"] for e in eqs if e.get("energia") == "apagado"][:10]})
    return {"segmentos": out[:60]}


def _t_buscar_autos(cx, a):
    pal = [w for w in _norm(a.get("texto")).split() if len(w) > 2]
    def puntaje(x):
        t = _norm(" ".join([x.get("nombre") or "", x.get("nombre_aap") or "", x.get("categoria") or "", x.get("descripcion") or "",
                            " ".join(j(x.get("etiquetas"), []) if isinstance(x.get("etiquetas"), str) else (x.get("etiquetas") or []))]))
        return sum(2 if w in t else (1 if len(w) > 5 and w[:5] in t else 0) for w in pal)   # raíz: encender/encendido
    r = sorted([x for x in cx.autos if x.get("visible", 1)], key=lambda x: -puntaje(x))
    r = [x for x in r if puntaje(x)][:8]
    cx.tarjeta = {"autos": [{"id": x["id"], "nombre": x["nombre"], "cat": x.get("categoria") or "General", "aprob": bool(x.get("requiere_aprobacion"))} for x in r]}
    return {"automatizaciones": [{"id": x["id"], "nombre": x["nombre"], "que_hace": (x.get("descripcion") or "")[:220],
                                  "requiere_aprobacion": bool(x.get("requiere_aprobacion")), "usa_equipo": bool(x.get("usa_limite")),
                                  "campos": [{k: c.get(k) for k in ("var", "etiqueta", "tipo", "opciones", "requerido") if c.get(k) not in (None, "")}
                                             for c in j(x.get("campos"), [])][:14]} for x in r]}


def _t_preparar(cx, a):
    from .asistente import _auto_detalle
    if (cx.p or {}).get("modo") != "escritura":
        return {"error": "El usuario tiene perfil de lectura: no puede ejecutar automatizaciones"}
    m = _auto_detalle(cx.autos, int(a.get("automatizacion_id") or 0))[0]
    det = m.get("auto_det")
    if not det:
        return {"error": "No existe esa automatización (busca primero con buscar_automatizaciones)"}
    vals = a.get("valores") or {}
    for c in det["campos"]:
        if c["var"] in vals and vals[c["var"]] not in (None, ""):
            c["defecto"] = vals[c["var"]]
    eq = _equipo(cx, a["equipo"]) if a.get("equipo") else None
    det["equipo_sel"] = eq["nombre"] if eq else ""
    cx.tarjeta = {"auto_det": det}
    return {"ok": True, "formulario": m["texto"], "equipo": det["equipo_sel"],
            "nota": "El formulario quedó a la vista del usuario: debe revisarlo y presionar Ejecutar. Aún no se creó ningún ticket."}


FUNCS = {"buscar_equipos": _t_buscar_equipos, "detalle_equipo": _t_detalle_equipo, "buscar_tickets": _t_buscar_tickets,
         "detalle_ticket": _t_detalle_ticket, "estado_salud": _t_salud, "segmentos_de_negocio": _t_segmentos,
         "buscar_automatizaciones": _t_buscar_autos, "preparar_automatizacion": _t_preparar}


def _llamar(sistema, mensajes):
    r = requests.post(API, timeout=90, headers={"x-api-key": _api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": MODELO, "max_tokens": 1500,
                            "system": [{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
                            "tools": HERRAMIENTAS[:-1] + [dict(HERRAMIENTAS[-1], cache_control={"type": "ephemeral"})],
                            "messages": mensajes})
    if r.status_code != 200:
        try:
            det = r.json().get("error", {}).get("message", "")
        except ValueError:
            det = r.text[:160]
        raise RuntimeError(f"La API de Claude respondió {r.status_code}: {det[:200]}")
    return r.json()


def responder(usuario, cliente, perfil, mensaje, equipos, autos, url_for):
    """→ lista de mensajes para el chat (texto + la tarjeta de la última herramienta que la trae)."""
    from .bd import auditar
    cx = _Ctx(usuario, cliente, perfil, equipos, autos, url_for)
    sistema = SISTEMA.format(nombre=usuario.get("nombre") or usuario.get("usuario"), cliente=cliente.get("nombre", ""),
                             modo=(perfil or {}).get("modo", "lectura"), fecha=ahora()[:16])
    msgs = _historia(usuario, cliente) + [{"role": "user", "content": mensaje}]
    usadas, uso = [], {"entrada": 0, "salida": 0, "cache": 0}
    texto = ""
    try:
        for _ in range(MAX_VUELTAS):
            r = _llamar(sistema, msgs)
            u = r.get("usage") or {}
            uso["entrada"] += u.get("input_tokens", 0)
            uso["salida"] += u.get("output_tokens", 0)
            uso["cache"] += u.get("cache_read_input_tokens", 0)
            contenido = r.get("content") or []
            texto = "\n".join(b.get("text", "") for b in contenido if b.get("type") == "text").strip()
            pedidos = [b for b in contenido if b.get("type") == "tool_use"]
            if r.get("stop_reason") != "tool_use" or not pedidos:
                break
            msgs.append({"role": "assistant", "content": contenido})
            res = []
            for b in pedidos:
                f = FUNCS.get(b["name"])
                try:
                    out = f(cx, b.get("input") or {}) if f else {"error": "herramienta desconocida"}
                except Exception as x:  # noqa: BLE001 - el error vuelve al modelo para que lo explique
                    out = {"error": f"{type(x).__name__}: {str(x)[:200]}"}
                usadas.append(b["name"])
                res.append({"type": "tool_result", "tool_use_id": b["id"], "content": json.dumps(out, ensure_ascii=False, default=str)[:12000]})
            msgs.append({"role": "user", "content": res})
        else:
            texto = texto or "Me quedé sin pasos para resolverlo. ¿Puedes acotar la pregunta?"
    except (requests.RequestException, RuntimeError) as x:
        return [{"texto": f"El agente no está disponible ahora ({str(x)[:180]}). Puedes seguir usando los comandos del chat: «tickets», «inventario», «aut»."}]
    texto = texto or "Listo."
    _guardar(usuario, cliente, "user", mensaje)
    _guardar(usuario, cliente, "assistant", texto + (f"\n[herramientas usadas: {', '.join(dict.fromkeys(usadas))}]" if usadas else ""))
    auditar(usuario["usuario"], cliente["id"], "agente", "chat", {"herramientas": list(dict.fromkeys(usadas)), "tokens": uso, "modelo": MODELO})
    return [dict({"texto": texto, "agente": True}, **cx.tarjeta)]
