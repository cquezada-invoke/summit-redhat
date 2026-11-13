#!/bin/bash
# A21 · plan «FaceOps · Vincular máquina on-premise» (C4, C3 Linux, C1 y C2 Windows)
rm -f /tmp/a21-b05.log
SHA=ba3859f6; for i in $(seq 1 120); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); [ -n "$D" ] && git -C ~/summit-redhat merge-base --is-ancestor $SHA $D 2>/dev/null && break; sleep 10; done
echo "desplegado: $(cut -c1-8 ~/faceops-autodeploy/desplegado) · $(cut -c1-140 ~/faceops-autodeploy/estado)"
# b05 · corre en un contenedor aparte (misma imagen, montajes, red y variables que «faceops»). Sin secretos en la salida.
if [ "$(sudo virsh domstate srv-windows-iis-01 2>/dev/null)" = running ]; then sudo virsh shutdown srv-windows-iis-01 >/dev/null; for i in $(seq 1 24); do [ "$(sudo virsh domstate srv-windows-iis-01)" = "shut off" ] && break; sleep 10; done; [ "$(sudo virsh domstate srv-windows-iis-01)" = "shut off" ] || sudo virsh destroy srv-windows-iis-01 >/dev/null; fi
echo "srv-windows-iis-01: $(sudo virsh domstate srv-windows-iis-01) · clave de instalación en el anfitrión: $(sudo test -f /var/lib/libvirt/images/faceops/claves/srv-windows-iis-01.txt && echo sí || echo no)"
echo "srv-e1prueba-01: $(sudo virsh domstate srv-e1prueba-01 2>&1)"
cat > /tmp/b05.py <<'PY'

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

# Plan de prueba «FaceOps · Vincular máquina on-premise» sobre VM de prueba del lab (no producción).
from app.operacion import VINCULAR_ONPREM, VINCULAR, INVENTARIAR
from app.boveda import leer
import hashlib
A = auto(VINCULAR_ONPREM); print("AUTO", A["id"], pruebas.estado(dict(A))["estado"])
casos, tks = [], []
def caso(id_, ok, ev, t=None):
    casos.append({"id": id_, "resultado": "ok" if ok else "fallo", "evidencia": ev[:300]})
    if t: tks.append(t["numero"])
    print("CASO", id_, "ok" if ok else "FALLO", "·", ev[:260], flush=True)
def lanzar(e, motivo):
    return solicitar(u, cl, A, None, {"vo_maquina": e["nombre"], "vo_usuario": "faceops"}, motivo, None,
                     {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=dict(e), prueba=True)
def hijos(t, nombre_aap, desde_id, maxs=1800):
    """Tickets que lanzó FaceOps tras t (vinculación SSH, inventario) para la misma máquina: espera a que terminen."""
    t0 = time.time(); out = []
    while time.time() - t0 < maxs:
        out = [dict(x) for x in q("SELECT t.* FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND a.nombre_aap=? AND t.limite=? AND t.id>?",
                                  cid, nombre_aap, t["limite"], desde_id)]
        for x in out:
            if x["estado"] == "pendiente_aprobacion":
                try: resolver_aprobacion(u, x["id"], True, APROB)
                except Exception: pass
        if out and all(x["estado"] in ("exitoso", "fallido", "cancelado", "rechazado", "error") for x in out): break
        time.sleep(10)
    return out
def host_vars(nombre, inv):
    i = (aap.get("inventories/", name=inv).get("results") or [{}])[0].get("id")
    h = (aap.get("hosts/", name=nombre, inventory=i).get("results") or [None])[0] if i else None
    return j(h["variables"], {}) if h and str(h.get("variables") or "").strip().startswith("{") else {}
def huella(d): return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:10] if d else ""
def cambio_en(t, tarea):
    try:
        ids = [x["job"] for x in aap.todos(f"workflow_jobs/{t['aap_job_id']}/workflow_nodes/") if x.get("job")] if t["aap_job_tipo"] == "workflow" else [t["aap_job_id"]]
        for jb in ids:
            try:
                evs = aap.todos(f"jobs/{jb}/job_events/", event="runner_on_ok", task=tarea)
            except Exception: continue
            if evs: return any(x.get("changed") for x in evs)
    except Exception: pass
    return None

