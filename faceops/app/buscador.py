"""Buscador por intención: interpreta frases como "reiniciar jboss en producción" o "equipos sin parches en gcp"
y devuelve filtros entendidos + resultados (automatizaciones, equipos, hallazgos y tickets).
v1: diccionario de sinónimos y entidades del propio cliente (segmentos, skills, equipos). Diseñado para cambiar
el intérprete por un modelo de lenguaje sin tocar el resto."""
import re

from .bd import j, q
from .inventario import normal, segmentos_de, sistema_de, skills_de

ACCIONES = {
    "reiniciar": ["reinici", "restart", "levant", "rearranc", "caido", "caida"],
    "parchar": ["parch", "actualiz", "firmware", "update", "patch", "vulnerab"],
    "acceso de red": ["acceso", "puerto", "firewall", "abrir", "regla", "red "],
    "bloquear": ["bloque", "ataque", "sospech", "siem"],
    "identidad": ["usuario", "identidad", "alta ", "baja ", "cuenta", "clave", "contrasena", "midpoint", "keycloak"],
    "desplegar": ["despleg", "deploy", "version", "war", "pipeline"],
    "respaldar": ["respald", "backup", "restaur"],
    "verificar": ["verific", "revis", "estado", "salud", "diagnost", "reporte", "auditor"],
    "certificado": ["certificad", "tls", "ssl"],
    "disco": ["disco", "espacio", "lleno", "logs"],
    "nube": ["vm en la nube", "gcp", "google", "nube"],
}
AMBIENTES = {"produccion": ["prod", "produccion", "productivo"], "qa": ["qa", "calidad", "test", "pruebas"],
             "desarrollo": ["dev", "desarrollo"], "demo": ["demo", "stand", "laboratorio", "lab"]}
ESTADOS_T = {"fallido": ["fallid", "fallo", "error"], "pendiente_aprobacion": ["pendiente", "aprobar", "por aprobar"],
             "en_ejecucion": ["en curso", "ejecutando", "corriendo"], "exitoso": ["exitos", "ok ", "terminad"]}
PROBLEMAS = {"politica": ["no cumple", "incumpl", "riesgo", "seguridad", "hallazgo"], "recoleccion": ["sin datos", "no responde"],
             "parches": ["sin parche", "parches pendientes"], "disco": ["disco lleno", "sin espacio"]}


def interpretar(texto, cliente_id):
    t = " " + normal(texto) + " "
    m = re.search(r"fo-[a-z0-9]+-\d+", t)
    ticket = m.group(0).upper() if m else None
    if m:
        t = t.replace(m.group(0), " ")
        texto = re.sub(r"(?i)fo-[a-z0-9]+-\d+", " ", texto)
    f = {"texto": texto.strip(), "acciones": [], "ambientes": [], "segmentos": [], "skills": [], "equipos": [],
         "estados": [], "problemas": [], "ticket": None, "lectura": None, "libre": []}
    for a, sin in ACCIONES.items():
        if any(s in t for s in sin):
            f["acciones"].append(a)
    for a, sin in AMBIENTES.items():
        if any(re.search(r"\b" + re.escape(s) + r"\b", t) for s in sin):
            f["ambientes"].append(a)
    for e, sin in ESTADOS_T.items():
        if any(s in t for s in sin):
            f["estados"].append(e)
    for p, sin in PROBLEMAS.items():
        if any(s in t for s in sin):
            f["problemas"].append(p)
    if re.search(r"\bsolo lectura\b|\bconsultar\b|\bver\b", t):
        f["lectura"] = True
    f["ticket"] = ticket
    for s in q("SELECT nombre FROM segmentos WHERE cliente_id=?", cliente_id):
        n = normal(s["nombre"])
        if n in t or any(len(p) > 3 and re.search(r"\b" + re.escape(p) + r"\b", t) for p in n.split() if p not in ("data", "center")):
            f["segmentos"].append(s["nombre"])
    for s in q("SELECT nombre FROM skills WHERE cliente_id=?", cliente_id):
        claves = [normal(s["nombre"])] + SINONIMOS_SKILL.get(s["nombre"], [])
        if any(re.search(r"\b" + re.escape(c) + r"\b", t) for c in claves):
            f["skills"].append(s["nombre"])
    for e in q("SELECT DISTINCT nombre FROM equipos WHERE cliente_id=? AND estado!='retirado'", cliente_id):
        if normal(e["nombre"]) in t:
            f["equipos"].append(e["nombre"])
    conocidas = set()
    for lista in (ACCIONES, AMBIENTES, ESTADOS_T, PROBLEMAS):
        for sin in lista.values():
            conocidas.update(s.strip() for s in sin)
    vacias = {"de", "del", "la", "el", "en", "los", "las", "con", "sin", "para", "que", "un", "una", "y", "o", "a", "mis", "todos", "todas"}
    f["libre"] = [p for p in normal(texto).split() if p not in vacias and not any(p.startswith(c) for c in conocidas if c)]
    return f


