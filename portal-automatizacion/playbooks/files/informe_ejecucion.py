#!/usr/bin/env python3
# Arma el registro de una ejecución de workflow en AAP y lo envía al portal (PDF «qué se hizo» por correo).
# Uso: informe_ejecucion.py <id workflow job> <url /api/informe del portal> <correo>
import base64, json, os, ssl, sys, urllib.error, urllib.request
wf, portal, correo = sys.argv[1], sys.argv[2], sys.argv[3]
host = (os.environ.get("CONTROLLER_HOST") or os.environ.get("AAP_HOSTNAME") or "").rstrip("/")
usu = os.environ.get("CONTROLLER_USERNAME") or os.environ.get("AAP_USERNAME") or ""
cla = os.environ.get("CONTROLLER_PASSWORD") or os.environ.get("AAP_PASSWORD") or ""
ctx = ssl._create_unverified_context()
cab = {"Authorization": "Basic " + base64.b64encode(f"{usu}:{cla}".encode()).decode()}
def get(ruta, txt=False):
    for base in ("/api/controller/v2/", "/api/v2/"):
        try:
            with urllib.request.urlopen(urllib.request.Request(host + base + ruta, headers=cab), context=ctx, timeout=30) as r:
                return r.read().decode() if txt else json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
    raise SystemExit("no se encontró la API de AAP")
w = get(f"workflow_jobs/{wf}/")
ev = json.loads(w.get("extra_vars") or "{}")
pidio = (w["summary_fields"].get("created_by") or {}).get("username", "-")
wjt = (w["summary_fields"].get("workflow_job_template") or {}).get("id")
desc = get(f"workflow_job_templates/{wjt}/")["description"] if wjt else ""
partes = {l.split(":", 1)[0].strip(): l.split(":", 1)[1].strip() for l in desc.split("\n") if ":" in l}
nodos = get(f"workflow_jobs/{wf}/workflow_nodes/?page_size=50")["results"]
aprobo, resultados, estado = "-", [], "ok"
for n in sorted(nodos, key=lambda x: (x["summary_fields"].get("job") or {}).get("id") or 10**9):
    j = n["summary_fields"].get("job")
    if not j or n.get("identifier") == "informe":
        continue
    if j.get("type") == "workflow_approval":
        a = get(f"workflow_approvals/{j['id']}/")
        aprobo = (a["summary_fields"].get("approved_or_denied_by") or {}).get("username", "-")
        if a["status"] != "successful":
            estado = "no_aprobada"
        continue
    jb = get(f"jobs/{j['id']}/")
    if jb["status"] != "successful":
        estado = "atencion"
    art = jb.get("artifacts") or {}
    pr = art.get("portal_resultado")   # resultado que cada automatización publica para el portal (título, estado y filas)
    if isinstance(pr, dict):
        resultados.append({"titulo": pr.get("titulo", ""), "estado": pr.get("estado", ""), "filas": pr.get("filas") or []})
    elif art.get("resultado_texto"):
        resultados.append({"titulo": art["resultado_texto"], "estado": "", "filas": []})
    elif jb["status"] != "successful":
        fe = get(f"jobs/{j['id']}/job_events/?failed=true&order_by=-counter&page_size=1")["results"]
        r_ = ((fe[0].get("event_data") or {}).get("res") or {}) if fe else {}
        resultados.append({"titulo": "No se completó", "estado": "atencion", "filas": [["Motivo", str(r_.get("msg") or r_.get("stderr") or "ver el trabajo en AAP")[:500]]]})
nombre = w["name"].split("·", 1)[-1].strip()
datos = {"tipo": "ejecucion", "titulo": nombre, "archivo": "resultado-" + str(wf), "job": wf, "correo": correo, "estado": estado,
         "alcance": f"Pedido por {pidio} · aprobado por {aprobo}",
         "objetivo": partes.get("Para qué", ""), "que_hace": partes.get("Qué hace", "") or partes.get("Qué revisa", ""),
         "obtienes": partes.get("Qué obtienes", ""),
         "parametros": {k: v for k, v in ev.items() if not any(s_ in k.lower() for s_ in ("clave", "pass", "token", "secret"))},
         "resultados": resultados}
req = urllib.request.Request(portal, data=json.dumps(datos).encode(), headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=180) as r:
    print(r.read().decode())
if estado != "ok":   # el PDF ya salió; el workflow debe quedar como fallido para que nadie lo lea como éxito
    sys.exit("La automatización no se completó (" + {"atencion": "un paso falló", "no_aprobada": "no fue aprobada"}.get(estado, estado) + "); el PDF con el detalle se envió igual.")
