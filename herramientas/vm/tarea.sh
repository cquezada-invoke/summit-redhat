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
RES = {}
def X():
    return next(i for i in lista(cl["id"], equipos_visibles()) if i["id"] == IID)
def op():
    return N.opciones(cl["id"], IID) or {}
def crear_p(pid, form, caso, aprobar=True):
    p, r_, pr = N.validar_producto(X(), op(), pid, form)
    ej("INSERT INTO nube_productos(cliente_id, infra_id, producto, nombre, region, zona, parametros, estado, ticket_id, creado, costo_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
       cl["id"], IID, pid, r_["nombre"], r_["region"], r_["zona"], json.dumps(r_["parametros"]), "creando", 0, time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), pr["np_costo_usd_mes"])
    t = ticket(N.PRODUCTO, pr, f"Prueba {caso}: agregar {p['nombre']} «{r_['nombre']}»", aprobar=aprobar)
    RES.setdefault(caso, []).append(t["numero"]); ULT["pr"] = pr; return t
ULT = {}
def fila(pid, n):
    return q1("SELECT * FROM nube_productos WHERE cliente_id=? AND infra_id=? AND producto=? AND nombre=? ORDER BY id DESC LIMIT 1", cl["id"], IID, pid, n)
def eliminar_p(pid, n, caso):
    r = fila(pid, n)
    t = ticket(N.PRODUCTO, N.params_eliminar_producto(X(), r), f"Prueba {caso}: eliminar {pid} «{n}»")
    RES.setdefault(caso, []).append(t["numero"]); return t
def salud(pid, n):
    """Salud leída de la nube (lectura de opciones con este producto registrado)."""
    leer(["southamerica-west1"])
    r = fila(pid, n)
    return (r or {}).get("salud"), (r or {}).get("estado")
