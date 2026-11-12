cd ~/summit-redhat
cat > /tmp/motor-nube.py <<'PY'
"""Motor de prueba reanudable (ficha de nube). Cada invocación avanza lo que pueda en ~40 s y guarda el estado en
/var/lib/faceops (sobrevive a los redespliegues del contenedor). Uso: python3 motor.py <plan> [reiniciar]
Planes: opciones | maquinas | productos. Imprime PLAN_FIN al terminar."""
import json, os, re, sys, time
from app import create_app
from app.bd import q, q1, ej, j
a = create_app()
from app.operacion import sincronizar_catalogo, solicitar, resolver_aprobacion, VINCULAR, INVENTARIAR
from app.aap import AAP
from app import nube_recursos as N
from app import pruebas

PLAN = sys.argv[1]
RUTA = f"/var/lib/faceops/prueba-nube-{PLAN}.json"
APROB = "Aprobado por Claude (regla de Cristian)"
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 LIMIT 1")
INF = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo='gcp' AND detalle LIKE '%invoke-summit-cloud%' ORDER BY id LIMIT 1", cl["id"])
IID = INF["id"]
SECRETO = re.compile(r"private_key|BEGIN PRIVATE|client_secret|\"token\"|ya29\.", re.I)
FIN_T = ("exitoso", "fallido", "cancelado", "rechazado")


def cargar():
    if len(sys.argv) > 2 and sys.argv[2] == "reiniciar" and os.path.exists(RUTA):
        os.remove(RUTA)
    if os.path.exists(RUTA):
        return json.load(open(RUTA))
    return {"i": 0, "fase": "pasos", "casos": {}, "pend": {}, "V": {"ts": time.strftime("%m%d%H%M")}}


def guardar(st):
    json.dump(st, open(RUTA + ".tmp", "w"), ensure_ascii=False); os.replace(RUTA + ".tmp", RUTA)


def auto(n): return q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n)
def X():
    from app.infraestructuras import lista
    from app.vistas import equipos_visibles
    return next(i for i in lista(cl["id"], equipos_visibles()) if i["id"] == IID)
def op(): return N.opciones(cl["id"], IID) or {}
def vm(n): return next((m for m in op().get("maquinas") or [] if m["nombre"] == n), None)
def t_row(tid): return q1("SELECT id, numero, estado, resultado, aap_job_id, aap_job_tipo, fin FROM tickets WHERE id=?", tid)
def salida_t(tid, n=400):
    t = t_row(tid); aap = AAP.de_cliente(cl)
    if not t or not t["aap_job_id"]: return ""
    if t["aap_job_tipo"] == "job": return aap.salida(t["aap_job_id"], n)
    try:
        return "\n".join(aap.salida(nd["id"], n) for nd in aap.nodos_trabajo_workflow(t["aap_job_id"]) if nd.get("type") == "job" and nd.get("id"))
    except Exception: return ""


def leer_nb(pend, regiones=("southamerica-west1",)):
    """Lectura de opciones sin bloquear: True cuando terminó una lectura lanzada para este paso."""
    if pend.get("lectura"):
        N.recoger_opciones(cl, IID)
        tr = q1("SELECT estado FROM nube_opciones_trabajos WHERE job=?", pend["lectura"])
        if tr and tr["estado"] == "en curso": return None
        pend["lectura_estado"] = tr["estado"] if tr else "?"
        return True
    job, mot = N.lanzar_opciones(cl, dict(INF), list(regiones), prueba=True)
    if job: pend["lectura"] = job
    elif "en curso" in (mot or ""): N.recoger_opciones(cl, IID)
    else: print("  lectura no lanzada:", mot)
    return None


# ----------------------------------------------------------------- pasos: (caso, accion, verificar, opciones)
# accion(V, pend) → {"ticket": (nombre_aap, params, motivo, aprobar)} | {"ok": bool, "nota": str} | None (esperar)
# verificar(V, t, pend) → (ok, nota) | None (esperar). Opciones: releer (lectura después del ticket), limpieza (corre siempre)
def form_crear(V, **extra):
    f = {"nombre": V["vm"], "region": "southamerica-west1", "zona": "southamerica-west1-a", "tipo": "e2-micro", "imagen": "rocky-9", "disco_gb": "20",
         "disco_tipo": "pd-balanced", "red": "default", "subred": "default", "publica": "1", "etiquetas": "prueba=faceops\nequipo=plataforma", "etiquetas_red": "fo-prueba", "ambiente": "desarrollo"}
    f.update(extra); return f


