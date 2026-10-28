"""Buscador por intención: interpreta frases como "reiniciar jboss en producción" o "equipos sin parches en gcp"
y devuelve filtros entendidos + resultados (automatizaciones, equipos, hallazgos y tickets).
v1: diccionario de sinónimos y entidades del propio cliente (segmentos, skills, equipos). Diseñado para cambiar
el intérprete por un modelo de lenguaje sin tocar el resto."""
import re

from .bd import j, q
from .inventario import normal, segmentos_de, skills_de

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
        txt = normal(" ".join([a["nombre"], a["nombre_aap"], a["descripcion"], a["categoria"], a["ambito"], " ".join(j(a["etiquetas"], []))]))
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
