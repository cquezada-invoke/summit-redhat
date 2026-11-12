#!/bin/bash
# Cola de acciones: espera el despliegue, mide crear/aprobar, prueba AAP caído (encola) → vuelve (lanza) y corre los planes
# «Plataforma · Medir salud» y «Plataforma · Programar medición». Sin secretos en la salida.
OBJ=9ac33429
cd ~/summit-redhat && git fetch -q origin main
for i in $(seq 1 90); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); [ -n "$D" ] && git merge-base --is-ancestor $OBJ $D 2>/dev/null && break; sleep 20; done
echo "desplegado: ${D:0:9} · $(cut -c1-140 ~/faceops-autodeploy/estado)"
sleep 20
echo "== salud previa"; sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>/dev/null <<'Q' | tail -2
from app import create_app
create_app()
from app.bd import ajuste, q1
print("cola_aap:", ajuste("cola_aap_1")); print("pendientes en cola:", q1("SELECT COUNT(*) n FROM cola_acciones WHERE estado IN ('pendiente','en_curso')")["n"])
Q
echo "== proceso de la cola"; sudo podman exec faceops sh -c 'for p in /proc/[0-9]*; do c=$(tr "\0" " " < $p/cmdline 2>/dev/null); case "$c" in *app.cola*) echo "pid ${p#/proc/} $c";; esac; done'
echo "== camino anterior (sin cola)"
cat > /tmp/cola_antes.py <<'PY'
import time
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app import operacion, cola
    from app.aap import AAP
    operacion.ejecutar = lambda tid, sec=None, apr=False: operacion.lanzar_ahora(tid, sec, apr)   # camino anterior
    def encolar_sincrono(tid, tipo="lanzar", datos=None, secretos=None, usuario=""):
        if tipo == "aprobar":
            try: AAP.de_cliente(cl).responder_aprobacion(datos["aprobacion"], bool(datos.get("aprobar")))
            except Exception as x: print("resp", str(x)[:80])
    cola.encolar = encolar_sincrono
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); u = q1("SELECT * FROM usuarios WHERE id=1")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], "Plataforma · Medir salud")
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='nodo-tomcat'", cl["id"])
    for n in range(1):
        t0 = time.time()
        t = operacion.solicitar_en_equipo(u, cl, a, dict(e), {"pm_plataforma": "redis"}, "Medición de tiempos: camino anterior sin cola", None, prueba=True)
        print(f"ANTES CREAR {(time.time()-t0)*1000:.0f} ms · {t['numero']} job={t['aap_job_id']}", flush=True)
        fin = time.time() + 400; da = None
        while time.time() < fin:
            time.sleep(3)
            try: operacion.actualizar_tickets()
            except Exception: pass
            x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
            if x["estado"] == "pendiente_aprobacion" and da is None:
                t1 = time.time(); operacion.resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)"); da = time.time() - t1
                print(f"ANTES APROBAR {da*1000:.0f} ms", flush=True)
            if x["estado"] in ("exitoso", "fallido", "rechazado", "vencido", "cancelado"): break
        print("ANTES FINAL", x["numero"], x["estado"], flush=True)
PY
sudo podman cp /tmp/cola_antes.py faceops:/tmp/cola_antes.py
timeout 1200 sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/cola_antes.py 2>&1 | grep -E "^(ANTES|resp)|Traceback|Error" | grep -v -i -E "token|authorization|password"
sudo podman exec faceops rm -f /tmp/cola_antes.py

