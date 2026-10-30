"""Economía del servicio: créditos que consume cada ticket (por unidad: equipo, servidor, réplica…), factor de beneficio
(tiempo automatizado ÷ tiempo manual) y ahorro en dinero. Se liquida al cerrar el ticket; solo cobra lo que termina bien."""
import os
from collections import defaultdict

import yaml

from .bd import ej, j, q, q1

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "creditos.yml")
UNIDADES = {"equipo": ("equipo", "equipos"), "servidor": ("servidor", "servidores"), "réplica": ("réplica", "réplicas"),
            "flujo": ("flujo", "flujos"), "segmento": ("segmento", "segmentos"), "ejecución": ("ejecución", "ejecuciones")}


def conf():
    try:
        return yaml.safe_load(open(RUTA, encoding="utf-8")) or {}
    except OSError:
        return {}


def regla(a, c=None):
    c = c or conf()
    r = (c.get("automatizaciones") or {}).get(a.get("nombre_aap") or "")
    if r:
        return {"creditos": float(r.get("creditos", 0)), "unidad": r.get("unidad", "ejecución"), "por_que": r.get("por_que", "")}
    base = (c.get("por_defecto") or {}).get(a.get("tipo") or "escritura", 2)
    return {"creditos": float(base), "unidad": "equipo" if a.get("usa_limite") else "ejecución",
            "por_que": "Valor por defecto (" + ("solo lectura" if a.get("tipo") == "lectura" else "cambia el entorno") + ")"}


def unidades(a, t, params, unidad):
    if unidad == "servidor":
        return max(1, int(params.get("hab_replicas_nuevas") or (1 if params.get("hab_nombre_exacto") else params.get("hab_replicas") or 1)))
    if unidad == "réplica":
        return max(1, int(params.get("ha_replicas_nuevas") or 1))
    if unidad == "equipo":
        n = len([x for x in (t.get("limite") or "").split(":") if x])
        for c in j(a.get("campos"), []):
            if c.get("tipo") in ("equipo", "equipos") and params.get(c["var"]):
                v = params[c["var"]]
                n = max(n, len(v) if isinstance(v, list) else 1)
        return max(1, n)
    return 1


def estimar(a, params=None, limite=""):
    r = regla(a)
    n = unidades(a, {"limite": limite}, params or {}, r["unidad"])
    return dict(r, unidades=n, total=r["creditos"] * n)


def liquidar(ticket_id):
    """Créditos, factor de beneficio y ahorro del ticket (0 si no terminó bien)."""
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"]) if t else None
    if not a:
        return
    c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
    r = regla(a)
    n = unidades(a, t, j(t["parametros"], {}), r["unidad"])
    man = (a.get("minutos_manual") or 0) * n
    auto = (t.get("dur_ejecucion_s") or t.get("duracion_s") or 0) / 60
    ok = t["estado"] == "exitoso"
    factor = round(auto / man, 4) if ok and man else None
    ahorro = round(max(0.0, man - auto) / 60 * (c.get("costo_hora") or 0), 2) if ok else 0
    ej("UPDATE tickets SET unidades=?, creditos=?, factor=?, ahorro=?, valor_min=? WHERE id=?",
       n, r["creditos"] * n if ok else 0, factor, ahorro, man if ok else t["valor_min"], ticket_id)


def pendientes():
    """Tickets cerrados sin liquidar (los anteriores a los créditos)."""
    for t in q("SELECT id FROM tickets WHERE estado IN ('exitoso','fallido','rechazado','vencido','cancelado') AND creditos IS NULL"):
        liquidar(t["id"])


def plan(cliente):
    c = conf().get("plan") or {}
    return {"nombre": c.get("nombre", "Managed Service"), "creditos_mes": int(cliente.get("creditos_mes") or c.get("creditos_mes", 10000)),
            "valor_credito": float(c.get("valor_credito", 1))}


def resumen(cliente, mes, tickets):
    """Consumo del mes: bolsa, usado, disponible, por automatización y por día."""
    p = plan(cliente)
    del_mes = [t for t in tickets if (t.get("creado") or "").startswith(mes)]
    usados = sum(t.get("creditos") or 0 for t in del_mes)
    por_auto = defaultdict(lambda: {"ejecuciones": 0, "unidades": 0, "creditos": 0.0, "ahorro": 0.0, "factores": []})
    por_dia = defaultdict(float)
    for t in del_mes:
        if t["estado"] != "exitoso":
            continue
        x = por_auto[t.get("auto_nombre") or "—"]
        x["ejecuciones"] += 1
        x["unidades"] += t.get("unidades") or 1
        x["creditos"] += t.get("creditos") or 0
        x["ahorro"] += t.get("ahorro") or 0
        if t.get("factor") is not None:
            x["factores"].append(t["factor"])
        por_dia[(t.get("creado") or "")[:10]] += t.get("creditos") or 0
    filas = []
    for nombre, x in por_auto.items():
        f = sum(x["factores"]) / len(x["factores"]) if x["factores"] else None
        filas.append(dict(x, nombre=nombre, factor=f))
    filas.sort(key=lambda r: -r["creditos"])
    return {"plan": p, "usados": usados, "disponibles": p["creditos_mes"] - usados, "pct": min(100, usados / p["creditos_mes"] * 100) if p["creditos_mes"] else 0,
            "valor": usados * p["valor_credito"], "ahorro": sum(t.get("ahorro") or 0 for t in del_mes),
            "ejecuciones": len([t for t in del_mes if t["estado"] == "exitoso"]),
            "unidades": sum((t.get("unidades") or 1) for t in del_mes if t["estado"] == "exitoso"),
            "por_auto": filas, "por_dia": sorted(por_dia.items()), "tickets": [t for t in del_mes if t["estado"] in ("exitoso", "fallido", "rechazado")]}


def estadisticas(cliente_id):
    """Por automatización: tiempo medio manual (h, por unidad), tiempo medio automatizado (de sus ejecuciones exitosas) y
    factor de beneficio medio (tiempo automatizado ÷ tiempo manual)."""
    out = {}
    for r in q("SELECT automatizacion_id aid, COUNT(*) n, AVG(dur_ejecucion_s) seg, AVG(factor) f, SUM(ahorro) ahorro, "
               "AVG(dur_ejecucion_s * 1.0 / COALESCE(NULLIF(unidades, 0), 1)) seg_u FROM tickets "
               "WHERE cliente_id=? AND estado='exitoso' AND dur_ejecucion_s IS NOT NULL GROUP BY automatizacion_id", cliente_id):
        out[r["aid"]] = {"n": r["n"], "auto_min": (r["seg"] or 0) / 60, "auto_min_u": (r["seg_u"] or 0) / 60,
                         "factor": r["f"], "ahorro": r["ahorro"] or 0}
    return out


def metricas(a, est):
    """Lo que se muestra en cada automatización."""
    s = est.get(a["id"]) or {}
    man_h = (a.get("minutos_manual") or 0) / 60
    auto_min = s.get("auto_min_u")
    f = s.get("factor") or ((auto_min / (man_h * 60)) if auto_min and man_h else None)
    return {"manual_h": man_h, "auto_min": auto_min, "factor": f, "veces": (1 / f) if f else None, "n": s.get("n", 0), "ahorro": s.get("ahorro", 0)}
