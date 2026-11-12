#!/bin/bash
# Plan de prueba «Seguridad · Aplicar parches de seguridad» en nodo-so: política «no reiniciar», límite 15 min.
# Tickets reales por AAP (solicitar_en_equipo prueba=True), aprobados con el comentario de la regla. Sin secretos.
for i in $(seq 1 20); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done
cat > /tmp/p3004.py <<'PY'
import json, re, time
from app import create_app
from app.bd import q, q1, j
app = create_app()
from app import pruebas
from app.aap import AAP
from app.cves import ParcheInvalido, parametros_parche, pendientes_parche, PARCHAR
from app.operacion import resolver_aprobacion, solicitar_en_equipo
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); cid = cl["id"]
u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 LIMIT 1")
a = dict(q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cid, PARCHAR))
e = dict(q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='nodo-so'", cid))
aap = AAP.de_cliente(cl); aap.en_vivo = True
print("ESTADO_ANTES", pruebas.estado(dict(a, cliente_id=cid))["estado"], flush=True)
rev, pend = pendientes_parche(cid, e)
print("PENDIENTES", rev, [x["cve"] for x in pend], flush=True)

def correr(params, motivo):
    t = solicitar_en_equipo(u, cl, a, e, params, motivo, prueba=True)
    tid = t["id"]
    for _ in range(150):
        t = q1("SELECT * FROM tickets WHERE id=?", tid)
        if t["estado"] == "pendiente_aprobacion":
            try: resolver_aprobacion(u, tid, True, "Aprobado por Claude (regla de Cristian)")
            except Exception as x: print("  aprobar:", type(x).__name__, str(x)[:100])
        if t["estado"] in ("exitoso", "fallido", "cancelado", "rechazado", "error"): break
        time.sleep(6)
    stats, msgs = {}, []
    try:
        nodos = aap.get(f"workflow_jobs/{t['aap_job_id']}/workflow_nodes/")["results"] if t["aap_job_tipo"] == "workflow" else [{"job": t["aap_job_id"]}]
        for n in nodos:
            jid = n.get("job") or (n.get("summary_fields") or {}).get("job", {}).get("id")
            if not jid: continue
            jb = aap.get(f"jobs/{jid}/")
            if "Aplicar parches" not in (jb.get("name") or ""): continue
            stats = (jb.get("artifacts") or {}).get("parches_seguridad") or {}
            txt = aap.s.get(aap.base + f"jobs/{jid}/stdout/", params={"format": "txt"}, timeout=30).text
            msgs = [l.strip() for l in txt.splitlines() if re.search(r'"msg"|fatal:|FAILED|Datos válidos|Todo aplicado|nodo-so:|changed=|failed=', l)][-8:]
    except Exception as x:
        msgs = ["salida no disponible: " + type(x).__name__]
    print(f"TICKET {t['numero']} {t['estado']} params={t['parametros']}", flush=True)
    print("  stats=", json.dumps(stats, ensure_ascii=False)[:400])
    for m in msgs: print("  |", m[:230])
    print("  resultado:", " | ".join((t["resultado"] or "").strip().splitlines()[-3:])[:300], flush=True)
    return t, stats

casos, tks = [], []
def caso(id_, ok, ev, t=None):
    casos.append({"id": id_, "resultado": "ok" if ok else "fallo", "evidencia": ev[:300]})
    if t: tks.append(t["numero"])
    print("CASO", id_, "ok" if ok else "FALLO", "·", ev[:200], flush=True)

# C7 · FaceOps no crea tickets sin parámetros ni sin pendientes (vía real, sin prueba=True)
n0 = q1("SELECT max(id) m FROM tickets")["m"]
c = app.test_client()
with c.session_transaction() as s: s["uid"] = u["id"]; s["csrf"] = "plan"
r1 = c.post(f"/lab/inventario/{e['id']}/cve/parche", data={"_csrf": "plan"})
r2 = c.post(f"/lab/inventario/{e['id']}/habilidad", data={"_csrf": "plan", "hid": "parches"})
jb = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre='nodo-jboss'", cid)["id"]
r3 = c.post(f"/lab/inventario/{jb}/cve/parche", data={"_csrf": "plan", "pp_alcance": "todos", "pp_reinicio": "no", "pp_limite_min": "15"})
try:
    solicitar_en_equipo(u, cl, a, e, {}, "plan C7"); r4 = "creó"
except Exception as x: r4 = type(x).__name__ + ": " + str(x)[:90]
n1 = q1("SELECT max(id) m FROM tickets")["m"]
caso("C7", r1.status_code == 400 and r2.status_code == 400 and r3.status_code == 400 and r4 != "creó" and n1 == n0,
     f"sin parámetros 400 «{(r1.get_json() or {}).get('error')}»; skill directa 400; nodo-jboss sin pendientes 400; Acciones/solicitar sin parámetros: {r4}; tickets nuevos {n1 - n0}")

# C5 · sin parámetros por AAP: falla en «Datos válidos» sin tocar la máquina
t, st = correr({}, "Plan de prueba C5: sin parámetros")
salida = q1("SELECT resultado FROM tickets WHERE id=?", t["id"])["resultado"] or ""
caso("C5", t["estado"] == "fallido" and "Datos válidos" in salida, "fallido en «Datos válidos»: " + salida.strip().splitlines()[-1][:160] if salida.strip() else "sin resultado", t)

# C6 · límite fuera de rango: falla en «Datos válidos»
t, st = correr({"pp_alcance": "todos", "pp_reinicio": "no", "pp_limite_min": 5, "pp_lote": 20}, "Plan de prueba C6: límite fuera de rango")
salida = q1("SELECT resultado FROM tickets WHERE id=?", t["id"])["resultado"] or ""
caso("C6", t["estado"] == "fallido" and "Datos válidos" in salida, "límite 5 min: fallido en «Datos válidos», no se toca la máquina", t)

# C1 · selección del CVE pendiente, no reiniciar, límite 15, lote 1 (parámetros validados por FaceOps)
if pend:
    p1 = dict(parametros_parche(cid, [e], {"pp_alcance": "seleccion", "cves": pend[0]["cve"], "pp_reinicio": "no", "pp_limite_min": "15"}), pp_lote=1)
    t, st = correr(p1, f"Plan de prueba C1: {pend[0]['cve']} sin reinicio")
    s_ = st.get("nodo-so") or {}
    caso("C1", t["estado"] == "exitoso" and s_.get("lotes_aplicados") == s_.get("lotes") and (s_.get("lotes") or 0) >= 1 and s_.get("lotes_sin_tiempo") == 0 and s_.get("reinicio") == "no",
         f"{pend[0]['cve']}: {s_.get('avisos')} aviso(s), {s_.get('lotes_aplicados')}/{s_.get('lotes')} lotes, reinicio requerido {s_.get('reinicio_requerido')}, no se reinició; {s_.get('minutos')} min", t)
    req = bool(s_.get("reinicio_requerido"))
else:
    caso("C1", False, "nodo-so ya no tiene CVE pendientes"); req = True

# C2 · repetir «todos» (forzado por AAP): nada que aplicar
t, st = correr({"pp_alcance": "todos", "pp_reinicio": "no", "pp_limite_min": 15, "pp_lote": 2}, "Plan de prueba C2: repetir todos")
s_ = st.get("nodo-so") or {}
caso("C2", t["estado"] == "exitoso" and s_.get("avisos") == 0 and s_.get("lotes") == 0, f"repetición: {s_.get('avisos')} avisos, {s_.get('lotes')} lotes, exitoso sin cambios", t)

# C3 / C4 · «reiniciar si hace falta» y «programar»: solo si el reinicio NO es requerido (consigna: no reiniciar nodo-so)
if not req:
    t, st = correr({"pp_alcance": "todos", "pp_reinicio": "si_hace_falta", "pp_limite_min": 15, "pp_lote": 20}, "Plan de prueba C3: reiniciar solo si hace falta")
    s_ = st.get("nodo-so") or {}
    caso("C3", t["estado"] == "exitoso" and s_.get("reinicio") == "no" and s_.get("reinicio_requerido") is False, "sin reinicio requerido: no reinicia (política «si hace falta» respetada)", t)
    t, st = correr({"pp_alcance": "todos", "pp_reinicio": "programar", "pp_reinicio_hora": "03:30", "pp_limite_min": 15, "pp_lote": 20}, "Plan de prueba C4: programar reinicio")
    s_ = st.get("nodo-so") or {}
    caso("C4", t["estado"] == "exitoso" and s_.get("reinicio") == "no", "sin reinicio requerido: no programa nada (política «programar» respetada)", t)
else:
    caso("C3", False, "reinicio requerido tras C1: no se prueba para no reiniciar nodo-so")
    caso("C4", False, "reinicio requerido tras C1: no se prueba para no programar reinicio en nodo-so")

print("RESUMEN", [(x["id"], x["resultado"]) for x in casos], tks, flush=True)
if all(x["resultado"] == "ok" for x in casos):
    pruebas.certificar(cid, PARCHAR, "planes-prueba.yml · " + PARCHAR, casos, tks, "aprobada", "Claude (regla de Cristian)",
                       "nodo-so, política no reiniciar, límite 15 min; el reinicio real y el agotamiento del límite no se ejercitaron en vivo")
else:
    pruebas.certificar(cid, PARCHAR, "planes-prueba.yml · " + PARCHAR, casos, tks, "rechazada", "Claude (regla de Cristian)", "casos fallidos o no ejecutables")
print("ESTADO_DESPUES", pruebas.estado(dict(a, cliente_id=cid))["estado"], flush=True)
PY
sudo podman cp /tmp/p3004.py faceops:/tmp/p3004.py
timeout 5400 sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3004.py 2>&1 | grep -v -i -E "warn|token|password|secret|autorizacion|bearer" | tail -70
sudo podman exec faceops rm -f /tmp/p3004.py