# C4 · máquina inexistente (equipo de prueba solo en FaceOps)
FIX = "srv-e1-inexistente-01"
base_e = equipo("srv-e1prueba-01") or equipo("srv-windows-iis-01")
if not equipo(FIX):
    ej("INSERT INTO equipos(cliente_id, nombre, inventario, so, ip, datos, ubicacion, ambiente, estado, visto, categoria, forma) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
       cid, FIX, base_e["inventario"], "Red Hat Enterprise Linux 9", "pendiente", "{}", base_e["ubicacion"] or "", "desarrollo", "inventariado", base_e["visto"], "servidor", "virtual")
t = esperar(lanzar(equipo(FIX), "Plan de prueba C4: máquina inexistente en el anfitrión"), 900)
sal = "\n".join(salida_job(t, r"No existe|fatal|failed=", 20))
caso("C4", t["estado"] == "fallido" and "No existe la máquina" in sal + (t["resultado"] or ""), f"{t['numero']} fallido: «No existe la máquina {FIX} en el anfitrión KVM»; no se tocó nada", t)
ej("UPDATE equipos SET estado='retirado' WHERE cliente_id=? AND nombre=?", cid, FIX)

# C3 · Linux on-premise existente
e = equipo("srv-e1prueba-01")
if e:
    t0id = q1("SELECT max(id) m FROM tickets")["m"]
    t = esperar(lanzar(e, "Plan de prueba C3: Linux on-premise existente"), 1200)
    vin = hijos(t, VINCULAR, t0id); inv = hijos(t, INVENTARIAR, t0id) if vin and vin[-1]["estado"] == "exitoso" else []
    e2 = equipo("srv-e1prueba-01")
    caso("C3", t["estado"] == "exitoso" and "IP" in (t["resultado"] or "") and e2["ip"] and e2["ip"][0].isdigit() and vin and vin[-1]["estado"] == "exitoso" and inv and inv[-1]["estado"] == "exitoso",
         f"{t['numero']} exitoso · IP {e2['ip']} registrada · vinculación SSH {vin[-1]['numero'] if vin else '-'} {vin[-1]['estado'] if vin else ''} · inventario {inv[-1]['numero'] if inv else '-'} {inv[-1]['estado'] if inv else ''}", t)
    print("  resultado:", " | ".join((t["resultado"] or "").strip().splitlines()[-2:])[:240])
else:
    caso("C3", False, "falta la VM de prueba srv-e1prueba-01")

# C1 · Windows on-premise apagada y sin vínculo
w = equipo("srv-windows-iis-01")
print("WIN antes", w["estado"], w["ip"], w.get("energia"), (j(w["datos"], {}).get("vinculo") or {}).get("ok"))
t0id = q1("SELECT max(id) m FROM tickets")["m"]
t = esperar(lanzar(w, "Plan de prueba C1: Windows on-premise apagada y sin vínculo"), 3000)
print("  resultado:", " | ".join((t["resultado"] or "").strip().splitlines()[-2:])[:300])
for l in salida_job(t, r'"msg"|fatal|ok=|failed=', 8): print("  |", l[:200])
inv = hijos(t, INVENTARIAR, t0id) if t["estado"] == "exitoso" else []
w2 = equipo("srv-windows-iis-01"); hv = host_vars("srv-windows-iis-01", w2["inventario"]); bov = leer("maquinas/srv-windows-iis-01") or {}
plan = j(q1("SELECT recoleccion FROM clientes WHERE id=?", cid)["recoleccion"], {}).get(w2["inventario"], [])
caso("C1", t["estado"] == "exitoso" and "WinRM HTTPS 5986" in (t["resultado"] or "") and hv.get("ansible_port") == 5986 and bov.get("conexion") == "winrm" and bov.get("usuario") == "faceops"
     and any(x.get("limite") == "windows" for x in plan) and inv and inv[-1]["estado"] == "exitoso" and w2["estado"] == "con datos",
     f"{t['numero']} exitoso: encendida, IP {w2['ip']} en FaceOps y AAP (WinRM HTTPS 5986), credencial en la bóveda (faceops, winrm) y en AAP (plan del grupo windows); inventario {inv[-1]['numero'] if inv else '-'} {inv[-1]['estado'] if inv else ''}; SO «{w2['so']}»", t)
