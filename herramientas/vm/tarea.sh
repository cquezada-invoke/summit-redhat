#!/bin/bash
# Etapa 1 · checklist de pantallas con Playwright headless (usuario de prueba qa.nucleo). Sin secretos en la salida.
echo "desplegado: $(cut -c1-8 ~/faceops-autodeploy/desplegado)"
cat > /tmp/e1pw.py <<'PWPY'
"""Checklist de pantallas de la Etapa 1 (Núcleo) con Playwright headless contra el lab. Lee ids y cookies de un archivo 0600.
Por pantalla: carga caliente (mediana de 3 recargas, DOMContentLoaded) < 500 ms, errores de consola propios de FaceOps,
móvil 390 px sin scroll horizontal y distintivo «Etapa 1» en el título. Nada secreto se imprime."""
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

D = json.load(open(sys.argv[1]))
B = D["base"]
AJENAS = ("/consola-web/", "/guacamole", "/web/", "/vault/", "/ui/")   # interfaces de terceros servidas a través de FaceOps


def propio(url):
    return url.startswith(B) and not any(x in url for x in AJENAS)


def medir(ctx, url, n=3, espera="domcontentloaded"):
    pg = ctx.new_page()
    errs, fallas = [], []
    pg.on("console", lambda m: errs.append((m.text[:160], (m.location or {}).get("url", ""))) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(("pageerror: " + str(e)[:160], url)))
    pg.on("response", lambda r: fallas.append((r.status, r.url)) if r.status >= 400 else None)
    t0 = time.time()
    r = pg.goto(url, wait_until=espera, timeout=60000)
    fria = (time.time() - t0) * 1000
    st = r.status if r else 0
    try:
        pg.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    tiempos = []
    for _ in range(n):
        pg.reload(wait_until="domcontentloaded", timeout=60000)
        tiempos.append(pg.evaluate("(()=>{const n=performance.getEntriesByType('navigation')[0];return n?n.domContentLoadedEventEnd-n.startTime:0})()"))
        try:
            pg.wait_for_load_state("networkidle", timeout=6000)
        except Exception:  # noqa: BLE001
            pass
    chip = pg.evaluate("(()=>{const c=document.querySelector('main .etapa-chip');return c?{txt:c.textContent.trim(),enH1:!!c.closest('h1,h2')}:null})()")
    titulo = pg.evaluate("(()=>{const h=document.querySelector('main h1,main h2');return h?h.textContent.trim().slice(0,60):''})()")
    aviso = pg.evaluate("!!document.querySelector('.aviso-aap')")
    horas = pg.evaluate("[...document.querySelectorAll('time[datetime][data-local]')].slice(0,40).map(t=>[t.getAttribute('datetime'),t.textContent.trim(),t.dataset.local])")
    pg.close()
    e_prop = [e for e in errs if propio(e[1]) or not e[1]]
    e_aj = [e for e in errs if e not in e_prop]
    f_prop = [f for f in fallas if propio(f[1])]
    f_aj = [f for f in fallas if not propio(f[1])]
    return {"st": st, "fria": round(fria), "caliente": round(statistics.median(tiempos)) if tiempos else None, "errores": e_prop + [(f"HTTP {s}", u) for s, u in f_prop],
            "ajenos": e_aj + [(f"HTTP {s}", u) for s, u in f_aj], "chip": chip, "titulo": titulo, "aviso": aviso, "horas": horas}


def movil(ctx_m, url):
    pg = ctx_m.new_page()
    pg.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        pg.wait_for_load_state("networkidle", timeout=6000)
    except Exception:  # noqa: BLE001
        pass
    w = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    pg.close()
    return w[0] <= w[1] + 1, w


def hora_ok(horas):
    """Las marcas <time> quedan en la hora de America/Santiago (el navegador corre en esa zona)."""
    n = ok = 0
    for iso, txt, modo in horas:
        if modo not in ("fecha-hora", "segundos", "hora", "hora-seg"):
            continue
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Santiago"))
        except Exception:  # noqa: BLE001
            continue
        n += 1
        ok += dt.strftime("%H:%M") in txt
    return n, ok


