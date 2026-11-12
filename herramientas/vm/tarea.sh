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
with a.test_request_context():
    ctx()
    casos, ok = [], True
    j1, e1 = leer(["southamerica-west1"])
    op = N.opciones(cl["id"], IID) or {}
    s1 = aap.salida(j1, 2000) if j1 else ""
    print("C1 job", j1, e1, "| regiones", len(op.get("regiones") or []), "| zonas sw1", (op.get("zonas") or {}).get("southamerica-west1"),
          "| tipos", len(op.get("tipos") or {}), "| imágenes ok", sum(1 for v in (op.get("imagenes") or {}).values() if v.get("ok")), "/", len(op.get("imagenes") or {}),
          "| redes", [r["nombre"] for r in op.get("redes") or []], "| subredes", len(op.get("subredes") or []), "| cuentas", len(op.get("cuentas") or []),
          "| firewall", len(op.get("firewall") or []), "| máquinas", [m["nombre"] for m in op.get("maquinas") or []], "| precios", {k: sorted(v) for k, v in (op.get("precios") or {}).items()},
          "| errores", op.get("errores"))
    print("   ROLES de la cuenta de servicio:", op.get("roles"))
    print("   PERMISOS QUE FALTAN:", op.get("permisos_faltan"))
    print("   ROLES QUE FALTAN:", sorted({N.rol_de("gcp", p) for p in op.get("permisos_faltan") or []}))
    print("   APIs:", len(op.get("apis") or []) if op.get("apis") is not None else None, [x for x in (op.get("apis") or []) if x.split(".")[0] in ("compute", "storage", "pubsub", "bigquery", "secretmanager", "artifactregistry", "dns", "cloudbilling", "cloudresourcemanager", "iam", "serviceusage", "sqladmin", "container", "run", "redis", "file", "firestore", "alloydb", "aiplatform", "cloudkms")])
    c1 = e1 == "ok" and op.get("regiones") and op.get("tipos") and op.get("redes") is not None and op.get("permisos_faltan") is not None
    j2, e2 = leer(["southamerica-west1"])
    op2 = N.opciones(cl["id"], IID) or {}
    c2 = e2 == "ok" and len(op2.get("tipos") or {}) == len(op.get("tipos") or {}) and len(op2.get("maquinas") or []) == len(op.get("maquinas") or [])
    print("C2 job", j2, e2, "mismos conteos:", c2)
    ao_ = auto(N.OPCIONES)
    j3 = aap.lanzar("job", ao_["aap_id"], extra_vars={"nb_infra_id": IID, "nb_proyecto": "X_no_valido", "nb_entrada": {}}, credenciales=[N.credencial(aap, INF)])
    for _ in range(60):
        st3 = aap.get(f"jobs/{j3}/")["status"]
        if st3 in ("successful", "failed", "error", "canceled"): break
        time.sleep(5)
    s3 = aap.salida(j3, 200)
    c3 = st3 == "failed" and "Datos válidos" in s3
    print("C3 job", j3, st3, "falla en Datos válidos:", "Datos válidos" in s3)
    c4 = bool(s1) and not SECRETO.search(s1 + aap.salida(j2, 2000) + s3)
    print("C4 salida sin secretos:", c4, "(bytes revisados", len(s1), ")")
    casos = [{"id": "C1", "resultado": "ok" if c1 else "falla", "evidencia": f"job {j1}: {len(op.get('regiones') or [])} regiones, {len(op.get('tipos') or {})} tipos, permisos que faltan calculados"},
             {"id": "C2", "resultado": "ok" if c2 else "falla", "evidencia": f"job {j2}: mismos conteos"},
             {"id": "C3", "resultado": "ok" if c3 else "falla", "evidencia": f"job {j3}: fallido en Datos válidos"},
             {"id": "C4", "resultado": "ok" if c4 else "falla", "evidencia": "sin private_key, client_secret ni tokens en la salida"}]
    from app import pruebas
    todo = all(c["resultado"] == "ok" for c in casos)
    pruebas.certificar(cl["id"], N.OPCIONES, "planes-prueba.yml · " + N.OPCIONES, casos, [f"job {j1}", f"job {j2}", f"job {j3}"],
                       "aprobada" if todo else "rechazada", "Claude (regla de Cristian)")
    print("CERTIFICADA" if todo else "NO certificada", N.OPCIONES, "·", pruebas.estado(dict(auto(N.OPCIONES), cliente_id=cl["id"]))["estado"])
PY
sudo podman cp /tmp/m.py faceops:/tmp/m.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops timeout 3000 python3 /tmp/m.py 2>&1 | grep -v "Warning\|warnings.warn" | tail -30; sudo podman exec faceops rm -f /tmp/m.py
