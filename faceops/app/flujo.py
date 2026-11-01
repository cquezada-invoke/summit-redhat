"""Flujo de cada automatización: la secuencia de tareas y las aprobaciones (por área, en serie o en paralelo), sacado de
la propia automatización: su cadena de aprobación en FaceOps, los nodos de su workflow en AAP (si es un workflow) y las
tareas del catálogo que la componen. El mismo flujo se pinta en el ticket con el estado de cada paso en tiempo real.

Formato: lista de columnas; cada columna es una lista de nodos que corren en paralelo.
Nodo: {tipo: inicio | aprobacion | tarea | fin, titulo, sub, estado}"""
from .bd import j, q

ESTADOS = {"espera": "en espera", "curso": "en curso", "ok": "listo", "falla": "falló", "rechazo": "rechazada", "omitido": "no se ejecutó"}


def _min(m):
    m = float(m or 0)
    return f"{m:g} min" if m < 60 else f"{m / 60:.1f} h"


def plan(a):
    """Columnas del flujo de la automatización (sin estado)."""
    from .aprobacion_areas import grupos_de, nombre
    from .economia import beneficio
    tec = j(a.get("tecnico"), {})
    nodos_aap = tec.get("nodos") or []
    cols = [[{"tipo": "inicio", "titulo": "Solicitud", "sub": "ticket en FaceOps"}]]
    for gi, gr in enumerate(grupos_de(a), 1):   # la cadena de aprobación por áreas (en AAP: un nodo de aprobación por área)
        if True:
            cols.append([{"tipo": "aprobacion", "titulo": f"Aprueba {nombre(x)}", "sub": f"etapa {gi}" + (" · en paralelo" if len(gr) > 1 else ""),
                          "area": x, "grupo": gi} for x in gr])
    if nodos_aap:   # workflow de AAP: sus nodos por nivel (mismo nivel = en paralelo), con sus aprobaciones propias
        niveles = {}
        for n in nodos_aap:
            niveles.setdefault(n.get("nivel", len(niveles)), []).append(n)
        for nv in sorted(niveles):   # las aprobaciones del workflow ya están arriba, como etapas por área
            col = [{"tipo": "tarea", "titulo": n["nombre"], "nodo_aap": n["nombre"],
                    "sub": n.get("cuando") if n.get("cuando") not in ("inicio", "si sale bien") else "plantilla de AAP"}
                   for n in niveles[nv] if n["tipo"] != "aprobacion"]
            if col:
                cols.append(col)
    else:   # plantilla: la suma secuencial de sus tareas
        for t in beneficio(a)["tareas"]:
            cols.append([{"tipo": "tarea", "titulo": t["nombre"], "sub": f"{_min(t.get('manual'))} a mano · " + ("por máquina" if t.get("alcance") == "unidad" else "por ejecución")}])
    cols.append([{"tipo": "fin", "titulo": "Cierre", "sub": "resultado y valor en el ticket"}])
    return cols


def en_vivo(t, a, aap=None):
    """El plan con el estado de cada paso para este ticket."""
    from .aprobacion_areas import dias, etapas
    cols = plan(a)
    est = t["estado"]
    filas = list(etapas(t, a))
    usadas = set()
    nodos_job = {}
    if aap is not None and t.get("aap_job_id") and t.get("aap_job_tipo") == "workflow":
        try:
            nodos_job = {(n.get("name") or ""): n for n in aap.nodos_trabajo_workflow(t["aap_job_id"]) if n.get("id")}
        except Exception:  # noqa: BLE001 - sin AAP se muestra el estado del ticket
            nodos_job = {}
    rechazado = est == "rechazado"
    for col in cols:
        for n in col:
            if n["tipo"] == "inicio":
                n["estado"] = "ok"
                n["sub"] = f"{t['numero']} · {(t.get('creado') or '')[:16].replace('T', ' ')}"
            elif n["tipo"] == "aprobacion" and n.get("area"):
                f = next((x for x in filas if x["area"] == n["area"] and x["id"] not in usadas), None)
                if f:
                    usadas.add(f["id"])
                    n["estado"] = {"pendiente": "curso", "aprobada": "ok", "rechazada": "rechazo"}.get(f["estado"], "espera")
                    d = dias(f)
                    n["sub"] = (f"{f['quien']} · " if f.get("quien") else "") + (f"{d:.1f} días" if d >= 1 else f"{d * 24:.1f} h")
                else:
                    n["estado"] = "omitido" if rechazado else ("ok" if est in ("en_ejecucion", "exitoso", "fallido") else "espera")
            elif n.get("nodo_aap") and nodos_job.get(n["nodo_aap"]):
                s = nodos_job[n["nodo_aap"]].get("status")
                n["estado"] = {"successful": "ok", "failed": "falla", "error": "falla", "running": "curso", "pending": "curso",
                               "waiting": "espera", "canceled": "omitido"}.get(s, "espera")
            elif n["tipo"] == "fin":
                n["estado"] = {"exitoso": "ok", "fallido": "falla", "rechazado": "rechazo", "cancelado": "omitido"}.get(est, "espera")
            else:
                n["estado"] = {"en_ejecucion": "curso", "exitoso": "ok", "fallido": "falla", "rechazado": "omitido",
                               "cancelado": "omitido"}.get(est, "espera")
    return cols


def uso_mensual(cliente_id, aid, meses=12):
    """Tickets por mes (12 meses) de una automatización en este cliente: exitosos y el resto."""
    from datetime import datetime, timezone
    hoy = datetime.now(timezone.utc)
    y, m, lista = hoy.year, hoy.month, []
    for _ in range(meses):
        lista.insert(0, f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    cuenta = {k: {"ok": 0, "otros": 0} for k in lista}
    for r in q("SELECT substr(creado, 1, 7) mes, estado, count(*) n FROM tickets WHERE cliente_id=? AND automatizacion_id=? GROUP BY 1, 2", cliente_id, aid):
        if r["mes"] in cuenta:
            cuenta[r["mes"]]["ok" if r["estado"] == "exitoso" else "otros"] += r["n"]
    serie = [{"mes": k[5:7] + "/" + k[2:4], **v, "total": v["ok"] + v["otros"]} for k, v in cuenta.items()]
    return {"serie": serie, "max": max([x["total"] for x in serie] + [1]), "total": sum(x["total"] for x in serie)}
