#!/usr/bin/python3
# Migración WebLogic → OpenShift · publica archivos en repositorio/openshift-manifiestos, carpeta migraciones-weblogic/ (Gitea),
# creando o actualizando cada uno.
# Uso: weblogic_git.py <ruta en el repo> <archivo local> [...]; entorno: G (URL de Gitea), T (token), MSG (mensaje del commit)
import base64, json, os, sys, time, urllib.request

api = os.environ["G"].rstrip("/") + "/api/v1/repos/repositorio/openshift-manifiestos/contents/migraciones-weblogic/"
hd = {"Authorization": "token " + os.environ["T"], "Content-Type": "application/json"}
ok = 0
for ruta, local in zip(sys.argv[1::2], sys.argv[2::2]):
    sha = None
    try:
        sha = json.load(urllib.request.urlopen(urllib.request.Request(api + ruta, headers=hd), timeout=30)).get("sha")
    except Exception:  # noqa: BLE001 - no existe todavía
        pass
    body = {"content": base64.b64encode(open(local, "rb").read()).decode(), "message": os.environ.get("MSG", "FaceOps")}
    if sha:
        body["sha"] = sha
    for intento in range(3):   # Gitea puede estar reiniciándose: reintenta
        try:
            urllib.request.urlopen(urllib.request.Request(api + ruta, data=json.dumps(body).encode(), headers=hd, method="PUT" if sha else "POST"), timeout=60)
            ok += 1
            break
        except Exception as x:  # noqa: BLE001
            if intento == 2:
                print(f"git sin publicar {ruta}: {str(x)[:160]}")
            time.sleep(5)
print(f"git ok {ok}")
