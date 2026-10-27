#!/usr/bin/python3
# Demo 4 · deja vacío el registro de accesos de red (accesos/registro.yml en Gitea invoke/red-laboratorio).
# Úsalo junto con reiniciar-lab.sh, que borra las reglas del firewall: así el registro y el firewall quedan iguales.
# (Las reglas de la base de datos las deja como al inicio cerrar-app-bd.sh.)  No imprime claves.
import base64, json, os, urllib.error, urllib.request

SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
URL = "http://127.0.0.1:3000/api/v1/repos/invoke/red-laboratorio/contents/accesos/registro.yml"
H = {"Authorization": f"token {SEC['gitea_token']}", "Content-Type": "application/json"}
try:
    actual = json.load(urllib.request.urlopen(urllib.request.Request(URL, headers=H), timeout=20))
except urllib.error.HTTPError as e:
    if e.code == 404:
        print("   registro de accesos: no existe (nada que vaciar)")
        raise SystemExit(0)
    raise
texto = base64.b64decode(actual["content"]).decode()
if texto.strip() in ("accesos: {}", ""):
    print("   registro de accesos: ya estaba vacío")
    raise SystemExit(0)
nuevo = "# Accesos de red vigentes habilitados por ticket. Lo mantiene AAP; no editar a mano.\naccesos: {}\n"
datos = {"content": base64.b64encode(nuevo.encode()).decode(), "sha": actual["sha"], "message": "Registro de accesos vaciado (reinicio del laboratorio)",
         "author": {"name": "Ansible Automation Platform", "email": "aap@invoke.cl"}}
urllib.request.urlopen(urllib.request.Request(URL, data=json.dumps(datos).encode(), headers=H, method="PUT"), timeout=20).read()
print("   registro de accesos: vaciado")