PANTALLAS = [  # (funcionalidad, ruta, quién)
    ("2 Panel · productividad", "/lab/", "n"),
    ("5 Buscador global", "/lab/buscar?q=nodo-vault", "n"),
    ("6 Catálogo de acciones", "/lab/automatizaciones", "n"),
    ("7 Ficha de la acción", f"/lab/automatizaciones/{D['auto_id']}", "n"),
    ("137 Tickets", "/lab/tickets", "n"),
    ("8 Ticket en vivo", f"/lab/tickets/{D['ticket_id']}", "n"),
    ("14 Inventario · virtuales", "/lab/inventario?forma=virtual", "n"),
    ("14 Inventario · físicos", "/lab/inventario?forma=fisico", "n"),
    ("16 Ficha · encendida", f"/lab/inventario/{D['eq_on']}", "n"),
    ("16 Ficha · apagada", f"/lab/inventario/{D['eq_off']}", "n"),
    ("138 Aprobaciones pendientes", "/lab/monitoreo/aprobaciones-pendientes", "n"),
    ("139 Trazabilidad de aprobaciones", "/lab/aprobaciones/trazabilidad", "n"),
    ("144 Eventos de AAP", "/lab/monitoreo/eventos-aap", "n"),
    ("146 Cola de acciones", "/lab/cola", "n"),
    ("147 Usuarios y perfiles", "/lab/configuracion/usuarios", "n"),
    ("148 Usuario (ver como)", f"/lab/configuracion/usuarios/{D['lector_id']}", "n"),
    ("149 Pruebas de acciones", "/lab/configuracion/pruebas", "n"),
    ("152 Auditoría", "/lab/auditoria", "n"),
    ("158 Doble factor (Mi perfil)", "/lab/cuenta/doble-factor", "n"),
    ("150 Catálogo y tarifas (admin)", "/admin/catalogo", "n"),
    ("153 Panel admin (datos demo)", "/admin/", "n"),
]

