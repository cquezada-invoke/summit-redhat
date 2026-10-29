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
        if re.search(r"\b" + re.escape(normal(s["nombre"])) + r"\b", t):
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
INFRA_ALIAS = {"data center": ["data center", "datacenter", "dc1", "dc "], "nube": ["nube", "gcp", "google", "cloud"],
               "red": ["laboratorio de red", " red ", "routers", "containerlab"], "stand": ["stand", "kvm", "nodos kvm"]}


def _coincide(palabra, texto):
    """Coincidencia flexible: la palabra o su raíz (5 letras) aparece en el texto."""
    return palabra in texto or (len(palabra) > 5 and palabra[:5] in texto)


def buscar_equipos(texto, cliente_id, equipos, hallazgos_por_equipo):
    """Interpreta frases como «jboss de producción con hallazgos», «windows sin parches», «data center» o «nodo-so»
    y devuelve (lo entendido, equipos que cumplen todo)."""
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
    f["sistemas"] = [s for s, sin in {"windows": ["windows"], "linux": ["linux", "rhel", "red hat"]}.items() if any(x in t for x in sin)]
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
    for a in f["infra"]:
        usadas.update(x.strip() for k, v in INFRA_ALIAS.items() if k in normal(a) for x in v)
    palabras = [p for p in re.findall(r"[a-z0-9.\-]+", normal(texto))
                if p not in VACIAS_EQ and p not in usadas and not any(p.startswith(u) for u in usadas if len(u) > 3)
                and not re.fullmatch(r"hallazgos?|problemas?|riesgos?|cumplen?|incumpl\w*|sanos?", p)]
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
        if f["sistemas"] and sistema_de(e) not in f["sistemas"]:
            continue
        if f["infra"] and e.get("ubicacion") not in f["infra"]:
            continue
        if f["hallazgos"] == "con" and not hs or f["hallazgos"] == "sin" and hs:
            continue
        txt = normal(" ".join([e["nombre"], e.get("so") or "", e.get("ip") or "", e.get("estado") or ""] +
                              [h["titulo"] + " " + (h.get("detalle") or "") for h in hs]))
        if not all(_coincide(p, txt) for p in palabras):
            continue
        out.append(e)
    return f, out
