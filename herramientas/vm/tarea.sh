cd ~/summit-redhat; git log --oneline -1 | cut -c1-50; git log --oneline -15 | grep -c "una sola iniciativa"
sudo podman exec -i faceops grep -c "reporte_iniciativas_u" /app/app/reportes.py 2>/dev/null || sudo podman exec -i faceops sh -c 'grep -rl reporte_iniciativas_u / --include=reportes.py 2>/dev/null | head -2'
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v -i warn | tail -15
from app import create_app
from app.bd import q
app = create_app()
with app.app_context():
    from app import reportes as R
    R.iniciar()
    print(q("SELECT seccion, GROUP_CONCAT(pilar, ',') p, COUNT(*) n FROM reporte_iniciativas WHERE cliente_id=1 GROUP BY 1"))
    print(q("SELECT name FROM sqlite_master WHERE name LIKE 'reporte_inic%'"))
Q