with a.test_request_context("/", method="POST"):
    ctx()
    ok, extra = {}, {}
    leer(["southamerica-west1"])
    falt = set(op().get("permisos_faltan") or [])
    apis = set(op().get("apis") or [])
    B = "fo-prueba-" + time.strftime("%m%d%H%M") + "-invoke"
    creados = []
    try:
        t = crear_p("bucket", {"nombre": B, "clase": "STANDARD", "gb_estimados": "1"}, "C1"); creados.append(("bucket", B))
        s = salud("bucket", B); print("C1 salud:", s)
        ok["C1"] = t["estado"] == "exitoso" and s == ("operativo", "activo")
        pr1 = ULT["pr"]
        t = ticket(N.PRODUCTO, pr1, f"Prueba C2: repetir bucket {B}")
        RES["C2"] = [t["numero"]]
        ok["C2"] = t["estado"] == "exitoso" and "ya existe" in (t["resultado"] or "") + salida_t(t["id"], 300)
        t = eliminar_p("bucket", B, "C3"); s = salud("bucket", B); print("C3 salud tras eliminar:", s)
        ok["C3"] = t["estado"] == "exitoso" and (fila("bucket", B) or {}).get("estado") == "eliminado"
        if ok["C3"]: creados.remove(("bucket", B))
        mal = dict(N.validar_producto(X(), op(), "bucket", {"nombre": B + "-mal"})[2])
        mal["nb_entrada"] = dict(mal["nb_entrada"], pasos=[{"metodo": "POST", "url": "https://example.com/x", "cuerpo": {}}])
        t = ticket(N.PRODUCTO, mal, "Prueba C4: URL que no es de Google"); RES["C4"] = [t["numero"]]
        ok["C4"] = t["estado"] == "fallido"
        res5 = []
        for pid, f, debe in (("gke", {"nombre": "gke-prueba", "canal": "REGULAR"}, True), ("gke", {"nombre": "GKE_mal"}, False),
                             ("cloudsql", {"nombre": "sql-prueba", "motor": "POSTGRES_16", "nivel": "db-custom-1-3840", "disco_gb": "20"}, True), ("cloudsql", {"nombre": "sql-prueba", "disco_gb": "5"}, False),
                             ("alloydb", {"nombre": "alloy-prueba", "cpu": "2"}, True), ("alloydb", {"nombre": "alloy-prueba", "cpu": "3"}, False),
                             ("vertex", {"nombre": "vertex-prueba"}, True), ("vertex", {"nombre": "-mal"}, False)):
            try:
                N.validar_producto(X(), op(), pid, f); res5.append(debe)
            except ValueError as e:
                res5.append(not debe)
        cert = {pid: N.certificado_producto("gcp", N.producto_def("gcp", pid)) for pid in ("gke", "cloudsql", "alloydb", "vertex")}
        ok["C5"] = all(res5) and all(not c[0] for c in cert.values())
        print("C5 formularios:", res5, "| sin certificar:", {k: v[0] for k, v in cert.items()})
        B2 = B + "-r"
        t = crear_p("bucket", {"nombre": B2}, "C6", aprobar=False)
        ok["C6"] = t["estado"] == "rechazado" and salud("bucket", B2)[0] in ("no existe", "", None)
        # productos baratos: prueba real (crear, salud, eliminar) si la cuenta de servicio tiene el rol y la API está habilitada
        for pid, nom in (("pubsub", "fo-prueba-tema"), ("bigquery", "fo_prueba_ds"), ("secretmanager", "fo-prueba-secreto"), ("artifactregistry", "fo-prueba-repo"), ("dns", "fo-prueba-zona")):
            p = N.producto_def("gcp", pid)
            mot = [x for x in p.get("permisos") or [] if x in falt]
            if mot or p.get("servicio") not in apis:
                extra[pid] = "sin probar: " + (("falta " + ", ".join(sorted({N.rol_de('gcp', x) for x in mot}))) if mot else f"API {p.get('servicio')} deshabilitada"); continue
            try:
                t = crear_p(pid, {"nombre": nom}, "P-" + pid); creados.append((pid, nom))
                s1 = salud(pid, nom)
                t2 = eliminar_p(pid, nom, "P-" + pid)
                s2 = salud(pid, nom)
                if t2["estado"] == "exitoso": creados.remove((pid, nom))
                if t["estado"] == "exitoso" and s1[0] == "operativo" and t2["estado"] == "exitoso" and (fila(pid, nom) or {}).get("estado") == "eliminado":
                    N.certificar_producto("gcp", pid, RES["P-" + pid], "Claude (regla de Cristian)"); extra[pid] = "certificado " + ", ".join(RES["P-" + pid])
                else:
                    extra[pid] = f"falló: {t['estado']} {s1} {t2['estado']}"
            except Exception as x:
                extra[pid] = f"error: {str(x)[:150]}"
    finally:
        for pid, n in list(creados):   # rescate: lo que quedó creado se elimina, pase o falle
            try:
                eliminar_p(pid, n, "limpieza")
            except Exception as x:
                print("limpieza", pid, n, x)
        leer(["southamerica-west1"])
        vivos = [(r["producto"], r["nombre"], r["salud"]) for r in q("SELECT * FROM nube_productos WHERE cliente_id=? AND infra_id=? AND nombre LIKE 'fo%prueba%'", cl["id"], IID)
                 if r["salud"] not in ("no existe",) and r["estado"] not in ("eliminado",) and r["estado"] != "fallido"]
        print("VERIFICACIÓN FINAL productos que quedan:", vivos)
    if ok.get("C1") and ok.get("C3"):
        N.certificar_producto("gcp", "bucket", RES["C1"] + RES["C3"], "Claude (regla de Cristian)")
    print("PRODUCTO casos:", ok, "| baratos:", extra)
    print("TICKETS:", json.dumps(RES))
    from app import pruebas
    casos = [{"id": k, "resultado": "ok" if v else "falla", "evidencia": ", ".join(RES.get(k, []))} for k, v in ok.items()]
    todo = len(casos) == 6 and all(c["resultado"] == "ok" for c in casos) and not vivos
    pruebas.certificar(cl["id"], N.PRODUCTO, "planes-prueba.yml · " + N.PRODUCTO, casos, [t for v in RES.values() for t in v], "aprobada" if todo else "rechazada", "Claude (regla de Cristian)",
                       nota="bucket en invoke-summit-cloud, eliminado al terminar; GKE, Cloud SQL, AlloyDB y Vertex solo formulario (sin certificar)")
    print("CERTIFICADA" if todo else "NO certificada", N.PRODUCTO)

PY
sudo podman cp /tmp/m.py faceops:/tmp/m.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops timeout 3300 python3 /tmp/m.py 2>&1 | grep -v "Warning\|warnings.warn" | tail -60; sudo podman exec faceops rm -f /tmp/m.py