def T(nombre_aap, params, motivo, aprobar=True): return {"ticket": (nombre_aap, params, motivo, aprobar)}
def OPR(V, accion, form, caso): return T(N.OPERAR, N.validar_operar(X(), op(), V["vm"], accion, form), f"Prueba {caso}: {accion} {V['vm']}")
def ok_t(t): return t["estado"] == "exitoso"


def rechaza(fn):
    try: fn(); return False
    except ValueError: return True


def plan_maquinas():
    P = []
    def previo(V, pend):   # máquinas de prueba que hayan quedado de corridas cortadas: se eliminan primero
        r = leer_nb(pend)
        if r is None: return None
        resto = [m["nombre"] for m in op().get("maquinas") or [] if m["nombre"].startswith("fo-prueba-") and m["nombre"] != V["vm"]]
        if not resto: return {"ok": True, "nota": "sin restos"}
        n = resto[0]
        return T(N.OPERAR, N.validar_operar(X(), op(), n, "eliminar", {"confirmar": n, "entiendo": "1"}), f"Prueba (limpieza previa): eliminar {n}")
    P.append(("PREVIO", previo, lambda V, t, p: (True, "resto eliminado") if ok_t(t) else (False, "no se eliminó el resto"), {"repetir": True, "limpieza": True}))
    def c1v(V, t, p):
        m = vm(V["vm"]); eq = q1("SELECT nombre, estado FROM equipos WHERE cliente_id=? AND nombre=?", cl["id"], V["vm"])
        if not ok_t(t) or not m: return False, f"ticket {t['estado']} · vm {bool(m)}"
        ts = {r["nombre_aap"]: r["estado"] for r in q("SELECT a.nombre_aap, t.estado FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.limite=? AND a.nombre_aap IN (?,?)", cl["id"], V["vm"], VINCULAR, INVENTARIAR)}
        if ts.get(INVENTARIAR) not in FIN_T and ts.get(VINCULAR) not in ("fallido", "rechazado", "cancelado"):
            if time.time() - p.setdefault("t_v", time.time()) < 1800: return None
        bloq = q1("SELECT detalle FROM auditoria WHERE cliente_id=? AND accion='inventario automático no lanzado' AND detalle LIKE ? ORDER BY id DESC LIMIT 1", cl["id"], f'%{V["vm"]}%')
        if ts.get(INVENTARIAR) is None and not bloq and time.time() - p.setdefault("t_v", time.time()) < 1800: return None
        inv = ts.get(INVENTARIAR) == "exitoso" or bool(bloq and "bloqueada" in bloq["detalle"])   # lanzado; si su acción está bloqueada por su propia regla de prueba, queda registrado
        return (m["estado"] == "RUNNING" and m["etiquetas"].get("prueba") == "faceops" and bool(eq) and ts.get(VINCULAR) == "exitoso" and inv,
                f"{m['estado']} {m['tipo']} etiquetas ok · vínculo {ts.get(VINCULAR)} · inventario {ts.get(INVENTARIAR) or ('pedido; «Inventariar máquina» bloqueada por su regla de prueba' if bloq else 'no lanzado')}")
    P.append(("CR-C1", lambda V, p: T(N.CREAR, N.validar_crear(X(), op(), form_crear(V)), f"Prueba C1: crear {V['vm']} (e2-micro; se elimina al terminar)"), c1v, {"releer": True}))
    def c2a(V, p):
        V["rep"] = rechaza(lambda: N.validar_crear(X(), op(), form_crear(V)))
        pr = N.validar_crear(X(), dict(op(), maquinas=[]), form_crear(V)); return T(N.CREAR, pr, f"Prueba C2: repetir crear {V['vm']}")
    P.append(("CR-C2", c2a, lambda V, t, p: (ok_t(t) and V["rep"] and (vm(V["vm"]) or {}).get("estado") == "RUNNING", f"{t['estado']} · formulario rechaza repetido: {V['rep']}"), {"releer": True}))
    def c3a(V, p):
        pr = N.validar_crear(X(), dict(op(), maquinas=[]), form_crear(V)); pr.update(nm_nombre=V["vm"] + "-x", nm_tipo="m1-ultramem-40")
        return T(N.CREAR, pr, "Prueba C3: tipo fuera de las familias")
    P.append(("CR-C3", c3a, lambda V, t, p: (t["estado"] == "fallido" and "Datos válidos" in (t["resultado"] or "") + salida_t(t["id"]), t["estado"]), {}))
    def c4a(V, p):
        pr = N.validar_crear(X(), dict(op(), maquinas=[]), form_crear(V)); pr.update(nm_nombre=V["vm"] + "-r")
        return T(N.CREAR, pr, "Prueba C4: rechazar la aprobación", False)
    P.append(("CR-C4", c4a, lambda V, t, p: (t["estado"] == "rechazado" and not vm(V["vm"] + "-r"), t["estado"]), {"releer": True}))
    def c5a(V, p):
        mal = []
        for f, esp in ((form_crear(V, nombre="Mal_Nombre"), "nombre"), (form_crear(V, nombre="fo-z", zona="us-central1-a"), "zona"),
                       (form_crear(V, nombre="fo-z", disco_gb="5"), "disco"), (form_crear(V, nombre="fo-z", imagen="windows-2022", disco_gb="60"), "sin certificar")):
            try: N.validar_crear(X(), op(), f); mal.append(esp)
            except ValueError as e:
                if esp not in str(e).lower(): mal.append(f"{esp}: {e}")
        return {"ok": not mal, "nota": "rechazos con motivo" if not mal else str(mal)}
    P.append(("CR-C5", c5a, None, {}))
    est = lambda V: (vm(V["vm"]) or {}).get("estado")
    energia = lambda V: (q1("SELECT energia FROM equipos WHERE cliente_id=? AND nombre=?", cl["id"], V["vm"]) or {}).get("energia")
    P.append(("OP-C1", lambda V, p: OPR(V, "apagar", {}, "C1"), lambda V, t, p: (ok_t(t) and est(V) == "TERMINATED" and energia(V) == "apagado", f"{est(V)} · FaceOps {energia(V)}"), {"releer": True}))
    P.append(("OP-C2", lambda V, p: OPR(V, "apagar", {}, "C2"), lambda V, t, p: (ok_t(t) and "sin cambios" in (t["resultado"] or "") + salida_t(t["id"]), "idempotente"), {"releer": True}))
    P.append(("OP-C3", lambda V, p: OPR(V, "redimensionar", {"tipo": "e2-small"}, "C3"), lambda V, t, p: (ok_t(t) and (vm(V["vm"]) or {}).get("tipo") == "e2-small" and est(V) == "TERMINATED", f"{(vm(V['vm']) or {}).get('tipo')} {est(V)}"), {"releer": True}))
    P.append(("OP-C4", lambda V, p: OPR(V, "encender", {}, "C4"), lambda V, t, p: (ok_t(t) and est(V) == "RUNNING", est(V)), {"releer": True}))
    def c5o(V, p):
        V["sin_conf"] = rechaza(lambda: N.validar_operar(X(), op(), V["vm"], "redimensionar", {"tipo": "e2-micro"}))
        return OPR(V, "redimensionar", {"tipo": "e2-micro", "detener": "1"}, "C5")
    P.append(("OP-C5", c5o, lambda V, t, p: (ok_t(t) and V["sin_conf"] and (vm(V["vm"]) or {}).get("tipo") == "e2-micro" and est(V) == "RUNNING", f"sin confirmar rechazado {V['sin_conf']} · {(vm(V['vm']) or {}).get('tipo')} {est(V)}"), {"releer": True}))
    P.append(("OP-C6", lambda V, p: OPR(V, "reiniciar", {}, "C6"), lambda V, t, p: (ok_t(t) and est(V) == "RUNNING", est(V)), {"releer": True}))
    def d2(V): return next((d for d in (vm(V["vm"]) or {}).get("discos") or [] if not d["arranque"]), {})
    P.append(("OP-C7a", lambda V, p: OPR(V, "disco_agregar", {"gb": "10", "disco_tipo": "pd-balanced"}, "C7"), lambda V, t, p: (ok_t(t) and d2(V).get("gb") == 10, str(d2(V))), {"releer": True}))
    def c7b(V, p):
        V["achicar"] = rechaza(lambda: N.validar_operar(X(), op(), V["vm"], "disco_ampliar", {"disco": d2(V).get("nombre", ""), "gb": "9"}))
        return OPR(V, "disco_ampliar", {"disco": d2(V).get("nombre", ""), "gb": "12"}, "C7")
    P.append(("OP-C7", c7b, lambda V, t, p: (ok_t(t) and V["achicar"] and d2(V).get("gb") == 12, f"achicar rechazado {V['achicar']} · {d2(V)}"), {"releer": True}))
    P.append(("OP-C8", lambda V, p: OPR(V, "etiquetas", {"etiquetas": "prueba=faceops\ncentro-costo=ti", "etiquetas_red": "fo-prueba, web"}, "C8"),
              lambda V, t, p: (ok_t(t) and (vm(V["vm"]) or {}).get("etiquetas", {}).get("centro-costo") == "ti" and "web" in ((vm(V["vm"]) or {}).get("etiquetas_red") or []), "etiquetas"), {"releer": True}))
    def boot(V): return next((d["nombre"] for d in (vm(V["vm"]) or {}).get("discos") or [] if d["arranque"]), V["vm"])
    P.append(("OP-C9", lambda V, p: OPR(V, "snapshot", {"disco": boot(V)}, "C9"),
              lambda V, t, p: (ok_t(t) and any(s.get("maquina") == V["vm"] for s in op().get("snapshots") or []), "snapshot con etiqueta faceops-vm"), {"releer": True}))
    regla = lambda V: f"fo-{V['vm']}-web"
    P.append(("OP-C10a", lambda V, p: OPR(V, "firewall", {"modo": "agregar", "regla": "web", "protocolo": "tcp", "puertos": "8080", "origenes": "10.0.0.0/8"}, "C10"),
              lambda V, t, p: (ok_t(t) and any(f["nombre"] == regla(V) for f in op().get("firewall") or []) and regla(V) in ((vm(V["vm"]) or {}).get("etiquetas_red") or []), "regla y etiqueta"), {"releer": True}))
    P.append(("OP-C10", lambda V, p: OPR(V, "firewall", {"modo": "quitar", "regla": "web"}, "C10"),
              lambda V, t, p: (ok_t(t) and regla(V) not in ((vm(V["vm"]) or {}).get("etiquetas_red") or []), "etiqueta retirada"), {"releer": True}))
    P.append(("OP-C12", lambda V, p: T(N.OPERAR, {"nb_infra_id": IID, "nb_entrada": {"accion": "apagar", "maquina": "fo-no-existe-123", "zona": "southamerica-west1-a"}}, "Prueba C12: máquina inexistente"),
              lambda V, t, p: (t["estado"] == "fallido" and "no existe" in (t["resultado"] or "") + salida_t(t["id"]), t["estado"]), {}))
    def c11(V, p):
        V["sin_nombre"] = rechaza(lambda: N.validar_operar(X(), op(), V["vm"], "eliminar", {"confirmar": "otro"}))
        return OPR(V, "eliminar", {"confirmar": V["vm"], "entiendo": "1"}, "C11")
    def c11v(V, t, p):
        eq = q1("SELECT estado FROM equipos WHERE cliente_id=? AND nombre=?", cl["id"], V["vm"])
        o = op()
        quedan = [s["nombre"] for s in o.get("snapshots") or [] if s.get("maquina") == V["vm"]] + [f["nombre"] for f in o.get("firewall") or [] if f["nombre"].startswith(f"fo-{V['vm']}-")]
        return (ok_t(t) and V["sin_nombre"] and not vm(V["vm"]) and (eq or {}).get("estado") == "retirado" and not quedan, f"sin nombre rechazado {V['sin_nombre']} · equipo {(eq or {}).get('estado')} · quedan {quedan}")
    P.append(("OP-C11", c11, c11v, {"releer": True}))
    def limpieza(V, pend):
        r = leer_nb(pend)
        if r is None: return None
        resto = [m["nombre"] for m in op().get("maquinas") or [] if m["nombre"].startswith("fo-prueba-")]
        if not resto:
            o = op()
            q_ = [s["nombre"] for s in o.get("snapshots") or [] if (s.get("maquina") or "").startswith("fo-prueba-")] + [f["nombre"] for f in o.get("firewall") or [] if f["nombre"].startswith("fo-fo-prueba-")]
            return {"ok": not q_, "nota": "nada de prueba en GCP" if not q_ else f"quedan {q_}"}
        n = resto[0]
        return T(N.OPERAR, N.validar_operar(X(), op(), n, "eliminar", {"confirmar": n, "entiendo": "1"}), f"Prueba (limpieza final): eliminar {n}")
    P.append(("LIMPIEZA", limpieza, lambda V, t, p: (ok_t(t), f"eliminada · {t['estado']}"), {"repetir": True, "limpieza": True}))
    return P


