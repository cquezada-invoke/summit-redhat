#!/bin/bash
# Certificación módulo Núcleo · fase 1 (solo lectura): versión desplegada, AAP, estado de prueba de las acciones del núcleo, equipos y usuarios. Sin secretos.
echo "host: $(date -u +%FT%TZ) · disco $(df -h / | awk 'NR==2{print $4" libres"}')"
echo "autodeploy: $(systemctl is-active faceops-autodeploy.timer 2>/dev/null) · $(systemctl --user is-active faceops-autodeploy.timer 2>/dev/null)"
echo "desplegado: $(sudo podman exec faceops cat /opt/faceops/app/version.json 2>/dev/null)"
echo "AAP ping: $(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 https://127.0.0.1/api/controller/v2/ping/)"
cat > /tmp/n1.py <<'PY'
import json
from app import create_app
from app.bd import q, q1
app = create_app()
with app.app_context():
    from app.pruebas import estado
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
    print("cliente lab id", cl["id"], "· aap_url host", (cl.get("aap_url") or "").split("//")[-1].split("/")[0])
    noms = ["Infra · Encender o apagar equipo", "FaceOps · Vincular máquina a la bóveda", "Sistema · Inventariar máquina",
            "Sistema · Paquetes · inventario y respaldo", "Plataforma · Controlar servicio", "Sistema · Quitar agente",
            "FaceOps · Vincular máquina on-premise", "FaceOps · Servidores (alta manual)", "FaceOps · Incorporar máquinas",
            "FaceOps · Apagar equipo", "FaceOps · Agente de monitoreo (Linux)", "FaceOps · Habilitar servidor"]
    for n in noms:
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n)
        if not a: print("AUTO", n, "-> no está en el catálogo"); continue
        e = estado(dict(a, cliente_id=cl["id"]))
        print("AUTO", a["id"], n, "| aprob", a["requiere_aprobacion"], "| tipo", a["aap_tipo"], "| prueba:", e.get("estado"), e.get("fecha") or e.get("cuando") or "", "| tickets", (e.get("tickets") or "")[:80] if isinstance(e.get("tickets"), str) else e.get("tickets"))
    tot = {}
    for e in q("SELECT id, nombre, tipo, estado, COALESCE(energia,'') energia FROM equipos WHERE cliente_id=? ORDER BY id", cl["id"]) if 0 else []:
        pass
    cols = [r["name"] for r in q("PRAGMA table_info(equipos)")]
    print("cols equipos:", ",".join(c for c in cols if c in ("energia","estado","encendido","power","tipo","clase","vinculado","so","ip")))
    rows = q("SELECT * FROM equipos WHERE cliente_id=?", cl["id"])
    import collections
    k = next((c for c in ("energia", "encendido", "power", "estado") if c in cols), None)
    print("equipos:", len(rows), "· por", k, dict(collections.Counter(str(r.get(k)) for r in rows)))
    for r in rows[:60]:
        print("  EQ", r["id"], r["nombre"], "|", r.get("tipo") or r.get("clase"), "|", k, r.get(k))
    print("usuarios:", [(u["id"], u["usuario"], u["es_admin"], u.get("totp_activo")) for u in q("SELECT * FROM usuarios WHERE usuario LIKE 'qa%' OR usuario LIKE 'prueba%' OR usuario LIKE 'test%'")])
    print("n usuarios", q1("SELECT COUNT(*) n FROM usuarios")["n"])
    print("tablas:", [t["name"] for t in q("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%grupo%' OR name LIKE '%perfil%' OR name LIKE '%cola%' OR name LIKE '%prueba%')")])
    t = q1("SELECT id, numero, estado FROM tickets WHERE cliente_id=? ORDER BY id DESC LIMIT 1", cl["id"]); print("ultimo ticket", t)
    print("cola:", q("SELECT estado, COUNT(*) n FROM cola_acciones GROUP BY estado"))
PY
sudo podman cp /tmp/n1.py faceops:/tmp/n1.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/n1.py 2>&1 | grep -v -i -E "token|password|secret|warn" | tail -120; sudo podman exec faceops rm -f /tmp/n1.py
python3 -c "import playwright; print('playwright host ok')"