with sync_playwright() as p:
    br = p.chromium.launch()
    def contexto(ck, ancho=1440, alto=1000, tz="America/Santiago"):
        c = br.new_context(ignore_https_errors=True, viewport={"width": ancho, "height": alto}, timezone_id=tz, locale="es-CL")
        if ck:
            c.add_cookies([{"name": D["cookie_nombre"], "value": ck, "url": D["origen"]}])
        return c
    ctx = contexto(D["ck_nucleo"])
    ctx_m = contexto(D["ck_nucleo"], 390, 844)
    filas = []
    for fun, ruta, _ in PANTALLAS:
        r = medir(ctx, B + ruta)
        mv, w = movil(ctx_m, B + ruta)
        r.update(fun=fun, ruta=ruta, movil=mv, ancho=w)
        filas.append(r)
        ok = r["st"] == 200 and (r["caliente"] or 9999) < 500 and not r["errores"] and mv and r["chip"] and r["chip"]["txt"] == "Etapa 1" and r["chip"]["enH1"]
        print(f"PANTALLA {'OK ' if ok else 'MAL'} {fun} | {ruta} | {r['st']} | caliente {r['caliente']} ms (fría {r['fria']} ms) | errores propios {len(r['errores'])} | ajenos {len(r['ajenos'])} | "
              f"móvil {'ok' if mv else 'scroll ' + str(w)} | chip {r['chip']} | {r['titulo'][:40]}", flush=True)
        for e in r["errores"][:3]:
            print("   err:", e[0][:120], "@", e[1].replace(B, "")[:90])
        for e in r["ajenos"][:2]:
            print("   ajeno:", e[0][:100], "@", e[1].replace(B, "")[:90])
    # hora local: tickets y auditoría en America/Santiago
    n_h = ok_h = 0
    for r in filas:
        a, b_ = hora_ok(r["horas"])
        n_h += a; ok_h += b_
    print(f"HORA_LOCAL {ok_h}/{n_h} marcas en hora de Santiago", flush=True)
    # indicador de riesgo (JSON) y consolas (interfaces de terceros: sus errores no son de FaceOps)
    t0 = time.time(); rr = ctx.request.get(B + "/lab/riesgo/estado"); print(f"RIESGO /lab/riesgo/estado {rr.status} {round((time.time()-t0)*1000)} ms estado={rr.json().get('estado') if rr.ok else '-'}")
    for fun, ruta in (("23 Consola SSH", f"/lab/inventario/{D['eq_on']}/consola"), ("24 Consola web Vault", f"/lab/inventario/{D['eq_on']}/consola-web/vault/")):
        r = medir(ctx, B + ruta, n=1, espera="load")
        print(f"CONSOLA {fun} | {r['st']} | carga {r['fria']} ms | errores propios {len(r['errores'])} | ajenos {len(r['ajenos'])} | {r['titulo'][:40]}")
        for e in (r["errores"] + r["ajenos"])[:4]:
            print("   ", "propio" if e in r["errores"] else "ajeno", e[0][:110], "@", e[1].replace(B, "")[:90])
    # menú: puntos de etapa y filtro «Ver solo Etapa 1»
    pg = ctx.new_page(); pg.goto(B + "/lab/inventario", wait_until="networkidle")
    pts = pg.evaluate("document.querySelectorAll('.lateral .etapa-pt, .gob-menu .etapa-pt').length")
    vis0 = pg.evaluate("[...document.querySelectorAll('.lateral nav .nav-padre, .lateral nav > a')].filter(e=>e.offsetParent).map(e=>e.textContent.trim())")
    pg.check("#etapa-solo"); pg.wait_for_timeout(200)
    vis1 = pg.evaluate("[...document.querySelectorAll('.lateral nav .nav-padre, .lateral nav > a')].filter(e=>e.offsetParent).map(e=>e.textContent.trim())")
    pg.goto(B + "/lab/redes", wait_until="domcontentloaded"); pg.wait_for_timeout(300)
    aten = pg.evaluate("document.querySelector('main').classList.contains('pantalla-fuera-etapa')")
    pg.goto(B + "/lab/inventario", wait_until="domcontentloaded"); pg.wait_for_timeout(300)
    aten_inv = pg.evaluate("document.querySelector('main').classList.contains('pantalla-fuera-etapa')")
    pg.uncheck("#etapa-solo"); pg.close()
    print(f"MENU puntos {pts} · visibles {len(vis0)} → con filtro {len(vis1)} {vis1[:6]} · /lab/redes atenuada {aten} · /lab/inventario atenuada {aten_inv}")
    # lector: acciones, alta manual, cola y administración prohibidas
    ctx_l = contexto(D["ck_lector"])
    pl = ctx_l.new_page()
    res = {}
    for ruta in ("/lab/inventario", f"/lab/inventario/{D['eq_on']}", "/lab/cola", "/lab/configuracion/usuarios", "/admin/catalogo"):
        r = pl.goto(B + ruta, wait_until="domcontentloaded"); res[ruta] = r.status if r else 0
    r = ctx_l.request.post(B + "/lab/equipos/agregar/inventario", data=json.dumps({"_csrf": "x"}), headers={"Content-Type": "application/json"})
    res["POST alta manual"] = r.status
    print("LECTOR", res)
    # ingreso con doble factor por la URL pública (usuario de prueba qa.nucleo)
    if D.get("clave"):
        import base64, hashlib, hmac, struct
        def totp(s):
            k = base64.b32decode(s + "=" * (-len(s) % 8)); h = hmac.new(k, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest(); o = h[-1] & 15
            return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)
        c2 = contexto(None); pg = c2.new_page()
        t0 = time.time(); pg.goto(D["publica"] + "/ingresar", wait_until="domcontentloaded"); t_ing = round((time.time() - t0) * 1000)
        chip_ing = pg.evaluate("!!document.querySelector('.etapa-chip')")
        pg.fill("input[name=usuario]", "qa.nucleo"); pg.fill("input[name=clave]", D["clave"]); pg.click("button[type=submit], form button")
        pg.wait_for_load_state("domcontentloaded"); en_codigo = "/ingresar/codigo" in pg.url
        pg.goto(D["publica"] + "/lab/", wait_until="domcontentloaded"); sin_codigo_fuera = "/ingresar" in pg.url
        pg.goto(D["publica"] + "/ingresar/codigo", wait_until="domcontentloaded")
        pg.fill("input[name=codigo]", "000000" if totp(D["totp"]) != "000000" else "111111"); pg.click("form button"); pg.wait_for_load_state("domcontentloaded")
        rechaza = "/ingresar" in pg.url
        pg.goto(D["publica"] + "/ingresar/codigo", wait_until="domcontentloaded")
        if "/ingresar/codigo" not in pg.url:   # el código erróneo pudo devolver al inicio: se ingresa de nuevo
            pg.goto(D["publica"] + "/ingresar"); pg.fill("input[name=usuario]", "qa.nucleo"); pg.fill("input[name=clave]", D["clave"]); pg.click("button[type=submit], form button"); pg.wait_for_load_state("domcontentloaded")
        pg.fill("input[name=codigo]", totp(D["totp"])); pg.click("form button"); pg.wait_for_load_state("domcontentloaded")
        entra = "/ingresar" not in pg.url
        print(f"DOBLE_FACTOR /ingresar {t_ing} ms chip={chip_ing} · pide código {en_codigo} · sin código no entra {sin_codigo_fuera} · código erróneo rechazado {rechaza} · código correcto entra {entra}")
        c2.close()
    br.close()

