"""«Probar» una alerta desde su flotante: dispara una alerta SINTÉTICA por la misma cadena real (Alertmanager del anfitrión →
Event-Driven Ansible → acción elegida) sin tocar la máquina, y sigue lo que pasa:
  · Solo avisar: Alertmanager la recibe y la entrega a EDA; FaceOps envía la notificación al correo del grupo responsable.
  · Diagnóstico (u otra acción de solo lectura con informe): EDA pide el workflow con aprobación; como es una prueba de solo
    lectura FaceOps lo aprueba («Aprobado por Claude (regla de Cristian) · PRUEBA»), corre y el resultado se envía por correo.
  · Acción que cambia algo (reiniciar, ajustar…): en modo prueba NO se ejecuta: se genera la aprobación y FaceOps la rechaza
    marcada «PRUEBA»; se notifica por correo que la cadena llegó hasta la aprobación.
La alerta de prueba lleva prueba="si" y faceops_alerta=<id>-prueba-<hhmm> (EDA la agrupa aparte: no frena las reales)."""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from .bd import ahora, auditar, con, ej, q1

ALERTMANAGER = os.environ.get("FACEOPS_ALERTMANAGER", "http://127.0.0.1:9093")


def _tabla():
    con().executescript("""CREATE TABLE IF NOT EXISTS alerta_pruebas (
      id INTEGER PRIMARY KEY, cliente_id INTEGER, equipo TEXT, ambito TEXT, alerta TEXT, accion TEXT, etiqueta TEXT,
      inicio TEXT, fin TEXT, estado TEXT, pasos TEXT DEFAULT '[]', usuario TEXT);""")


def ultima(cliente_id, equipo, ambito, aid):
    _tabla()
    r = q1("SELECT * FROM alerta_pruebas WHERE cliente_id=? AND equipo=? AND ambito=? AND alerta=? ORDER BY id DESC LIMIT 1",
           cliente_id, equipo, ambito or "maquina", aid)
    return dict(r, pasos=_pasos_utc(r)) if r else None


def leer(pid):
    _tabla()
    r = q1("SELECT * FROM alerta_pruebas WHERE id=?", pid)
    return dict(r, pasos=_pasos_utc(r)) if r else None


def _pasos_utc(r):
    """Los pasos se guardan como «HH:MM:SS · texto» (hora del contenedor, que corre en UTC, sin día ni zona). Al leerlos se
    completan con el día de la prueba y la Z para que el navegador los muestre en su hora local; no se migra nada."""
    from .hora_local import a_utc
    ps, base = json.loads(r["pasos"] or "[]"), a_utc(r.get("inicio"))
    if not base:
        return ps
    out, dia = [], base.replace(hour=0, minute=0, second=0, microsecond=0)
    prev = base.replace(microsecond=0) - timedelta(seconds=60)
    for p in ps:
        h = p[:8]
        if len(p) > 10 and p[2] == ":" and p[5] == ":" and p[8:11] == " · " and h.replace(":", "").isdigit():
            t = dia + timedelta(hours=int(h[:2]), minutes=int(h[3:5]), seconds=int(h[6:8]))
            if t < prev - timedelta(hours=1):   # pasó la medianoche UTC
                dia += timedelta(days=1)
                t += timedelta(days=1)
            prev = t
            p = t.strftime("%Y-%m-%dT%H:%M:%SZ") + p[8:]
        out.append(p)
    return out


def _paso(pid, texto, estado=None):
    r = q1("SELECT pasos FROM alerta_pruebas WHERE id=?", pid)
    ps = json.loads((r or {}).get("pasos") or "[]") + [f"{datetime.now().strftime('%H:%M:%S')} · {texto}"]
    if estado:
        ej("UPDATE alerta_pruebas SET pasos=?, estado=?, fin=? WHERE id=?", json.dumps(ps, ensure_ascii=False), estado,
           ahora() if estado in ("ok", "falló") else None, pid)
    else:
        ej("UPDATE alerta_pruebas SET pasos=? WHERE id=?", json.dumps(ps, ensure_ascii=False), pid)


