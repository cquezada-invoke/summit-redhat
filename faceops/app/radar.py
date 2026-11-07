"""Radar de salud: los mismos 5 principios (Disponibilidad, Hallazgos, Rendimiento, Seguridad, Recuperación) en todo FaceOps.

- Segmentos de negocio: la salud de cada segmento (topologia.salud_segmento) con sus criterios.
- Cualquier conjunto de equipos (servidores, storage, redes, sistemas operativos, plataformas, bases de datos…): criterios
  por máquina; cada criterio es un «caso» con su barra de cumplimiento (% de equipos que lo cumplen) y el principio es el
  promedio de sus criterios. Lo que no aplica a un equipo no lo castiga."""
import re

from .bd import j
from .topologia import PRINCIPIOS_SALUD, metas_salud

# Criterios por equipo: (id, principio, nombre, explicación)
CRITERIOS_EQ = [
    ("D1", "Disponibilidad", "Encendido", "El equipo está encendido o respondiendo"),
    ("D2", "Disponibilidad", "Vigilado: alerta de disponibilidad", "Tiene configurada en Ansible la alerta «sin respuesta» (botón Alertas)"),
    ("H1", "Hallazgos", "Sin hallazgos graves", "Ningún hallazgo de severidad alta abierto"),
    ("H2", "Hallazgos", "Sin advertencias", "Ningún hallazgo de severidad media abierto"),
    ("P1", "Rendimiento", "Con métricas de rendimiento", "Tiene el agente de métricas (node_exporter o windows_exporter)"),
    ("P2", "Rendimiento", "Vigilado: alertas de capacidad", "Tiene alertas de CPU, memoria o disco configuradas"),
    ("S1", "Seguridad", "Sin hallazgos de seguridad", "Sin hallazgos de seguridad, CVE, accesos o cifrado abiertos"),
    ("S2", "Seguridad", "Con soporte del fabricante", "Su sistema o plataforma recibe parches (no está fuera de soporte ni solo con soporte extendido)"),
    ("R1", "Recuperación", "Con respaldo continuo", "Configuración, paquetes y servicios respaldados en Git para reconstruirlo"),
]
# Criterios por segmento: los de topologia.salud_segmento, reconocidos por su título
CRITERIOS_SEG = [
    ("SD1", "Disponibilidad", "Con servicio", "No está completamente apagado", r"completamente apagado"),
    ("SD2", "Disponibilidad", "Máquinas encendidas", "Ninguna máquina del segmento apagada", r"máquina\(s\) apagada"),
    ("SD3", "Disponibilidad", "Alta disponibilidad en producción", "Ningún componente con una sola máquina", r"sin alta disponibilidad"),
    ("SH1", "Hallazgos", "Sin hallazgos graves", "Ningún hallazgo alto en sus máquinas", None),
    ("SH2", "Hallazgos", "Sin advertencias", "Ningún hallazgo medio en sus máquinas", None),
    ("SP1", "Rendimiento", "Flujos abiertos", "Todos los flujos entre componentes pasan", r"flujo\(s\) cerrado"),
    ("SP2", "Rendimiento", "Latencia bajo 20 ms", "Ningún flujo con latencia alta", r"Latencia alta"),
    ("SS1", "Seguridad", "Flujos cifrados", "Todos los flujos con TLS", r"sin TLS"),
    ("SR1", "Recuperación", "Máquinas con respaldo", "Todas con respaldo continuo", r"sin respaldo continuo"),
    ("SR2", "Recuperación", "RTO y RPO definidos", "La ficha del segmento define RTO y RPO", r"RTO/RPO"),
]
SEG_KW = re.compile(r"seguridad|cve|vulner|acceso|ssh|root|tls|cifr|certific|contraseñ|firewall|selinux|puerto", re.I)


def _datos(e):
    return e.get("_datos") if isinstance(e.get("_datos"), dict) else (j(e.get("datos"), {}) or {})


def casos_equipo(e, hs_eq):
    """{criterio: True/False/None (no aplica)} para un equipo; hs_eq = sus hallazgos abiertos."""
    from .alertas_objeto import tipo_de
    d = _datos(e)
    sk = set(e.get("skills") or []) | set(j(e.get("skills_auto"), []) if isinstance(e.get("skills_auto"), str) else []) \
        | set(j(e.get("skills_manual"), []) if isinstance(e.get("skills_manual"), str) else [])
    al = {x.get("id") for x in ((d.get("alertas") or {}).get("alertas") or [])}
    vigilable = bool(tipo_de(e))
    sev = [str(h.get("severidad") or "").lower() for h in hs_eq]
    try:
        from .ciclo_vida import evaluar
        cv = evaluar(dict(e, _datos=d)) or {}
    except Exception:  # noqa: BLE001
        cv = {}
    es_srv = (e.get("categoria") or "servidor") in ("servidor", "almacenamiento", "estación de trabajo")
    return {
        "D1": (e.get("energia") or e.get("estado")) not in ("apagado", "suspendido"),
        "D2": ("D01" in al or "D02" in al) if vigilable else None,
        "H1": not any(s in ("alta", "critica", "crítica", "critical", "high") for s in sev),
        "H2": not any(s in ("media", "medium") for s in sev),
        "P1": bool(sk & {"monitoreo", "node_exporter", "windows_exporter"}) or bool(d.get("monitoreo")) if es_srv else None,
        "P2": bool(al & {"C01", "C02", "C03", "W01", "W02", "W03", "D03"}) if vigilable else None,
        "S1": not any(SEG_KW.search(f"{h.get('titulo', '')} {h.get('tipo', '')} {h.get('politica', '')}") for h in hs_eq),
        "S2": (cv.get("fase") not in ("fuera", "extendido")) if cv.get("fase") not in (None, "desconocido") else None,
        "R1": ("respaldo-git" in sk) if es_srv else None,
    }


