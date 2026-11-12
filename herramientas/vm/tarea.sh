#!/bin/bash
# Prueba previa (sin desplegar): copia de /opt/faceops con el arreglo del candado del mapa, medida en frío y con caché vencida. Sin secretos.
sudo podman exec faceops bash -c 'rm -rf /tmp/fo3105 && cp -r /opt/faceops /tmp/fo3105'
cat > /tmp/parche3105.py <<'PY0'
p='/tmp/fo3105/app/certificados_panel.py'; s=open(p).read()
s=s.replace("\n\ndef buscar_serie", "\n\n"+'_en_curso = {}\n\n\ndef leer_sin_esperar(cliente, equipos, espera=0.8):\n    """Para vistas que no deben bloquearse (el mapa): la lectura vigente si está en caché; si venció, la renueva en segundo\n    plano (una sola a la vez por cliente) y entrega la anterior sin esperar; sin lectura previa espera a lo más `espera` s\n    y, si no alcanza, devuelve None (sin lectura)."""\n    import threading\n    k = cliente["id"]\n    c = _cache.get(k)\n    if c and time.time() - c[0] < 60:\n        return c[1]\n    th = _en_curso.get(k)\n    if not th or not th.is_alive():\n        def _renovar(cl, eqs):\n            try:\n                leer(cl, eqs)\n            except Exception:  # noqa: BLE001 - AAP caída o lenta: queda la lectura anterior\n                pass\n        th = threading.Thread(target=_renovar, args=(dict(cliente), list(equipos)), daemon=True, name=f"certificados-{k}")\n        _en_curso[k] = th\n        th.start()\n    if not c:\n        th.join(espera)\n        c = _cache.get(k)\n    return c[1] if c else None\n\n\n'+"\ndef buscar_serie",1); open(p,'w').write(s)
p='/tmp/fo3105/app/topologia.py'; s=open(p).read()
a='        from .certificados_panel import de_equipo, leer\n        r = leer(cliente, eqs)\n        return " 🔒CA interna" if de_equipo(r, f["hacia"]) else " 🔒"'
assert a in s, "ancla topologia"
s=s.replace(a,'        from .certificados_panel import de_equipo, leer_sin_esperar\n        r = leer_sin_esperar(cliente, eqs)\n        return " 🔒CA interna" if r and de_equipo(r, f["hacia"]) else " 🔒"'); open(p,'w').write(s)
print("parche ok")
PY0
sudo podman cp /tmp/parche3105.py faceops:/tmp/parche3105.py; sudo podman exec faceops python3 /tmp/parche3105.py
cat > /tmp/p3105.py <<'PY'
import time, cProfile, pstats, io
from app import create_app
app = create_app()
import app.certificados_panel as CP
SEG = "App de pagos"
DAT = {"vista": "negocio", "nivel": "segmento", "valor": SEG, "ambiente": "produccion"}
with app.test_client() as c:
    with c.session_transaction() as s: s["uid"] = 1; s["csrf"] = "x"
    def m(et):
        t0 = time.time(); r = c.get("/lab/mapa/datos", query_string=DAT); d = time.time() - t0
        cand = r.get_data(as_text=True).count("CA interna")
        print(f"{et}: {d:.2f}s [{r.status_code}] candados_CA={cand}")
    t0 = time.time(); r = c.get("/lab/mapa", query_string={"vista": "aplicaciones", "segmento": SEG}); print(f"pagina frio {time.time()-t0:.2f}s [{r.status_code}]")
    m("datos frio (sin cache de certificados)")
    time.sleep(6); m("datos tras renovar en 2o plano")
    m("datos con cache")
    for k in list(CP._cache): CP._cache[k] = (CP._cache[k][0] - 120, CP._cache[k][1])
    m("datos con cache vencida (entrega la anterior)")
    time.sleep(6); m("datos tras renovar")
    t0 = time.time(); r = c.get("/lab/mapa", query_string={"vista": "aplicaciones", "segmento": SEG}); print(f"pagina tibia {time.time()-t0:.2f}s [{r.status_code}]")
PY
sudo podman cp /tmp/p3105.py faceops:/tmp/p3105.py; sudo podman exec -w /tmp/fo3105 -e PYTHONPATH=/tmp/fo3105 -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3105.py 2>&1 | grep -v -i -E "token|password|secret|clave|warn" | tail -30
sudo podman exec faceops rm -rf /tmp/p3105.py /tmp/parche3105.py /tmp/fo3105
