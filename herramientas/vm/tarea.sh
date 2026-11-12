#!/bin/bash
# Medición (solo lectura): tiempo de /mapa del segmento «App de pagos» y de /mapa/datos, con perfil. Sin secretos.
cat > /tmp/p3101.py <<'PY'
import time, cProfile, pstats, io
from app import create_app
app = create_app()
from app.bd import q1
SEG = "App de pagos"
def t(c, url, qs, n=2, prof=False):
    out = []
    for i in range(n):
        h = {"X-FaceOps-Perfil": "1"} if prof and i == n - 1 else {}
        t0 = time.time(); r = c.get(url, query_string=qs, headers=h); dt = time.time() - t0
        out.append(f"{dt:.2f}s[{r.status_code},{len(r.data)//1024}KB]")
        if h:
            print("   PERFIL", url, (r.headers.get("X-FaceOps-Perfil") or "")[:1800])
    print(url, qs.get("segmento") or qs.get("valor") or "-", " ".join(out))
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    t(c, "/faceops/mapa", {"vista": "aplicaciones"}, 2)
    t(c, "/faceops/mapa", {"vista": "aplicaciones", "segmento": SEG}, 3, prof=True)
    t(c, "/faceops/mapa/datos", {"vista": "negocio", "nivel": "segmento", "valor": SEG, "ambiente": "produccion"}, 3, prof=True)
    t(c, "/faceops/mapa/datos", {"vista": "negocio", "nivel": "global", "valor": ""}, 2)
    # funciones sueltas dentro de una petición
    from app import segmento_ops as so
    from flask import g
    with app.test_request_context("/faceops/mapa"):
        app.preprocess_request()
        with c.session_transaction() as s: pass
    import flask
    with app.test_request_context("/faceops/mapa?vista=aplicaciones"):
        flask.session["uid"] = 1
        app.preprocess_request()
        seg = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], SEG)
        for nombre, f in (("alertas_segmento", so.alertas_segmento), ("habilidades_segmento", so.habilidades_segmento)):
            for i in range(2):
                pr = cProfile.Profile(); t0 = time.time(); pr.enable(); f(seg); pr.disable()
                print(nombre, f"{time.time()-t0:.2f}s")
            s_ = io.StringIO(); pstats.Stats(pr, stream=s_).sort_stats("cumulative").print_stats(18); print("\n".join(l[:170] for l in s_.getvalue().splitlines()[6:32]))
        from app.vistas import _accesos_apps
        pr = cProfile.Profile(); t0 = time.time(); pr.enable(); _accesos_apps([{"nombre": SEG, "ambientes": []}]); pr.disable()
        print("_accesos_apps", f"{time.time()-t0:.2f}s")
        s_ = io.StringIO(); pstats.Stats(pr, stream=s_).sort_stats("cumulative").print_stats(15); print("\n".join(l[:170] for l in s_.getvalue().splitlines()[6:28]))
PY
sudo podman cp /tmp/p3101.py faceops:/tmp/p3101.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/p3101.py 2>&1 | grep -v -i -E "token|password|secret|clave" | tail -120; sudo podman exec faceops rm -f /tmp/p3101.py
