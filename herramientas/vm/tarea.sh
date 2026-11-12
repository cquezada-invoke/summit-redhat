sudo podman exec -i faceops python3 - 2>/dev/null <<'Q' | tail -5
from app import create_app
app=create_app()
with app.app_context():
    from app.bd import q1
    from app.pruebas import estado
    for n in ["Plataforma · Controlar servicio","Sistema · Quitar agente","FaceOps · Agente de monitoreo (Linux)","FaceOps · Respaldo continuo en Git"]:
        print(n, estado(dict(q1("SELECT * FROM automatizaciones WHERE cliente_id=1 AND nombre_aap=?", n)))["estado"])
Q
