# Detalle de los casos fallidos de autocuidado (sin secretos)
sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -30
from app import create_app
create_app()
from app.bd import q1, q
for n in ("FO-LAB-02256", "FO-LAB-02268", "FO-LAB-02269"):
    t = q1("SELECT estado, resultado, aap_job_id FROM tickets WHERE numero=?", n)
    print("==", n, t["estado"], t["aap_job_id"]); print((t["resultado"] or "")[-900:])
for r in q("SELECT t.numero, t.estado, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE a.nombre_aap LIKE 'Autocuidado%' AND t.id > (SELECT id FROM tickets WHERE numero='FO-LAB-02273') ORDER BY t.id"):
    print("después:", r["numero"], r["estado"], r["nombre_aap"])
Q