def buscar(texto, cliente_id, autos, equipos, tickets, hallazgos):
    """Recibe colecciones ya filtradas por permisos; devuelve (filtros, resultados ordenados)."""
    f = interpretar(texto, cliente_id)
    segmentos = q("SELECT * FROM segmentos WHERE cliente_id=?", cliente_id)

    def puntaje_auto(a):
        gu = j(a.get("guia"), {})
        txt = normal(" ".join([a["nombre"], a["nombre_aap"], a["descripcion"], a["categoria"], a["ambito"], a.get("demo") or "",
                               " ".join(j(a["etiquetas"], [])), " ".join(j(a.get("skills"), [])),
                               gu.get("proposito", ""), gu.get("cuando", ""), gu.get("cambios", "")]))
        p = 0
        for acc in f["acciones"]:
            p += 5 * any(s.strip() in txt for s in ACCIONES[acc]) + 3 * (normal(acc) in txt)
        for s in f["skills"] + f["segmentos"]:
            p += 3 * (normal(s) in txt)
        for w in f["libre"]:
            p += 2 * (w in txt)
        if f["lectura"]:
            p += 2 * (a["tipo"] == "lectura")
        return p

    def ok_equipo(e):
        if f["equipos"] and e["nombre"] not in f["equipos"]:
            return False
        if f["skills"] and not set(f["skills"]) & set(skills_de(e)):
            return False
        if f["segmentos"] and not set(f["segmentos"]) & set(segmentos_de(e, segmentos)):
            return False
        if f["ambientes"] and normal(e["ambiente"]) not in f["ambientes"]:
            return False
        return bool(f["equipos"] or f["skills"] or f["segmentos"] or f["ambientes"] or
                    any(w in normal(e["nombre"] + " " + e["so"]) for w in f["libre"]))

    r_autos = sorted(((puntaje_auto(a), a) for a in autos), key=lambda x: -x[0])
    r_autos = [a for p, a in r_autos if p > 0][:12]
    r_eq = [e for e in equipos if ok_equipo(e)][:40]
    ids_eq = {e["id"] for e in r_eq}
    r_hal = [h for h in hallazgos if (h["equipo_id"] in ids_eq if ids_eq else True) and
             (not f["problemas"] or h["tipo"] in f["problemas"] or any(p in normal(h["titulo"]) for p in f["problemas"]))]
    r_hal = r_hal[:30] if (f["problemas"] or ids_eq) else []
    r_tk = [t for t in tickets if (f["ticket"] and t["numero"] == f["ticket"]) or
            (f["estados"] and t["estado"] in f["estados"]) or
            (not f["ticket"] and not f["estados"] and any(w in normal(t.get("auto_nombre", "")) for w in f["libre"]))][:20]
    return f, {"automatizaciones": r_autos, "equipos": r_eq, "hallazgos": r_hal, "tickets": r_tk}


# ------------------------------------------------------------------ inventario
VACIAS_EQ = {"de", "del", "la", "el", "en", "los", "las", "con", "sin", "para", "que", "un", "una", "y", "o", "a", "mis",
             "todos", "todas", "equipo", "equipos", "servidor", "servidores", "maquina", "maquinas", "nodo", "nodos",
             "muestrame", "mostrar", "ver", "dame", "lista", "cuales", "estan", "esta", "tienen", "tiene", "hay"}
