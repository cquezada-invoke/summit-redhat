#!/bin/bash
# b04 · corre en un contenedor aparte (misma imagen, montajes, red y variables que «faceops»). Sin secretos en la salida.

cat > /tmp/b04.py <<'PY'

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

# Plan de prueba «FaceOps · Guardar credencial en la bóveda» (alta manual por AAP). Equipos de prueba en 192.0.2.0/24.
import secrets as _s
from app.operacion import sincronizar_catalogo
from app.alta_manual import agregar, CREDENCIAL
from app.boveda import leer
for _ in range(90):
    import os
    if os.path.exists("/opt/faceops/app/etapas.py"): break
    time.sleep(10)
n_ = sincronizar_catalogo(cl, "qa.nucleo"); print("catálogo sincronizado", n_)
a = auto(CREDENCIAL); print("AUTO", a and (a["id"], a["requiere_aprobacion"], a["visible"], pruebas.estado(dict(a))["estado"], j(a["tecnico"], {}).get("credenciales")))
casos, tks = [], []
def caso(id_, ok, ev, t=None):
    casos.append({"id": id_, "resultado": "ok" if ok else "fallo", "evidencia": ev[:300]})
    if t: tks.append(t["numero"])
    print("CASO", id_, "ok" if ok else "FALLO", "·", ev[:240], flush=True)
def host(nombre):
    r = aap.get("hosts/", name=nombre).get("results") or []
    return r
def limpio(t, sec):
    t2 = dict(q1("SELECT * FROM tickets WHERE id=?", t["id"]))
    aud = " ".join((x["detalle"] or "") + (x["objeto"] or "") for x in q("SELECT * FROM auditoria WHERE cliente_id=? AND objeto IN (?,?) ", cid, t["numero"], t["limite"]))
    sal = "\n".join(salida_job(t, r".", 4000))
    return sec not in (t2["parametros"] or "") and sec not in (t2["resultado"] or "") and sec not in aud and sec not in sal
c = app.test_client()
lec = dict(q1("SELECT * FROM usuarios WHERE usuario='qa.lector'"))
def post(uid, cuerpo):
    with c.session_transaction() as s: s["uid"] = uid; s["csrf"] = "plan"; s["cliente_id"] = cid
    r = c.post("/lab/equipos/agregar/inventario", json=dict(cuerpo, _csrf="plan"))
    return r.status_code, (r.get_json(silent=True) or {})
