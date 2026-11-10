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
nodos = get(f"workflow_jobs/{wf}/workflow_nodes/?page_size=50")["results"]
hall, pasos = [], 0
for n in sorted(nodos, key=lambda x: (x["summary_fields"].get("job") or {}).get("id") or 10**9):
    j = n["summary_fields"].get("job")
    if not j or n.get("identifier") == "informe":
        continue
    pasos += 1
    if j.get("type") == "workflow_approval":
        a = get(f"workflow_approvals/{j['id']}/")
        quien = (a["summary_fields"].get("approved_or_denied_by") or {}).get("username", "-")
        ok = a["status"] == "successful"
        hall.append({"sev": "ok" if ok else "alto", "titulo": f"Aprobación: {'aprobada' if ok else 'rechazada o vencida'}",
                     "detalle": f"«{j['name']}» · respondió: {quien}"})
        continue
    jb = get(f"jobs/{j['id']}/")
    res = get(f"jobs/{j['id']}/job_host_summaries/?page_size=100")["results"]
    obj = [f"{h.get('host_name') or (h['summary_fields'].get('host') or {}).get('name', 'localhost')}: {h['changed']} cambio(s), {h['ok']} ok, {h['failures'] + h['dark']} falla(s)" for h in res]
    cambios = sum(h["changed"] for h in res)
    evs = get(f"jobs/{j['id']}/job_events/?event=runner_on_ok&order_by=-counter&page_size=200")["results"]
    msgs = []
    for e in evs:
        d_ = e.get("event_data") or {}
        if (d_.get("task_action") or "").endswith("debug") and "msg" in (d_.get("res") or {}):
            m = d_["res"]["msg"]
            msgs.append(m if isinstance(m, str) else json.dumps(m, ensure_ascii=False, indent=1))
        if len(msgs) >= 3:
            break
    falla = ""
    if jb["status"] != "successful":
        fe = get(f"jobs/{j['id']}/job_events/?failed=true&order_by=-counter&page_size=5")["results"]
        if fe:
            r_ = (fe[0].get("event_data") or {}).get("res") or {}
            falla = f"Falló en «{fe[0].get('task', '')}»: {str(r_.get('msg') or r_.get('stderr') or '')[:600]}"
    hall.append({"sev": "ok" if jb["status"] == "successful" else "critico",
                 "titulo": f"{j['name']}: {'terminó bien' if jb['status'] == 'successful' else 'falló'} · {cambios} cambio(s) · {round(jb.get('elapsed') or 0)} s",
                 "detalle": ("\n".join(reversed(msgs)) or "Sin mensajes de resultado.")[:2500],
                 "porque": falla, "objetos": obj})
nombre = w["name"].split("·", 1)[-1].strip()
datos = {"tipo": "ejecucion", "titulo": f"Qué se hizo · {nombre}", "archivo": "ejecucion-" + str(wf), "job": wf, "correo": correo,
         "alcance": f"Pedido por {pidio} · resultado: {'con fallas' if any(h['sev'] != 'ok' for h in hall) else 'completada sin errores'}",
         "parametros": {k: v for k, v in ev.items() if not any(s in k.lower() for s in ("clave", "pass", "token", "secret"))},
         "secciones": [{"titulo": "Pasos de la ejecución", "que": f"{pasos} paso(s), en el orden en que corrieron.", "hallazgos": hall}]}
req = urllib.request.Request(portal, data=json.dumps(datos).encode(), headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=180) as r:
    print(r.read().decode())