def plan_productos():
    P = []
    def fila(pid, n): return q1("SELECT * FROM nube_productos WHERE cliente_id=? AND infra_id=? AND producto=? AND nombre=? ORDER BY id DESC LIMIT 1", cl["id"], IID, pid, n)
    def crear(V, pid, n, form, caso, aprobar=True):
        p, r_, pr = N.validar_producto(X(), op(), pid, dict(form, nombre=n))
        ej("INSERT INTO nube_productos(cliente_id, infra_id, producto, nombre, region, zona, parametros, estado, ticket_id, creado, costo_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
           cl["id"], IID, pid, n, r_["region"], r_["zona"], json.dumps(r_["parametros"]), "creando", 0, time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), pr["np_costo_usd_mes"])
        V.setdefault("pr", {})[pid + n] = pr
        return T(N.PRODUCTO, pr, f"Prueba {caso}: agregar {p['nombre']} «{n}»", aprobar)
    def eliminar(V, pid, n, caso): return T(N.PRODUCTO, N.params_eliminar_producto(X(), fila(pid, n)), f"Prueba {caso}: eliminar {pid} «{n}»")
    B = lambda V: f"fo-prueba-{V['ts']}-invoke"
    sal = lambda V, pid, n: ((fila(pid, n) or {}).get("salud"), (fila(pid, n) or {}).get("estado"))
    P.append(("C1", lambda V, p: crear(V, "bucket", B(V), {"clase": "STANDARD", "gb_estimados": "1"}, "C1"), lambda V, t, p: (ok_t(t) and sal(V, "bucket", B(V)) == ("operativo", "activo"), str(sal(V, "bucket", B(V)))), {"releer": True}))
    P.append(("C2", lambda V, p: T(N.PRODUCTO, V["pr"]["bucket" + B(V)], f"Prueba C2: repetir bucket {B(V)}"), lambda V, t, p: (ok_t(t) and "ya existe" in (t["resultado"] or "") + salida_t(t["id"]), t["estado"]), {}))
    P.append(("C3", lambda V, p: eliminar(V, "bucket", B(V), "C3"), lambda V, t, p: (ok_t(t) and (fila("bucket", B(V)) or {}).get("estado") == "eliminado", str(sal(V, "bucket", B(V)))), {"releer": True}))
    def c4(V, p):
        mal = dict(N.validar_producto(X(), op(), "bucket", {"nombre": B(V) + "-mal"})[2])
        mal["nb_entrada"] = dict(mal["nb_entrada"], pasos=[{"metodo": "POST", "url": "https://example.com/x", "cuerpo": {}}])
        return T(N.PRODUCTO, mal, "Prueba C4: URL que no es de Google")
    P.append(("C4", c4, lambda V, t, p: (t["estado"] == "fallido", t["estado"]), {}))
    def c5(V, p):
        res = []
        for pid, f, debe in (("gke", {"nombre": "gke-prueba", "canal": "REGULAR"}, True), ("gke", {"nombre": "GKE_mal"}, False),
                             ("cloudsql", {"nombre": "sql-prueba", "motor": "POSTGRES_16", "nivel": "db-custom-1-3840", "disco_gb": "20"}, True), ("cloudsql", {"nombre": "sql-prueba", "disco_gb": "5"}, False),
                             ("alloydb", {"nombre": "alloy-prueba", "cpu": "2"}, True), ("alloydb", {"nombre": "alloy-prueba", "cpu": "3"}, False),
                             ("vertex", {"nombre": "vertex-prueba"}, True), ("vertex", {"nombre": "-mal"}, False)):
            try: N.validar_producto(X(), op(), pid, f); res.append(debe)
            except ValueError: res.append(not debe)
        cert = [N.certificado_producto("gcp", N.producto_def("gcp", pid))[0] for pid in ("gke", "cloudsql", "alloydb", "vertex")]
        return {"ok": all(res) and not any(cert), "nota": f"formularios {res} · sin certificar {not any(cert)}"}
    P.append(("C5", c5, None, {}))
    P.append(("C6", lambda V, p: crear(V, "bucket", B(V) + "-r", {}, "C6", False), lambda V, t, p: (t["estado"] == "rechazado", t["estado"]), {}))
    for pid, nom in (("pubsub", "fo-prueba-tema"), ("bigquery", "fo_prueba_ds"), ("secretmanager", "fo-prueba-secreto"), ("artifactregistry", "fo-prueba-repo"), ("dns", "fo-prueba-zona")):
        def ca(V, p, pid=pid, nom=nom):
            pr = N.producto_def("gcp", pid); o = op()
            falt = [x for x in pr.get("permisos") or [] if x in set(o.get("permisos_faltan") or [])]
            if falt or pr.get("servicio") not in set(o.get("apis") or []):
                V.setdefault("extra", {})[pid] = "sin probar: " + (("falta " + ", ".join(sorted({N.rol_de('gcp', x) for x in falt}))) if falt else f"API {pr.get('servicio')} deshabilitada")
                return {"ok": True, "nota": V["extra"][pid], "opcional": True}
            return crear(V, pid, nom, {}, "P-" + pid)
        def cv(V, t, p, pid=pid, nom=nom):
            V.setdefault("creados", []).append([pid, nom])
            return ok_t(t) and sal(V, pid, nom)[0] == "operativo", f"{t['estado']} {sal(V, pid, nom)}"
        def ea(V, p, pid=pid, nom=nom):
            if [pid, nom] not in V.get("creados", []): return {"ok": True, "nota": "no se creó", "opcional": True}
            return eliminar(V, pid, nom, "P-" + pid)
        def ev(V, t, p, pid=pid, nom=nom):
            ok = ok_t(t) and (fila(pid, nom) or {}).get("estado") == "eliminado"
            if ok:
                V["creados"].remove([pid, nom])
                c = V.get("casos_p", {}).get(pid)
                if c: N.certificar_producto("gcp", pid, c, "Claude (regla de Cristian)"); V.setdefault("extra", {})[pid] = "certificado " + ", ".join(c)
            return True, f"eliminado {ok}"
        P.append(("P-" + pid, ca, cv, {"releer": True, "opcional": True}))
        P.append(("P-" + pid + "-el", ea, ev, {"releer": True, "opcional": True, "limpieza": True}))
    def limpieza(V, pend):
        for pid, nom in list(V.get("creados", [])):
            r = fila(pid, nom)
            if r and r["estado"] not in ("eliminado",):
                V["creados"].remove([pid, nom]); return eliminar(V, pid, nom, "limpieza")
        r = leer_nb(pend)
        if r is None: return None
        vivos = [(x["producto"], x["nombre"], x["salud"]) for x in q("SELECT * FROM nube_productos WHERE cliente_id=? AND infra_id=? AND nombre LIKE 'fo%prueba%' AND estado IN ('activo','creando','eliminando')", cl["id"], IID)
                 if x["salud"] != "no existe"]
        return {"ok": not vivos, "nota": "nada de prueba en GCP" if not vivos else f"quedan {vivos}"}
    P.append(("LIMPIEZA", limpieza, lambda V, t, p: (ok_t(t), t["estado"]), {"repetir": True, "limpieza": True}))
    return P