def lanzar(app, cliente, e, ambito, aid, usuario):
    """Dispara la alerta de prueba y deja un hilo siguiendo la cadena. Devuelve el id de la prueba."""
    import requests
    from . import alertas_objeto as ao
    _tabla()
    t = ao.tipo_de(e)
    prom = ao.reglas_monitoreo(e, ambito, t)
    d = (prom or {}).get(aid)
    if not d:
        raise ValueError("La alerta no está cargada en el monitoreo: actívala primero (Generar ticket)")
    r0 = d["reglas"][0]
    lab = {k: str(v) for k, v in (r0.get("labels") or {}).items()}
    acc = lab.get("faceops_accion") or ("por_definir" if t == "openshift" else "avisar")
    etq = f"{aid}-prueba-{datetime.now().strftime('%H%M%S')}"
    lab.update(alertname=r0["name"], faceops_alerta=etq, prueba="si", faceops_prueba_de=aid)
    ann = {k: str(v) for k, v in (r0.get("annotations") or {}).items()}
    ann["summary"] = "[PRUEBA] " + ann.get("summary", f"{e['nombre']} · {aid}")
    ej("INSERT INTO alerta_pruebas(cliente_id, equipo, ambito, alerta, accion, etiqueta, inicio, estado, usuario) VALUES (?,?,?,?,?,?,?,?,?)",
       cliente["id"], e["nombre"], ambito or "maquina", aid, acc, etq, ahora(), "en curso", usuario.get("usuario"))
    pid = q1("SELECT MAX(id) m FROM alerta_pruebas")["m"]
    auditar(usuario.get("usuario"), cliente["id"], "probar alerta", e["nombre"], {"alerta": aid, "accion": acc, "etiqueta": etq})
    if t == "openshift":   # el clúster no tiene acciones todavía: se prueba la notificación al grupo responsable
        threading.Thread(target=_seguir, args=(app, pid, cliente, dict(e), aid, acc, etq, lab, ann, False), daemon=True).start()
        return pid
    ahora_ = datetime.now(timezone.utc)
    cuerpo = [{"labels": lab, "annotations": ann, "startsAt": ahora_.isoformat(), "endsAt": (ahora_ + timedelta(minutes=3)).isoformat(),
               "generatorURL": "faceops://prueba"}]
    try:
        requests.post(ALERTMANAGER + "/api/v2/alerts", json=cuerpo, timeout=5).raise_for_status()
    except Exception as x:  # noqa: BLE001
        _paso(pid, f"Alertmanager no aceptó la alerta de prueba ({type(x).__name__})", "falló")
        return pid
    _paso(pid, "alerta de prueba enviada a Alertmanager")
    threading.Thread(target=_seguir, args=(app, pid, cliente, dict(e), aid, acc, etq, lab, ann, True), daemon=True).start()
    return pid


def _seguir(app, pid, cliente, e, aid, acc, etq, lab, ann, por_am):
    with app.app_context():
        try:
            _seguir_(pid, cliente, e, aid, acc, etq, lab, ann, por_am)
        except Exception as x:  # noqa: BLE001
            _paso(pid, f"error siguiendo la prueba: {str(x)[:200]}", "falló")


def _correo(cliente, e, aid, asunto, cuerpo):
    from .alerta_diagnostico import destino
    from .correo import enviar
    para, quien = destino(cliente["id"], e)
    return enviar(para, asunto, cuerpo), para, quien