PWPY
cat > /tmp/e1prep.py <<'PY'
import json, os, secrets
from app import create_app
app = create_app()
from app.bd import q1, ej
from werkzeug.security import generate_password_hash
from app.doble_factor import nuevo_secreto
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); cid = cl["id"]
un = q1("SELECT * FROM usuarios WHERE usuario='qa.nucleo'"); ul = q1("SELECT * FROM usuarios WHERE usuario='qa.lector'")
ser = app.session_interface.get_signing_serializer(app)
eon = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre='nodo-vault'", cid)["id"]
eoff = (q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre='nodo-jboss-qa' AND estado!='retirado'", cid) or q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre='nodo-jboss'", cid))["id"]
tk = q1("SELECT id FROM tickets WHERE cliente_id=? AND estado='exitoso' ORDER BY id DESC LIMIT 1", cid)["id"]
au = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Infra · Encender o apagar equipo'", cid)["id"]
clave = secrets.token_urlsafe(16) + "aA9!"
sec = un["totp_secreto"] if un["totp_activo"] and un["totp_secreto"] else nuevo_secreto()
ej("UPDATE usuarios SET clave_hash=?, totp_secreto=?, totp_activo=1 WHERE id=?", generate_password_hash(clave), sec, un["id"])
d = {"base": os.environ.get("E1_BASE"), "publica": os.environ.get("E1_PUB"), "origen": os.environ.get("E1_ORIGEN"), "cookie_nombre": app.config.get("SESSION_COOKIE_NAME") or "session",
     "ck_nucleo": ser.dumps({"uid": un["id"], "csrf": "x", "cliente_id": cid}), "ck_lector": ser.dumps({"uid": ul["id"], "csrf": "x", "cliente_id": cid}),
     "eq_on": eon, "eq_off": eoff, "ticket_id": tk, "auto_id": au, "lector_id": ul["id"], "clave": clave, "totp": sec}
fd = os.open("/tmp/e1pw.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, json.dumps(d).encode()); os.close(fd)
print("PREP ok", eon, eoff, tk, au)
PY
B=https://127.0.0.1:8843/faceops
sudo podman cp /tmp/e1prep.py faceops:/tmp/e1prep.py
sudo podman exec -e E1_BASE=$B -e E1_PUB=$B -e E1_ORIGEN=https://127.0.0.1:8843 -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/e1prep.py 2>&1 | grep PREP
sudo podman cp faceops:/tmp/e1pw.json /tmp/e1pw.json; sudo chown $(id -u) /tmp/e1pw.json; chmod 600 /tmp/e1pw.json; sudo podman exec faceops rm -f /tmp/e1pw.json /tmp/e1prep.py
curl -sk -o /dev/null -w "ingresar %{http_code}\n" $B/ingresar
timeout 1500 ~/pw/bin/python /tmp/e1pw.py /tmp/e1pw.json 2>&1 | grep -v -i -E "token|password|secret|clave" | tail -120
rm -f /tmp/e1pw.json /tmp/e1pw.py
