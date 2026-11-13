#!/bin/bash
# b08 · corre en un contenedor aparte (misma imagen, montajes, red y variables que «faceops»). Sin secretos en la salida.
echo "timer: $(systemctl is-active faceops-autodeploy.timer 2>&1) · $(systemctl list-timers faceops-autodeploy.timer --no-legend 2>&1 | head -1 | cut -c1-120)"; echo "estado: $(cut -c1-160 ~/faceops-autodeploy/estado)"
cat > /tmp/b08.py <<'PY'

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

# Licencia de AAP llena (50 hosts): se retiran de AAP los hosts de equipos ya retirados en FaceOps; luego se da de alta la VM de prueba.
from app.operacion import registrar_servidores, vincular_creados
li = aap.get("config/").get("license_info") or {}
print("LICENCIA antes", {k: li.get(k) for k in ("instance_count", "current_instances", "free_instances", "automated_instances")})
vivos = {e["nombre"] for e in q("SELECT nombre FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid)}
ret = q("SELECT nombre, aap_host_id, inventario FROM equipos WHERE cliente_id=? AND estado='retirado' AND aap_host_id IS NOT NULL", cid)
borrados = []
for r in ret:
    if r["nombre"] in vivos: continue
    try:
        h = aap.get(f"hosts/{r['aap_host_id']}/")
    except Exception:
        continue
    if h.get("name") != r["nombre"]: continue
    try:
        aap._r("DELETE", f"hosts/{h['id']}/"); borrados.append(r["nombre"])
    except Exception as x: print("no se pudo borrar", r["nombre"], str(x)[:100])
print("HOSTS retirados de AAP", len(borrados), borrados)
li = aap.get("config/").get("license_info") or {}
print("LICENCIA después", {k: li.get(k) for k in ("instance_count", "current_instances", "free_instances")})
t = dict(q1("SELECT * FROM tickets WHERE numero='FO-LAB-02458'"))
if not equipo("srv-e1prueba-01"):
    alta = registrar_servidores(aap, cl, t); print("ALTA", alta[:300])
    ej("UPDATE tickets SET resultado=? WHERE id=?", (t["resultado"] or "") + "\nAlta tras liberar licencias de AAP: " + alta, t["id"])
    vincular_creados(cl, t, alta)
time.sleep(15)
for _ in range(90):
    pend = q("SELECT id, estado FROM tickets WHERE cliente_id=? AND limite='srv-e1prueba-01' AND estado IN ('en_cola','en_ejecucion','pendiente_aprobacion')", cid)
    for x in pend:
        if x["estado"] == "pendiente_aprobacion":
            try: resolver_aprobacion(u, x["id"], True, APROB)
            except Exception as e_: print("aprobar", e_)
    if not pend: break
    time.sleep(10)
for x in q("SELECT t.numero, t.estado, a.nombre_aap, t.resultado FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.limite='srv-e1prueba-01' ORDER BY t.id", cid):
    print("TK", x["numero"], x["estado"], x["nombre_aap"], "|", " | ".join((x["resultado"] or "").strip().splitlines()[-2:])[:200])
e = equipo("srv-e1prueba-01"); print("EQUIPO", e and {k: e[k] for k in ("id", "nombre", "estado", "ip", "so", "inventario")})

PY
sudo podman inspect faceops > /tmp/b08-inspect.json
python3 - <<'PX' > /tmp/b08.args
import json, shlex
d = json.load(open("/tmp/b08-inspect.json"))[0]
a = ["--rm", "--name", "fo-b08", "--network", d["HostConfig"].get("NetworkMode") or "host", "--security-opt", "label=disable"]
if d["Config"].get("User"): a += ["--user", d["Config"]["User"]]
if d["HostConfig"].get("UsernsMode"): a += ["--userns", d["HostConfig"]["UsernsMode"]]
for m in d.get("Mounts", []):
    src = m.get("Source") if m.get("Type") == "bind" else m.get("Name") or m.get("Source")
    a += ["-v", f"{src}:{m['Destination']}" + (":ro" if not m.get("RW", True) else "")]
for e_ in d["Config"].get("Env", []): a += ["-e", e_]
a += ["-e", "FACEOPS_VIGIA=0", "-e", "PYTHONPATH=/opt/faceops", "-w", "/opt/faceops", "-v", "/tmp/b08.py:/tmp/b08.py:ro", "--entrypoint", "python3", d["ImageName"] or d["Image"], "/tmp/b08.py"]
print(" ".join(shlex.quote(x) for x in a))
PX
rm -f /tmp/b08-inspect.json
sudo podman rm -f fo-b08 >/dev/null 2>&1
eval "timeout 7000 sudo podman run $(cat /tmp/b08.args)" 2>&1 | grep -v -i -E "warn|token|password|secret|autorizacion|bearer|llave_privada|BEGIN OPENSSH" | tail -150
rm -f /tmp/b08.args /tmp/b08.py
# despliegue de main si el timer no lo tomó
if ! git -C ~/summit-redhat merge-base --is-ancestor 6cac31ac $(cat ~/faceops-autodeploy/desplegado) 2>/dev/null; then
  sudo systemctl start faceops-autodeploy.timer 2>/dev/null; timeout 1500 bash ~/summit-redhat/herramientas/faceops-autodeploy.sh > /tmp/e1-dep.log 2>&1; tail -4 /tmp/e1-dep.log | cut -c1-200
fi
echo "estado: $(cut -c1-200 ~/faceops-autodeploy/estado)"
grep -E "Guardar credencial|Traceback" ~/faceops-autodeploy/configurar.log | cut -c1-160 | head -5
