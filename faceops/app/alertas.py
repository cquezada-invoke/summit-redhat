"""Alertas de FaceOps = las mismas que maneja AAP: reglas de Event-Driven Ansible que se dispararon, activaciones de EDA
con problemas, trabajos fallidos y aprobaciones que esperan. Se leen en vivo desde AAP (cache de 30 s)."""
import time
from datetime import datetime, timedelta, timezone

from .aap import AAP, ErrorAAP
from .bd import q

_cache = {}
MALAS_ACT = {"failed", "error", "unresponsive", "workers offline"}


def _eda(aap, ruta, **params):
    return aap._r("GET", aap.web + "/api/eda/v1/" + ruta.lstrip("/"), params=params)


def leer(cliente, dias=7):
    k = (cliente["id"], dias)
    if k in _cache and time.time() - _cache[k][0] < 30:
        return _cache[k][1]
    aap = AAP.de_cliente(cliente)
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    errores, out = [], []
    tickets = {str(t["aap_job_id"]): t for t in q("SELECT id, numero, aap_job_id FROM tickets WHERE cliente_id=? AND aap_job_id IS NOT NULL", cliente["id"])}
    # 1. Event-Driven Ansible: reglas disparadas
    try:
        for r in _eda(aap, "audit-rules/", page_size=100, order_by="-fired_at").get("results", []):
            if (r.get("fired_at") or "") < desde:
                continue
            ai = r.get("activation_instance") or {}
            ok = (r.get("status") or "").lower() in ("successful", "success")
            out.append({"origen": "eda", "nivel": "alta" if not ok else "info", "titulo": r.get("name") or "regla",
                        "detalle": f"Rulebook «{ai.get('name') or r.get('ruleset_name') or '?'}» · regla {r.get('ruleset_name') or ''}".strip(" ·"),
                        "estado": r.get("status") or "", "cuando": r.get("fired_at"),
                        "url": f"{aap.publica}/eda/rule-audits/{r['id']}/details"})
    except ErrorAAP as e:
        errores.append(f"EDA (reglas): {str(e)[:160]}")
    # 2. Event-Driven Ansible: activaciones con problemas
    try:
        for a in _eda(aap, "activations/", page_size=200).get("results", []):
            st = (a.get("status") or "").lower()
            if st in MALAS_ACT or (a.get("is_enabled") and st in ("stopped", "completed")):
                out.append({"origen": "eda-activacion", "nivel": "critica" if st in MALAS_ACT else "media",
                            "titulo": f"Activación «{a['name']}» {st}", "detalle": (a.get("status_message") or "La regla ya no está escuchando eventos")[:240],
                            "estado": st, "cuando": a.get("modified_at"), "url": f"{aap.publica}/eda/rulebook-activations/{a['id']}/details"})
    except ErrorAAP as e:
        errores.append(f"EDA (activaciones): {str(e)[:160]}")
    # 3. Trabajos fallidos: una alerta por plantilla (no una por ejecución); si la última ejecución salió bien, queda resuelta
    try:
        recientes = aap.get("unified_jobs/", finished__gt=desde, order_by="-finished", page_size=400,
                            status__in="successful,failed,error").get("results", [])
        por_pl = {}
        for jb in recientes:
            if jb.get("type") not in ("job", "workflow_job"):
                continue
            por_pl.setdefault(jb.get("name") or "trabajo", []).append(jb)
        for nombre, js in por_pl.items():
            fallas = [x for x in js if x["status"] in ("failed", "error")]
            if not fallas:
                continue
            ult = js[0]  # la más reciente
            resuelta = ult["status"] == "successful"
            f = fallas[0]
            t = tickets.get(str(f["id"]))
            out.append({"origen": "trabajo", "nivel": "info" if resuelta else "alta", "titulo": nombre,
                        "detalle": (f"{len(fallas)} falla{'s' if len(fallas) != 1 else ''} de {len(js)} ejecuciones"
                                    + (" · la última salió bien" if resuelta else " · la última falló")
                                    + (f" · {(f.get('job_explanation') or '')[:120]}" if f.get("job_explanation") else "")),
                        "estado": "resuelta" if resuelta else f["status"], "cuando": f.get("finished"), "ticket": t,
                        "url": f"{aap.publica}/execution/jobs/{'workflow' if f['type'] == 'workflow_job' else 'playbook'}/{f['id']}/output"})
    except ErrorAAP as e:
        errores.append(f"Trabajos: {str(e)[:160]}")
    # 4. Aprobaciones pendientes
    try:
        for ap in aap.get("workflow_approvals/", status="pending", order_by="-created", page_size=100).get("results", []):
            wj = (ap.get("summary_fields") or {}).get("source_workflow_job") or {}
            t = tickets.get(str(wj.get("id")))
            creado = ap.get("created") or ""
            viejo = creado and creado < (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.append({"origen": "aprobacion", "nivel": "media" if viejo else "info", "titulo": ap.get("name") or "aprobación",
                        "detalle": f"Workflow «{wj.get('name') or '?'}»" + (" · espera hace más de 4 h" if viejo else ""),
                        "estado": "pendiente", "cuando": creado, "ticket": t,
                        "url": f"{aap.publica}/execution/workflow-approvals/{ap['id']}/details"})
    except ErrorAAP as e:
        errores.append(f"Aprobaciones: {str(e)[:160]}")
    orden = {"critica": 0, "alta": 1, "media": 2, "info": 3}
    out.sort(key=lambda x: x.get("cuando") or "", reverse=True)
    out.sort(key=lambda x: orden.get(x["nivel"], 9))
    res = {"alertas": out, "errores": errores, "cuenta": {n: sum(1 for x in out if x["nivel"] == n) for n in orden}}
    _cache[k] = (time.time(), res)
    return res