INFRA_ALIAS = {"centro de datos": ["centro de datos", "data center", "datacenter", " dc ", "santiago", "on premise", "onpremise"],
               "gcp": ["nube", "gcp", "google", "cloud"]}


def _coincide(palabra, texto):
    """Coincidencia flexible: la palabra o su raíz (5 letras) aparece en el texto."""
    return palabra in texto or (len(palabra) > 5 and palabra[:5] in texto)


# Cómo se nombra cada producto al buscar («git» → servidores con Gitea, «registry» → registros, etc.)
SINONIMOS_SKILL = {
    "gitea": ["git", "repositorio git", "repositorios", "control de versiones", "scm"],
    "registro-paquetes": ["registry", "registro de paquetes", "rpm", "repositorio de paquetes", "instaladores", "isos"],
    "automation-hub": ["registry", "registro de contenedores", "container registry", "contenedores", "imagenes", "hub", "private automation hub"],
    "aap": ["ansible", "automation platform", "controller"],
    "certificate-system": ["ca", "pki", "certificate system", "autoridad certificadora", "certificados"],
    "kvm": ["hipervisor", "virtualizacion", "libvirt"],
    "vault": ["vault", "boveda", "contrasenas", "secretos", "credenciales", "password manager"],
    "postgres": ["postgresql", "base de datos", "bd"],
    "jboss": ["eap", "wildfly", "servidor de aplicaciones"],
    "nginx": ["proxy", "balanceador web"],
    "keycloak": ["sso", "single sign on"],
    "active-directory": ["ad", "directorio activo"],
    "node-exporter": ["monitoreo", "prometheus"],
}


