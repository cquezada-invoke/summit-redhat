"""Resumen ejecutivo del contrato: costo (créditos y su precio contratado), beneficio (horas liberadas y ahorro) y su
impacto por área del cliente (cada segmento de negocio pertenece a un área), para orientar el beneficio donde corresponde."""
from collections import defaultdict
from datetime import datetime, timezone

from .bd import q

SIN_AREA = "Transversal (sin área)"


def periodo(meses):
    hoy = datetime.now(timezone.utc)
    y, m = hoy.year, hoy.month
    lista = []
    for _ in range(meses):
        lista.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(lista)


def calcular(cliente, meses=3):
    from .creditos import estado as estado_creditos
    ms = periodo(meses)
    desde = ms[0]
    precio = float(cliente.get("precio_credito") or 0)
    from .bd import j
    # área del segmento: la asignada en Gobierno o, si no, el «área de negocio» de la ficha de su arquitectura
    areas_seg = {s["nombre"]: ((s.get("area") or "").strip() or str(j(s.get("ficha"), {}).get("area_negocio") or "").strip())
                 for s in q("SELECT nombre, area, ficha FROM segmentos WHERE cliente_id=?", cliente["id"])}
    ts = q("SELECT t.*, a.nombre auto, a.categoria FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
           "WHERE t.cliente_id=? AND substr(t.creado, 1, 7) >= ?", cliente["id"], desde)
    vacio = lambda: {"ejecuciones": 0, "maquinas": 0, "creditos": 0.0, "horas": 0.0, "ahorro": 0.0, "segmentos": set(), "servicios": defaultdict(float)}
    por_area, por_mes, por_serv = defaultdict(vacio), defaultdict(lambda: {"creditos": 0.0, "fijos": 0.0, "ahorro": 0.0, "horas": 0.0}), defaultdict(lambda: {"creditos": 0.0, "horas": 0.0, "ejecuciones": 0})
    exitosos = fallidos = 0
    for t in ts:
        if t["estado"] == "fallido":
            fallidos += 1
        if t["estado"] != "exitoso":
            continue
        exitosos += 1
        area = areas_seg.get(t["segmento"] or "") or SIN_AREA
        horas = max(0.0, (t["valor_min"] or 0) / 60 - (t["dur_ejecucion_s"] or 0) / 3600)
        x = por_area[area]
        x["ejecuciones"] += 1
        x["maquinas"] += t["unidades"] or 1
        x["creditos"] += t["creditos"] or 0
        x["horas"] += horas
        x["ahorro"] += t["ahorro"] or 0
        if t["segmento"]:
            x["segmentos"].add(t["segmento"])
        x["servicios"][t["categoria"] or "General"] += horas
        mm = por_mes[(t["creado"] or "")[:7]]
        mm["creditos"] += t["creditos"] or 0
        mm["ahorro"] += t["ahorro"] or 0
        mm["horas"] += horas
        sv = por_serv[t["categoria"] or "General"]
        sv["creditos"] += t["creditos"] or 0
        sv["horas"] += horas
        sv["ejecuciones"] += 1
    for f in q("SELECT periodo, SUM(-creditos) c FROM creditos_mov WHERE cliente_id=? AND tipo IN ('base','managed_service') AND periodo >= ? "
               "GROUP BY periodo", cliente["id"], desde):
        por_mes[f["periodo"]]["fijos"] += f["c"] or 0
    fijos = sum(v["fijos"] for v in por_mes.values())
    cred_ejec = sum(v["creditos"] for v in por_area.values())
    ahorro = sum(v["ahorro"] for v in por_area.values())
    horas = sum(v["horas"] for v in por_area.values())
    costo = (cred_ejec + fijos) * precio
    areas = []
    for nombre, v in por_area.items():
        c_area = v["creditos"] * precio
        areas.append({"area": nombre, "ejecuciones": v["ejecuciones"], "maquinas": v["maquinas"], "creditos": round(v["creditos"], 2),
                      "horas": round(v["horas"], 1), "ahorro": round(v["ahorro"], 2), "costo": round(c_area, 2),
                      "retorno": (v["ahorro"] / c_area) if c_area else None, "pct": (v["ahorro"] / ahorro * 100) if ahorro else 0,
                      "segmentos": sorted(v["segmentos"]),
                      "servicios": sorted(((k, round(h, 1)) for k, h in v["servicios"].items()), key=lambda z: -z[1])[:3]})
    areas.sort(key=lambda a: (a["area"] == SIN_AREA, -a["ahorro"]))
    incid = None
    try:
        from . import kb
        if kb.disponible():
            r = kb.q1("SELECT count(*) total, count(*) FILTER (WHERE estado='resuelto') resueltos, "
                      "count(*) FILTER (WHERE problema_id IS NOT NULL) reconocidos FROM incidentes WHERE cliente_id=%s "
                      "AND to_char(creado, 'YYYY-MM') >= %s", cliente["id"], desde)
            incid = dict(r) if r else None
    except Exception:
        incid = None
    return {"meses": ms, "precio": precio, "moneda": cliente.get("moneda") or "USD", "costo_hora": cliente.get("costo_hora") or 0,
            "creditos_ejecuciones": round(cred_ejec, 2), "creditos_fijos": round(fijos, 2), "costo": round(costo, 2),
            "ahorro": round(ahorro, 2), "horas": round(horas, 1), "retorno": (ahorro / costo) if costo else None,
            "exitosos": exitosos, "fallidos": fallidos, "areas": areas,
            "por_mes": [{"mes": m, **{k: round(v, 2) for k, v in por_mes[m].items()}} for m in ms],
            "servicios": sorted(({"servicio": k, **{kk: round(vv, 2) for kk, vv in v.items()}} for k, v in por_serv.items()), key=lambda z: -z["horas"]),
            "saldo": estado_creditos(cliente), "incidentes": incid}