for n in ("srv-e1alta-01", "rtr-e1alta-01", "srv-e1alta-02"):   # restos de una corrida anterior
    for h in host(n):
        try: aap._r("DELETE", f"hosts/{h['id']}/")
        except Exception: pass
    ej("UPDATE equipos SET estado='retirado' WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cid, n)
# C4 · validaciones y permisos (sin crear nada)
n0 = (q1("SELECT max(id) m FROM tickets")["m"], q1("SELECT count(*) n FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid)["n"], len(host("srv_mal!")) + len(host("srv-e1alta-01")))
base = {"marca": "linux-ssh", "tipo": "servidor", "ip": "192.0.2.11", "auth": "clave", "usuario": "prueba", "ambiente": "desarrollo", "forma": "virtual"}
r1 = post(u["id"], dict(base, nombre="srv_mal!", secreto="x" * 12))
r2 = post(u["id"], dict(base, nombre="srv-e1alta-01", secreto=""))
r3 = post(lec["id"], dict(base, nombre="srv-e1alta-01", secreto="y" * 12))
n1 = (q1("SELECT max(id) m FROM tickets")["m"], q1("SELECT count(*) n FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid)["n"], len(host("srv_mal!")) + len(host("srv-e1alta-01")))
caso("C4", r1[0] == 400 and r2[0] == 400 and r3[0] in (400, 403) and n1 == n0,
     f"nombre inválido {r1[0]} «{r1[1].get('error','')[:60]}»; sin secreto {r2[0]} «{r2[1].get('error','')[:40]}»; lector {r3[0]}; sin tickets, equipos ni hosts nuevos")
# C1 · servidor Linux con clave
sec1 = _s.token_urlsafe(18)
eid, t = agregar(u, cl, "inventario", dict(base, nombre="srv-e1alta-01", secreto=sec1), prueba=True)
t = esperar(t, 900)
e = dict(q1("SELECT * FROM equipos WHERE id=?", eid)); hs = host("srv-e1alta-01")
hv = j(hs[0]["variables"], {}) if hs else {}
bov = leer("maquinas/srv-e1alta-01") or {}
caso("C1", t["estado"] == "exitoso" and len(hs) == 1 and sec1 not in json.dumps(hv) and hs[0]["summary_fields"]["inventory"]["name"] == "FaceOps · Servidores (alta manual)"
     and e["estado"] == "inventariado" and bov.get("clave") == sec1 and bov.get("metodo") == "clave" and bov.get("usuario") == "prueba" and limpio(t, sec1)
     and (j(e["datos"], {}).get("vinculo") or {}).get("ok"),
     f"{t['numero']} exitoso con aprobación en AAP; host en «FaceOps · Servidores (alta manual)» sin secretos ({', '.join(sorted(hv))[:80]}); equipo inventariado y vinculado; bóveda maquinas/srv-e1alta-01 con usuario, método y clave (leída por FaceOps); el secreto no aparece en ticket, auditoría ni salida del trabajo", t)
for l in salida_job(t, r'"msg"|ok=|failed=', 4): print("  |", l[:200])
# C2 · vincular de nuevo con otra clave
sec2 = _s.token_urlsafe(18)
eid2, t = agregar(u, cl, "inventario", dict(base, nombre="srv-e1alta-01", secreto=sec2, vincular="1"), prueba=True)
t = esperar(t, 900)
bov2 = leer("maquinas/srv-e1alta-01") or {}
neq = q1("SELECT count(*) n FROM equipos WHERE cliente_id=? AND nombre='srv-e1alta-01' AND estado!='retirado'", cid)["n"]
caso("C2", t["estado"] == "exitoso" and eid2 == eid and bov2.get("clave") == sec2 and len(host("srv-e1alta-01")) == 1 and neq == 1 and limpio(t, sec2),
     f"{t['numero']} exitoso; credencial actualizada (otra clave) en la bóveda; 1 host en AAP y 1 equipo en FaceOps", t)
# C5 · por AAP sin secreto
e1 = equipo("srv-e1alta-01")
t = solicitar(u, cl, a, None, {"cb_maquina": "srv-e1alta-01", "cb_metodo": "clave", "cb_usuario": "prueba", "cb_protocolo": "ssh", "cb_puerto": 22},
              "Plan de prueba C5: sin secreto", None, {"ambientes": [e1["ambiente"]]} if e1.get("ambiente") else {}, equipo=e1, prueba=True)
t = esperar(t, 900)
bov5 = leer("maquinas/srv-e1alta-01") or {}
caso("C5", t["estado"] == "fallido" and "Datos válidos" in (t["resultado"] or "") + "\n".join(salida_job(t, r"TASK \[|fatal", 40)) and bov5.get("clave") == sec2,
     f"{t['numero']} fallido en «Datos válidos»; la bóveda no cambió", t)
# C6 · rechazo
sec6 = _s.token_urlsafe(18)
t = solicitar(u, cl, a, None, {"cb_maquina": "srv-e1alta-01", "cb_metodo": "clave", "cb_usuario": "prueba", "cb_protocolo": "ssh", "cb_puerto": 22, "cb_secreto": "••••••"},
              "Plan de prueba C6: rechazar", {"cb_secreto": sec6}, {"ambientes": [e1["ambiente"]]} if e1.get("ambiente") else {}, equipo=e1, prueba=True)
for _ in range(100):
    t = dict(q1("SELECT * FROM tickets WHERE id=?", t["id"]))
    if t["estado"] == "pendiente_aprobacion":
        resolver_aprobacion(u, t["id"], False, "Rechazado por Claude (plan de prueba C6, regla de Cristian)"); break
    time.sleep(5)
t = esperar(t, 600, aprobar=False)
bov6 = leer("maquinas/srv-e1alta-01") or {}
caso("C6", t["estado"] in ("rechazado", "cancelado", "fallido") and bov6.get("clave") == sec2, f"{t['numero']} {t['estado']} tras rechazar la aprobación; la bóveda no cambió", t)
def retirar(n):
    for h in host(n):
        try: aap._r("DELETE", f"hosts/{h['id']}/")
        except Exception as x: print("borrar host", n, type(x).__name__)
    ej("UPDATE equipos SET estado='retirado' WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cid, n)
retirar("srv-e1alta-01")   # licencia de AAP al límite: un equipo de prueba a la vez
# C3 · equipo de red con llave SSH
llave = "-----BEGIN OPENSSH PRIVATE KEY-----\n" + _s.token_urlsafe(120) + "\n-----END OPENSSH PRIVATE KEY-----\n"
def post_red(cuerpo):
    return agregar(u, cl, "redes", cuerpo, prueba=True)
eid3, t = post_red({"marca": "cisco-ios", "tipo": "router", "nombre": "rtr-e1alta-01", "ip": "192.0.2.21", "auth": "llave", "usuario": "netops", "secreto": llave, "ambiente": "desarrollo"})
t = esperar(t, 900)
hs3 = host("rtr-e1alta-01"); hv3 = j(hs3[0]["variables"], {}) if hs3 else {}
bov3 = leer("maquinas/rtr-e1alta-01") or {}
caso("C3", t["estado"] == "exitoso" and hv3.get("ansible_network_os") == "cisco.ios.ios" and bov3.get("llave_privada", "").strip() == llave.strip() and bov3.get("metodo") == "llave" and limpio(t, llave.strip().splitlines()[1]),
     f"{t['numero']} exitoso; host con ansible_network_os {hv3.get('ansible_network_os')} y {hv3.get('ansible_connection')}; llave privada en la bóveda; el trabajo corre en el entorno de ejecución (no entra al equipo)", t)
retirar("rtr-e1alta-01")
print("RESUMEN", [(x["id"], x["resultado"]) for x in casos], tks, flush=True)
ok = all(x["resultado"] == "ok" for x in casos)
pruebas.certificar(cid, CREDENCIAL, "planes-prueba.yml · " + CREDENCIAL, casos, tks, "aprobada" if ok else "rechazada", "Claude (regla de Cristian)",
                   "equipos de prueba en 192.0.2.0/24 (no se entra al equipo); retirados al terminar" if ok else "casos fallidos")
print("ESTADO", pruebas.estado(dict(a))["estado"])
# Punta a punta por la pantalla ya certificada: Servidores › Agregar (POST de la consola) con un equipo más
if ok:
    sec7 = _s.token_urlsafe(18)
    r7 = post(u["id"], dict(base, nombre="srv-e1alta-02", ip="192.0.2.12", secreto=sec7))
    print("PANTALLA", r7[0], {k: v for k, v in r7[1].items() if k != "url"})
    if r7[0] == 200:
        t7 = dict(q1("SELECT * FROM tickets WHERE numero=?", r7[1]["ticket"])); t7 = esperar(t7, 900)
        print("PANTALLA-TICKET", t7["numero"], t7["estado"], (leer("maquinas/srv-e1alta-02") or {}).get("clave") == sec7, limpio(t7, sec7))
# limpieza: equipos de prueba retirados y hosts fuera de AAP (la bóveda queda solo con credenciales de prueba sin uso)
for n in ("srv-e1alta-01", "rtr-e1alta-01", "srv-e1alta-02"):
    for h in host(n):
        try: aap._r("DELETE", f"hosts/{h['id']}/")
        except Exception as x: print("borrar host", n, type(x).__name__)
    ej("UPDATE equipos SET estado='retirado' WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cid, n)
print("LIMPIEZA ok")

PY
sudo podman inspect faceops > /tmp/b04-inspect.json
python3 - <<'PX' > /tmp/b04.args
import json, shlex
d = json.load(open("/tmp/b04-inspect.json"))[0]
a = ["--rm", "--name", "fo-b04", "--network", d["HostConfig"].get("NetworkMode") or "host", "--security-opt", "label=disable"]
if d["Config"].get("User"): a += ["--user", d["Config"]["User"]]
if d["HostConfig"].get("UsernsMode"): a += ["--userns", d["HostConfig"]["UsernsMode"]]
for m in d.get("Mounts", []):
    src = m.get("Source") if m.get("Type") == "bind" else m.get("Name") or m.get("Source")
    a += ["-v", f"{src}:{m['Destination']}" + (":ro" if not m.get("RW", True) else "")]
for e_ in d["Config"].get("Env", []): a += ["-e", e_]
a += ["-e", "FACEOPS_VIGIA=0", "-e", "PYTHONPATH=/opt/faceops", "-w", "/opt/faceops", "-v", "/tmp/b04.py:/tmp/b04.py:ro", "--entrypoint", "python3", d["ImageName"] or d["Image"], "/tmp/b04.py"]
print(" ".join(shlex.quote(x) for x in a))
PX
rm -f /tmp/b04-inspect.json
sudo podman rm -f fo-b04 >/dev/null 2>&1
eval "timeout 7000 sudo podman run $(cat /tmp/b04.args)" 2>&1 | grep -v -i -E "warning|x-vault-token|authorization:|password=|bearer |BEGIN OPENSSH" | tail -150
rm -f /tmp/b04.args /tmp/b04.py
