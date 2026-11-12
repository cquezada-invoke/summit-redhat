cd ~/summit-redhat
# Espera el despliegue con la ficha de nube (máquinas y productos) y sus plantillas en AAP
for i in $(seq 1 60); do
  if sudo podman exec faceops test -f /opt/faceops/app/nube_recursos.py 2>/dev/null && sudo podman exec faceops grep -q "nube_recursos" /opt/faceops/app/__init__.py 2>/dev/null; then break; fi; sleep 20; done
sudo podman exec faceops test -f /opt/faceops/app/nube_recursos.py && echo "desplegado" || { echo "SIN DESPLIEGUE"; exit 1; }
cat > /tmp/m.py <<'PY'
import time, re, json
from app import create_app
from app.bd import q, q1, j, ej
a = create_app()
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 LIMIT 1")
from app.operacion import sincronizar_catalogo, solicitar, resolver_aprobacion
from app.aap import AAP
from app import nube_recursos as N
for _i in range(60):
    try:
        sincronizar_catalogo(cl)
    except Exception as _x:
        print("AAP aún no responde:", str(_x)[:80]) if _i % 10 == 0 else None; time.sleep(20); continue
    if all(q1("SELECT 1 x FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n_) for n_ in ("Nube · Leer opciones de la nube", "Nube · Crear máquina", "Nube · Operar máquina", "Nube · Producto de la nube")): break
    time.sleep(20)
print("plantillas en AAP:", [n_ for n_ in ("Nube · Leer opciones de la nube", "Nube · Crear máquina", "Nube · Operar máquina", "Nube · Producto de la nube") if q1("SELECT 1 x FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n_)])
aap = AAP.de_cliente(cl)
INF = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo='gcp' AND detalle LIKE '%invoke-summit-cloud%' ORDER BY id LIMIT 1", cl["id"])
IID = INF["id"]
for _i in range(60):   # la réplica de AAP en FaceOps debe tener la credencial de la nube antes de lanzar
    try:
        aap.get(f"credentials/{N.credencial(aap, INF)}/"); print("credencial de la nube disponible"); break
    except Exception as _x:
        print("esperando AAP:", str(_x)[:90]) if _i % 10 == 0 else None; time.sleep(20)
APROB = "Aprobado por Claude (regla de Cristian)"
def auto(n): return q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n)
def ctx():
    from flask import g
    g.usuario, g.cliente, g.perfil = u, cl, {"modo": "escritura", "segmentos": ["*"]}
def ticket(nombre, params, motivo, aprobar=True, espera=900):
    t = solicitar(u, cl, auto(nombre), "", params, motivo, prueba=True)
    fin = time.time() + espera
    while time.time() < fin:
        r = q1("SELECT numero, estado, resultado FROM tickets WHERE id=?", t["id"])
        if r["estado"] == "pendiente_aprobacion":
            try: resolver_aprobacion(u, t["id"], aprobar, APROB if aprobar else "Rechazado por Claude (prueba: camino de rechazo)")
            except Exception as x: print("  aprobación:", str(x)[:120])
        if r["estado"] in ("exitoso", "fallido", "cancelado", "rechazado"):
            break
        time.sleep(6)
    r = q1("SELECT id, numero, estado, resultado FROM tickets WHERE id=?", t["id"])
    print(f"  {r['numero']} {r['estado']} · {(r['resultado'] or '')[-220:]}".replace("\n", " | "))
    return r
def leer(regiones=None):
    for _k in range(30):
        job, mot = N.lanzar_opciones(cl, dict(INF), regiones, prueba=True)
        if job or "en curso" not in (mot or ""): break
        N.recoger_opciones(cl, IID); time.sleep(6)
    if not job:
        print("  lectura no lanzada:", mot); return None, None
    for _ in range(100):
        N.recoger_opciones(cl, IID)
        tr = q1("SELECT * FROM nube_opciones_trabajos WHERE job=?", job)
        if tr["estado"] != "en curso": break
        time.sleep(6)
    return job, q1("SELECT estado FROM nube_opciones_trabajos WHERE job=?", job)["estado"]
SECRETO = re.compile(r"private_key|BEGIN PRIVATE|client_secret|\"token\"|ya29\.", re.I)
def salida_t(tid, n=400):
    t = q1("SELECT aap_job_id, aap_job_tipo FROM tickets WHERE id=?", tid)
    if not t or not t["aap_job_id"]: return ""
    if t["aap_job_tipo"] == "job": return aap.salida(t["aap_job_id"], n)
    try:
        return "\n".join(aap.salida(nd["id"], n) for nd in aap.nodos_trabajo_workflow(t["aap_job_id"]) if nd.get("type") == "job" and nd.get("id"))
    except Exception: return ""
from app.infraestructuras import lista
from app.vistas import equipos_visibles
from app.operacion import VINCULAR, INVENTARIAR
RES = {}
def X():
    return next(i for i in lista(cl["id"], equipos_visibles()) if i["id"] == IID)
def op():
    return N.opciones(cl["id"], IID) or {}
def vm_en_nube(n):
    leer(["southamerica-west1"])
    return next((m for m in op().get("maquinas") or [] if m["nombre"] == n), None)
def operar(n, accion, form, caso, espera_ok=True):
    try:
        pr = N.validar_operar(X(), op(), n, accion, form)
    except ValueError as e:
        print(f"{caso} formulario rechaza: {e}"); return None
    r = ticket(N.OPERAR, pr, f"Prueba {caso}: {accion} {n}")
    RES.setdefault(caso, []).append(r["numero"])
    return r
def esperar_vinculo(n, minutos=12):
    fin = time.time() + minutos * 60
    while time.time() < fin:
        ts = q("SELECT t.numero, t.estado, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.limite=? AND a.nombre_aap IN (?,?) ORDER BY t.id", cl["id"], n, VINCULAR, INVENTARIAR)
        hechos = {t["nombre_aap"]: t for t in ts if t["estado"] in ("exitoso", "fallido", "rechazado", "cancelado")}
        if INVENTARIAR in hechos or (VINCULAR in hechos and hechos[VINCULAR]["estado"] != "exitoso"):
            break
        time.sleep(15)
    ts = q("SELECT t.numero, t.estado, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.limite=? AND a.nombre_aap IN (?,?) ORDER BY t.id", cl["id"], n, VINCULAR, INVENTARIAR)
    print("  vínculo e inventario:", [(t["nombre_aap"].split(" · ")[-1], t["numero"], t["estado"]) for t in ts])
    return ts
def crear(n):
    f = {"nombre": n, "region": "southamerica-west1", "zona": "southamerica-west1-a", "tipo": "e2-micro", "imagen": "rocky-9", "disco_gb": "20",
         "disco_tipo": "pd-balanced", "red": "default", "subred": "default", "publica": "1", "etiquetas": "prueba=faceops\nequipo=plataforma", "etiquetas_red": "fo-prueba", "ambiente": "desarrollo"}
    pr = N.validar_crear(X(), op(), f)
    return pr, ticket(N.CREAR, pr, f"Prueba: crear {n} (e2-micro, se elimina al terminar)", espera=1200)
def limpiar(n):
    """Rescate: pase o falle, la VM de prueba se elimina por ticket y se verifica que no quede nada."""
    m = vm_en_nube(n)
    if m:
        pr = N.validar_operar(X(), op(), n, "eliminar", {"confirmar": n, "entiendo": "1"})
        r = ticket(N.OPERAR, pr, f"Prueba (limpieza): eliminar {n}")
        RES.setdefault("limpieza", []).append(r["numero"])
    m = vm_en_nube(n)
    o = op()
    quedan = {"vm": bool(m), "snapshots": [s["nombre"] for s in o.get("snapshots") or [] if s.get("maquina") == n or s.get("disco", "").startswith(n)],
              "firewall": [f["nombre"] for f in o.get("firewall") or [] if f["nombre"].startswith(f"fo-{n}-")]}
    print("VERIFICACIÓN FINAL", n, "· queda:", quedan)
    return not any(quedan.values())
with a.test_request_context("/", method="POST"):
    ctx()
    VM = "fo-prueba-a-" + time.strftime("%H%M")
    ok, o = {}, {}
    leer(["southamerica-west1"])
    try:
        pr, r = crear(VM)
        RES["C1"] = [r["numero"]]
        vl = esperar_vinculo(VM) if r["estado"] == "exitoso" else []
        m = vm_en_nube(VM)
        eq = q1("SELECT nombre, energia, ip, estado FROM equipos WHERE cliente_id=? AND nombre=?", cl["id"], VM)
        print("C1 VM:", m and {k: m[k] for k in ("estado", "tipo", "etiquetas", "etiquetas_red")}, "| equipo FaceOps:", eq and dict(eq))
        ok["C1"] = r["estado"] == "exitoso" and m and m["estado"] == "RUNNING" and m["etiquetas"].get("prueba") == "faceops" and eq and \
            {t["nombre_aap"]: t["estado"] for t in vl}.get(INVENTARIAR) == "exitoso"
        r2 = ticket(N.CREAR, pr, f"Prueba C2: repetir crear {VM}")
        try:
            N.validar_crear(X(), op(), dict(nombre=VM, region="southamerica-west1", zona="southamerica-west1-a", tipo="e2-micro", imagen="rocky-9", disco_gb="20", disco_tipo="pd-balanced", red="default"))
            rep = False
        except ValueError as e:
            rep = "Ya existe" in str(e); print("C2 formulario:", e)
        ok["C2"] = r2["estado"] == "exitoso" and rep and (vm_en_nube(VM) or {}).get("estado") == "RUNNING"
        RES["C2"] = [r2["numero"]]
        r3 = ticket(N.CREAR, dict(pr, nm_nombre=VM + "-x", nm_tipo="m1-ultramem-40"), "Prueba C3: tipo fuera de las familias")
        ok["C3"] = r3["estado"] == "fallido" and "Datos válidos" in (r3["resultado"] or "") + salida_t(r3["id"], 400)
        r4 = ticket(N.CREAR, dict(pr, nm_nombre=VM + "-r"), "Prueba C4: rechazar la aprobación", aprobar=False)
        ok["C4"] = r4["estado"] == "rechazado" and not vm_en_nube(VM + "-r")
        RES["C3"], RES["C4"] = [r3["numero"]], [r4["numero"]]
        mal = []
        for f, esperado in (({"nombre": "Mal_Nombre"}, "Nombre"), ({"nombre": "fo-z", "region": "southamerica-west1", "zona": "us-central1-a", "tipo": "e2-micro"}, "zona"),
                            ({"nombre": "fo-z", "region": "southamerica-west1", "zona": "southamerica-west1-a", "tipo": "e2-micro", "imagen": "rocky-9", "disco_gb": "5", "disco_tipo": "pd-balanced", "red": "default"}, "Disco"),
                            ({"nombre": "fo-z", "region": "southamerica-west1", "zona": "southamerica-west1-a", "tipo": "e2-micro", "imagen": "windows-2022", "disco_gb": "60", "disco_tipo": "pd-balanced", "red": "default"}, "sin certificar")):
            try:
                N.validar_crear(X(), op(), f); mal.append(esperado)
            except ValueError as e:
                if esperado.lower() not in str(e).lower(): mal.append(f"{esperado}: {e}")
        ok["C5"] = not mal
        print("C5 validaciones:", "ok" if not mal else mal)
        # ---- Operar C1–C6
        o = {}
        r = operar(VM, "apagar", {}, "O-C1"); o["C1"] = r and r["estado"] == "exitoso" and (vm_en_nube(VM) or {}).get("estado") == "TERMINATED" and \
            (q1("SELECT energia FROM equipos WHERE cliente_id=? AND nombre=?", cl["id"], VM) or {}).get("energia") == "apagado"
        r = operar(VM, "apagar", {}, "O-C2"); o["C2"] = r and r["estado"] == "exitoso" and "sin cambios" in (r["resultado"] or "") + salida_t(r["id"], 300)
        r = operar(VM, "redimensionar", {"tipo": "e2-small"}, "O-C3"); m = vm_en_nube(VM) or {}; o["C3"] = r and r["estado"] == "exitoso" and m.get("tipo") == "e2-small" and m.get("estado") == "TERMINATED"
        r = operar(VM, "encender", {}, "O-C4"); o["C4"] = r and r["estado"] == "exitoso" and (vm_en_nube(VM) or {}).get("estado") == "RUNNING"
        sin = operar(VM, "redimensionar", {"tipo": "e2-micro"}, "O-C5a")
        r = operar(VM, "redimensionar", {"tipo": "e2-micro", "detener": "1"}, "O-C5"); m = vm_en_nube(VM) or {}; o["C5"] = sin is None and r and r["estado"] == "exitoso" and m.get("tipo") == "e2-micro" and m.get("estado") == "RUNNING"
        r = operar(VM, "reiniciar", {}, "O-C6"); o["C6"] = r and r["estado"] == "exitoso" and (vm_en_nube(VM) or {}).get("estado") == "RUNNING"
        sin = operar(VM, "eliminar", {"confirmar": "otro"}, "O-C11a")
        r = operar(VM, "eliminar", {"confirmar": VM, "entiendo": "1"}, "O-C11")
        eq = q1("SELECT estado FROM equipos WHERE cliente_id=? AND nombre=?", cl["id"], VM)
        o["C11"] = sin is None and r and r["estado"] == "exitoso" and not vm_en_nube(VM) and (eq or {}).get("estado") == "retirado"
        print("OPERAR (parte A):", {k: bool(v) for k, v in o.items()})
    finally:
        limpio = limpiar(VM)
    print("CREAR casos:", {k: bool(v) for k, v in ok.items()}, "| limpio:", limpio)
    print("TICKETS:", json.dumps(RES))
    from app import pruebas
    casos = [{"id": k, "resultado": "ok" if v else "falla", "evidencia": ", ".join(RES.get(k, []))} for k, v in ok.items()]
    todo = len(casos) == 5 and all(c["resultado"] == "ok" for c in casos) and limpio
    pruebas.certificar(cl["id"], N.CREAR, "planes-prueba.yml · " + N.CREAR, casos, [t for v in RES.values() for t in v], "aprobada" if todo else "rechazada", "Claude (regla de Cristian)",
                       nota="VM e2-micro Rocky Linux 9 en invoke-summit-cloud, eliminada al terminar")
    print("CERTIFICADA" if todo else "NO certificada", N.CREAR)
    open("/var/lib/faceops/prueba-operar-a.json", "w").write(json.dumps({"casos": {k: bool(v) for k, v in o.items()}, "tickets": RES}))

PY
sudo podman cp /tmp/m.py faceops:/tmp/m.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops timeout 3300 python3 /tmp/m.py 2>&1 | grep -v "Warning\|warnings.warn" | tail -60; sudo podman exec faceops rm -f /tmp/m.py