def buscar_equipos(texto, cliente_id, equipos, hallazgos_por_equipo):
    """Interpreta frases como «jboss de producción con hallazgos», «windows sin parches», «data center» o «nodo-so»
    y devuelve (lo entendido, equipos que cumplen todo)."""
    # un nombre de máquina, hostname o IP (o parte de él) se busca tal cual, antes de interpretar la frase:
    # «srv-test-vinculacion-01» no debe leerse como ambiente «test»
    tq = normal(texto).strip()
    if tq and " " not in tq:
        def _claves(e):
            d = e.get("_datos") or e.get("datos") or {}
            if isinstance(d, str):
                try:
                    import json as _json
                    d = _json.loads(d or "{}")
                except ValueError:
                    d = {}
            return [normal(x) for x in (e.get("nombre"), e.get("ip"), (d or {}).get("hostname"), e.get("codigo_cliente")) if x]
        directos = [e for e in equipos if any(tq == k for k in _claves(e))] or \
                   ([e for e in equipos if any(tq in k for k in _claves(e))] if len(tq) >= 3 and re.search(r"[-.\d]", tq) else [])
        if directos:
            return {"equipos": [e["nombre"] for e in directos], "segmentos": [], "skills": [], "ambientes": [], "infra": [],
                    "categorias": [], "formas": [], "sistemas": [], "palabras": [], "hallazgos": None, "energia": None, "vacios": False}, directos
    f = interpretar(texto, cliente_id)
    t = " " + normal(texto) + " "
    # «nodo-jboss3» no debe traer también «nodo-jboss»; y solo cuentan los ambientes que existen en el parque
    f["equipos"] = [n for n in f["equipos"] if not any(o != n and normal(n) in normal(o) for o in f["equipos"])]
    hay_amb = {normal(e.get("ambiente") or "") for e in equipos}
    f["ambientes"] = [a for a in f["ambientes"] if a in hay_amb]
    infras = sorted({e["ubicacion"] for e in equipos if e.get("ubicacion")})
    f["infra"] = []
    for u in infras:
        nu = normal(u)
        alias = [a for k, v in INFRA_ALIAS.items() if k in nu for a in v]
        if nu in t or any(a in t for a in alias):
            f["infra"].append(u)
    cats = {"router": ["router"], "switch": ["switch"], "firewall": ["firewall", "fortigate", "cortafuego"],
            "servidor": ["servidor", "server"], "balanceador": ["balanceador"], "estación de trabajo": ["estacion", "pc "]}
    f["categorias"] = [c for c, sin in cats.items() if any(s in t for s in sin)]
    if re.search(r"firewall\w* (apagad|inactiv|desactiv|caid)|sin firewall", t):  # es un hallazgo, no la categoría
        f["categorias"] = [c for c in f["categorias"] if c != "firewall"]
    f["formas"] = [x for x, sin in {"virtual": ["virtual", " vm"], "físico": ["fisico", "bare metal", "baremetal"]}.items() if any(s in t for s in sin)]
    f["sistemas"] = [s for s, sin in {"windows": ["windows"], "linux": ["linux", "rhel", "red hat"]}.items() if any(x in t for x in sin)]
    f["vacios"] = bool(re.search(r"\bvaci[oa]s?\b|sin skills?|sin uso|apagar", t))
    t_sin_fw = re.sub(r"firewall\w* (apagad\w*|inactiv\w*|desactiv\w*|caid\w*)", " ", t)
    f["energia"] = "apagado" if re.search(r"\bapagad[oa]s?\b|\bsin respuesta\b|\bcaid[oa]s?\b", t_sin_fw) else \
        ("encendido" if re.search(r"\bencendid[oa]s?\b|\bprendid[oa]s?\b|\bactiv[oa]s\b", t_sin_fw) else None)
    f["hallazgos"] = "con" if re.search(r"con (hallazgos|problemas|riesgos)|no cumple|incumpl", t) else \
        ("sin" if re.search(r"sin (hallazgos|problemas)|\bcumplen\b|\bsanos\b", t) else None)
    # Palabras que no se usaron para segmento, skill, ambiente, infraestructura o equipo: se buscan en nombre, sistema,
    # IP y en el texto de los hallazgos (p. ej. «firewall apagado», «parches», «registro»)
    usadas = set()
    for n in f["segmentos"] + f["skills"] + f["infra"] + f["equipos"]:
        usadas.update(normal(n).split())
    for a in f["ambientes"]:
        usadas.update(AMBIENTES[a])
    usadas.update({"windows", "linux", "rhel"} if f["sistemas"] else set())
    usadas.update({"router", "routers", "switch", "switches", "firewall", "firewalls", "fortigate", "servidor", "server", "balanceador",
                   "estacion"} if f["categorias"] else set())
    usadas.update({"virtual", "virtuales", "vm", "vms", "fisico", "fisicos"} if f["formas"] else set())
    for a in f["infra"]:
        usadas.update(x.strip() for k, v in INFRA_ALIAS.items() if k in normal(a) for x in v)
    palabras = [p for p in re.findall(r"[a-z0-9.\-]+", normal(texto))
                if p not in VACIAS_EQ and p not in usadas and not any(p.startswith(u) for u in usadas if len(u) > 3)
                and not re.fullmatch(r"hallazgos?|problemas?|riesgos?|cumplen?|incumpl\w*|sanos?|vaci[oa]s?|skills?|uso|apagar", p)
                and not (f["energia"] and re.fullmatch(r"apagad[oa]s?|encendid[oa]s?|prendid[oa]s?|activ[oa]s|caid[oa]s?|respuesta", p))]
    f["palabras"] = palabras
    segmentos = q("SELECT * FROM segmentos WHERE cliente_id=?", cliente_id)
    out = []
    for e in equipos:
        hs = hallazgos_por_equipo.get(e["id"], [])
        if f["equipos"] and e["nombre"] not in f["equipos"]:
            continue
        if f["skills"] and not set(f["skills"]) & set(skills_de(e)):
            continue
        if f["segmentos"] and not set(f["segmentos"]) & set(segmentos_de(e, segmentos)):
            continue
        if f["ambientes"] and normal(e.get("ambiente") or "") not in f["ambientes"]:
            continue
        if f["categorias"] and (e.get("categoria") or "") not in f["categorias"]:
            continue
        if f["formas"] and (e.get("forma") or "") not in f["formas"]:
            continue
        if f["vacios"] and (skills_de(e) or e.get("estado") != "con datos" or (e.get("categoria") or "servidor") != "servidor"):
            continue
        if f["sistemas"] and sistema_de(e) not in f["sistemas"]:
            continue
        if f["infra"] and e.get("ubicacion") not in f["infra"]:
            continue
        if f["hallazgos"] == "con" and not hs or f["hallazgos"] == "sin" and hs:
            continue
        if f["energia"] and e.get("energia") != f["energia"]:
            continue
        txt = normal(" ".join([e["nombre"], e.get("so") or "", e.get("ip") or "", e.get("estado") or ""] +
                              [h["titulo"] + " " + (h.get("detalle") or "") for h in hs]))
        if not all(_coincide(p, txt) for p in palabras):
            continue
        out.append(e)
    return f, out


