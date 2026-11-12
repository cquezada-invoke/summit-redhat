sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -20
from app import create_app
create_app()
from app.bd import q
for t in q("SELECT t.numero,t.estado,t.creado,a.nombre_aap,t.motivo FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.creado>='2026-11-12T22:30' AND (a.nombre_aap LIKE 'Migración%' OR t.motivo LIKE '%mig-%' OR a.nombre_aap LIKE 'Sistema · Inv%' OR t.motivo LIKE '%srv-mig%') ORDER BY t.id"): print(t["numero"], t["estado"], t["creado"][11:16], t["nombre_aap"], (t["motivo"] or "")[:40])
Q
