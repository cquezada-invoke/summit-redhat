"""Escaneo de seguridad: evalúa toda la infraestructura en busca de puntos de vulnerabilidad, por dimensiones que se van
sumando gradualmente. Fase 1: servidores (automatización de AAP de solo lectura «Seguridad · Escaneo de servidores»).
Cada escaneo es un ticket con aprobación; sus hallazgos quedan con severidad, recomendación y la automatización que lo
corrige (si existe)."""
import json

from .bd import ahora, ej, q, q1

AUTOMATIZACION = "Seguridad · Escaneo de servidores"

# Dimensiones del escaneo: las activas se evalúan hoy; las demás se van incorporando
DIMENSIONES = [
    ("servidores", "Servidores", "Parches y CVE, SSH, puertos expuestos, SELinux, firewall, cuentas, sudo, permisos, contraseñas, servicios inseguros, suscripción y fin de vida", True),
    ("aplicaciones", "Aplicaciones", "Librerías vulnerables, secretos en el código, configuración de los servidores de aplicación", False),
    ("plataformas", "Plataformas", "JBoss, bases de datos, cachés y colas: versiones, credenciales por defecto, consolas expuestas", False),
    ("contenedores", "Contenedores y OpenShift", "Imágenes vulnerables, políticas de ACS, permisos y namespaces", False),
    ("redes", "Redes", "Reglas de firewall abiertas, puertos expuestos a internet, equipos sin respaldo de configuración", False),
    ("datos", "Datos", "Bases de datos expuestas, respaldos sin cifrar, datos sensibles sin protección", False),
    ("identidades", "Identidades y accesos", "Usuarios huérfanos, privilegios excesivos, cuentas compartidas, MFA", False),
]

AREA = {"parches": "Parches y CVE", "acceso": "Acceso SSH", "red": "Red y puertos", "datos": "Datos expuestos", "servicios": "Servicios inseguros",
        "endurecimiento": "Endurecimiento", "cuentas": "Cuentas y privilegios", "sistema": "Ciclo de vida"}
SEV_ORDEN = {"critica": 0, "alta": 1, "media": 2, "baja": 3}
SEV_TXT = {"critica": "crítica", "alta": "alta", "media": "media", "baja": "baja"}
PESO = {"critica": 10, "alta": 5, "media": 2, "baja": 1}

