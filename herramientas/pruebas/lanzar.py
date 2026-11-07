"""Arnés de la regla de prueba: lanza UN caso como ticket real (prueba=True), aprueba con la regla de Cristian, espera y
devuelve una línea «RES {json}». Corre dentro del contenedor de FaceOps:
  sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -u - '<json del caso>' < lanzar.py
Caso: {"auto": nombre AAP, "equipo": nombre o "", "params": {...}, "min": minutos, "caso": "C1", "motivo": "..."}"""
import json
import sys
import time

caso = json.loads(sys.argv[1])
from app import create_app  # noqa: E402

app = create_app()
with app.app_context():
    from app.aap import AAP
    from app.bd import q1
    from app.operacion import actualizar_tickets, resolver_aprobacion, solicitar, solicitar_en_equipo
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
    u = q1("SELECT * FROM usuarios WHERE id=1")
    aap = AAP.de_cliente(cl)

    def fallo(t):
        try:
            jobs = [t["aap_job_id"]]
            if t["aap_job_tipo"] == "workflow":
                jobs = [x["job"] for x in aap.todos(f"workflow_jobs/{t['aap_job_id']}/workflow_nodes/") if x.get("job")]
            for jb in jobs:
                try:
                    evs = aap.get(f"jobs/{jb}/job_events/", event="runner_on_failed").get("results") or []
                except Exception:  # noqa: BLE001  (nodo de aprobación u otro tipo)
                    continue
                for x in evs[:1]:
                    r = x.get("event_data", {}).get("res", {})
                    return f"{x.get('task')} · {str(r.get('msg', ''))[:400]} · {str(r.get('stderr', ''))[-400:]}"
        except Exception as e:  # noqa: BLE001
            return f"(sin detalle: {e})"
        return ""

    def stats(t):
        try:
            if t["aap_job_tipo"] == "workflow":
                art = {}
                for x in aap.todos(f"workflow_jobs/{t['aap_job_id']}/workflow_nodes/"):
                    if x.get("job") and (x.get("summary_fields", {}).get("job", {}).get("type") in (None, "job")):
                        try:
                            art.update(aap.get(f"jobs/{x['job']}/").get("artifacts") or {})
                        except Exception:  # noqa: BLE001
                            pass
                return art
            return aap.get(f"jobs/{t['aap_job_id']}/").get("artifacts") or {}
        except Exception:  # noqa: BLE001
            return {}

    n, eq, params = caso["auto"], caso.get("equipo"), caso.get("params") or {}
    out = {"caso": caso.get("caso", ""), "auto": n}
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n)
    if not a:
        out["estado"] = "sin automatización"
        print("RES", json.dumps(out, ensure_ascii=False), flush=True)
        sys.exit(0)
    motivo = caso.get("motivo") or f"Plan de prueba {caso.get('caso', '')} de «{n}» (regla de prueba)"
    try:
        if eq:
            e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cl["id"], eq)
            if not e:
                raise ValueError(f"no existe el equipo {eq}")
            if caso.get("apagado"):   # «FaceOps · Apagar equipo» se lanza por su propio camino
                from app.operacion import solicitar_apagado
                t = solicitar_apagado(u, cl, dict(e), motivo, prueba=True)
            else:
                t = solicitar_en_equipo(u, cl, a, dict(e), dict(params), motivo, prueba=True)
        else:
            t = solicitar(u, cl, a, caso.get("segmento", ""), dict(params), motivo, prueba=True)
    except Exception as x:  # noqa: BLE001
        out.update(estado="rechazado al solicitar", error=f"{type(x).__name__}: {x}"[:600])
        print("RES", json.dumps(out, ensure_ascii=False), flush=True)
        sys.exit(0)
    out["ticket"] = t["numero"]
    fin = time.time() + float(caso.get("min", 20)) * 60
    aprob = set()
    while time.time() < fin:
        time.sleep(8)
        try:
            actualizar_tickets()
        except Exception:  # noqa: BLE001
            pass
        x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
        clave = (x.get("aap_aprobacion_id"), x.get("aprobado_en"))
        if x["estado"] == "pendiente_aprobacion" and clave not in aprob:
            aprob.add(clave)
            try:
                resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)")
            except Exception as e:  # noqa: BLE001
                out["aprobacion"] = str(e)[:200]
        if x["estado"] in ("exitoso", "fallido", "rechazado", "vencido", "cancelado", "error"):
            break
    x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
    out["estado"] = x["estado"]
    out["stats"] = stats(x) if x.get("aap_job_id") else {}
    out["detalle"] = fallo(x) if x["estado"] != "exitoso" and x.get("aap_job_id") else ""
    out["resultado"] = (x["resultado"] or "")[-700:]
    print("RES", json.dumps(out, ensure_ascii=False, default=str), flush=True)
