sudo podman exec -i faceops python3 - <<'Q' 2>&1 | grep -v -i warn | tail -12
from app import create_app
create_app()
from app.bd import q
for t in q("SELECT numero,estado,substr(resultado,-1500) r FROM tickets WHERE numero IN ('FO-LAB-01816')"): print(t["numero"], t["estado"], (t["r"] or "").replace("\n"," | "))
for e in q("SELECT id,nombre,estado,so,ip,ambiente FROM equipos WHERE nombre LIKE 'srv-mig%'"): print(dict(e))
Q
cd ~/summit-redhat/herramientas/pruebas
cd ~/summit-redhat && git log -1 --format=%h; python3 faceops/aap/configurar.py 2>&1 | grep -E "Inventariar|Traceback" | head -3
