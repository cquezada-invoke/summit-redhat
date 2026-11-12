cd ~/summit-redhat && git log --oneline -1 | cut -c1-50
sudo podman exec faceops grep -c "hipervisor_accion" /app/app/mod_hipervisores.py 2>/dev/null || sudo podman exec faceops sh -c 'grep -rlc hipervisor_accion / --include=mod_hipervisores.py 2>/dev/null | head -2'
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v Warning | tail -40
import re
from app import create_app
from app.bd import q
app = create_app()
with app.app_context():
    hs = q("SELECT i.id, i.tipo, i.nombre FROM infraestructuras i JOIN clientes c ON c.id=i.cliente_id WHERE c.codigo='lab' AND i.tipo IN ('kvm','vmware','openshift-virt','nutanix','vsphere','hyperv','proxmox')")
    u = q("SELECT id FROM usuarios WHERE es_admin=1 AND activo=1 ORDER BY id LIMIT 1")[0]
print([(h["id"], h["tipo"], h["nombre"]) for h in hs])
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = u["id"]; s["csrf"] = "x"
    for h_ in hs:
        r = c.get(f"/lab/hipervisores/{h_['id']}")
        h = r.get_data(as_text=True)
        txt = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", h, flags=re.S))
        print(h_["id"], r.status_code, "barra", "sl-barra" in h, "energia", 'id="fi-energia"' in h, "sal", 'id="fi-sal"' in h, "cx", 'id="fi-cx"' in h, "secreto_visible", "BEGIN" in h, "acc_llave" in h)
        if h_["id"] == 6:
            i = txt.find("Salud de "); print(txt[i:i+1000])
            i = txt.find("Encendido"); print(txt[i:i+400])
            open("/tmp/h6.html","w").write(h)
        elif r.status_code != 200: print(h[-800:])
Q
