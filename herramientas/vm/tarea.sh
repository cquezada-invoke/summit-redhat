# Detalle de fallos de respaldo y certificados (sin secretos)
sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -30
from app import create_app
create_app()
from app.bd import q1
import re
for n in ("FO-LAB-02337", "FO-LAB-02340", "FO-LAB-02345"):
    t = q1("SELECT estado, resultado FROM tickets WHERE numero=?", n)
    m = re.findall(r'"msg": "([^"]{0,400})', t["resultado"] or "")
    print("==", n, t["estado"], "|", (m[-1] if m else (t["resultado"] or "")[-400:]))
Q
sudo tail -5 /var/lib/faceops/autocuidado/registro.jsonl | cut -c1-300
