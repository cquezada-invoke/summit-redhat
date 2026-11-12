cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-60
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 15
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | tail -25
import re
from app import create_app
from app.bd import q1, q
app = create_app()
with app.app_context():
    cl = q1("SELECT id FROM clientes WHERE codigo='lab'")
    from app.mod_edge import sembrar_demo_lab
    print("demo nuevo:", sembrar_demo_lab(cl["id"]))
    ss = q("SELECT id, codigo, alias, nodo, equipos, ubicaciones, ubic_eq, demo FROM edge_sitios WHERE cliente_id=?", cl["id"]); print(ss); print(q("SELECT id, nombre, demo FROM edge_grupos WHERE cliente_id=?", cl["id"]))
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"; s["cliente_id"] = cl["id"]
    for u in ["/edge", "/estaciones"] + [f"/edge/{x['id']}" for x in ss]:
        r = c.get(u); h = r.get_data(as_text=True)
        t = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", h, flags=re.S))
        print(u, r.status_code, "menu-edge", 'nav-sub' in h and '>Edge<' in h, "barra", "sl-barra" in h, "cx", 'id="fi-cx"' in h, "grupos", 'id="fi-gr"' in h, "foco", "Dónde focalizar" in h, "svg", 'id="diagrama"' in h, "tipo-alta", 'name=tipo required' in h, "grupo-form", 'name="grupo_id"' in h, "est-sitios", 'id="est-sitios"' in h, "crearVM", 'Crear nueva máquina virtual' in h, re.findall(r'class="ed-stx"[^>]*>([^<]*)', h), h.count('class="ed-link"'))
        i = t.find("Código Nombre") if u.endswith("edge") else t.find("Execution node y hop"); print("  ", t[i:i+500])
    r = c.get("/inventario/29"); print("ocp 29", r.status_code)
Q
