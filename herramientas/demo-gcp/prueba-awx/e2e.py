"""Recorrido de punta a punta de FaceOps en modo awx contra AWX/EDA reales (corre DENTRO del contenedor de FaceOps):
login → inventario → acción con ticket (aprobación en AWX → ejecución) → workflow con aprobación → alerta EDA → estado en FaceOps.
    podman exec -i -e CLAVE_PRUEBA=... faceops python3 - < e2e.py"""
import json
import os
import sys
import time

import requests

sys.path.insert(0, "/opt/faceops")
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402

APP = create_app()
OK = []


def chk(c, txt):
    OK.append(bool(c))
    print(("  ✓ " if c else "  ✗ ") + txt, flush=True)
    return c


with APP.app_context():
    from app.aap import AAP
    from app.bd import ej, q, q1
    cli = q1("SELECT * FROM clientes WHERE codigo='PRUEBA'")
    a = AAP.de_cliente(cli)
    chk(a.modo == "awx" and a.ping().get("version"), f"FaceOps ve AWX {a.ping().get('version')} (modo {a.modo})")
    # 1. login por HTTP con un usuario local
    u = q1("SELECT * FROM usuarios WHERE usuario='prueba'")
    ej("UPDATE usuarios SET clave_hash=? WHERE id=?", generate_password_hash(os.environ["CLAVE_PRUEBA"]), u["id"])
    s = requests.Session()
    r = s.post("http://127.0.0.1:8110/ingresar", data={"usuario": "prueba", "clave": os.environ["CLAVE_PRUEBA"]}, allow_redirects=False)
    r2 = s.get("http://127.0.0.1:8110/", allow_redirects=True)
    chk(r.status_code in (302, 303) and r2.status_code == 200 and "ingresar" not in r2.url, f"login (HTTP {r.status_code} → {r2.url.split('8110')[-1]})")
    # 2. inventario y catálogo desde AWX
    from app.inventario import sincronizar
    from app.operacion import sincronizar_catalogo
    try:
        sincronizar(cli)
    except Exception as x:  # noqa: BLE001
        print("    sincronizar inventario:", str(x)[:300])
    eqs = q("SELECT * FROM equipos WHERE cliente_id=?", cli["id"])
    chk(any(e["nombre"] == "nodo-prueba" for e in eqs), f"inventario: {[e['nombre'] for e in eqs]}")
    try:
        sincronizar_catalogo(cli)
    except Exception as x:  # noqa: BLE001
        print("    sincronizar catálogo:", str(x)[:300])
    autos = {x["nombre_aap"]: x for x in q("SELECT * FROM automatizaciones WHERE cliente_id=?", cli["id"])}
    chk({"Prueba · Ping", "Prueba · Workflow con aprobación"} <= set(autos), f"catálogo: {sorted(autos)}")
    # 3. acción con ticket → aprobación en AWX → ejecución
    from app.creditos import cargar
    from app.operacion import actualizar_tickets, resolver_aprobacion, solicitar
    cargar(cli["id"], 1000, "prueba", "prueba")
    usuario = dict(q1("SELECT * FROM usuarios WHERE usuario='prueba'"))
    eq = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='nodo-prueba'", cli["id"])

    def recorrer(auto, etiqueta):
        try:
            t = solicitar(usuario, cli, dict(auto), "", {}, f"Prueba FaceOps-AWX · {etiqueta}", equipo=dict(eq) if eq else None, prueba=True)
        except Exception as x:  # noqa: BLE001
            chk(False, f"{etiqueta}: solicitar → {type(x).__name__}: {str(x)[:300]}")
            return None
        print(f"    ticket {t['numero']} estado {t['estado']} job {t['aap_job_id']}", flush=True)
        vistos = set()
        for _ in range(90):
            actualizar_tickets()
            t = q1("SELECT * FROM tickets WHERE id=?", t["id"])
            if t["estado"] not in vistos:
                vistos.add(t["estado"])
                print(f"    → {t['estado']}", flush=True)
            if t["estado"] == "pendiente_aprobacion":
                resolver_aprobacion(usuario, t["id"], True, "aprobado en la prueba")
                print("    aprobado desde FaceOps", flush=True)
            if t["estado"] in ("exitoso", "fallido", "rechazado", "cancelado", "error"):
                break
            time.sleep(5)
        chk("pendiente_aprobacion" in vistos and t["estado"] == "exitoso",
            f"{etiqueta}: ticket {t['numero']} {' → '.join(sorted(vistos))} · AWX {t['aap_job_tipo']} #{t['aap_job_id']} · {(t.get('resultado') or '')[:120]}")
        return t

    t1 = recorrer(autos.get("Prueba · Ping") or {}, "acción con ticket")
    if t1:
        wf = q1("SELECT aprobacion_wf FROM automatizaciones WHERE id=?", t1["automatizacion_id"])["aprobacion_wf"]
        chk(wf and "workflow_job_templates" not in a.url_ui("workflow_template", wf) and "/#/templates/workflow_job_template/" in a.url_ui("workflow_template", wf),
            f"workflow de aprobación creado en AWX (id {wf}) · {a.url_ui('workflow_template', wf)}")
        chk(a.url_trabajo(t1["aap_job_tipo"], t1["aap_job_id"]).startswith("http://127.0.0.1:30080/#/jobs/"), f"enlace: {a.url_trabajo(t1['aap_job_tipo'], t1['aap_job_id'])}")
    # 4. workflow propio de AWX con su aprobación
    recorrer(autos.get("Prueba · Workflow con aprobación") or {}, "workflow con aprobación")
    # 5. alerta → EDA → respuesta en AWX, visible en FaceOps
    acts = a.eda("GET", "activations/").get("results", [])
    act = next((x for x in acts if x["name"] == "Prueba alertas"), {})
    print(f"    activación EDA: {act.get('status')}", flush=True)
    for _ in range(40):
        if a.eda("GET", f"activations/{act['id']}/").get("status") == "running":
            break
        time.sleep(6)
    import subprocess
    disparo = None
    for _ in range(20):
        try:
            disparo = requests.post(os.environ.get("EDA_WEBHOOK", "http://127.0.0.1:30500/"), json={"alerta": "disco", "equipo": "nodo-prueba"}, timeout=5).status_code
            break
        except requests.RequestException:
            time.sleep(6)
    print(f"    webhook → {disparo}", flush=True)
    regla = None
    for _ in range(40):
        rs = a.eda("GET", "audit-rules/", params={"page_size": 20}).get("results", [])
        regla = next((x for x in rs if x.get("name") == "Disco lleno"), None)
        if regla:
            break
        time.sleep(6)
    chk(regla, f"EDA disparó la regla «Disco lleno» ({(regla or {}).get('status')})")
    job = None
    for _ in range(40):
        js = a.get("jobs/", name="Prueba · Responder alerta", order_by="-id").get("results", [])
        if js and js[0]["status"] in ("successful", "failed", "error"):
            job = js[0]
            break
        time.sleep(6)
    chk(job and job["status"] == "successful", f"respuesta en AWX lanzada por EDA: {(job or {}).get('status')}")
    from app import alertas
    alertas._cache.clear()
    al = alertas.leer(cli)
    eda_al = [x for x in al["alertas"] if x["origen"] == "eda"]
    chk(not al["errores"] and eda_al, f"FaceOps › Alertas: {len(al['alertas'])} alertas, EDA: {[x['titulo'] for x in eda_al]} · errores {al['errores']}")
    if eda_al:
        print("    enlace:", eda_al[0]["url"])
    # 6. licencia y nodos
    from app.licencias import licencia_aap
    chk((licencia_aap(cli) or {}).get("comunitario"), "Licencias: AWX sin suscripción (no bloquea)")
    print(f"\n{sum(OK)}/{len(OK)} verificaciones OK")
