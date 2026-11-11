"""Radar de salud: los mismos 6 pilares (Disponibilidad, Rendimiento, Seguridad, Recuperación, Costo, Excelencia operativa; AWS Well-Architected) en todo FaceOps.

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
    ("H1", "Excelencia operativa", "Sin hallazgos graves", "Ningún hallazgo de severidad alta abierto"),
    ("H2", "Excelencia operativa", "Sin advertencias", "Ningún hallazgo de severidad media abierto"),
    ("P1", "Rendimiento", "Con métricas de rendimiento", "Tiene el agente de métricas (node_exporter o windows_exporter)"),
    ("P2", "Rendimiento", "Vigilado: alertas de capacidad", "Tiene alertas de CPU, memoria o disco configuradas"),
    ("S1", "Seguridad", "Sin hallazgos de seguridad", "Sin hallazgos de seguridad, CVE, accesos o cifrado abiertos"),
    ("S2", "Seguridad", "Con soporte del fabricante", "Su sistema o plataforma recibe parches (no está fuera de soporte ni solo con soporte extendido)"),
    ("R1", "Recuperación", "Con respaldo continuo", "Configuración, paquetes y servicios respaldados en Git para reconstruirlo"),
    ("C1", "Costo", "Sin recursos ociosos", "No tiene hallazgos de recursos sin uso (CPU, memoria o máquina completa)"),
    ("C2", "Costo", "Costo imputable", "Pertenece a un segmento de negocio: su costo se asigna a un servicio"),
    ("E1", "Excelencia operativa", "Gestionado por Ansible", "Está en el inventario de AAP: se opera con automatizaciones, no a mano"),
]
# Criterios por segmento: los de topologia.salud_segmento, reconocidos por su título
CRITERIOS_SEG = [
    ("SD1", "Disponibilidad", "Con servicio", "No está completamente apagado", r"completamente apagado"),
    ("SD2", "Disponibilidad", "Máquinas encendidas", "Ninguna máquina del segmento apagada", r"máquina\(s\) apagada"),
    ("SD3", "Disponibilidad", "Alta disponibilidad en producción", "Ningún componente con una sola máquina", r"sin alta disponibilidad"),
    ("SH1", "Excelencia operativa", "Sin hallazgos graves", "Ningún hallazgo alto en sus máquinas", None),
    ("SH2", "Excelencia operativa", "Sin advertencias", "Ningún hallazgo medio en sus máquinas", None),
    ("SP1", "Rendimiento", "Flujos abiertos", "Todos los flujos entre componentes pasan", r"flujo\(s\) cerrado"),
    ("SP2", "Rendimiento", "Latencia bajo el umbral", "Ningún flujo con latencia sobre el umbral del cliente", r"Latencia (alta|sobre el umbral)"),
    ("SS1", "Seguridad", "Flujos cifrados", "Todos los flujos con TLS", r"sin TLS"),
    ("SR1", "Recuperación", "Máquinas con respaldo", "Todas con respaldo continuo", r"sin respaldo continuo"),
    ("SR2", "Recuperación", "RTO y RPO definidos", "La ficha del segmento define RTO y RPO", r"RTO/RPO"),
    ("SC1", "Costo", "Sin máquinas ociosas", "Ninguna máquina del segmento sin uso o sobredimensionada", r"sin uso|ocios|sobredimension"),
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
        "C1": not any((h.get("tipo") or "") == "sin_uso" for h in hs_eq),
        "C2": bool(e.get("segmentos")) if es_srv else None,
        "E1": bool(e.get("aap_host_id")) or bool(e.get("inventarios") and e.get("inventarios") not in ("[]", [])),
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
                falla = any(x["area"] == "Excelencia operativa" and x["nivel"] == "alta" for x in items)
            elif cid == "SH2":
                falla = any(x["area"] == "Excelencia operativa" and x["nivel"] == "media" for x in items)
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
    """Radar de una plataforma instalada en la máquina: los mismos pilares. Disponibilidad, Excelencia operativa (remediaciones) y Seguridad
    salen de lo medido para la plataforma (su servicio, sus remediaciones, sus CVEs y políticas); Rendimiento y Recuperación
    son los de la máquina que la aloja (la plataforma comparte sus métricas y su respaldo)."""
    import copy
    if not r_maq:
        return None
    secs = {s.get("clave"): s.get("salud") for s in (c.get("secciones") or [])}
    pri = copy.deepcopy(r_maq["principios"])
    def fijar(nombre, *claves, base=True, peor=False):
        # Promedio: lo medido para la plataforma (cada sección es un subítem) junto con el pilar de la máquina (base=True).
        # peor=True = criterio bloqueante: el pilar es el peor de todos (solo Disponibilidad: si el servicio cae, no está disponible).
        vals = [secs[k] for k in claves if secs.get(k) is not None]
        for p in pri:
            if p["nombre"] == nombre and vals:
                v = min(vals) if peor else round(sum(vals) / len(vals))
                todos = vals + ([p["pct"]] if (base and p["pct"] is not None) else [])
                p["pct"] = min(todos) if peor else round(sum(todos) / len(todos))
                p["criterios"] = p["criterios"] + [{"id": "PL", "nombre": f"{c.get('nombre')}: " + ", ".join(k for k in claves if secs.get(k) is not None),
                                                    "pct": v, "explica": "medido para esta plataforma", "principio": nombre}]
    fijar("Disponibilidad", "servicio", peor=True)
    fijar("Excelencia operativa", "remediaciones", base=False)
    fijar("Seguridad", "cves", "politicas")
    vals = [p["pct"] for p in pri if p["pct"] is not None]
    return dict(r_maq, principios=pri, pct=round(sum(vals) / len(vals)) if vals else None, n=1)


def _pct_uso(u):
    """Salud de un recurso según su uso frente a lo asignado: 15–70 % es lo sano; sobre 70 % baja; bajo 15 % está sobredimensionado."""
    if u is None:
        return None
    if u > 85:
        return max(0, round(70 - (u - 85) * 4.6))
    if u > 70:
        return round(100 - (u - 70) * 2)
    return 100 if u >= 15 else 85


def radar_maquina(r_maq, e, uso=None, componentes=None, punto=None, instancias=None, inv_horas=None):
    """Radar de UNA máquina con los principios medidos en continuo (no solo criterios sí/no):
    Disponibilidad por instancias (ella y sus réplicas), Excelencia operativa con el mismo cálculo de las remediaciones preventivas,
    Rendimiento por uso de CPU, memoria y disco frente a lo asignado, Recuperación por la efectividad real de recuperarla
    (cobertura del último punto de restauración y su frescura). Cada principio dice qué falta para llegar al 100 %."""
    import copy
    if not r_maq:
        return None
    pri = {p["nombre"]: p for p in copy.deepcopy(r_maq["principios"])}
    for p in pri.values():
        p["falta"], p["detalle"] = [], []
    crit = {c["id"]: c for p in pri.values() for c in p["criterios"]}
    # Disponibilidad: encendida y cuántas instancias sirven lo mismo
    d = pri.get("Disponibilidad")
    if d is not None:
        inst = instancias or [{"id": e["id"], "nombre": e["nombre"], "energia": e.get("energia"), "esta": True}]
        vivas = [x for x in inst if (x.get("energia") or "encendido") not in ("apagado", "suspendido")]
        enc = (e.get("energia") or e.get("estado")) not in ("apagado", "suspendido")
        base = 0 if not enc else {1: 60, 2: 85}.get(len(vivas), 100 if len(vivas) >= 3 else 60)
        vig = (crit.get("D2") or {}).get("pct")
        d["pct"] = max(0, base - (10 if vig == 0 else 0))
        d["instancias"] = inst
        if not enc:
            d["falta"].append("Encender la máquina")
        if len(vivas) < 2:
            d["falta"].append("Agregar al menos una réplica (otra instancia que sirva lo mismo): con 2 sube a 85 %, con 3 o más a 100 %")
        elif len(vivas) < 3:
            d["falta"].append("Una tercera instancia lleva la disponibilidad al 100 %")
        if vig == 0:
            d["falta"].append("Configurar la alerta de disponibilidad (sin respuesta) en Alertas")
    # Excelencia operativa: igual que las remediaciones preventivas (sistema y plataformas)
    h = pri.get("Excelencia operativa")
    if h is not None:
        vals = [(c.get("nombre"), s.get("salud"), s.get("motivos") or []) for c in componentes or [] for s in (c.get("secciones") or [])
                if s.get("clave") == "remediaciones" and s.get("salud") is not None]
        if vals:
            h["pct"] = round(sum(v for _, v, _ in vals) / len(vals))
            for n, v, mot in vals:
                h["detalle"].append({"t": f"Remediaciones preventivas · {n}", "v": v})

    # Seguridad: lo medido en el sistema y sus plataformas (CVE y políticas); el pilar es el promedio de esos subítems
    # (los criterios sí/no S1/S2 solo cuentan cuando no hay nada medido)
    sg = pri.get("Seguridad")
    if sg is not None:
        vals = [(c.get("nombre"), s.get("titulo") or s.get("clave"), s.get("salud"), s.get("motivos") or []) for c in componentes or []
                for s in (c.get("secciones") or []) if s.get("clave") in ("cves", "politicas") and s.get("salud") is not None]
        if vals:
            sg["pct"] = round(sum(v for _, _, v, _ in vals) / len(vals))
            for n, t, v, mot in vals:
                sg["detalle"].append({"t": f"{'CVE' if t == 'cves' else ('Políticas de seguridad' if t == 'politicas' else t)} · {n}", "v": v})

    # Rendimiento: uso medio frente a lo asignado
    r = pri.get("Rendimiento")
    if r is not None and uso:
        dk = max([x.get("uso") for x in (uso.get("discos") or []) if x.get("uso") is not None] or [None]) if uso.get("discos") else None
        partes = [("CPU", uso.get("cpu_u"), f"{e.get('cpu') or '—'} vCPU"), ("Memoria", uso.get("ram_u"), f"{round((e.get('ram_mb') or 0) / 1024, 1):g} GB"),
                  ("Disco (volumen más lleno)", dk, "su capacidad")]
        sc = []
        for n, u, asig in partes:
            s_ = _pct_uso(u)
            if s_ is None:
                continue
            sc.append(s_)
            r["detalle"].append({"t": n, "uso": u, "asignado": asig, "v": s_})
            if u > 85:
                r["falta"].append(f"{n} al {u} %: subir lo asignado ({asig}) o reducir la carga")
            elif u > 70:
                r["falta"].append(f"{n} al {u} %: conviene ampliar antes de llegar al 85 %")
            elif u < 15:
                r["falta"].append(f"{n} al {u} %: sobredimensionado, se puede bajar y liberar el anfitrión")
        if sc:
            r["pct"] = round(sum(sc) / len(sc))
    # Recuperación: efectividad real de recuperarla
    rc = pri.get("Recuperación")
    if rc is not None:
        f = (punto or {}).get("f") or {}
        cob = f.get("cobertura")
        frescura = 1.0
        if inv_horas is not None:
            frescura = 1.0 if inv_horas <= 26 else (0.8 if inv_horas <= 24 * 7 else 0.5)
        if cob and cob.get("pct") is not None:
            rc["pct"] = round(cob["pct"] * frescura)
            rc["detalle"].append({"t": "Cobertura del último punto de restauración", "v": cob["pct"]})
            for x in cob.get("items") or []:
                if not x.get("ok"):
                    rc["falta"].append(f"{x['que']}: {x['detalle']} → {x['mecanismo']}")
        else:
            from .puntos import recuperacion
            rp = recuperacion(f) if punto else None
            if rp:
                rc["pct"] = round(rp["pct"] * frescura)
                rc["detalle"].append({"t": "Recuperación del último punto de restauración", "v": rp["pct"]})
                rc["falta"] += [f"{m['t']} (+{m['d']} %): {m['como']}" for m in rp["mejorar"]]
            else:
                rc["pct"] = 0
                rc["falta"].append("Registrar un punto de restauración (se crea con el inventario diario)")
        if inv_horas is not None and inv_horas > 26:
            rc["falta"].append(f"El último inventario tiene {round(inv_horas / 24)} día(s): sin inventario diario se pierde lo cambiado desde entonces")
    out = [pri[n] for n in [p["nombre"] for p in r_maq["principios"]]]
    vals = [p["pct"] for p in out if p["pct"] is not None]
    return dict(r_maq, principios=out, pct=round(sum(vals) / len(vals)) if vals else None,
                actual={p["nombre"]: (p["pct"] if p["pct"] is not None else 100) for p in out})
