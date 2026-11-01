"""Avance del ticket caja por caja: qué está haciendo AAP en cada tarea del flujo, su log y si algo se quedó pegado.
Lo único que puede detener un ticket es una aprobación; si una ejecución deja de avanzar, FaceOps lo detecta, la cancela en
AAP y cierra el ticket con el motivo (nunca queda «pegado»)."""
import time
from datetime import datetime, timezone

from .aap import ErrorAAP
from .bd import j

SIN_AVANCE_AVISO_S = 5 * 60      # sin eventos nuevos: la caja se marca «sin avance»
SIN_AVANCE_CORTE_S = 20 * 60     # sin eventos nuevos: FaceOps cancela en AAP y cierra el ticket
EN_COLA_AVISO_S = 5 * 60         # AAP no toma el trabajo (sin capacidad o sin nodo de ejecución)
_total_cache = {}


def _seg(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(iso).replace("Z", "+00:00"))).total_seconds()
    except (ValueError, TypeError):
        return None


def _hace(s):
    if s is None:
        return ""
    s = int(s)
    return f"{s} s" if s < 90 else (f"{s // 60} min" if s < 5400 else f"{s // 3600} h {s % 3600 // 60} min")


def total_tareas(a):
    """Cuántas tareas tiene el playbook de la automatización (para repartir el avance entre las cajas)."""
    k = a["id"]
    if k in _total_cache and time.time() - _total_cache[k][0] < 600:
        return _total_cache[k][1]
    n = 0
    try:
        from .readme import pasos_playbook
        n = sum(len(ts) + 1 for _, ts in pasos_playbook(j(a.get("tecnico"), {})))   # +1: recolección de datos de cada play
    except Exception:  # noqa: BLE001
        n = 0
    _total_cache[k] = (time.time(), n)
    return n


def trabajos(aap, t):
    """[(job_id, nombre, estado)] de las plantillas que corren para el ticket (sin las aprobaciones)."""
    if not t.get("aap_job_id"):
        return []
    if t["aap_job_tipo"] == "job":
        r = aap.trabajo("job", t["aap_job_id"])
        return [(t["aap_job_id"], r.get("name", ""), r.get("status"))]
    return [(n["id"], n.get("name", ""), n.get("status")) for n in sorted(aap.nodos_trabajo_workflow(t["aap_job_id"]), key=lambda x: x.get("id") or 0)
            if n.get("type") == "job" and n.get("id")]


def ultimo_evento(aap, job_id):
    try:
        r = aap.get(f"jobs/{job_id}/job_events/", order_by="-counter", page_size=1).get("results") or []
    except ErrorAAP:
        return None
    return r[0] if r else None


def vigilancia(aap, t):
    """Estado de avance de la ejecución en curso: {trabajo, tarea, host, hace_s, aviso, cortar}."""
    out = {"aviso": "", "cortar": False}
    try:
        js = trabajos(aap, t)
    except ErrorAAP:
        return out
    vivo = next((x for x in js if x[2] in ("running", "pending", "waiting", "new")), None)
    if not vivo:
        return out
    jid, nombre, est = vivo
    out["trabajo"] = nombre
    if est in ("pending", "waiting", "new"):
        try:
            r = aap.trabajo("job", jid)
        except ErrorAAP:
            return out
        s = _seg(r.get("created"))
        if s and s > EN_COLA_AVISO_S:
            out["aviso"] = f"AAP aún no toma «{nombre}» (en cola hace {_hace(s)}): sin capacidad en los nodos de ejecución"
        out["hace_s"] = s
        return out
    ev = ultimo_evento(aap, jid)
    if not ev:
        return out
    s = _seg(ev.get("created"))
    out.update(tarea=ev.get("task") or "", host=ev.get("host_name") or "", hace_s=s)
    if s is not None and s > SIN_AVANCE_AVISO_S:
        out["aviso"] = f"Sin avance hace {_hace(s)} en «{out['tarea'] or nombre}»" + (f" ({out['host']})" if out["host"] else "")
    out["cortar"] = s is not None and s > SIN_AVANCE_CORTE_S
    return out


def repartir(aap, t, a, cols):
    """Asigna a cada caja de tarea los pasos de AAP que le corresponden y su estado real (hecha, en curso, en espera)."""
    from .bitacora import pasos_de_trabajo
    try:
        js = trabajos(aap, t)
    except ErrorAAP:
        return {}
    cajas = [n for col in cols for n in col if n["tipo"] == "tarea"]
    if not cajas or not js:
        return {}
    res = {}
    por_nodo = {n.get("nodo_aap") for n in cajas if n.get("nodo_aap")}
    if por_nodo:   # workflow de AAP: cada caja es un nodo (una plantilla) con su propio log
        for jid, nombre, est in js:
            pasos, _ = pasos_de_trabajo(aap, jid)
            res[nombre] = {"pasos": [p for p in pasos if p["tipo"] == "tarea"], "job": jid, "estado_aap": est}
        return {"por_nodo": res}
    pasos = []
    est_job = None
    for jid, _, est in js:
        ps, _ = pasos_de_trabajo(aap, jid)
        pasos += [dict(p, job=jid) for p in ps if p["tipo"] == "tarea"]
        est_job = est
    tot = max(total_tareas(a), len(pasos), 1)
    n = len(cajas)
    tramos = []
    for i in range(n):   # la caja i cubre las tareas [i·T/n, (i+1)·T/n) del playbook
        ini, fin = round(i * tot / n), round((i + 1) * tot / n)
        tramos.append({"pasos": pasos[ini:fin], "completa": len(pasos) >= fin, "empezada": len(pasos) > ini})
    return {"tramos": tramos, "estado_aap": est_job, "jobs": [x[0] for x in js]}
