#!/bin/bash
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>&1 <<'Q' | grep -E "^(T|F|M)" | grep -v -i -E "token|authorization|password|secret"
import time, json
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1, q
    from app.aap import AAP
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); aap = AAP.de_cliente(cl)
    for n in ("FO-LAB-02261", "FO-LAB-02263"):
        t = q1("SELECT * FROM tickets WHERE numero=?", n)
        print("T", n, t["estado"], t["aap_job_tipo"], t["aap_job_id"], t["limite"], (t["resultado"] or "")[-300:].replace("\n", " "))
        try:
            jobs = [x["job"] for x in aap.todos(f"workflow_jobs/{t['aap_job_id']}/workflow_nodes/") if x.get("job")] if t["aap_job_tipo"] == "workflow" else [t["aap_job_id"]]
            for jb in jobs:
                try:
                    evs = aap.get(f"jobs/{jb}/job_events/", event="runner_on_failed", page_size=3).get("results") or []
                except Exception: continue
                for x in evs[:2]:
                    r = x.get("event_data", {}).get("res", {})
                    print("F", jb, x.get("task"), "·", str(r.get("msg", ""))[:300])
        except Exception as e: print("F err", str(e)[:200])
    # tiempo de una ida y vuelta POST a AAP comparable a responder una aprobación (respuesta 4xx esperada, no cambia nada)
    for i in range(3):
        t0 = time.time()
        try: aap.post("workflow_approvals/999999999/approve/")
        except Exception as e: pass
        print(f"M ida y vuelta POST AAP {(time.time()-t0)*1000:.0f} ms")
Q
