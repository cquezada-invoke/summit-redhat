# Detalle del caso C3 de Atender alerta (sin secretos)
sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -14
from app import create_app
create_app()
from app.bd import q1, q
t = q1("SELECT estado, resultado FROM tickets WHERE numero='FO-LAB-02416'")
print(t["estado"]); print("\n".join(l for l in (t["resultado"] or "").splitlines() if "fatal" in l or "msg" in l or "FaceOps no" in l)[-1200:])
for e in q("SELECT cuando, regla, decision FROM autocuidado_eventos WHERE regla='AC07' ORDER BY id DESC LIMIT 3"): print(e)
Q
