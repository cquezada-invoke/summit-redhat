# Evidencia del caso fallido de Respaldar (sin secretos)
sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -8
from app import create_app
create_app()
from app.bd import q1
import json
r = q1("SELECT casos FROM pruebas_automatizacion WHERE nombre_aap='Autocuidado · Respaldar bases de AAP y FaceOps' ORDER BY id DESC LIMIT 1")
for c in json.loads(r["casos"]): print(c["id"], c["resultado"], c["evidencia"][:300])
Q
sudo ls -la /var/backups/faceops-autocuidado/ | tail -6
