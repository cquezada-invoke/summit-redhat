#!/bin/bash
cat > /tmp/e1s23.py <<'PY'
import time, cProfile, pstats, io
from app import create_app
app = create_app()
from app.bd import q1
u = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")["id"]
c = app.test_client()
with c.session_transaction() as s: s["uid"] = u; s["csrf"] = "x"; s["cliente_id"] = 1
for tid in (2486, 2484, 2478, 2392, 2300):
    ts = []
    for i in range(4):
        t0 = time.time(); r = c.get(f"/lab/tickets/{tid}"); ts.append(round((time.time() - t0) * 1000))
    print("TICKET", tid, r.status_code, ts)
pr = cProfile.Profile(); pr.enable(); c.get("/lab/tickets/2486"); pr.disable()
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(25); print("\n".join(l[:170] for l in s.getvalue().splitlines()[:60]))
PY
sudo podman cp /tmp/e1s23.py faceops:/tmp/e1s23.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/e1s23.py 2>&1 | grep -v -i warn | tail -70
