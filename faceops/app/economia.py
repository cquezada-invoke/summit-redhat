"""Economía del servicio: cobro por beneficio. Una fórmula global (datos/cobro.yml) da a cada automatización su factor de
beneficio a partir del esfuerzo manual que reemplaza y del tiempo que toma automatizada; cada ejecución exitosa cobra ese
factor por unidad (equipo, servidor, réplica…). Se liquida al cerrar el ticket; solo cobra lo que termina bien."""
import os
from collections import defaultdict

import yaml

from .bd import ej, j, q, q1

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "creditos.yml")
RUTA_COBRO = os.path.join(os.path.dirname(RUTA), "cobro.yml")
UNIDADES = {"equipo": ("equipo", "equipos"), "servidor": ("servidor", "servidores"), "réplica": ("réplica", "réplicas"),
            "flujo": ("flujo", "flujos"), "segmento": ("segmento", "segmentos"), "ejecución": ("ejecución", "ejecuciones")}


def conf():
    try:
        return yaml.safe_load(open(RUTA, encoding="utf-8")) or {}
    except OSError:
        return {}


def cobro():
    try:
        d = yaml.safe_load(open(RUTA_COBRO, encoding="utf-8")) or {}
    except OSError:
        d = {}
    return {"valor_factor": float(d.get("valor_factor", 15)), "factor_minimo": float(d.get("factor_minimo", 0.1)),
            "estimado_min": d.get("estimado_min") or {"lectura": 1, "escritura": 5}, "sin_cobro": set(d.get("sin_cobro_categorias") or [])}


def tiempos_medidos():
    """Tiempo automatizado medido por automatización (todas las ejecuciones exitosas de todos los clientes), en minutos por unidad."""
    return {r["n_aap"]: {"min": (r["seg_u"] or 0) / 60, "n": r["n"]} for r in q(
        "SELECT a.nombre_aap n_aap, COUNT(*) n, AVG(t.dur_ejecucion_s * 1.0 / COALESCE(NULLIF(t.unidades, 0), 1)) seg_u FROM tickets t "
        "JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.estado='exitoso' AND t.dur_ejecucion_s IS NOT NULL GROUP BY a.nombre_aap")}


RUTA_TAREAS = os.path.join(os.path.dirname(RUTA), "tareas.yml")


def catalogo_tareas():
    """Tareas automatizadas (con los ajustes de esfuerzo manual hechos en Administración) y la composición de cada automatización."""
    try:
        d = yaml.safe_load(open(RUTA_TAREAS, encoding="utf-8")) or {}
    except OSError:
        d = {}
    ajustes = {}
    try:
        ajustes = {r["codigo"]: r["manual"] for r in q("SELECT codigo, manual FROM tareas_ajuste")}
    except Exception:   # tabla aún no creada
        pass
    tareas = {}
    for k, t in (d.get("tareas") or {}).items():
        tareas[k] = {"codigo": k, "nombre": t.get("nombre", k), "manual": float(ajustes.get(k, t.get("manual", 0))),
                     "manual_base": float(t.get("manual", 0)), "auto": float(t.get("auto", 1)), "alcance": t.get("alcance", "unidad")}
    comp = {n: [x if isinstance(x, str) else x.get("tarea") for x in (lista or [])] for n, lista in (d.get("automatizaciones") or {}).items()}
    return tareas, comp


def factor_tarea(manual, auto):
    """F = (M − A) ÷ 60 × O, con O = (M − A) ÷ M."""
    o = max(0.0, (manual - auto) / manual) if manual else 0.0
    return o, max(0.0, (manual - auto) / 60 * o)


