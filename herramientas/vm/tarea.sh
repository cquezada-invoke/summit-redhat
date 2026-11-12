#!/bin/bash
# Estado de nodo-so tras el plan y semántica del filtro --cve (solo lectura). Sin secretos.
S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ansible@192.168.122.12"
echo "virsh: $(sudo virsh domstate nodo-so)"
$S 'echo "uptime: $(uptime -p) · kernel: $(uname -r)"; echo "dnf/rpm en curso: $(pgrep -c -f "dnf|rpm -" )";
  echo "needs-restarting: $(sudo -n needs-restarting -r >/dev/null 2>&1 && echo no || echo si)";
  echo "avisos seguridad pendientes: $(sudo -n dnf -q updateinfo list --security 2>/dev/null | wc -l)";
  echo "con --cve=CVE-2026-8932: $(sudo -n dnf -q updateinfo list --security --cve=CVE-2026-8932 2>/dev/null | wc -l)";
  echo "con --cve=CVE-1999-0001 (inexistente): $(sudo -n dnf -q updateinfo list --security --cve=CVE-1999-0001 2>/dev/null | wc -l)";
  echo "info CVE-2026-8932: $(sudo -n dnf -q updateinfo info --cve=CVE-2026-8932 2>/dev/null | grep -c "^  *Update ID")";
  echo "historial dnf hoy:"; sudo -n dnf history list 2>/dev/null | head -8' 2>&1 | head -30
cat > /tmp/p3012.py <<'PY'
from app import create_app
from app.bd import q, q1
create_app()
cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
for t in q("SELECT t.numero, t.estado, t.motivo, t.resultado FROM tickets t WHERE t.cliente_id=? AND t.id>=2390 AND (t.motivo LIKE 'Plan de prueba%') ORDER BY t.id", cl["id"]):
    print(t["numero"], t["estado"], t["motivo"][:60], "|", " | ".join((t["resultado"] or "").strip().splitlines()[-1:])[:160])
e = q1("SELECT estado, datos FROM equipos WHERE cliente_id=? AND nombre='nodo-so'", cl["id"]); import json
print("FaceOps nodo-so:", e["estado"], (json.loads(e["datos"] or "{}") or {}).get("energia"))
PY
sudo podman cp /tmp/p3012.py faceops:/tmp/p3012.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3012.py 2>&1 | grep -v -i warn | tail -15; sudo podman exec faceops rm -f /tmp/p3012.py
