cat > /tmp/d.py <<'PY'
from app import create_app
from app.bd import q1
a = create_app()
t = q1("SELECT numero, estado, resultado, parametros FROM tickets WHERE numero='FO-LAB-02359'")
print(t["estado"]); print((t["resultado"] or "")[-1500:]); print(t["parametros"][:600])
PY
for i in $(seq 1 20); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done
sudo podman cp /tmp/d.py faceops:/tmp/d.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/d.py 2>&1 | grep -v "Warning\|warnings.warn" | grep -v -i "token\|private" | cut -c1-700 | tail -30