def plan_opciones():
    P = []
    def c1(V, p):
        if leer_nb(p) is None: return None
        o = op(); V["cuenta"] = {"tipos": len(o.get("tipos") or {}), "maquinas": len(o.get("maquinas") or []), "regiones": len(o.get("regiones") or [])}
        V["j1"] = p.get("lectura")
        print("   ROLES de la cuenta de servicio:", o.get("roles")); print("   PERMISOS QUE FALTAN:", o.get("permisos_faltan"))
        print("   ROLES QUE FALTAN:", sorted({N.rol_de("gcp", x) for x in o.get("permisos_faltan") or []}))
        for k_, v_ in (o.get("errores") or {}).items():
            print("   error", k_, "→", (re.findall(r"[a-z]+\.googleapis\.com", v_) or [""])[0], v_[:90])
        print("   APIs:", None if o.get("apis") is None else [x for x in o["apis"] if x.split(".")[0] in ("compute", "storage", "pubsub", "bigquery", "secretmanager", "artifactregistry", "dns", "cloudbilling", "cloudresourcemanager", "iam", "serviceusage", "sqladmin", "container", "run", "redis", "file", "firestore", "alloydb", "aiplatform", "cloudkms")])
        print("   precios:", {k: sorted(v) for k, v in (o.get("precios") or {}).items()})
        ok = p.get("lectura_estado") == "ok" and V["cuenta"]["regiones"] > 0 and V["cuenta"]["tipos"] > 0 and (o.get("permisos_faltan") is not None or bool((o.get("errores") or {}).get("permisos")))
        return {"ok": ok, "nota": f"job {p.get('lectura')}: {V['cuenta']}"}
    P.append(("C1", c1, None, {}))
    def c2(V, p):
        if leer_nb(p) is None: return None
        o = op(); c = {"tipos": len(o.get("tipos") or {}), "maquinas": len(o.get("maquinas") or []), "regiones": len(o.get("regiones") or [])}
        V["j2"] = p.get("lectura")
        return {"ok": p.get("lectura_estado") == "ok" and c == V["cuenta"], "nota": f"job {p.get('lectura')}: {c}"}
    P.append(("C2", c2, None, {}))
    def c3(V, p):
        aap = AAP.de_cliente(cl)
        if not p.get("job"):
            p["job"] = aap.lanzar("job", auto(N.OPCIONES)["aap_id"], extra_vars={"nb_infra_id": IID, "nb_proyecto": "X_no_valido", "nb_entrada": {}}, credenciales=[N.credencial(aap, INF)])
            return None
        st_ = aap.get(f"jobs/{p['job']}/")["status"]
        if st_ not in ("successful", "failed", "error", "canceled"): return None
        V["j3"] = p["job"]
        return {"ok": st_ == "failed" and "Datos válidos" in aap.salida(p["job"], 200), "nota": f"job {p['job']} {st_}"}
    P.append(("C3", c3, None, {}))
    def c4(V, p):
        aap = AAP.de_cliente(cl)
        s = "".join(aap.salida(V[k], 3000) for k in ("j1", "j2", "j3") if V.get(k))
        return {"ok": bool(s) and not SECRETO.search(s), "nota": f"{len(s)} bytes de salida sin secretos"}
    P.append(("C4", c4, None, {}))
    return P


