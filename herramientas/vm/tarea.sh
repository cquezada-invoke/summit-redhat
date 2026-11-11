sudo podman exec -i faceops python3 - 2>/dev/null <<'Q' | tail -40
from app import create_app
create_app()
from app.bd import q
for e in q("SELECT nombre, so, estado, ip!='' tiene_ip, energia FROM equipos WHERE (nombre LIKE 'srv-prueba-%' OR nombre LIKE 'nodo-%') ORDER BY nombre"): print(dict(e))
Q