def _seguir_(pid, cliente, e, aid, acc, etq, lab, ann, por_am):
    import requests
    from .aap import AAP
    from .alertas_objeto import catalogo
    cat = catalogo()
    a_cat = next((x for x in cat["alertas"] if x["id"] == aid), {})
    info_acc = (cat.get("acciones") or {}).get(acc) or {}
    t0 = datetime.now(timezone.utc) - timedelta(seconds=30)
    if por_am:
        rec = []
        for _ in range(12):
            try:
                xs = requests.get(ALERTMANAGER + "/api/v2/alerts", params={"filter": [f'faceops_alerta="{etq}"']}, timeout=5).json()
                rec = sorted({r["name"] for x in xs for r in x.get("receivers") or []})
                if xs:
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(5)
        if not rec:
            _paso(pid, "Alertmanager no muestra la alerta de prueba", "falló")
            return
        _paso(pid, f"Alertmanager la recibió y la entrega a {', '.join(rec)}" + (" (Event-Driven Ansible)" if "faceops-objetos" in rec else ""))
    titulo = f"[FaceOps · PRUEBA] {a_cat.get('nombre', aid)} ({aid}) en {e['nombre']}"
    if acc in ("avisar", "por_definir"):
        res, para, quien = _correo(cliente, e, aid, titulo,
                                   f"Prueba de la alerta «{a_cat.get('nombre', aid)}» ({aid}, {lab.get('severity', '')}) en {e['nombre']}.\n"
                                   f"Acción configurada: {'solo avisar' if acc == 'avisar' else 'por definir'}.\n"
                                   + ("La alerta de prueba pasó por Alertmanager y llegó a Event-Driven Ansible, que la registra.\n" if por_am else
                                      "Las alertas del clúster aún no tienen acción: se prueba la notificación al grupo responsable.\n")
                                   + "Esta es la notificación que llega al grupo responsable cuando la alerta se dispara de verdad.")
        _paso(pid, f"notificación: correo a {quien} ({para or 'sin correo'}): {res}", "ok" if res.startswith("enviado") else "falló")
        return
    # acción con automatización: EDA lanza «Monitoreo · Responder alerta»
    aap = AAP.de_cliente(cliente)
    desde = t0.strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = None
    for _ in range(36):
        for j in aap.get("jobs/", name="Monitoreo · Responder alerta", created__gte=desde, order_by="-id", page_size=20).get("results") or []:
            try:
                ev = json.loads(j.get("extra_vars") or "{}")
            except ValueError:
                ev = {}
            if ev.get("ra_alerta") == etq:
                resp = j
        if resp and resp.get("status") in ("successful", "failed", "error", "canceled"):
            break
        time.sleep(5)
    if not resp:
        _paso(pid, "Event-Driven Ansible no lanzó «Monitoreo · Responder alerta» en 3 minutos", "falló")
        return
    _paso(pid, f"EDA lanzó «Monitoreo · Responder alerta» (trabajo {resp['id']}: {resp['status']})")
    if info_acc.get("pide_datos"):   # queda como propuesta: se avisa
        res, para, quien = _correo(cliente, e, aid, titulo, f"Prueba: la alerta {aid} en {e['nombre']} pidió «{info_acc.get('auto')}» como propuesta "
                                                       f"(necesita {info_acc.get('pide_datos')}); en una alerta real queda esperando esos datos en FaceOps.")
        _paso(pid, f"propuesta registrada; correo a {quien} ({para}): {res}", "ok" if res.startswith("enviado") else "falló")
        return
    wf = None
    for _ in range(24):
        for w in aap.get("workflow_jobs/", name__startswith="FaceOps · Alerta › ", created__gte=desde, order_by="-id", page_size=20).get("results") or []:
            try:
                ev = json.loads(w.get("extra_vars") or "{}")
            except ValueError:
                ev = {}
            if ev.get("faceops_alerta") == etq:
                wf = w
        if wf:
            break
        time.sleep(5)
    if not wf:
        _paso(pid, "no apareció el workflow de la acción con aprobación", "falló")
        return
    from .alerta_diagnostico import _tabla as _tc, notificar
    _tc()
    informe = bool(info_acc.get("informe"))
    _paso(pid, f"workflow «{wf['name']}» ({wf['id']}) esperando aprobación")
    hecho = False
    for _ in range(24):
        for x in aap.get("workflow_approvals/", status="pending", page_size=50).get("results") or []:
            if (x.get("summary_fields") or {}).get("source_workflow_job", {}).get("id") == wf["id"]:
                aap.post(f"workflow_approvals/{x['id']}/{'approve' if informe else 'deny'}/", {})
                hecho = True
        if hecho:
            break
        time.sleep(5)
    if not informe:
        if not hecho:
            _paso(pid, "no se encontró la aprobación pendiente del workflow", "falló")
            return
        ej("INSERT OR REPLACE INTO alerta_correos(cliente_id, trabajo, equipo, alerta, para, resultado, fecha) VALUES (?,?,?,?,?,?,?)",
           cliente["id"], f"workflow_job:{wf['id']}", e["nombre"], etq, "", "prueba: rechazada", ahora())
        res, para, quien = _correo(cliente, e, aid, titulo,
                                   f"Prueba de la alerta {aid} en {e['nombre']}: la cadena llegó hasta la aprobación de «{info_acc.get('auto')}» "
                                   f"(workflow {wf['id']}). Por ser PRUEBA, FaceOps la rechazó y la acción NO se ejecutó.")
        _paso(pid, f"aprobación PRUEBA rechazada: la acción no se ejecutó; correo a {quien} ({para}): {res}", "ok" if res.startswith("enviado") else "falló")
        return
    _paso(pid, "aprobada (PRUEBA, solo lectura · regla de Cristian); corriendo el diagnóstico")
    st = wf.get("status")
    for _ in range(60):
        st = aap.get(f"workflow_jobs/{wf['id']}/").get("status")
        if st in ("successful", "failed", "error", "canceled"):
            break
        time.sleep(10)
    r = notificar(cliente, aap, aap.get(f"workflow_jobs/{wf['id']}/"))
    res = (r or {}).get("resultado") or "sin correo"
    _paso(pid, f"diagnóstico {st}; correo a {(r or {}).get('para') or '—'}: {res}", "ok" if st == "successful" and res.startswith("enviado") else "falló")