def beneficio(a, medidos=None, cb=None, cat=None):
    """La automatización es la suma de sus tareas: cada tarea aporta su factor (por unidad o por ejecución). Si la
    automatización tiene ejecuciones medidas, su tiempo real se reparte entre sus tareas según la estimación de cada una."""
    cb = cb or cobro()
    medidos = tiempos_medidos() if medidos is None else medidos
    tareas, comp = cat or catalogo_tareas()
    codigos = [c for c in comp.get(a.get("nombre_aap") or "", []) if c in tareas]
    if codigos:
        lista = [dict(tareas[c]) for c in codigos]
        desglose = True
    else:   # sin desglose todavía: una sola tarea con el esfuerzo de la automatización
        lista = [{"codigo": "", "nombre": "Tarea única (sin desglose)", "manual": float(a.get("minutos_manual") or 0),
                  "auto": float(cb["estimado_min"].get(a.get("tipo") or "escritura", 5)), "alcance": "unidad"}]
        desglose = False
    med = medidos.get(a.get("nombre_aap") or "")
    est = sum(t["auto"] for t in lista) or 1
    fuente = f"medido · {med['n']} ejecución(es)" if med and med["n"] else "estimado"
    if med and med["n"]:
        escala = max(med["min"], 0.05) / est
        for t in lista:
            t["auto"] = t["auto"] * escala
    sin_cobro = (a.get("categoria") or "") in cb["sin_cobro"]
    for t in lista:
        t["optimizacion"], t["factor"] = (0.0, 0.0) if sin_cobro else factor_tarea(t["manual"], t["auto"])
    fu = sum(t["factor"] for t in lista if t["alcance"] != "ejecucion")
    fe = sum(t["factor"] for t in lista if t["alcance"] == "ejecucion")
    man = sum(t["manual"] for t in lista)
    auto = sum(t["auto"] for t in lista)
    return {"tareas": lista, "desglose": desglose, "manual": man, "auto": auto, "fuente": fuente, "sin_cobro": sin_cobro,
            "manual_unidad": sum(t["manual"] for t in lista if t["alcance"] != "ejecucion"),
            "manual_ejecucion": sum(t["manual"] for t in lista if t["alcance"] == "ejecucion"),
            "factor_unidad": round(fu, 2), "factor_ejecucion": round(fe, 2),
            "factor": 0.0 if sin_cobro else round(max(fu + fe, cb["factor_minimo"]), 2),
            "optimizacion": max(0.0, (man - auto) / man) if man else 0.0, "veces": (man / auto) if auto else None}


def creditos_de(b, n, cb=None):
    """Créditos de una ejecución sobre n unidades (máquinas, servidores, réplicas, flujos)."""
    if b["sin_cobro"]:
        return 0.0
    cb = cb or cobro()
    return round(max(b["factor_unidad"] * n + b["factor_ejecucion"], cb["factor_minimo"]), 2)


def regla(a, c=None, medidos=None):
    """Créditos = Σ factor de las tareas por unidad × unidades + Σ factor de las tareas por ejecución."""
    c = c or conf()
    r = (c.get("automatizaciones") or {}).get(a.get("nombre_aap") or "") or {}
    b = beneficio(a, medidos)
    unidad = r.get("unidad") or ("equipo" if a.get("usa_limite") else "ejecución")
    return {"creditos": b["factor_unidad"], "fijo": b["factor_ejecucion"], "unidad": unidad, "beneficio": b,
            "por_que": f"{len(b['tareas'])} tarea(s) · {b['manual']:.0f} min a mano → {b['auto']:.1f} min automatizada ({b['fuente']})"}


def sincronizar_minutos():
    """El esfuerzo manual de cada automatización es la suma de sus tareas (lo usan el ahorro y los paneles)."""
    tareas, comp = catalogo_tareas()
    for nombre, codigos in comp.items():
        m = sum(tareas[c]["manual"] for c in codigos if c in tareas)
        if m:
            ej("UPDATE automatizaciones SET minutos_manual=? WHERE nombre_aap=?", int(round(m)), nombre)


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
    return dict(r, unidades=n, total=creditos_de(r["beneficio"], n))


def liquidar(ticket_id):
    """Créditos, factor de beneficio y ahorro del ticket (0 si no terminó bien)."""
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"]) if t else None
    if not a:
        return
    c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
    r = regla(a)
    n = unidades(a, t, j(t["parametros"], {}), r["unidad"])
    b = r["beneficio"]
    man = b["manual_unidad"] * n + b["manual_ejecucion"]
    auto = (t.get("dur_ejecucion_s") or t.get("duracion_s") or 0) / 60
    ok = t["estado"] == "exitoso"
    factor = round(auto / man, 4) if ok and man else None
    ahorro = round(max(0.0, man - auto) / 60 * (c.get("costo_hora") or 0), 2) if ok else 0
    ej("UPDATE tickets SET unidades=?, creditos=?, factor=?, ahorro=?, valor_min=? WHERE id=?",
       n, creditos_de(b, n) if ok else 0, factor, ahorro, man if ok else t["valor_min"], ticket_id)
    from .creditos import registrar_ejecucion
    registrar_ejecucion(q1("SELECT * FROM tickets WHERE id=?", ticket_id))


def pendientes():
    """Tickets cerrados sin liquidar (los anteriores a los créditos)."""
    for t in q("SELECT id FROM tickets WHERE estado IN ('exitoso','fallido','rechazado','vencido','cancelado') AND creditos IS NULL"):
        liquidar(t["id"])


def plan(cliente):
    c = conf().get("plan") or {}
    return {"nombre": c.get("nombre", "Managed Service"), "creditos_mes": int(cliente.get("creditos_mes") or c.get("creditos_mes", 10000)),
            "valor_credito": cobro()["valor_factor"]}   # 1 crédito = 1 punto de factor de beneficio


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