PLANES = {"maquinas": plan_maquinas, "productos": plan_productos, "opciones": plan_opciones}


def certificar_fin(st):
    C = st["casos"]
    def cert(nombre, ids, nota):
        casos = [{"id": i.split("-")[-1] if i.startswith(("CR-", "OP-")) else i, "resultado": "ok" if C.get(i, {}).get("ok") else "falla",
                  "evidencia": (C.get(i, {}).get("nota") or "") + " · " + ", ".join(C.get(i, {}).get("tickets") or [])} for i in ids]
        todo = all(c["resultado"] == "ok" for c in casos)
        tks = [t for c in C.values() for t in c.get("tickets") or []]
        pruebas.certificar(cl["id"], nombre, "planes-prueba.yml · " + nombre, casos, tks, "aprobada" if todo else "rechazada", "Claude (regla de Cristian)", nota=nota)
        print("CERTIFICADA" if todo else "NO certificada", nombre, {c["id"]: c["resultado"] for c in casos})
    limpio = C.get("LIMPIEZA", {}).get("ok", True)
    if PLAN == "opciones":
        cert(N.OPCIONES, ["C1", "C2", "C3", "C4"], "lectura de invoke-summit-cloud")
    if PLAN == "maquinas":
        if not limpio: C["CR-C1"] = dict(C.get("CR-C1", {}), ok=False)
        cert(N.CREAR, [f"CR-C{i}" for i in range(1, 6)], "VM e2-micro Rocky Linux 9 en invoke-summit-cloud, eliminada al terminar")
        C["OP-C7"] = dict(C.get("OP-C7", {}), ok=C.get("OP-C7", {}).get("ok") and C.get("OP-C7a", {}).get("ok"))
        C["OP-C10"] = dict(C.get("OP-C10", {}), ok=C.get("OP-C10", {}).get("ok") and C.get("OP-C10a", {}).get("ok"))
        if not limpio: C["OP-C11"] = dict(C.get("OP-C11", {}), ok=False)
        cert(N.OPERAR, [f"OP-C{i}" for i in range(1, 13)], "VM e2-micro de prueba en invoke-summit-cloud, eliminada al terminar")
    if PLAN == "productos":
        if C.get("C1", {}).get("ok") and C.get("C3", {}).get("ok"):
            N.certificar_producto("gcp", "bucket", C["C1"].get("tickets", []) + C["C3"].get("tickets", []), "Claude (regla de Cristian)")
        if not limpio: C["C1"] = dict(C.get("C1", {}), ok=False)
        cert(N.PRODUCTO, ["C1", "C2", "C3", "C4", "C5", "C6"], "bucket en invoke-summit-cloud eliminado al terminar; GKE, Cloud SQL, AlloyDB y Vertex solo formulario (sin certificar)")
        print("productos adicionales:", st["V"].get("extra"))


