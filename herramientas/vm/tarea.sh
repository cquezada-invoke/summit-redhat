sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | tail -60
from app import create_app
from app.bd import q
app = create_app()
with app.app_context():
    print("CLIENTES", [(c["id"], c["nombre"]) for c in q("SELECT id, nombre FROM clientes")])
    for e in q("SELECT id, cliente_id, nombre, categoria, so, estado, ip, ambiente FROM equipos WHERE categoria LIKE 'estaci%' OR lower(nombre) LIKE '%pc%' OR lower(nombre) LIKE '%estac%' OR lower(nombre) LIKE '%desk%' OR lower(nombre) LIKE '%lapt%'"):
        print("EQ", dict(e))
    print("CATS", [dict(r) for r in q("SELECT categoria, count(*) n FROM equipos GROUP BY categoria")])
    print("SOS", [dict(r) for r in q("SELECT so, count(*) n FROM equipos WHERE cliente_id=1 GROUP BY so")][:30])
    import re
    pat = re.compile(r"(?i)parche|limpi|log|servicio|cis|cumpl|escaneo|respald|licenc|inventari|windows|certific|usuario|firewall|cifr|paquete|software")
    for a in q("SELECT DISTINCT nombre_aap FROM automatizaciones"):
        if pat.search(a["nombre_aap"] or ""): print("AUTO", a["nombre_aap"])
Q
