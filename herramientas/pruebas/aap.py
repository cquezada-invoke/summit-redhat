"""Consultas a AAP para las pruebas (dentro del contenedor de FaceOps):
  jobs '<plantilla>' '<desde ISO UTC>'   → trabajos (jobs o workflow_jobs) de esa plantilla creados desde esa hora
  aprobar <workflow_job_id>              → aprueba las aprobaciones pendientes de ese workflow (regla de Cristian)"""
import json
import sys

from app import create_app

app = create_app()
with app.app_context():
    from app.aap import AAP
    from app.bd import q1
    a = AAP.de_cliente(q1("SELECT * FROM clientes ORDER BY id LIMIT 1"))
    if sys.argv[1] == "jobs":
        out = []
        for ep in ("jobs/", "workflow_jobs/"):
            for j in a.get(ep, name=sys.argv[2], created__gte=sys.argv[3], order_by="-id", page_size=20).get("results") or []:
                ev = j.get("extra_vars") or "{}"
                try:
                    ev = json.loads(ev)
                except ValueError:
                    ev = {}
                out.append({"id": j["id"], "tipo": j["type"], "estado": j["status"], "lanzado": j.get("launch_type"),
                            "ra_accion": ev.get("ra_accion"), "ra_alerta": ev.get("ra_alerta"), "ra_namespace": ev.get("ra_namespace"), "ra_pod": ev.get("ra_pod")})
        print("JOBS", json.dumps(out, ensure_ascii=False))
    elif sys.argv[1] == "aprobar":
        n = 0
        for x in a.get("workflow_approvals/", status="pending", page_size=50).get("results") or []:
            wf = (x.get("summary_fields") or {}).get("source_workflow_job", {}).get("id")
            if str(wf) == sys.argv[2]:
                a.s.post(a.base + f"workflow_approvals/{x['id']}/approve/", json={}, timeout=30).raise_for_status()
                n += 1
        print("APROBADAS", n)