def radar_equipos(eqs, hallazgos, cliente=None):
    """Radar de un conjunto de equipos: % por principio, cada criterio con su barra y los equipos que no lo cumplen."""
    por_eq = {}
    for h in hallazgos or []:
        por_eq.setdefault(h.get("equipo_id"), []).append(h)
    cuenta = {c[0]: [0, 0, []] for c in CRITERIOS_EQ}   # cumplen, aplica, quiénes no
    for e in eqs:
        for k, v in casos_equipo(e, por_eq.get(e["id"], [])).items():
            if v is None:
                continue
            cuenta[k][1] += 1
            if v:
                cuenta[k][0] += 1
            else:
                cuenta[k][2].append({"id": e["id"], "nombre": e["nombre"]})
    crit = []
    for cid, pr, nom, exp in CRITERIOS_EQ:
        ok, n, faltan = cuenta[cid]
        crit.append({"id": cid, "principio": pr, "nombre": nom, "explica": exp, "ok": ok, "n": n, "pct": round(100 * ok / n) if n else None,
                     "faltan": faltan[:30], "n_faltan": len(faltan)})
    return _armar(crit, len(eqs), cliente)


def _armar(crit, n_obj, cliente=None):
    meta = metas_salud()
    principios = []
    for p in PRINCIPIOS_SALUD:
        cs = [c for c in crit if c["principio"] == p]
        val = [c["pct"] for c in cs if c["pct"] is not None]
        principios.append({"nombre": p, "pct": round(sum(val) / len(val)) if val else None, "meta": meta.get(p, 100), "criterios": cs})
    vals = [p["pct"] for p in principios if p["pct"] is not None]
    return {"principios": principios, "pct": round(sum(vals) / len(vals)) if vals else None, "n": n_obj,
            "actual": {p["nombre"]: (p["pct"] if p["pct"] is not None else 100) for p in principios}, "meta": meta}


def radar_segmentos(cliente, eqs, hallazgos, ambiente="produccion", grupo="aplicaciones"):
    """Radar de los segmentos de negocio: cada criterio = % de segmentos que lo cumplen."""
    from .bd import q
    from .topologia import resumen_segmento
    segs = [s["nombre"] for s in q("SELECT nombre FROM segmentos WHERE cliente_id=? AND grupo=?", cliente["id"], grupo)]
    filas, cuenta = [], {c[0]: [0, 0, []] for c in CRITERIOS_SEG}
    for sg in segs:
        try:
            r = resumen_segmento(cliente, eqs, hallazgos, sg, ambiente)
        except Exception:  # noqa: BLE001
            continue
        if not r["equipos"] and not r.get("ocp"):
            continue
        items = r.get("salud") or []
        filas.append({"nombre": sg, "pct": r["salud_pct"], "areas": {a["area"]: a["pct"] for a in r["salud_areas"]}})
        for cid, pr, nom, exp, rx in CRITERIOS_SEG:
            if cid == "SH1":
                falla = any(x["area"] == "Hallazgos" and x["nivel"] == "alta" for x in items)
            elif cid == "SH2":
                falla = any(x["area"] == "Hallazgos" and x["nivel"] == "media" for x in items)
            else:
                falla = any(re.search(rx, x.get("titulo", ""), re.I) for x in items)
            cuenta[cid][1] += 1
            if falla:
                cuenta[cid][2].append({"nombre": sg})
            else:
                cuenta[cid][0] += 1
    crit = [{"id": cid, "principio": pr, "nombre": nom, "explica": exp, "ok": cuenta[cid][0], "n": cuenta[cid][1],
             "pct": round(100 * cuenta[cid][0] / cuenta[cid][1]) if cuenta[cid][1] else None, "faltan": cuenta[cid][2][:30], "n_faltan": len(cuenta[cid][2])}
            for cid, pr, nom, exp, _ in CRITERIOS_SEG]
    out = _armar(crit, len(filas))
    out["segmentos"] = sorted(filas, key=lambda f: f["pct"])
    return out


def radar_componente(r_maq, c):
    """Radar de una plataforma instalada en la máquina: los mismos 5 principios. Disponibilidad, Hallazgos y Seguridad
    salen de lo medido para la plataforma (su servicio, sus remediaciones, sus CVEs y políticas); Rendimiento y Recuperación
    son los de la máquina que la aloja (la plataforma comparte sus métricas y su respaldo)."""
    import copy
    if not r_maq:
        return None
    secs = {s.get("clave"): s.get("salud") for s in (c.get("secciones") or [])}
    pri = copy.deepcopy(r_maq["principios"])
    def fijar(nombre, *claves, base=True):
        vals = [secs[k] for k in claves if secs.get(k) is not None]
        for p in pri:
            if p["nombre"] == nombre and vals:
                v = min(vals)
                p["pct"] = min(p["pct"], v) if (base and p["pct"] is not None) else v
                p["criterios"] = p["criterios"] + [{"id": "PL", "nombre": f"{c.get('nombre')}: " + ", ".join(k for k in claves if secs.get(k) is not None),
                                                    "pct": v, "explica": "medido para esta plataforma", "principio": nombre}]
    fijar("Disponibilidad", "servicio")
    fijar("Hallazgos", "remediaciones", base=False)
    fijar("Seguridad", "cves", "politicas")
    vals = [p["pct"] for p in pri if p["pct"] is not None]
    return dict(r_maq, principios=pri, pct=round(sum(vals) / len(vals)) if vals else None, n=1)