echo "== con cola"
cat > /tmp/cola_t.py <<'PY'
import time, json, sys
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1, ajuste
    from app.operacion import solicitar_en_equipo, resolver_aprobacion, actualizar_tickets
    from app.cola import info_ticket
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); u = q1("SELECT * FROM usuarios WHERE id=1")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], "Plataforma · Medir salud")
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='nodo-tomcat'", cl["id"])
    def correr(etq, caido):
        if caido: ajuste("cola_prueba_aap_caido_hasta", str(time.time() + 600))
        t0 = time.time()
        t = solicitar_en_equipo(u, cl, a, dict(e), {"pm_plataforma": "redis"}, f"Prueba cola de acciones ({etq})", None, prueba=True)
        print(f"{etq} CREAR {(time.time()-t0)*1000:.0f} ms · {t['numero']} estado={t['estado']} job={t['aap_job_id']}", flush=True)
        if caido:
            time.sleep(25)
            x = q1("SELECT * FROM tickets WHERE id=?", t["id"]); ci = info_ticket(t["id"]) or {}
            print(f"{etq} CON AAP CAIDO tras 25 s: ticket={x['estado']} job={x['aap_job_id']} · cola: {ci.get('texto')}", flush=True)
            ajuste("cola_prueba_aap_caido_hasta", "0"); tv = time.time()
            for _ in range(90):
                time.sleep(2); x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
                if x["aap_job_id"]: break
            print(f"{etq} AAP VUELVE: lanzado en {time.time()-tv:.0f} s · job={x['aap_job_id']} · cola: {(info_ticket(t['id']) or {}).get('texto')}", flush=True)
        fin = time.time() + 600; da = None
        while time.time() < fin:
            time.sleep(3)
            try: actualizar_tickets()
            except Exception: pass
            x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
            if x["estado"] == "pendiente_aprobacion" and da is None:
                t1 = time.time(); resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)"); da = time.time() - t1
                print(f"{etq} APROBAR {da*1000:.0f} ms", flush=True)
            if x["estado"] in ("exitoso", "fallido", "rechazado", "vencido", "cancelado"): break
        x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
        print(f"{etq} FINAL {x['numero']} {x['estado']} job={x['aap_job_id']}", flush=True)
    correr("NORMAL", False)
    correr("CAIDO", True)
PY
sudo podman cp /tmp/cola_t.py faceops:/tmp/cola_t.py
timeout 1800 sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/cola_t.py 2>&1 | grep -E "^(NORMAL|CAIDO)|Traceback|Error" | grep -v -i -E "token|authorization|password" | tee /tmp/cola_t.out
sudo podman exec faceops rm -f /tmp/cola_t.py
echo "== HTTP: la página de la cola y el estado de un ticket"
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>/dev/null <<'Q' | tail -3
import time
from app import create_app
app = create_app()
with app.test_client() as c:
    with c.session_transaction() as s: s["uid"] = 1; s["csrf"] = "x"
    t0 = time.time(); r = c.get("/lab/cola"); print("cola", r.status_code, f"{(time.time()-t0)*1000:.0f} ms", "Pendientes y en curso" in r.get_data(as_text=True))
Q
echo "== log del proceso de la cola"; sudo podman logs --since 40m faceops 2>&1 | grep -E "^cola" | tail -12
echo "== planes de prueba"
if ! grep -q "NORMAL FINAL .* exitoso" /tmp/cola_t.out || ! grep -q "CAIDO FINAL .* exitoso" /tmp/cola_t.out; then echo "PLANES OMITIDOS: el entorno no terminó bien los tickets de control (no se tocan certificaciones)"; exit 0; fi
echo "-- OpenShift · Relevamiento de imágenes"; bash ~/summit-redhat/herramientas/pruebas/p-imagenes.sh 2>&1 | grep -E "^(CASO|CERT|en uso|antiguas)" | grep -v -i -E "token|authorization|password"
sed -n '1,16p' ~/summit-redhat/herramientas/pruebas/p-varios.sh > /tmp/p-cv.sh
echo "-- Ciclo de vida · Evaluar destino"; bash /tmp/p-cv.sh 2>&1 | grep -E "^(CASO|CERT|C[0-9]|energía)" | grep -v -i -E "token|authorization|password"; rm -f /tmp/p-cv.sh