# Automatización del catálogo que corrige cada control (si existe)
REMEDIO = {"cve-criticos": "Seguridad · Aplicar parches de seguridad", "cve-importantes": "Seguridad · Aplicar parches de seguridad",
           "sin-suscripcion": "Sistema · Registrar en Red Hat", "reinicio": "Seguridad · Aplicar parches de seguridad",
           "eol": "Migración · RHEL a versión soportada", "ssh-root": "FaceOps · Aplicar política de seguridad",
           "ssh-clave": "FaceOps · Aplicar política de seguridad", "selinux": "FaceOps · Aplicar política de seguridad",
           "firewall": "FaceOps · Aplicar política de seguridad"}


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS escaneos (
      id INTEGER PRIMARY KEY, cliente_id INTEGER, ticket_id INTEGER, dimension TEXT, inicio TEXT, fin TEXT, estado TEXT,
      equipos INTEGER DEFAULT 0, sin_respuesta TEXT DEFAULT '[]', resumen TEXT DEFAULT '{}', tickets TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS escaneo_hallazgos (
      id INTEGER PRIMARY KEY, escaneo_id INTEGER, cliente_id INTEGER, equipo TEXT, dimension TEXT, area TEXT, control TEXT,
      severidad TEXT, titulo TEXT, detalle TEXT, recomendacion TEXT);""")


def objetivos(equipos):
    """Servidores Linux encendidos que se escanean (los apagados quedan fuera y se informan)."""
    linux = [e for e in equipos if (e.get("categoria") or "servidor") == "servidor"
             and "windows" not in ((e.get("so") or "") + (e.get("so_familia") or "")).lower()]
    return [e for e in linux if e.get("energia") != "apagado"], [e for e in linux if e.get("energia") == "apagado"]


def solicitar(usuario, cliente, equipos, motivo=""):
    """Un escaneo = un ticket por inventario de AAP (cada uno con su credencial), todos con aprobación."""
    from .cves import solicitar as solicitar_por_inventario
    on, off = objetivos(equipos)
    if not on:
        raise ValueError("No hay servidores Linux encendidos para escanear")
    _tabla()
    ts = solicitar_por_inventario(usuario, cliente, AUTOMATIZACION, on, {"esc_dimension": "servidores"},
                                  motivo or f"Escaneo de seguridad de servidores ({len(on)} encendidos)")
    if not ts:
        raise ValueError("Ningún servidor tiene una credencial conocida en AAP para escanearlo")
    ej("INSERT INTO escaneos(cliente_id, ticket_id, dimension, inicio, estado, equipos, sin_respuesta, tickets) VALUES (?,?,?,?,?,?,?,?)",
       cliente["id"], ts[0]["id"], "servidores", ahora(), "en curso", len(on), json.dumps(sorted(e["nombre"] for e in off)),
       json.dumps([t["id"] for t in ts]))
    return ts


def registrar(cliente_id, ticket, artefactos, estado_ticket):
    """Al terminar cada ticket del escaneo: guarda los hallazgos de sus servidores; cuando terminan todos, cierra el escaneo."""
    _tabla()
    esc = next((e for e in q("SELECT * FROM escaneos WHERE cliente_id=? AND estado='en curso'", cliente_id)
                if ticket["id"] in json.loads(e["tickets"] or "[]") or e["ticket_id"] == ticket["id"]), None)
    if not esc:
        return
    datos = artefactos.get("faceops_escaneo") or {}
    pedidos = [x for x in (ticket.get("limite") or "").split(":") if x]
    for equipo in pedidos:
        ej("DELETE FROM escaneo_hallazgos WHERE escaneo_id=? AND equipo=?", esc["id"], equipo)
    for equipo, d in datos.items():
        for linea in (d or {}).get("hallazgos") or []:
            dim, control, sev, titulo, detalle, rec = (linea.split("\t") + [""] * 6)[:6]
            if sev not in SEV_ORDEN:
                continue
            ej("INSERT INTO escaneo_hallazgos(escaneo_id, cliente_id, equipo, dimension, area, control, severidad, titulo, detalle, recomendacion) "
               "VALUES (?,?,?,?,?,?,?,?,?,?)", esc["id"], cliente_id, equipo, "servidores", dim, control, sev, titulo[:240], detalle[:400], rec[:300])
    sin = set(json.loads(esc["sin_respuesta"] or "[]")) | {x for x in pedidos if x not in datos}   # apagados o sin respuesta
    res = json.loads(esc["resumen"] or "{}")
    res["escaneados"] = sorted(set(res.get("escaneados") or []) | set(datos))
    ej("UPDATE escaneos SET sin_respuesta=?, resumen=? WHERE id=?", json.dumps(sorted(sin)), json.dumps(res), esc["id"])
    ids = json.loads(esc["tickets"] or "[]") or [esc["ticket_id"]]
    pend = q(f"SELECT id FROM tickets WHERE id IN ({','.join('?' * len(ids))}) AND estado IN ('pendiente_aprobacion','en_cola','en_ejecucion')", *ids)
    if not pend or (len(pend) == 1 and pend[0]["id"] == ticket["id"]):
        hs = q("SELECT severidad FROM escaneo_hallazgos WHERE escaneo_id=?", esc["id"])
        res["por_severidad"] = {s_: sum(1 for h in hs if h["severidad"] == s_) for s_ in SEV_ORDEN}
        ej("UPDATE escaneos SET fin=?, estado=?, resumen=? WHERE id=?", ahora(), "terminado" if res["escaneados"] else "sin datos",
           json.dumps(res), esc["id"])


def puntaje(hallazgos, n_equipos):
    """0-100: 100 = sin hallazgos. Resta por severidad, normalizado por la cantidad de servidores escaneados."""
    if not n_equipos:
        return None
    castigo = sum(PESO.get(h["severidad"], 0) for h in hallazgos) / n_equipos
    return max(0, round(100 - castigo * 4))


def vista(cliente_id):
    _tabla()
    escs = q("SELECT e.*, t.numero, t.estado ticket_estado FROM escaneos e LEFT JOIN tickets t ON t.id=e.ticket_id WHERE e.cliente_id=? ORDER BY e.id DESC LIMIT 20", cliente_id)
    for e in escs:
        e["resumen"] = json.loads(e["resumen"] or "{}")
        e["sin_respuesta"] = json.loads(e["sin_respuesta"] or "[]")
    ult = next((e for e in escs if e["estado"] == "terminado"), None)
    hall = []
    if ult:
        hall = q("SELECT * FROM escaneo_hallazgos WHERE escaneo_id=?", ult["id"])
        hall.sort(key=lambda h: (SEV_ORDEN.get(h["severidad"], 9), h["area"], h["equipo"]))
        ult["n_escaneados"] = len(ult["resumen"].get("escaneados") or [])
        ult["puntaje"] = puntaje(hall, ult["n_escaneados"])
    autos = {a["nombre_aap"]: a for a in q("SELECT id, nombre, nombre_aap FROM automatizaciones WHERE cliente_id=?", cliente_id)}
    for h in hall:
        h["remedio"] = autos.get(REMEDIO.get(h["control"], ""))
        h["area_txt"] = AREA.get(h["area"], h["area"])
    por_area = {}
    for h in hall:
        a = por_area.setdefault(h["area_txt"], {"n": 0, "peor": "baja"})
        a["n"] += 1
        if SEV_ORDEN[h["severidad"]] < SEV_ORDEN[a["peor"]]:
            a["peor"] = h["severidad"]
    por_equipo = {}
    for h in hall:
        x = por_equipo.setdefault(h["equipo"], {"n": 0, "critica": 0, "alta": 0})
        x["n"] += 1
        if h["severidad"] in ("critica", "alta"):
            x[h["severidad"]] += 1
    en_curso = next((e for e in escs if e["estado"] == "en curso"), None)
    return {"escaneos": escs, "ultimo": ult, "hallazgos": hall, "por_area": por_area,
            "por_equipo": sorted(por_equipo.items(), key=lambda kv: (-kv[1]["critica"], -kv[1]["alta"], -kv[1]["n"])), "en_curso": en_curso}


# ── Miradas NIST CSF 2.0 y CIS Controls v8 ──────────────────────────────────────────────────────────────────────────
def _mapeo():
    import os

    import yaml
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "escaneo-marcos.yml")
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _tipo(mp, control):
    for t in mp.get("tipos") or []:
        if t.get("control") == control or (t.get("prefijo") and (control or "").startswith(t["prefijo"])):
            return t
    return None


def _cuenta(hs):
    return {s_: sum(1 for h in hs if h["severidad"] == s_) for s_ in SEV_ORDEN}


def _pct(hs, tipos, n_srv):
    """Cumplimiento = pares (servidor × verificación) sin hallazgo sobre el total de pares evaluados."""
    if not tipos or not n_srv:
        return None
    malos = {(h["equipo"], h["_tipo"]) for h in hs if h.get("_tipo")}
    return round(100 * (1 - len(malos) / (len(tipos) * n_srv)))


def marcos(hall, n_srv):
    """Agrupa los hallazgos por función/categoría NIST CSF 2.0 y por control CIS v8, con «sin clasificar» visible."""
    from .seguridad_mapa import FUNCIONES, NIVELES, nivel
    mp = _mapeo()
    cats, subs, sg = mp.get("nist_categorias") or {}, mp.get("nist_subcategorias") or {}, mp.get("cis_salvaguardas") or {}
    for h in hall:
        t = _tipo(mp, h["control"])
        h["_tipo"] = (t.get("control") or t.get("prefijo")) if t else None
        h["_tnom"] = t.get("nombre") if t else h["control"]
        h["_nist"] = t.get("nist") if t else None
        h["_cis"] = str(t["cis"]) if t and t.get("cis") else None
    tipos = mp.get("tipos") or []
    tid = lambda t: t.get("control") or t.get("prefijo")  # noqa: E731
    # NIST
    funciones = []
    for fk, fn in FUNCIONES:
        cs = []
        for ck, cn in cats.items():
            if not ck.startswith(fk + "."):
                continue
            ts = [t for t in tipos if (t.get("nist") or "").startswith(ck + "-")]
            hs = [h for h in hall if (h["_nist"] or "").startswith(ck + "-")]
            if not ts and not hs:
                continue
            p = _pct(hs, [tid(t) for t in ts], n_srv)
            sub = {}
            for h in hs:
                sub.setdefault(h["_nist"], []).append(h)
            cs.append({"id": ck, "nombre": cn, "n": len(hs), "sev": _cuenta(hs), "pct": p, "nivel": nivel(p),
                       "verificaciones": [t["nombre"] for t in ts],
                       "sub": [{"id": k, "nombre": subs.get(k, ""), "hallazgos": sorted(v, key=lambda h: (SEV_ORDEN[h["severidad"]], h["equipo"]))}
                               for k, v in sorted(sub.items())]})
        ts_f = [tid(t) for t in tipos if (t.get("nist") or "").startswith(fk + ".")]
        hs_f = [h for h in hall if (h["_nist"] or "").startswith(fk + ".")]
        p = _pct(hs_f, ts_f, n_srv)
        funciones.append({"id": fk, "nombre": fn, "n": len(hs_f), "sev": _cuenta(hs_f), "pct": p, "nivel": nivel(p),
                          "evaluada": bool(ts_f), "categorias": cs})
    sin_nist = [h for h in hall if not h["_nist"]]
    # CIS
    controles = []
    for num, nom in (mp.get("cis_controles") or {}).items():
        ts = [t for t in tipos if t.get("cis") and str(t["cis"]).split(".")[0] == str(num)]
        hs = [h for h in hall if h["_cis"] and h["_cis"].split(".")[0] == str(num)]
        if not ts and not hs:
            continue
        sal = {}
        for t in ts:
            sal.setdefault(str(t["cis"]), {"id": str(t["cis"]), **(sg.get(str(t["cis"])) or {"nombre": "", "ig": None}), "hallazgos": [], "tipos": []})["tipos"].append(t["nombre"])
        for h in hs:
            sal.setdefault(h["_cis"], {"id": h["_cis"], **(sg.get(h["_cis"]) or {"nombre": "", "ig": None}), "hallazgos": [], "tipos": []})["hallazgos"].append(h)
        for s in sal.values():
            s["hallazgos"].sort(key=lambda h: (SEV_ORDEN[h["severidad"]], h["equipo"]))
        p = _pct(hs, [tid(t) for t in ts], n_srv)
        igs = sorted({s["ig"] for s in sal.values() if s.get("ig")})
        controles.append({"num": int(num), "nombre": nom, "n": len(hs), "sev": _cuenta(hs), "pct": p,
                          "igs": igs, "salvaguardas": sorted(sal.values(), key=lambda s: [int(x) for x in s["id"].split(".")])})
    sin_cis = [h for h in hall if not h["_cis"]]
    return {"funciones": funciones, "sin_nist": sin_nist, "controles": controles, "sin_cis": sin_cis, "NIVELES": NIVELES,
            "no_evaluados": [f"{n} · {nom}" for n, nom in (mp.get("cis_controles") or {}).items() if int(n) not in {c["num"] for c in controles}],
            "fuente": mp.get("fuente", ""), "n_tipos": len(tipos)}


def refs_politicas():
    """Referencias NIST CSF 2.0 / CIS v8 de cada política (seguridad y desarrollo), desde la tabla de mapeo compartida.
    Una política sin entrada (o con listas vacías) queda «sin clasificar»."""
    mp = _mapeo()
    subs, sg, ctl = mp.get("nist_subcategorias") or {}, mp.get("cis_salvaguardas") or {}, mp.get("cis_controles") or {}
    out = {}
    for cod, r in (mp.get("politicas") or {}).items():
        r = r or {}
        out[cod] = {"nist": [{"id": n, "cat": n.split("-")[0], "fn": n[:2], "nombre": subs.get(n, "")} for n in r.get("nist") or []],
                    "cis": [{"id": str(c), "control": int(str(c).split(".")[0]), "nombre": (sg.get(str(c)) or {}).get("nombre", ""),
                             "ig": (sg.get(str(c)) or {}).get("ig"), "control_nombre": ctl.get(int(str(c).split(".")[0]), "")} for c in r.get("cis") or []]}
    return out


def cis_controles():
    return {int(k): v for k, v in (_mapeo().get("cis_controles") or {}).items()}
