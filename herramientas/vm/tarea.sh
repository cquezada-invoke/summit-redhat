cat > /tmp/m.py <<'PY'
from app import create_app
from app.bd import q, q1
create_app()
from app.aap import AAP
cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); aap = AAP.de_cliente(cl)
t = q("SELECT t.* FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE a.nombre_aap='Migración · RHEL a versión soportada' AND t.motivo LIKE '%C4%' ORDER BY t.id DESC LIMIT 1")[0]
print("TICKET", t["numero"], t["estado"], t["creado"])
jid = t["aap_job_id"]
if t["aap_job_tipo"] == "workflow":
    jid = [x["job"] for x in aap.todos(f"workflow_jobs/{jid}/workflow_nodes/") if x.get("job")][-1]
for e in aap.todos(f"jobs/{jid}/job_events/?event__in=runner_on_ok,runner_on_failed,runner_on_unreachable"):
    tk = e.get("task") or ""
    if any(k in tk for k in ("Leapp", "leapp", "Upgrade", "Reinici", "apague", "Análisis previo", "Bloqueos", "Acceso de root", "Responde", "Volver", "Esperar")):
        r = e.get("event_data", {}).get("res", {})
        print(e["created"][11:19], e["event"], tk[:45], "|", str(r.get("stdout", ""))[-250:].replace("\n", " / "), "|", str(r.get("msg", ""))[:160], "| elapsed", r.get("elapsed"))
PY
sudo podman cp /tmp/m.py faceops:/tmp/m.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/m.py 2>&1 | grep -v -i "warn" | tail -30 | cut -c1-600; sudo podman exec faceops rm -f /tmp/m.py
free -g | sed -n 2p
