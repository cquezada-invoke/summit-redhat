"""Indicadores del panel para el director: productividad por área de negocio y consumo de la bolsa de créditos."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .bd import q

RESUELTO = ("exitoso",)
FALLIDO = ("fallido",)
DETENIDO = ("vencido", "rechazado", "pendiente_aprobacion")
TRANSVERSAL = "Transversal (sin segmento)"


def _area_de_ticket(t, area_seg, seg_eq):
    if t["segmento"] and t["segmento"] in area_seg:
        return area_seg[t["segmento"]]
    for m in (t["limite"] or "").replace(";", ",").replace(" ", ",").split(","):
        for s in seg_eq.get(m.strip(), []):
            if s in area_seg:
                return area_seg[s]
    return TRANSVERSAL


def productividad(cliente_id, ts, eqs, autos):
    """Por área: de lo que se pidió, cuánto se resolvió solo (exitoso) vs falló o quedó detenido (vencido, rechazado,
    esperando aprobación). Ordenado de peor a mejor para saber dónde focalizar."""
    area_seg = {r["nombre"]: (r["area"] or "Sin área") for r in q("SELECT nombre, area FROM segmentos WHERE cliente_id=?", cliente_id)}
    seg_eq = {e["nombre"]: e["segmentos"] for e in eqs}
    por_auto = {a["id"]: a for a in autos}
    areas = defaultdict(lambda: {"ok": 0, "fal": 0, "det": 0, "h": 0.0, "segmentos": set()})
    for s, a in area_seg.items():
        areas[a]["segmentos"].add(s)
    for t in ts:
        if t["estado"] not in RESUELTO + FALLIDO + DETENIDO:
            continue
        x = areas[_area_de_ticket(t, area_seg, seg_eq)]
        if t["estado"] in RESUELTO:
            x["ok"] += 1
            x["h"] += (t["valor_min"] or 0) / 60
        elif t["estado"] in FALLIDO:
            x["fal"] += 1
        else:
            x["det"] += 1
    out = []
    for n, x in areas.items():
        tot = x["ok"] + x["fal"] + x["det"]
        if not tot:   # solo las áreas que usan automatizaciones
            continue
        out.append({"area": n, "total": tot, "ok": x["ok"], "fal": x["fal"], "det": x["det"], "h": round(x["h"], 1),
                    "pct": round(100 * x["ok"] / tot) if tot else None, "segmentos": sorted(x["segmentos"])})
    out.sort(key=lambda a: (a["pct"] is not None, a["pct"] or 0, -a["total"]))
    tot = sum(a["total"] for a in out)
    glob = round(100 * sum(a["ok"] for a in out) / tot) if tot else 0
    # reducción de tiempo: del tiempo que tomaría a mano lo ejecutado con éxito, cuánto se eliminó
    man = eje = 0
    for t in ts:
        if t["estado"] == "exitoso":
            m = ((por_auto.get(t["automatizacion_id"]) or {}).get("minutos_manual") or 0) * 60
            if m:
                man += m
                eje += min(t["dur_ejecucion_s"] or 0, m)
    # 12 meses: horas que habría tomado a mano lo ejecutado vs horas reales en las automatizaciones
    hoy = datetime.now(timezone.utc)
    meses, y, mo = [], hoy.year, hoy.month
    for _ in range(12):
        meses.insert(0, f"{y:04d}-{mo:02d}")
        mo -= 1
        if mo == 0:
            y, mo = y - 1, 12
    man_m, aut_m = Counter(), Counter()
    for t in ts:
        if t["estado"] != "exitoso" or _mes(t["creado"]) not in meses:
            continue
        a = por_auto.get(t["automatizacion_id"]) or {}
        man_m[_mes(t["creado"])] += (t["valor_min"] or a.get("minutos_manual") or 0) / 60
        aut_m[_mes(t["creado"])] += (t["dur_ejecucion_s"] or 0) / 3600
    linea = [{"mes": m[5:7] + "/" + m[2:4], "manual": round(man_m[m], 1), "auto": round(aut_m[m], 2)} for m in meses]
    return {"linea": linea, "linea_max": max([x["manual"] for x in linea] + [1]), "areas": out, "global": glob, "reduccion": round(100 * (man - eje) / man) if man else 0,
            "max": max([a["total"] for a in out] + [1])}


def _mes(fecha):
    return (fecha or "")[:7]


def creditos(cliente, eqs):
    """Bolsa total vs consumido, consumo por mes y por tipo, por área y por automatización, y cuánto alcanza lo que queda."""
    from .creditos import TIPOS, estado
    est = estado(cliente)
    movs = q("SELECT m.*, t.segmento, t.limite, a.nombre auto FROM creditos_mov m LEFT JOIN tickets t ON t.id=m.ticket_id "
             "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE m.cliente_id=? ORDER BY m.fecha", cliente["id"])
    total = sum(m["creditos"] for m in movs if m["creditos"] > 0)
    consumido = -sum(m["creditos"] for m in movs if m["creditos"] < 0)
    pct = round(100 * consumido / total) if total else 0
    hoy = datetime.now(timezone.utc)
    meses = []
    y, mo = hoy.year, hoy.month
    for _ in range(6):
        meses.insert(0, f"{y:04d}-{mo:02d}")
        mo -= 1
        if mo == 0:
            y, mo = y - 1, 12
    por_mes = {m: Counter() for m in meses}
    for m in movs:
        if m["creditos"] < 0 and _mes(m["fecha"]) in por_mes:
            por_mes[_mes(m["fecha"])][m["tipo"]] += -m["creditos"]
    serie = [{"mes": m[5:7] + "/" + m[2:4], "tipos": dict(c), "total": round(sum(c.values()), 1)} for m, c in por_mes.items()]
    area_seg = {r["nombre"]: (r["area"] or "Sin área") for r in q("SELECT nombre, area FROM segmentos WHERE cliente_id=?", cliente["id"])}
    seg_eq = {e["nombre"]: e["segmentos"] for e in eqs}
    por_area, por_auto = Counter(), Counter()
    for m in movs:
        if m["tipo"] == "ejecucion" and m["creditos"] < 0:
            por_area[_area_de_ticket(m, area_seg, seg_eq)] += -m["creditos"]
            por_auto[m["auto"] or "—"] += -m["creditos"]
    por_tipo = Counter()
    for m in movs:
        if m["creditos"] < 0:
            por_tipo[m["tipo"]] += -m["creditos"]
    ult3 = [s["total"] for s in serie[-3:]]
    ritmo = sum(ult3) / len(ult3) if ult3 else 0
    anual = anio_contrato(cliente, movs, est.get("ms") or {})
    return {"anual": anual, "total": round(total, 1), "consumido": round(consumido, 1), "pct": pct, "saldo": est["saldo"], "disponible": est["disponible"],
            "reservado": est["reservado"], "ms": est.get("ms"), "serie": serie, "max_mes": max([s["total"] for s in serie] + [1]),
            "tipos": [(k, TIPOS.get(k, k)) for k in ("base", "ejecucion", "ajuste")],
            "por_tipo": [(TIPOS.get(k, k), round(v, 1)) for k, v in por_tipo.most_common()],
            "por_area": [(k, round(v, 1)) for k, v in por_area.most_common()],
            "por_auto": [(k, round(v, 1)) for k, v in por_auto.most_common(10)],
            "ritmo": round(ritmo, 1), "meses_quedan": round(est["disponible"] / ritmo, 1) if ritmo > 0 else None}


def _area_resp(r):  # (sin uso: el panel agrupa por área de negocio, no por persona)
    """«Infraestructura (aprueba y asigna recursos) (infra.lider)» → «Infraestructura»."""
    import re
    partes = []
    for x in (r or "").split(", "):
        x = re.sub(r"\s*\([^()]*\)\s*$", "", x.strip())       # quita el (usuario)
        x = re.sub(r"\s*\([^()]*\)\s*$", "", x).strip()         # y la descripción entre paréntesis
        if x and x not in partes:
            partes.append(x)
    return " / ".join(partes) or "Jefatura del cliente"


# cuando la solicitud no nombra un segmento ni máquinas, el área impactada sale del tipo de automatización
AREA_CATEGORIA = {"Redes": "Tecnología", "Data center": "Tecnología", "Sistemas operativos": "Tecnología", "Storage": "Tecnología",
                  "Nube": "Tecnología", "OpenShift": "Tecnología", "Infraestructuras": "Tecnología", "Migraciones": "Tecnología",
                  "Operación y remediación": "Tecnología", "Skills": "Tecnología", "Cumplimiento y estado": "Seguridad de la información",
                  "Seguridad": "Seguridad de la información"}


def _areas_impactadas(d, categoria, area_seg, seg_eq):
    """Áreas de negocio a las que afecta la solicitud: su segmento; si no, los segmentos de sus máquinas; si no, el tipo de automatización."""
    if d.get("segmento") and d["segmento"] in area_seg:
        return [area_seg[d["segmento"]]]
    out = []
    for m in (d.get("limite") or "").replace(",", ":").split(":"):
        for sg in seg_eq.get(m.strip(), []):
            if sg in area_seg and area_seg[sg] not in out:
                out.append(area_seg[sg])
    if out:
        return out
    a = AREA_CATEGORIA.get(categoria or "")
    return [a] if a and a in set(area_seg.values()) else [TRANSVERSAL]


def aprobaciones_pendientes(cliente_id, eqs=None):
    """Aprobaciones sin respuesta hace más de 1 día (esperando o vencidas sin ejecutar), por área de negocio impactada y antigüedad.
    Una solicitud que toca varias áreas cuenta en cada una."""
    from .deuda import lista, sincronizar
    sincronizar(cliente_id)
    area_seg = {r["nombre"]: (r["area"] or "Sin área") for r in q("SELECT nombre, area FROM segmentos WHERE cliente_id=?", cliente_id)}
    seg_eq = {e["nombre"]: e["segmentos"] for e in (eqs or [])}
    cats = {r["id"]: r["categoria"] for r in q("SELECT id, categoria FROM automatizaciones")}
    areas = defaultdict(lambda: {"d1": 0, "d3": 0, "d7": 0, "autos": Counter()})
    n = 0
    for d in lista(cliente_id, solo_abiertas=True):
        if d["estado"] not in ("esperando", "vencida") or d["dias"] < 1:
            continue
        n += 1
        for ar in _areas_impactadas(d, cats.get(d["automatizacion_id"]), area_seg, seg_eq):
            a = areas[ar]
            a["d1" if d["dias"] < 3 else ("d3" if d["dias"] < 7 else "d7")] += 1
            a["autos"][d.get("auto") or "—"] += 1
    out = [{"area": k, "d1": v["d1"], "d3": v["d3"], "d7": v["d7"], "total": v["d1"] + v["d3"] + v["d7"],
            "autos": [f"{x} ({c})" for x, c in v["autos"].most_common(3)]} for k, v in areas.items()]
    out.sort(key=lambda x: (-x["d7"], -x["total"]))
    return {"areas": out, "total": n, "max": max([x["total"] for x in out] + [1])}


def anio_contrato(cliente, movs, ms):
    """Año de contrato vigente (12 meses desde que se activó el servicio): consumo mensual, acumulado, proyección al cierre
    y regla de cobro. Los créditos se acumulan y se pagan por consumo efectivo: con Managed Service, lo no consumido al
    cierre del primer año puede no cobrarse; sin Managed Service se cobra la bolsa completa."""
    ini = (cliente.get("contrato_inicio") or "")[:10]
    if not ini:
        return None
    try:
        d0 = datetime.fromisoformat(ini)
    except ValueError:
        return None
    hoy = datetime.now(timezone.utc).date().isoformat()
    n = 0
    while True:   # año de contrato que contiene hoy
        a = d0.replace(year=d0.year + n)
        b = d0.replace(year=d0.year + n + 1)
        if hoy < b.date().isoformat() or n > 50:
            break
        n += 1
    a_s, b_s = a.date().isoformat(), b.date().isoformat()
    y, mo, meses = a.year, a.month, []
    for _ in range(12):
        meses.append(f"{y:04d}-{mo:02d}")
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    por_mes = Counter()
    bolsa = 0.0
    for m in movs:
        f = (m["fecha"] or "")[:10]
        if m["creditos"] > 0 and f < b_s and (f >= a_s or n == 0):
            bolsa += m["creditos"]
        elif m["creditos"] < 0 and a_s <= f < b_s:
            por_mes[_mes(f)] += -m["creditos"]
    act = _mes(hoy)
    acum, serie = 0.0, []
    for i, m in enumerate(meses):
        futuro = m > act
        acum += por_mes[m]
        serie.append({"mes": m[5:7] + "/" + m[2:4], "consumo": round(por_mes[m], 1), "acum": None if futuro else round(acum, 1), "futuro": futuro})
    transcurridos = max(1, sum(1 for m in meses if m <= act))
    consumido = round(acum, 1)
    proy = round(consumido / transcurridos * 12, 1)
    con_ms = bool(ms.get("vigente"))
    primer = n == 0
    if con_ms and primer:
        cobro, regla = min(proy, bolsa) if bolsa else proy, "con Managed Service: se cobra el consumo efectivo; lo no consumido al cierre del primer año puede no cobrarse"
    elif con_ms:
        cobro, regla = min(proy, bolsa) if bolsa else proy, "con Managed Service: se cobra el consumo efectivo; los créditos son acumulativos"
    else:
        cobro, regla = bolsa, "sin Managed Service: se cobra la bolsa completa, se consuma o no"
    return {"anio": n + 1, "desde": a_s, "hasta": (b.date() - timedelta(days=1)).isoformat(), "serie": serie,
            "bolsa": round(bolsa, 1), "consumido": consumido, "proyeccion": proy, "cobro": round(cobro, 1), "regla": regla,
            "sin_consumir": round(max(bolsa - proy, 0), 1), "con_ms": con_ms,
            "max": max([bolsa, proy, max((x["consumo"] for x in serie), default=0)] + [1]) * 1.12,
            "max_mes": max([x["consumo"] for x in serie] + [1])}
