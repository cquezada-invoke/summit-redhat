cat > /tmp/diag.py <<'PY'
import json
from wsgi import app
from app.bd import q, q1, j
from app.aap import AAP
with app.app_context():
    x = q1("SELECT * FROM infraestructuras WHERE id=2")
    print("INFRA", x["nombre"], x["tipo"], x["estado"], "| cred:", x["credencial"], "| inv:", x["inventario"], "| verif:", x["verificado"], "| msg:", (x["mensaje"] or "")[:160])
    d = j(x["detalle"], {})
    print("DETALLE claves", sorted(d.keys()), "| contiene private_key:", "private_key" in (x["detalle"] or ""), "| contiene BEGIN:", "BEGIN" in (x["detalle"] or ""))
    cl = q1("SELECT * FROM clientes WHERE id=?", x["cliente_id"])
    a = AAP.de_cliente(cl)
    c = (a.get("credentials/", name=x["credencial"]).get("results") or [None])[0] if x["credencial"] else None
    if c:
        ins = c.get("inputs") or {}
        print("CRED AAP id", c["id"], "tipo", (c.get("summary_fields") or {}).get("credential_type", {}).get("name"), "mod", c.get("modified"),
              "| username(email):", bool(ins.get("username")), "| project:", ins.get("project"), "| ssh_key_data cifrada:", ins.get("ssh_key_data") == "$encrypted$")
    else:
        print("CRED AAP: no existe")
    for n in ("Nube · Validar conexión e inventariar", "Nube · Validar conexión e inventariar nube", "Nube · Encender o apagar VM"):
        r = q1("SELECT id, nombre FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", x["cliente_id"], n); print("AUTO", n, bool(r))
    print("AUTOS NUBE", [r["nombre_aap"] for r in q("SELECT nombre_aap FROM automatizaciones WHERE cliente_id=? AND nombre_aap LIKE 'Nube%'", x["cliente_id"])])
    jt = a.get("job_templates/", name__icontains="Nube").get("results") or []
    print("JT AAP", [t["name"] for t in jt])
    wf = a.get("workflow_job_templates/", name__icontains="Nube").get("results") or []
    print("WF AAP", [t["name"] for t in wf])
    au = q("SELECT * FROM auditoria WHERE cliente_id=? ORDER BY id DESC LIMIT 8", x["cliente_id"]) if q1("SELECT name FROM sqlite_master WHERE name='auditoria'") else []
    for r_ in au: print("AUD", {k: (str(v)[:90]) for k, v in dict(r_).items() if k not in ("detalle",)})
    print("TICKETS", [(t["numero"], t["estado"], (t.get("titulo") or t.get("motivo") or "")[:60]) for t in q("SELECT * FROM tickets WHERE cliente_id=? ORDER BY id DESC LIMIT 5", x["cliente_id"])])
PY
sudo podman cp /tmp/diag.py faceops:/tmp/diag.py && sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/diag.py 2>&1 | grep -v -i warn | tail -30
echo "--- LOGS POST"
sudo podman logs --since 3h faceops 2>&1 | grep -E "nubes/2|infraestructuras/2/|Traceback|Error" | grep -v -i "private\|BEGIN" | tail -25
