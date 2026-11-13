#!/bin/bash
# b03 · corre en un contenedor aparte (misma imagen, montajes, red y variables que «faceops»). Sin secretos en la salida.

cat > /tmp/b03.py <<'PY'

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

# Crea la VM de prueba de la Etapa 1 (RHEL 9 pequeña, on-premise KVM) con «FaceOps · Habilitar servidor».
inf = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo='kvm' ORDER BY id LIMIT 1", cid)
print("infra", inf["nombre"], inf["ubicacion"])
if equipo("srv-e1prueba-01"):
    print("ya existe", equipo("srv-e1prueba-01")["estado"])
else:
    a = auto("FaceOps · Habilitar servidor")
    p = {"hab_interno": True, "hab_so": "rhel9", "hab_destino": "onpremise", "hab_infraestructura": inf["nombre"], "hab_ubicacion": inf["ubicacion"] or "",
         "hab_nombre": "e1prueba", "hab_replicas": "1", "hab_cpu": "1", "hab_ram_gb": "2", "hab_disco_gb": "20", "hab_ambiente": "desarrollo"}
    t = solicitar(u, cl, a, None, p, "Etapa 1 · VM de prueba para los planes del Núcleo (se apaga o elimina al terminar)")
    t = esperar(t, 3000)
    print("HABILITAR", resumen(t))
    for l in salida_job(t): print("  |", l[:200])
time.sleep(20)
for x in q("SELECT t.numero, t.estado, a.nombre_aap, t.resultado FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.limite LIKE '%e1prueba%' ORDER BY t.id", cid):
    print("TK", x["numero"], x["estado"], x["nombre_aap"], "|", (x["resultado"] or "").strip().splitlines()[-1:] )
# espera la vinculación automática y el inventario que lanza
for _ in range(120):
    pend = q("SELECT t.id FROM tickets t WHERE t.cliente_id=? AND t.limite LIKE '%e1prueba%' AND t.estado IN ('en_cola','en_ejecucion','pendiente_aprobacion')", cid)
    for x in pend:
        tt = dict(q1("SELECT * FROM tickets WHERE id=?", x["id"]))
        if tt["estado"] == "pendiente_aprobacion":
            try: resolver_aprobacion(u, tt["id"], True, APROB)
            except Exception as e_: print("aprobar", e_)
    if not pend: break
    time.sleep(10)
for x in q("SELECT t.numero, t.estado, a.nombre_aap, t.resultado FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.limite LIKE '%e1prueba%' ORDER BY t.id", cid):
    print("TK", x["numero"], x["estado"], x["nombre_aap"], "|", " | ".join((x["resultado"] or "").strip().splitlines()[-2:])[:200])
e = equipo("srv-e1prueba-01"); print("EQUIPO", e and {k: e[k] for k in ("id", "nombre", "estado", "ip", "so", "inventario")})

PY
sudo podman inspect faceops > /tmp/b03-inspect.json
python3 - <<'PX' > /tmp/b03.args
import json, shlex
d = json.load(open("/tmp/b03-inspect.json"))[0]
a = ["--rm", "--name", "fo-b03", "--network", d["HostConfig"].get("NetworkMode") or "host", "--security-opt", "label=disable"]
if d["Config"].get("User"): a += ["--user", d["Config"]["User"]]
if d["HostConfig"].get("UsernsMode"): a += ["--userns", d["HostConfig"]["UsernsMode"]]
for m in d.get("Mounts", []):
    src = m.get("Source") if m.get("Type") == "bind" else m.get("Name") or m.get("Source")
    a += ["-v", f"{src}:{m['Destination']}" + (":ro" if not m.get("RW", True) else "")]
for e_ in d["Config"].get("Env", []): a += ["-e", e_]
a += ["-e", "FACEOPS_VIGIA=0", "-e", "PYTHONPATH=/opt/faceops", "-w", "/opt/faceops", "-v", "/tmp/b03.py:/tmp/b03.py:ro", "--entrypoint", "python3", d["ImageName"] or d["Image"], "/tmp/b03.py"]
print(" ".join(shlex.quote(x) for x in a))
PX
rm -f /tmp/b03-inspect.json
sudo podman rm -f fo-b03 >/dev/null 2>&1
eval "timeout 7000 sudo podman run $(cat /tmp/b03.args)" 2>&1 | grep -v -i -E "warn|token|password|secret|autorizacion|bearer|llave_privada|BEGIN OPENSSH" | tail -150
rm -f /tmp/b03.args /tmp/b03.py
