"""Sincroniza el catálogo de FaceOps con AAP y los formularios (para que un cambio de formulario —por ejemplo por_inventario— rija)."""
from app import create_app

app = create_app()
with app.app_context():
    from app.bd import q
    from app.operacion import sincronizar_catalogo
    for c in q("SELECT * FROM clientes WHERE aap_token IS NOT NULL AND aap_token != ''"):
        sincronizar_catalogo(dict(c), "Claude (regla de prueba)")
        print("SINCRONIZADO", c["codigo"])
