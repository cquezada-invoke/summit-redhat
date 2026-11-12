"""Diagnósticos y propuestas de las alertas por objeto, por correo al responsable de la máquina.

Cuando una alerta se dispara con una acción que produce un diagnóstico (p. ej. «Diagnóstico automático»), Event-Driven
Ansible lanza el workflow «FaceOps · Alerta › <automatización>» en AAP; las acciones que necesitan un dato quedan como
propuesta en «Monitoreo · Responder alerta». Cuando ese trabajo termina en AAP, FaceOps (vigía) envía su resultado al
grupo dueño de la sección responsable de la máquina (su correo de grupo; si no tiene, el de pruebas del laboratorio).
Cada trabajo se avisa una sola vez (tabla alerta_correos). FaceOps solo lee AAP: no toca las máquinas."""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from .bd import ahora, auditar, con, ej, q, q1

_ultima = {"t": 0.0}
PREFIJO = "FaceOps · Alerta › "


def _tabla():
    con().executescript("""CREATE TABLE IF NOT EXISTS alerta_correos (
      cliente_id INTEGER NOT NULL, trabajo TEXT NOT NULL, equipo TEXT, alerta TEXT, para TEXT, resultado TEXT, fecha TEXT,
      PRIMARY KEY (cliente_id, trabajo));""")


def _catalogo():
    from .alertas_objeto import catalogo
    return catalogo()


def acciones_con_informe():
    """Acciones del catálogo cuyo resultado se envía: las de diagnóstico (llevan «diagn» en su clave o en su automatización
    de solo lectura «Verificar error conocido») y las que quedan como propuesta (pide_datos)."""
    acc = _catalogo().get("acciones") or {}
    return {k for k, v in acc.items() if "diagn" in k or "Verificar error conocido" in (v.get("auto") or "") or v.get("pide_datos")}


SECCION_DE = {"almacenamiento": "storage", "router": "redes", "switch": "redes", "firewall": "redes", "balanceador": "redes",
              "clúster openshift": "contenedores"}   # categoría → sección dueña (la misma «Área responsable» de la ficha)


def destino(cliente_id, e):
    """(correo, quién): el correo del GRUPO dueño de la sección responsable de la máquina (Servidores, Contenedores…); si el
    grupo no tiene correo, el correo de pruebas del laboratorio (FACEOPS_CORREO_PRUEBAS) y, si no, el de alertas."""
    from .grupos_dueno import grupo
    sec = SECCION_DE.get(((e or {}).get("categoria") or "servidor").lower(), "servidores")
    g = grupo(cliente_id, sec) or {}
    if g.get("correo"):
        return g["correo"], g.get("nombre") or sec
    for var, quien in (("FACEOPS_CORREO_PRUEBAS", "correo de pruebas del laboratorio"), ("FACEOPS_CORREO_ALERTAS", "correo de alertas")):
        v = os.environ.get(var, "").strip()
        if v:
            return v, f"{g.get('nombre') or sec} (sin correo de grupo: {quien})"
    return "", g.get("nombre") or sec


def _resumen_trabajo(aap, job_id, n=12):
    """Mensajes de las tareas del trabajo (debug/fail) o, si no hay, el final de su salida."""
    msgs = []
    try:
        for ev in aap.todos(f"jobs/{job_id}/job_events/", event__in="runner_on_ok,runner_on_failed", page_size=200):
            r = (ev.get("event_data") or {}).get("res") or {}
            m = r.get("msg")
            if m and not isinstance(m, (dict, list)) and str(m).strip() and str(m) != "All assertions passed":
                msgs.append(f"• {ev.get('task', '')}: {str(m).strip()[:600]}")
    except Exception:  # noqa: BLE001
        pass
    if msgs:
        return "\n".join(msgs[-n:])
    try:
        txt = aap.s.get(aap.base + f"jobs/{job_id}/stdout/", params={"format": "txt"}, timeout=40).text
        return "\n".join(txt.strip().splitlines()[-25:])
    except Exception:  # noqa: BLE001
        return "(sin salida)"


def _url_ficha(cliente, e):
    pub = os.environ.get("FACEOPS_URL_PUBLICA", "https://faceops.34.176.42.56.sslip.io:8843/faceops").rstrip("/")
    return f"{pub}/{(cliente.get('codigo') or '').lower()}/inventario/{e['id']}" if e else pub


def _url_aap(aap, tipo, id_):
    return f"{aap.web.rstrip('/')}/execution/jobs/{'workflow' if tipo == 'workflow_job' else 'playbook'}/{id_}/output"


