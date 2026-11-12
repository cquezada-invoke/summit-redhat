#!/bin/bash
# Lectura (sin cambios): ticket 2181, su acción y la plantilla de AAP de «Aplicar parches de seguridad». Sin secretos.
cat > /tmp/p3001.py <<'PY'
import json
from app import create_app
from app.bd import q, q1
create_app()
t = q1("SELECT * FROM tickets WHERE id=2181") or q1("SELECT * FROM tickets WHERE numero LIKE '%2181'")
if not t:
    print("sin ticket 2181"); raise SystemExit
a = q1("SELECT id, nombre, nombre_aap, aap_tipo, aap_id, campos, requiere_aprobacion FROM automatizaciones WHERE id=?", t["automatizacion_id"])
print("TICKET", t["id"], t["numero"], "estado=", t["estado"], "cliente=", t["cliente_id"], "limite=", t["limite"], "creado=", t["creado"])
print("motivo=", t["motivo"]); print("parametros=", t["parametros"]); print("filtros=", t.get("filtros") if hasattr(t, "get") else "")
print("aap_job_id=", t["aap_job_id"], "tipo=", t["aap_job_tipo"], "resultado=", (t["resultado"] or "")[:300])
print("ACCION", a["id"], a["nombre"], "|", a["nombre_aap"], a["aap_tipo"], a["aap_id"], "aprob=", a["requiere_aprobacion"])
print("campos_accion=", a["campos"])
e = q1("SELECT id, nombre, so, ambiente FROM equipos WHERE cliente_id=? AND nombre=?", t["cliente_id"], (t["limite"] or "").split(":")[0])
if e:
    n = q1("SELECT count(*) n FROM cves_equipo WHERE cliente_id=? AND equipo_id=? AND estado='pendiente'", t["cliente_id"], e["id"])["n"]
    print("MAQUINA", e["id"], e["nombre"], e["so"], e["ambiente"], "cve_pendientes=", n)
for x in q("SELECT t.id, t.numero, t.estado, t.creado FROM tickets t WHERE t.cliente_id=? AND t.automatizacion_id=? ORDER BY t.id DESC LIMIT 6", t["cliente_id"], a["id"]):
    print("  otro", x["id"], x["numero"], x["estado"], x["creado"])
cl = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
from app.aap import AAP
aap = AAP.de_cliente(cl)
jt = aap.get("job_templates/", name="Seguridad · Aplicar parches de seguridad")["results"]
for x in jt:
    print("JT", x["id"], "timeout=", x.get("timeout"), "survey=", x.get("survey_enabled"), "ask_vars=", x.get("ask_variables_on_launch"),
          "ask_limit=", x.get("ask_limit_on_launch"), "forks=", x.get("forks"), "playbook=", x.get("playbook"), "extra_vars=", x.get("extra_vars"))
    if x.get("survey_enabled"):
        print("  survey=", json.dumps(aap.get(f"job_templates/{x['id']}/survey_spec/"), ensure_ascii=False)[:800])
if t["aap_job_id"]:
    try:
        w = aap.get(("workflow_jobs/" if t["aap_job_tipo"] == "workflow" else "jobs/") + f"{t['aap_job_id']}/")
        print("JOB", w.get("id"), w.get("status"), w.get("name"), "extra_vars=", (w.get("extra_vars") or "")[:400])
    except Exception as x_:
        print("JOB err", type(x_).__name__)
PY
sudo podman cp /tmp/p3001.py faceops:/tmp/p3001.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3001.py 2>&1 | grep -v -i -E "warn|token|password|secret" | tail -40; sudo podman exec faceops rm -f /tmp/p3001.py
