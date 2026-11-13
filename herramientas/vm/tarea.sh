#!/bin/bash
# b09 · corre en un contenedor aparte (misma imagen, montajes, red y variables que «faceops»). Sin secretos en la salida.
tail -15 ~/faceops-autodeploy/autodeploy.log | cut -c1-200; ps aux | grep -E "podman build|configurar.py|autodeploy" | grep -v grep | cut -c1-200 | head
cat > /tmp/b09.py <<'PY'

import json, re, time
from app import create_app
from app.bd import q, q1, j, ej
app = create_app()
from app.aap import AAP
from app.operacion import resolver_aprobacion, solicitar_en_equipo, solicitar
from app import pruebas
cl = dict(q1("SELECT * FROM clientes WHERE codigo='lab'")); cid = cl["id"]
u = dict(q1("SELECT * FROM usuarios WHERE usuario='qa.nucleo'"))
aap = AAP.de_cliente(cl); aap.en_vivo = True
APROB = "Aprobado por Claude (regla de Cristian)"
def auto(n):
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cid, n); return dict(a) if a else None
def equipo(n):
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cid, n); return dict(e) if e else None
def esperar(t, maxs=1800, aprobar=True):
    tid = t["id"]; t0 = time.time()
    while time.time() - t0 < maxs:
        t = dict(q1("SELECT * FROM tickets WHERE id=?", tid))
        if t["estado"] == "pendiente_aprobacion" and aprobar:
            try: resolver_aprobacion(u, tid, True, APROB)
            except Exception as x: print("  aprobar:", type(x).__name__, str(x)[:120], flush=True)
        if t["estado"] in ("exitoso", "fallido", "cancelado", "rechazado", "error"): break
        time.sleep(6)
    return t
def salida_job(t, filtro=r'"msg"|fatal:|FAILED|ok=|changed=|failed=|TASK \[', n=14):
    try:
        G = lambda p_: aap.s.get(aap.base + p_, timeout=30).json()
        ids = [t["aap_job_id"]] if t["aap_job_tipo"] == "job" else [ (x.get("summary_fields") or {}).get("job", {}).get("id") for x in G(f"workflow_jobs/{t['aap_job_id']}/workflow_nodes/")["results"]]
        out = []
        for jid in [x for x in ids if x]:
            jb = G(f"jobs/{jid}/")
            if jb.get("type") != "job": continue
            txt = aap.s.get(aap.base + f"jobs/{jid}/stdout/", params={"format": "txt"}, timeout=30).text
            out += [l.strip() for l in txt.splitlines() if re.search(filtro, l)]
        return out[-n:]
    except Exception as x:
        return ["salida no disponible: " + type(x).__name__]
def resumen(t):
    return f"{t['numero']} {t['estado']} | " + " | ".join((t["resultado"] or "").strip().splitlines()[-2:])[:260]

hs = aap.todos("hosts/")
from collections import Counter
inv = Counter((h["summary_fields"].get("inventory") or {}).get("name") for h in hs)
print("HOSTS", len(hs), "nombres distintos", len({h["name"] for h in hs}))
print("POR INVENTARIO", dict(inv))
try:
    hm = aap.get("host_metrics/", page_size=1); print("HOST_METRICS", hm.get("count"))
    hm_all = aap.todos("host_metrics/")
    vivos_aap = {h["name"] for h in hs}
    sin_host = [m for m in hm_all if m["hostname"] not in vivos_aap and not m.get("deleted")]
    print("METRICAS sin host", len(sin_host), [m["hostname"] for m in sin_host][:60])
    print("METRICAS borradas", sum(1 for m in hm_all if m.get("deleted")))
except Exception as x:
    print("host_metrics", str(x)[:200])
vivos = {e["nombre"] for e in q("SELECT nombre FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid)}
print("AAP no en FaceOps (vivos)", sorted({h["name"] for h in hs} - vivos)[:80])

PY
sudo podman inspect faceops > /tmp/b09-inspect.json
python3 - <<'PX' > /tmp/b09.args
import json, shlex
d = json.load(open("/tmp/b09-inspect.json"))[0]
a = ["--rm", "--name", "fo-b09", "--network", d["HostConfig"].get("NetworkMode") or "host", "--security-opt", "label=disable"]
if d["Config"].get("User"): a += ["--user", d["Config"]["User"]]
if d["HostConfig"].get("UsernsMode"): a += ["--userns", d["HostConfig"]["UsernsMode"]]
for m in d.get("Mounts", []):
    src = m.get("Source") if m.get("Type") == "bind" else m.get("Name") or m.get("Source")
    a += ["-v", f"{src}:{m['Destination']}" + (":ro" if not m.get("RW", True) else "")]
for e_ in d["Config"].get("Env", []): a += ["-e", e_]
a += ["-e", "FACEOPS_VIGIA=0", "-e", "PYTHONPATH=/opt/faceops", "-w", "/opt/faceops", "-v", "/tmp/b09.py:/tmp/b09.py:ro", "--entrypoint", "python3", d["ImageName"] or d["Image"], "/tmp/b09.py"]
print(" ".join(shlex.quote(x) for x in a))
PX
rm -f /tmp/b09-inspect.json
sudo podman rm -f fo-b09 >/dev/null 2>&1
eval "timeout 7000 sudo podman run $(cat /tmp/b09.args)" 2>&1 | grep -v -i -E "warn|token|password|secret|autorizacion|bearer|llave_privada|BEGIN OPENSSH" | tail -150
rm -f /tmp/b09.args /tmp/b09.py