ESTADOS_TICKET = {"exitoso": ["exitos", "ok", "bien", "termin", "complet"], "fallido": ["fallid", "fallo", "falló", "error", "malo", "fall"],
                  "pendiente_aprobacion": ["pendient", "aprobar", "aprobacion", "por aprobar", "esperando"],
                  "en_ejecucion": ["ejecucion", "ejecutando", "corriendo", "en curso"], "cancelado": ["cancel"], "rechazado": ["rechaz"],
                  "en_cola": ["cola"]}
VACIAS_T = {"de", "del", "la", "el", "los", "las", "en", "con", "que", "y", "a", "por", "para", "ticket", "tickets", "los", "un", "una"}


def buscar_tickets(texto, tickets):
    """Buscador de tickets: número (355, 00355, FO-LAB-00355), estado («fallidos», «por aprobar»), fecha («hoy», «ayer»,
    «semana»), y cualquier palabra de la automatización, solicitante, motivo, equipos, segmento o resultado.
    Devuelve (lo entendido, tickets que cumplen todo)."""
    from datetime import datetime, timedelta, timezone
    t = normal(texto).strip()
    ent = {"numero": None, "estados": [], "fecha": None, "palabras": []}
    if not t:
        return ent, tickets
    m = re.fullmatch(r"(?:[a-z]+-)*[a-z]*-?0*(\d+)", t.replace(" ", ""))
    if m and re.search(r"\d", t) and (t.replace(" ", "").isdigit() or "-" in t):
        n = int(m.group(1))
        dir_ = [x for x in tickets if x["id"] == n or re.search(r"(\d+)$", x["numero"] or "") and int(re.search(r"(\d+)$", x["numero"]).group(1)) == n]
        if dir_:
            ent["numero"] = dir_[0]["numero"]
            return ent, dir_
    resto = " " + t + " "
    for est, sins in ESTADOS_TICKET.items():
        for s_ in sins:
            if re.search(r"\b" + re.escape(normal(s_)), resto):
                if est not in ent["estados"]:
                    ent["estados"].append(est)
                resto = re.sub(r"\b" + re.escape(normal(s_)) + r"\w*", " ", resto)
    hoy = datetime.now(timezone.utc).date()
    for k, (d0, d1) in {"hoy": (hoy, hoy), "ayer": (hoy - timedelta(days=1), hoy - timedelta(days=1)),
                        "semana": (hoy - timedelta(days=7), hoy), "mes": (hoy - timedelta(days=31), hoy)}.items():
        if re.search(r"\b" + k + r"\b", resto):
            ent["fecha"] = (k, d0.isoformat(), d1.isoformat())
            resto = re.sub(r"\b(esta |este |ultima |ultimo )?" + k + r"\b", " ", resto)
    ent["palabras"] = [w for w in re.findall(r"[a-z0-9.\-_]+", resto) if w not in VACIAS_T and len(w) > 1]
    out = []
    for x in tickets:
        if ent["estados"] and x["estado"] not in ent["estados"]:
            continue
        if ent["fecha"] and not (ent["fecha"][1] <= (x["creado"] or "")[:10] <= ent["fecha"][2]):
            continue
        heno = normal(" ".join(str(x.get(k) or "") for k in ("numero", "auto_nombre", "categoria", "solicitante", "motivo", "limite",
                                                              "segmento", "parametros", "resultado", "estado")).replace(":", " ").replace("_", " "))
        if all(w in heno for w in ent["palabras"]):
            out.append(x)
    return ent, out
