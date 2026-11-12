cd ~/summit-redhat
echo "== colecciones google.cloud en el anfitrión"
sudo find / -xdev -path "*ansible_collections/google/cloud/MANIFEST.json" 2>/dev/null | head -5 | while read f; do echo "$f $(python3 -c "import json,sys;print(json.load(open('$f'))['collection_info']['version'])" 2>/dev/null)"; done
sudo bash -c 'for v in $(podman volume ls -q 2>/dev/null); do :; done; find /home -path "*google/cloud/MANIFEST.json" 2>/dev/null | head -3'
cat > /tmp/m.py <<'PY'
from app import create_app
from app.bd import q, q1, j
a = create_app(); cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
for i in q("SELECT id, nombre, tipo, credencial, inventario, region, detalle FROM infraestructuras WHERE cliente_id=? AND tipo IN ('gcp','aws','azure')", cl["id"]):
    d = j(i["detalle"], {}); print("infra", i["id"], i["nombre"], i["tipo"], i["credencial"], i["inventario"], {k: v for k, v in d.items() if not any(s in k.lower() for s in ("clave", "secret", "json", "key", "token", "pass"))})
for x in q("SELECT nombre_aap, aap_id FROM automatizaciones WHERE cliente_id=? AND nombre_aap LIKE 'Nube%'", cl["id"]): print("auto", dict(x))
from app.aap import AAP
aap = AAP.de_cliente(cl)
for c in aap.todos("credentials/?credential_type__kind=gce"): print("cred", c["id"], c["name"], c.get("inputs", {}).get("project"), c.get("inputs", {}).get("username"))
for t in aap.todos("job_templates/?name__startswith=Nube"): print("jt", t["id"], t["name"], t["playbook"])
PY
for i in $(seq 1 20); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done
sudo podman cp /tmp/m.py faceops:/tmp/m.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/m.py 2>&1 | grep -v "Warning\|warnings.warn" | tail -30; sudo podman exec faceops rm -f /tmp/m.py