h1 = huella(bov); ip1 = w2["ip"]
# C2 · repetir sobre la misma Windows
t = esperar(lanzar(w2, "Plan de prueba C2: repetir sobre la misma Windows"), 2400)
w3 = equipo("srv-windows-iis-01"); bov2 = leer("maquinas/srv-windows-iis-01") or {}
ch = cambio_en(t, "WinRM HTTPS (5986) con certificado propio, firewall y cuentas locales por red")
caso("C2", t["estado"] == "exitoso" and w3["ip"] == ip1 and huella(bov2) == h1 and ch is False,
     f"{t['numero']} exitoso; misma IP {w3['ip']}; misma credencial en la bóveda; WinRM sin cambios (changed={ch})", t)
print("RESUMEN", [(x["id"], x["resultado"]) for x in casos], tks, flush=True)
ok = all(x["resultado"] == "ok" for x in casos)
pruebas.certificar(cid, VINCULAR_ONPREM, "planes-prueba.yml · " + VINCULAR_ONPREM, casos, tks, "aprobada" if ok else "rechazada", "Claude (regla de Cristian)",
                   "VM de prueba srv-e1prueba-01 y srv-windows-iis-01; equipo inexistente de prueba retirado" if ok else "casos fallidos")
print("ESTADO", pruebas.estado(dict(A))["estado"])

PY
sudo podman inspect faceops > /tmp/b05-inspect.json
python3 - <<'PX' > /tmp/b05.args
import json, shlex
d = json.load(open("/tmp/b05-inspect.json"))[0]
a = ["--rm", "--name", "fo-a21v", "--network", d["HostConfig"].get("NetworkMode") or "host", "--security-opt", "label=disable"]
if d["Config"].get("User"): a += ["--user", d["Config"]["User"]]
if d["HostConfig"].get("UsernsMode"): a += ["--userns", d["HostConfig"]["UsernsMode"]]
for m in d.get("Mounts", []):
    src = m.get("Source") if m.get("Type") == "bind" else m.get("Name") or m.get("Source")
    a += ["-v", f"{src}:{m['Destination']}" + (":ro" if not m.get("RW", True) else "")]
for e_ in d["Config"].get("Env", []): a += ["-e", e_]
a += ["-e", "FACEOPS_VIGIA=0", "-e", "PYTHONPATH=/opt/faceops", "-w", "/opt/faceops", "-v", "/tmp/b05.py:/tmp/b05.py:ro", "--entrypoint", "python3", d["ImageName"] or d["Image"], "/tmp/b05.py"]
print(" ".join(shlex.quote(x) for x in a))
PX
rm -f /tmp/b05-inspect.json
sudo podman rm -f fo-a21v >/dev/null 2>&1
eval "timeout 7000 sudo podman run $(cat /tmp/b05.args)" 2>&1 | grep --line-buffered -v -i -E "warning|x-vault-token|authorization:|password=|bearer |BEGIN OPENSSH" | tee /tmp/a21-b05.log | tail -150
echo FIN-B05 >> /tmp/a21-b05.log
rm -f /tmp/b05.args /tmp/b05.py
