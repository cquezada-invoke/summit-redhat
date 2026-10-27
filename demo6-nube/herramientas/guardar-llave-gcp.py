#!/usr/bin/python3
# Demo 6 · guarda la llave JSON de la cuenta de servicio de GCP en Vault (secret/gcp, campo "llave") sin mostrarla.
# Uso en la VM: python3 guardar-llave-gcp.py ~/llave.json   (después borrar el archivo y correr configurar_nube.py)
import json, os, ssl, sys, urllib.request
d = json.load(open(os.path.expanduser(sys.argv[1])))
for k in ("client_email", "private_key", "project_id"):
    if k not in d:
        sys.exit(f"El archivo no parece una llave de cuenta de servicio (falta {k}).")
if d["project_id"] != "invoke-summit-cloud":
    sys.exit(f"La llave es del proyecto {d['project_id']}, no de invoke-summit-cloud. No se guardó.")
root = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"]
req = urllib.request.Request("https://127.0.0.1:8200/v1/secret/data/gcp", method="POST",
                             data=json.dumps({"data": {"llave": json.dumps(d)}}).encode(),
                             headers={"X-Vault-Token": root, "Content-Type": "application/json"})
urllib.request.urlopen(req, context=ssl._create_unverified_context(), timeout=20).read()
print(f"Guardada en Vault secret/gcp ({d['client_email']}). Ahora: python3 ~/summit-redhat/demo6-nube/configurar_nube.py")
