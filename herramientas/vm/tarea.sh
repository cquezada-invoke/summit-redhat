#!/bin/bash
# Descubrimiento (solo lectura): nubes en FaceOps, credencial en AAP (sin secretos), EE y sus módulos
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>/dev/null <<'Q'
import json
from app import create_app
create_app()
from app.bd import q, q1
from app.aap import AAP
for x in q("SELECT id,cliente_id,nombre,tipo,region,credencial,inventario,ubicacion,estado,verificado FROM infraestructuras WHERE tipo IN ('gcp','aws','azure')"):
    print("INFRA", json.dumps(x, ensure_ascii=False))
cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); print("CLIENTE", cl["id"], cl["codigo"])
aap = AAP.de_cliente(cl)
for c in aap.get("credentials/", page_size=100).get("results"):
    if any(k in c["name"] for k in ("GCP","Nube","AWS","Azure","gcp")):
        print("CRED", c["id"], c["name"], c["summary_fields"]["credential_type"]["name"], sorted((c.get("inputs") or {}).keys()), (c.get("inputs") or {}).get("project"), (c.get("inputs") or {}).get("username"))
t = aap.get("credential_types/", name="Google Compute Engine").get("results")[0]
print("INJ", json.dumps(t["injectors"]))
for jt in aap.get("job_templates/", name__startswith="Nube").get("results"):
    print("JT", jt["id"], jt["name"], jt["execution_environment"])
jt = aap.get("job_templates/", name="Migración a GCP · Servidor Linux").get("results")[0]
ee = aap.get(f"execution_environments/{jt['execution_environment']}/"); print("EE", ee["id"], ee["name"], ee["image"])
print("AUTOS", [a["nombre_aap"] for a in q("SELECT nombre_aap FROM automatizaciones WHERE cliente_id=? AND nombre_aap LIKE 'Nube%'", cl["id"])])
Q
IMG=$(sudo podman images --format '{{.Repository}}:{{.Tag}}' | grep -iE 'ee|execution' | head -5); echo "IMAGENES $IMG"
for i in $IMG; do echo "== $i"; sudo podman run --rm --entrypoint python3 $i -c "
import importlib
for m in ('google.auth','google.cloud.bigquery','googleapiclient','requests','boto3','azure.identity','azure.mgmt.costmanagement'):
    try: importlib.import_module(m); print('ok',m)
    except Exception as e: print('no',m)
" 2>&1 | tail -8; sudo podman run --rm --entrypoint sh $i -c "which gcloud bq; ansible-galaxy collection list 2>/dev/null | grep -E 'google|amazon|azure'" 2>&1|tail -5; done