def notificar(cliente, aap, trabajo, para=None):
    """Arma y envía el correo de un trabajo terminado. Devuelve el registro (o None si no corresponde)."""
    from .correo import enviar, hora   # hora en la zona del destinatario (perfil o último ingreso) o en UTC
    ev = trabajo.get("extra_vars") or "{}"
    try:
        ev = json.loads(ev)
    except ValueError:
        ev = {}
    es_wf = trabajo.get("type") == "workflow_job"
    if es_wf:   # FaceOps · Alerta › <automatización>: equipo en limit / origen_evento
        org = ev.get("origen_evento") or ""
        eq = trabajo.get("limit") or ev.get("limit") or (re.search(r" en ([A-Za-z0-9._-]+)$", org) or [None, ""])[1]
        alerta = ev.get("faceops_alerta") or ""
        sev = (re.search(r"\((\w+)\)", org) or [None, ""])[1]
        tipo_inf = "Diagnóstico"
        jobs = [n.get("summary_fields", {}).get("job", {}) for n in aap.todos(f"workflow_jobs/{trabajo['id']}/workflow_nodes/")]
        cuerpo_t = "\n\n".join(_resumen_trabajo(aap, j["id"]) for j in jobs if j.get("id") and j.get("type") == "job") or "(sin trabajos)"
        accion = trabajo["name"][len(PREFIJO):]
    else:       # Monitoreo · Responder alerta con una acción que pide un dato: queda como propuesta
        eq, alerta, sev = ev.get("ra_equipo", ""), ev.get("ra_alerta", ""), ev.get("ra_severidad", "")
        acc = (_catalogo().get("acciones") or {}).get(ev.get("ra_accion") or "") or {}
        if not acc.get("pide_datos"):
            return None
        tipo_inf, accion = "Propuesta", acc.get("auto", "")
        cuerpo_t = _resumen_trabajo(aap, trabajo["id"])
    prueba = "-prueba-" in (alerta or "")   # alerta sintética del botón «Probar»
    alerta = (alerta or "").split("-prueba-")[0]
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente["id"], eq) if eq else None
    a_cat = next((a for a in _catalogo().get("alertas") or [] if a["id"] == alerta), {})
    if para is None:
        para, quien = destino(cliente["id"], e)
    else:
        quien = para
    estado = {"successful": "terminó bien", "failed": "falló", "error": "falló", "canceled": "se canceló"}.get(trabajo.get("status"), trabajo.get("status"))
    asunto = f"[FaceOps{' · PRUEBA' if prueba else ''}] {tipo_inf} · alerta {a_cat.get('nombre', alerta)} ({alerta}, {sev or 'sin severidad'}) en {eq or 'equipo desconocido'}"
    cuerpo = (f"{tipo_inf} automático de la alerta «{a_cat.get('nombre', alerta)}» ({alerta}, severidad {sev or '—'}) en {eq}.\n"
              f"Acción: {accion} · el trabajo en AAP {estado} ({hora(trabajo.get('finished'), para) if trabajo.get('finished') else 'sin hora de término'}).\n\n"
              f"Resumen:\n{cuerpo_t}\n\n"
              f"Ficha de la máquina: {_url_ficha(cliente, e)}\n"
              f"Ticket / trabajo en AAP: {_url_aap(aap, trabajo.get('type'), trabajo['id'])}\n\n"
              f"Lo envía FaceOps al grupo responsable de la máquina ({quien or 'sin grupo'}).")
    res = enviar(para, asunto, cuerpo)
    _tabla()
    ej("INSERT OR REPLACE INTO alerta_correos(cliente_id, trabajo, equipo, alerta, para, resultado, fecha) VALUES (?,?,?,?,?,?,?)",
       cliente["id"], f"{trabajo.get('type')}:{trabajo['id']}", eq, alerta, para, res, ahora())
    auditar("sistema", cliente["id"], f"correo de {tipo_inf.lower()} de alerta", eq or "", {"trabajo": trabajo["id"], "para": para, "resultado": res, "asunto": asunto})
    return {"asunto": asunto, "para": para, "resultado": res, "cuerpo": cuerpo}


def revisar(cliente, horas=6):
    """Trabajos de respuesta a alertas terminados en las últimas horas y aún sin correo."""
    from .aap import AAP
    _tabla()
    aap = AAP.de_cliente(cliente)
    desde = (datetime.now(timezone.utc) - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ya = {r["trabajo"] for r in q("SELECT trabajo FROM alerta_correos WHERE cliente_id=?", cliente["id"])}
    autos = {(_catalogo().get("acciones") or {}).get(k, {}).get("auto") for k in acciones_con_informe()}
    cand = []
    for t in aap.get("workflow_jobs/", name__startswith=PREFIJO, finished__gte=desde, page_size=50, order_by="id").get("results") or []:
        if any(t["name"][len(PREFIJO):].startswith(a) for a in autos if a):
            cand.append(t)
    cand += aap.get("jobs/", name="Monitoreo · Responder alerta", finished__gte=desde, page_size=50, order_by="id").get("results") or []
    out = []
    for t in cand:
        if f"{t.get('type')}:{t['id']}" in ya or t.get("status") not in ("successful", "failed", "error", "canceled"):
            continue
        r = notificar(cliente, aap, t)
        if r:
            out.append(r)
    return out


def revisar_todos(cada=60):
    """Desde el vigía: como mucho una vez por minuto."""
    if time.time() - _ultima["t"] < cada:
        return
    _ultima["t"] = time.time()
    for c in q("SELECT * FROM clientes WHERE activo=1 AND aap_url!=''"):
        try:
            revisar(c)
        except Exception as x:  # noqa: BLE001
            auditar("sistema", c["id"], "error correo de diagnóstico de alertas", "alertas", {"error": str(x)[:300]})


def iniciar(cada=60):
    """Hilo propio (junto al vigía): revisa cada minuto los trabajos de respuesta a alertas terminados."""
    import threading
    from flask import current_app
    app = current_app._get_current_object()

    def bucle():
        while True:
            time.sleep(cada)
            try:
                with app.app_context():
                    revisar_todos(cada)
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=bucle, daemon=True, name="faceops-correo-alertas").start()
