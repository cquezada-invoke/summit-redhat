"""Racionalización del stack tecnológico: por cada tecnología (agrupando sus versiones) evalúa su uso real, su soporte y
si se superpone con otra del mismo tipo, y sugiere qué hacer con ella (modelo TIME ampliado):
  Mantener · Invertir · Migrar (a una versión soportada) · Reemplazar (por otra) · Retirar.
La decisión final es de las personas: queda registrada con reemplazo, responsable, fecha objetivo y nota."""
from .bd import ahora, ej, q

DECISIONES = {"mantener": ("Mantener", "Se usa y está soportada: seguir operándola tal cual"),
              "invertir": ("Invertir", "Sostiene servicios críticos: fortalecerla (alta disponibilidad, monitoreo, automatización)"),
              "migrar": ("Migrar", "Se queda la tecnología, pero hay que llevarla a una versión con soporte"),
              "reemplazar": ("Reemplazar", "Cambiarla por otra (consolidar en la que ya domina o en el estándar)"),
              "retirar": ("Retirar", "Casi no se usa o ya no aporta: sacarla del parque")}
ORDEN_FASE = {"fuera": 0, "extendido": 1, "mantencion": 2, "completo": 3, "desconocido": 4}
FASE_TXT = {"fuera": "sin soporte", "extendido": "soporte extendido", "mantencion": "mantención", "completo": "soporte completo", "desconocido": "sin dato"}


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS stack_decisiones (
      id INTEGER PRIMARY KEY, cliente_id INTEGER, tecnologia TEXT, decision TEXT, reemplazo TEXT, responsable TEXT,
      fecha_objetivo TEXT, nota TEXT, autor TEXT, fecha TEXT, UNIQUE(cliente_id, tecnologia));""")


def evaluar(cliente_id, filas, equipos):
    """filas: las del barrido del stack (por tecnología y versión). → tecnologías con uso, soporte, sugerencia y decisión."""
    from .riesgo_global import impacto
    from .permisos import segmentos_visibles  # noqa: F401 - mismo origen de segmentos que el resto de FaceOps
    _tabla()
    segs = {s["nombre"]: s for s in q("SELECT * FROM segmentos WHERE cliente_id=?", cliente_id)}
    eq = {e["nombre"]: e for e in equipos}
    tecs = {}
    for f in filas:
        k = (f["tipo"], f["nombre"])
        t = tecs.setdefault(k, {"tipo": f["tipo"], "nombre": f["nombre"], "versiones": [], "equipos": set(), "peor": "desconocido", "siguiente": ""})
        t["versiones"].append({"version": f["version"], "n": f["n"], "fase": f["fase"], "fin": f.get("fin_soporte") or ""})
        t["equipos"] |= set(f["equipos"])
        if ORDEN_FASE.get(f["fase"], 4) < ORDEN_FASE.get(t["peor"], 4):
            t["peor"], t["siguiente"] = f["fase"], f.get("siguiente") or ""
    por_tipo = {}
    for t in tecs.values():
        por_tipo.setdefault(t["tipo"], []).append(t)
    dec = {d["tecnologia"]: d for d in q("SELECT * FROM stack_decisiones WHERE cliente_id=?", cliente_id)}
    total_srv = max(1, sum(1 for e in equipos if (e.get("categoria") or "servidor") == "servidor"))
    import json as _j
    from datetime import datetime, timedelta, timezone
    desde = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    autos = q("SELECT id, nombre, nombre_aap, etiquetas, descripcion FROM automatizaciones WHERE cliente_id=? AND visible=1", cliente_id)
    usadas = {r["automatizacion_id"]: r["n"] for r in q("SELECT automatizacion_id, COUNT(*) n FROM tickets WHERE cliente_id=? AND creado>=? GROUP BY automatizacion_id", cliente_id, desde)}
    out = []
    for t in tecs.values():
        es = [eq[n] for n in t["equipos"] if n in eq]
        sg = sorted({s for e in es for s in (e.get("segmentos") or [])})
        imp = max([impacto(segs[s]) for s in sg if s in segs] or [0])
        amb = sorted({e.get("ambiente") or "" for e in es} - {""})
        n = len(t["equipos"])
        pares = [x for x in por_tipo[t["tipo"]] if x is not t]
        dominante = max(pares, key=lambda x: len(x["equipos"]), default=None)
        motivo = []
        if n == 0:
            sug = "retirar"; motivo.append("no se encontró en ningún servidor")
        elif dominante and t["tipo"] not in ("sistema operativo",) and len(dominante["equipos"]) >= 2 * n and n <= 2:
            sug = "reemplazar"; motivo.append(f"se usa en {n} servidor{'es' if n != 1 else ''} y {dominante['nombre']} (mismo tipo) en {len(dominante['equipos'])}: consolidar")
        elif t["peor"] == "fuera":
            sug = "migrar" if n >= 2 or imp >= 3 else "reemplazar"
            motivo.append("hay versiones sin soporte del fabricante" + (f" (siguiente: {t['siguiente']})" if t["siguiente"] else ""))
        elif t["peor"] == "extendido":
            sug = "migrar"; motivo.append("en soporte extendido: planificar la migración antes del fin")
        elif imp >= 3 and n >= 2:
            sug = "invertir"; motivo.append("sostiene segmentos de impacto alto o crítico")
        else:
            sug = "mantener"; motivo.append("se usa y tiene soporte")
        if len({v["version"] for v in t["versiones"]}) > 2:
            motivo.append(f"{len({v['version'] for v in t['versiones']})} versiones distintas: estandarizar")
        # nivel de uso: adopción en el parque y aprovechamiento (automatizaciones, monitoreo, respaldo)
        pal = [w for w in t["nombre"].lower().replace("red hat enterprise linux", "rhel").split() if len(w) > 2][:2]
        de_tec = [a for a in autos if pal and any(w in (a["nombre"] + " " + a["nombre_aap"] + " " + (a.get("etiquetas") or "")).lower() for w in pal)]
        en_uso = [a for a in de_tec if usadas.get(a["id"])]
        mon = sum(1 for e in es if {"node-exporter", "node_exporter", "monitoreo"} & set(e.get("skills") or []))
        res = sum(1 for e in es if "respaldo-git" in (e.get("skills") or []))
        adop = n / total_srv
        apro = (min(len(en_uso), 3) / 3 * 0.4 if de_tec else 0) + (mon / n * 0.3 if n else 0) + (res / n * 0.3 if n else 0)
        nivel = "alto" if adop >= 0.3 or n >= 5 else ("medio" if adop >= 0.1 or n >= 2 else "bajo")
        recs = []
        vers = sorted({str(v["version"]) for v in t["versiones"]})
        if len(vers) > 1:
            recs.append(f"Estandarizar en una sola versión (hoy hay {', '.join(vers)})")
        if imp >= 3 and n == 1:
            recs.append("Un solo servidor sostiene un segmento crítico: replicar con alta disponibilidad")
        if de_tec and len(en_uso) < len(de_tec):
            sin = [a["nombre"] for a in de_tec if not usadas.get(a["id"])][:3]
            recs.append(f"Hay {len(de_tec)} automatizaciones para {t['nombre']} y se usaron {len(en_uso)} en 90 días: aprovechar {', '.join(sin)}")
        elif not de_tec and n:
            recs.append(f"Sin automatizaciones propias de {t['nombre']} en el catálogo: crear las de operación frecuente (instalar, configurar, parchar)")
        if n and mon < n:
            recs.append(f"Monitoreo en {mon} de {n} servidores: habilitar el agente de monitoreo en el resto")
        if n and res < n and t["tipo"] != "sistema operativo":
            recs.append(f"Respaldo continuo de configuración en {res} de {n}: habilitarlo para poder recuperar")
        clave = f"{t['tipo']}|{t['nombre']}"
        out.append({"clave": clave, "tipo": t["tipo"], "nombre": t["nombre"], "n": n, "equipos": sorted(t["equipos"]),
                    "versiones": sorted(t["versiones"], key=lambda v: str(v["version"])), "peor": t["peor"], "peor_txt": FASE_TXT[t["peor"]],
                    "segmentos": sg, "impacto": imp, "ambientes": amb, "sugerencia": sug, "motivo": "; ".join(motivo),
                    "alternativas": [x["nombre"] for x in pares], "decision": dec.get(clave),
                    "nivel": nivel, "adopcion": round(100 * adop), "aprovechamiento": round(100 * apro), "autos": len(de_tec), "autos_usadas": len(en_uso),
                    "monitoreo": mon, "respaldo": res, "recomendaciones": recs if sug in ("mantener", "invertir", "migrar") else recs[:1]})
    out.sort(key=lambda x: (list(DECISIONES).index((x["decision"] or {}).get("decision") or x["sugerencia"]) * -1, x["tipo"], -x["n"]))
    return out


def decidir(cliente_id, clave, f, autor):
    _tabla()
    d = f.get("decision", "")
    if d not in DECISIONES:
        raise ValueError("Elige una decisión")
    if d == "reemplazar" and not (f.get("reemplazo") or "").strip():
        raise ValueError("Indica por qué tecnología se reemplaza")
    ej("INSERT INTO stack_decisiones(cliente_id, tecnologia, decision, reemplazo, responsable, fecha_objetivo, nota, autor, fecha) VALUES (?,?,?,?,?,?,?,?,?) "
       "ON CONFLICT(cliente_id, tecnologia) DO UPDATE SET decision=excluded.decision, reemplazo=excluded.reemplazo, responsable=excluded.responsable, "
       "fecha_objetivo=excluded.fecha_objetivo, nota=excluded.nota, autor=excluded.autor, fecha=excluded.fecha",
       cliente_id, clave[:200], d, (f.get("reemplazo") or "").strip()[:120], (f.get("responsable") or "").strip()[:80],
       (f.get("fecha_objetivo") or "")[:10], (f.get("nota") or "").strip()[:400], autor, ahora())
