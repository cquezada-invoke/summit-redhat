"""Bitácora del ticket: lo que hizo la automatización en AAP, paso a paso y en lenguaje simple.
Se arma con los eventos de los trabajos de AAP (job_events); en workflows, un tramo por nodo (incluida la aprobación)."""
from .aap import ErrorAAP

EVENTOS = "runner_on_ok,runner_on_failed,runner_on_unreachable,runner_on_skipped,runner_item_on_failed,playbook_on_play_start"
ESTADO = {"runner_on_ok": "ok", "runner_on_failed": "fallo", "runner_item_on_failed": "fallo",
          "runner_on_unreachable": "sin_conexion", "runner_on_skipped": "omitido"}
PESO = {"fallo": 4, "sin_conexion": 4, "cambio": 3, "ok": 2, "omitido": 1}
MAX_EVENTOS = 1500


def _texto(res):
    """Mensaje legible de un resultado de Ansible (debug msg, error, o salida corta)."""
    if not isinstance(res, dict):
        return []
    if res.get("censored"):
        return ["(dato protegido: no se muestra)"]
    out = []
    m = res.get("msg")
    if isinstance(m, list):
        out += [str(x) for x in m if str(x).strip()]
    elif m not in (None, "", "All items completed"):
        out.append(str(m))
    for k in ("stderr", "stdout"):
        if res.get("failed") and res.get(k) and not out:
            out.append(str(res[k])[-600:])
    return [x[:1500] for x in out]


def pasos_de_trabajo(aap, job_id):
    try:
        eventos = aap.todos(f"jobs/{job_id}/job_events/", event__in=EVENTOS, order_by="counter", page_size=200)[:MAX_EVENTOS]
    except ErrorAAP:
        return [], 0
    pasos, idx, omitidos, play = [], {}, 0, ""
    for e in eventos:
        if e["event"] == "playbook_on_play_start":
            play = e.get("play") or ""
            pasos.append({"tipo": "play", "titulo": play})
            continue
        est = ESTADO.get(e["event"], "ok")
        if est == "ok" and e.get("changed"):
            est = "cambio"
        tarea = e.get("task") or "(tarea)"
        clave = (play, e.get("task_uuid") or tarea)
        if clave not in idx:
            idx[clave] = len(pasos)
            pasos.append({"tipo": "tarea", "titulo": tarea, "hosts": {}, "mensajes": [], "estado": est, "hora": e.get("created", "")})
        p = pasos[idx[clave]]
        h = e.get("host_name") or "localhost"
        if PESO.get(est, 0) >= PESO.get(p["hosts"].get(h), 0):
            p["hosts"][h] = est
        if PESO[est] > PESO[p["estado"]]:
            p["estado"] = est
        if est != "omitido":
            for m in _texto((e.get("event_data") or {}).get("res")):
                if m not in p["mensajes"]:
                    p["mensajes"].append(m)
    # Las tareas omitidas en todos los equipos no aportan al lector: se cuentan y se ocultan
    limpios = []
    for p in pasos:
        if p["tipo"] == "tarea" and p["estado"] == "omitido":
            omitidos += 1
            continue
        limpios.append(p)
    # quitar títulos de play que quedaron vacíos
    final = [p for i, p in enumerate(limpios) if p["tipo"] != "play" or (i + 1 < len(limpios) and limpios[i + 1]["tipo"] == "tarea")]
    return final, omitidos


def construir(aap, ticket):
    """[{titulo, tipo (plantilla|aprobacion|workflow), estado, job_id, pasos, omitidos, detalle}]"""
    if not ticket.get("aap_job_id"):
        return []
    if ticket["aap_job_tipo"] == "job":
        pasos, om = pasos_de_trabajo(aap, ticket["aap_job_id"])
        r = aap.trabajo("job", ticket["aap_job_id"])
        return [{"titulo": r.get("name", ""), "tipo": "plantilla", "estado": r.get("status"), "job_id": ticket["aap_job_id"],
                 "pasos": pasos, "omitidos": om}]
    tramos = []
    nodos = [n for n in aap.nodos_trabajo_workflow(ticket["aap_job_id"]) if n.get("id")]
    for n in sorted(nodos, key=lambda x: x["id"]):
        t = {"titulo": n.get("name", ""), "estado": n.get("status"), "job_id": n["id"], "pasos": [], "omitidos": 0}
        if n.get("type") == "workflow_approval":
            t["tipo"] = "aprobacion"
            try:
                ap = aap.aprobacion(n["id"])
                quien = ((ap.get("summary_fields") or {}).get("approved_or_denied_by") or {}).get("username", "")
                t["detalle"] = ("venció sin respuesta" if ap.get("timed_out") else
                                {"pending": "esperando respuesta", "successful": f"aprobada por {quien}",
                                 "failed": f"rechazada por {quien}"}.get(n.get("status"), n.get("status")))
            except ErrorAAP:
                pass
        elif n.get("type") == "job":
            t["tipo"] = "plantilla"
            t["pasos"], t["omitidos"] = pasos_de_trabajo(aap, n["id"])
        else:
            t["tipo"] = "workflow"
        tramos.append(t)
    return tramos