def paso_actual(st, P):
    return P[st["i"]] if st["i"] < len(P) else None


def main():
    st = cargar()
    P = PLANES[PLAN]()
    if PLAN == "maquinas": st["V"].setdefault("vm", "fo-prueba-" + st["V"]["ts"])
    fin_ = time.time() + 40
    with a.test_request_context("/", method="POST"):
        from flask import g
        g.usuario, g.cliente, g.perfil = u, cl, {"modo": "escritura", "segmentos": ["*"]}
        while time.time() < fin_:
            if st["fase"] == "fin":
                print("PLAN_FIN"); return
            pa = paso_actual(st, P)
            if pa is None:
                certificar_fin(st); st["fase"] = "fin"; guardar(st); print("PLAN_FIN"); return
            caso, acc, ver, o = pa
            V, p = st["V"], st["pend"]
            if st["fase"] == "limpieza" and not o.get("limpieza"):
                st["i"] += 1; st["pend"] = {}; guardar(st); continue
            if "tid" not in p:
                try:
                    r = acc(V, p)
                except Exception as x:
                    r = {"ok": False, "nota": f"error: {str(x)[:200]}"}
                guardar(st)
                if r is None:
                    return
                if "ticket" in r:
                    n_, par, mot, apr = r["ticket"]
                    try:
                        t = solicitar(u, cl, auto(n_), "", par, mot, prueba=True)
                    except Exception as x:
                        r = {"ok": False, "nota": f"no se creó el ticket: {str(x)[:200]}"}
                    else:
                        p.update(tid=t["id"], aprobar=apr); st["casos"].setdefault(caso, {}).setdefault("tickets", []).append(t["numero"])
                        if caso.startswith("P-") and not caso.endswith("-el"):
                            V.setdefault("casos_p", {}).setdefault(caso[2:], []).append(t["numero"])
                        if caso.startswith("P-") and caso.endswith("-el"):
                            V.setdefault("casos_p", {}).setdefault(caso[2:-3], []).append(t["numero"])
                        print(f"{caso}: {t['numero']} creado · {mot}"); guardar(st); continue
                st["casos"].setdefault(caso, {}).update(ok=bool(r["ok"]), nota=r.get("nota", ""))
                print(f"{caso}: {'ok' if r['ok'] else 'FALLA'} · {r.get('nota', '')}")
                avanzar(st, o, r["ok"] or r.get("opcional")); continue
            t = t_row(p["tid"])
            if t["estado"] == "pendiente_aprobacion":
                try:
                    resolver_aprobacion(u, t["id"], p.get("aprobar", True), APROB if p.get("aprobar", True) else "Rechazado por Claude (prueba del camino de rechazo)")
                except Exception as x:
                    print("  aprobación:", str(x)[:120])
                return
            if t["estado"] not in FIN_T:
                return
            if o.get("releer") and not p.get("leido"):
                if leer_nb(p) is None:
                    guardar(st); return
                p["leido"] = True
            try:
                v = ver(V, t, p)
            except Exception as x:
                v = (False, f"error al verificar: {str(x)[:200]}")
            guardar(st)
            if v is None:
                return
            ok, nota = v
            st["casos"].setdefault(caso, {}).update(ok=bool(ok), nota=f"{t['numero']} {t['estado']} · {nota}")
            print(f"{caso}: {'ok' if ok else 'FALLA'} · {t['numero']} {t['estado']} · {nota}")
            if o.get("repetir") and ok:   # pasos de limpieza: se repiten hasta que no quede nada
                st["pend"] = {}; guardar(st); continue
            avanzar(st, o, ok or o.get("opcional"))


def avanzar(st, o, ok):
    if not ok and st["fase"] == "pasos" and not o.get("limpieza"):
        st["fase"] = "limpieza"; print("  → falla: se salta a la limpieza")
    st["i"] += 1; st["pend"] = {}; guardar(st)


main()
PY
FIN=$(($(date +%s) + 3300)); ARG="reiniciar"
while [ $(date +%s) -lt $FIN ]; do
  if sudo podman exec faceops test -f /opt/faceops/app/nube_recursos.py 2>/dev/null; then
    sudo podman cp /tmp/motor-nube.py faceops:/tmp/motor-nube.py 2>/dev/null
    OUT=$(sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops timeout 300 python3 /tmp/motor-nube.py maquinas $ARG 2>&1 | grep -v "Warning\|warnings.warn")
    ARG=""
    [ -n "$OUT" ] && echo "$OUT" | cut -c1-600
    echo "$OUT" | grep -q PLAN_FIN && break
  fi
  sleep 15
done
sudo podman exec faceops cat /var/lib/faceops/prueba-nube-maquinas.json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print('ESTADO', d['fase'], 'paso', d['i'], {k: v.get('ok') for k, v in d['casos'].items()})" 2>/dev/null
