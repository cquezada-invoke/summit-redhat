for i in $(seq 1 20); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done
cat > /tmp/hl.py <<'PYEOF'
# Arnés: python3 /tmp/h.py '<params json>' aprobar|rechazar 'motivo' [nombre_aap]
import json, sys, time, re
from app import create_app
from app.bd import q, q1, j
from app.aap import AAP
from app.operacion import solicitar_en_equipo, resolver_aprobacion
a = create_app()
with a.app_context():
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); cid = cl["id"]
    u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 LIMIT 1")
    nom = sys.argv[4] if len(sys.argv) > 4 else "Certificados · Limpiar certificados sobrantes"
    au = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cid, nom)
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='nodo-jboss'", cid)
    t = solicitar_en_equipo(u, cl, au, e, json.loads(sys.argv[1]), sys.argv[3], prueba=True)
    tid = t["id"]; print("TICKET", t["numero"], flush=True)
    for k in range(150):
        x = q1("SELECT * FROM tickets WHERE id=?", tid)
        if x["estado"] == "pendiente_aprobacion":
            resolver_aprobacion(u, tid, sys.argv[2] == "aprobar", "Aprobado por Claude (regla de Cristian)" if sys.argv[2] == "aprobar" else "Rechazado por Claude (plan de prueba)")
        if x["estado"] in ("exitoso", "fallido", "cancelado", "rechazado"):
            break
        time.sleep(6)
    print("ESTADO", x["numero"], x["estado"], "job", x["aap_job_id"], x["aap_job_tipo"])
    print("RESULTADO", " | ".join((x["resultado"] or "").strip().splitlines()[-4:])[:900])
    if x["aap_job_id"]:
        ap = AAP.de_cliente(cl); jid = x["aap_job_id"]
        if x["aap_job_tipo"] == "workflow":
            ns = ap.get(f"workflow_jobs/{jid}/workflow_nodes/").get("results", [])
            js = [n["summary_fields"]["job"]["id"] for n in ns if (n.get("summary_fields") or {}).get("job", {}).get("type") == "job"]
            jid = js[-1] if js else None
        if jid:
            jb = ap.get(f"jobs/{jid}/")
            print("JOB", jid, jb["status"], "ARTIF", json.dumps((jb.get("artifacts") or {}).get("faceops_cert_limpieza"), ensure_ascii=False)[:2500])
            out = ap.s.get(ap.base + f"jobs/{jid}/stdout/?format=txt", timeout=30).text
            for l in out.splitlines():
                if any(s in l for s in ("Se conserva", "Servicios falsos", "Directorios sin", "Sin tocar", "A revocar", "revocado", "fatal:", "\"msg\"", "PLAY RECAP", "changed=", "Limpieza:", "Revisión:", "Falló", "restaur")):
                    print("  ", re.sub(r"-----BEGIN[^\"]*", "[pem]", l.strip())[:400])
PYEOF
sudo podman cp /tmp/hl.py faceops:/tmp/hl.py
EX="sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/hl.py"
$EX '{"cert_limpiar_modo": "revisar"}' aprobar "Plan de prueba · Limpiar certificados sobrantes · C1 revisar" 2>&1 | grep -v -i warn
sudo podman exec faceops rm -f /tmp/hl.py
