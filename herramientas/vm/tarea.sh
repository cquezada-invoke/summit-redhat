#!/bin/bash
# Certificación Núcleo · fase 3: pantallas con Playwright headless contra la URL pública (ingreso con usuario local de prueba + doble factor).
# Mide carga (TTFB y DOMContentLoaded), errores de consola, datos y móvil 390 px. Las credenciales se leen de un archivo 600 y no se imprimen.
echo "desplegado: $(sudo podman exec faceops cat /opt/faceops/app/version.json 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["commit"][:8], d["fecha"])')"
cat > /tmp/n3t.py <<'PY'
import json
from app import create_app
from app.bd import q, q1
app = create_app()
with app.app_context():
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
    eqs = q("SELECT id, nombre, energia, estado FROM equipos WHERE cliente_id=? AND COALESCE(estado,'')!='retirado'", cl["id"])
    t = q1("SELECT id, numero FROM tickets WHERE cliente_id=? AND estado='exitoso' ORDER BY id DESC LIMIT 1", cl["id"])
    print("TARGETS " + json.dumps({"n_equipos": len(q("SELECT id FROM equipos WHERE cliente_id=?", cl["id"])), "n_visibles": len(eqs),
          "encendida": 26, "apagada": next(e["id"] for e in eqs if e["nombre"] == "srv-app-01"),
          "ticket": t["id"], "ticket_num": t["numero"], "auto": 95, "n_tickets": q1("SELECT COUNT(*) n FROM tickets WHERE cliente_id=?", cl["id"])["n"],
          "lector": q1("SELECT id FROM usuarios WHERE usuario='qa.lector'")["id"]}))
PY
sudo podman cp /tmp/n3t.py faceops:/tmp/n3t.py
TG=$(sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/n3t.py 2>&1 | grep -v -i -E "warn|token|secret" | tee /tmp/n3t.out | grep '^TARGETS ' | cut -c9-); [ -z "$TG" ] && tail -5 /tmp/n3t.out; sudo podman exec faceops rm -f /tmp/n3t.py
echo "objetivos: $TG"
sudo cat /tmp/qa-nucleo.json > /tmp/qa-n3.json; chmod 600 /tmp/qa-n3.json
cat > /tmp/n3.py <<'PY'
import base64, hashlib, hmac, json, struct, sys, time, statistics, re
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright
B = "https://faceops.34.176.42.56.sslip.io:8843/faceops"
T = json.loads(sys.argv[1]); C = json.load(open("/tmp/qa-n3.json"))
def totp(sec):
    k = base64.b32decode(sec.upper() + "=" * (-len(sec) % 8)); m = hmac.new(k, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    o = m[-1] & 15; return "%06d" % ((struct.unpack(">I", m[o:o + 4])[0] & 0x7fffffff) % 1000000)
NAV = "(() => {const n = performance.getEntriesByType('navigation')[0]; return n ? {ttfb: n.responseStart - n.requestStart, dcl: n.domContentLoadedEventEnd - n.startTime, load: n.loadEventEnd - n.startTime} : null})()"
def login(ctx, usu, codigo=True):
    pg = ctx.new_page(); pg.goto(B + "/ingresar"); pg.fill("input[name=usuario]", usu); pg.fill("input[name=clave]", C[usu]["clave"])
    pg.click("button.btn"); pg.wait_for_load_state()
    paso_codigo = "/ingresar/codigo" in pg.url
    if codigo and paso_codigo:
        pg.fill("input[name=codigo]", totp(C[usu]["totp"])); pg.click("button.btn"); pg.wait_for_load_state()
    return pg, paso_codigo
def medir(pg, ruta, chequeo=None, n=3):
    errs = []; h1 = lambda m: errs.append(("console", m.text[:160])) if m.type == "error" else None; h2 = lambda e: errs.append(("pageerror", str(e)[:160]))
    pg.on("console", h1); pg.on("pageerror", h2)
    ts = []; st = None
    for i in range(n):
        r = pg.goto(B + ruta, wait_until="load"); st = r.status if r else None
        ts.append(pg.evaluate(NAV))
    pg.wait_for_timeout(600)
    pg.remove_listener("console", h1); pg.remove_listener("pageerror", h2)
    dat = ""
    if chequeo:
        try: dat = chequeo(pg)
        except Exception as x: dat = "ERROR chequeo: " + str(x)[:120]
    ttfb = statistics.median(t["ttfb"] for t in ts if t); dcl = statistics.median(t["dcl"] for t in ts if t)
    aviso = pg.locator(".aviso-aap").count()
    return {"ruta": ruta, "status": st, "ttfb_ms": round(ttfb), "dcl_ms": round(dcl), "primera_dcl_ms": round(ts[0]["dcl"]) if ts[0] else None,
            "errores": errs[:4], "datos": dat, "aviso_aap": aviso}
def movil(pg, ruta):
    pg.goto(B + ruta, wait_until="load"); pg.wait_for_timeout(300)
    return pg.evaluate("(() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bw: document.body.scrollWidth}))()")
SEL_AUT = 'a[href*="/automatizaciones/"]'
txt = lambda pg, sel="body": pg.locator(sel).first.inner_text()
def ch_inv(pg):
    filas = pg.locator("table tbody tr").count(); b = txt(pg)
    return f"filas {filas} · encendido {b.lower().count('encendid')} · apagado {b.lower().count('apagad')}"
def ch_ficha(nombre):
    return lambda pg: f"nombre visible {nombre in txt(pg)} · dueño {'Dueño' in txt(pg) or 'dueño' in txt(pg)}"
def ch_hora(pg):
    els = pg.locator("time[datetime]"); n = els.count()
    for i in range(min(n, 10)):
        dt = els.nth(i).get_attribute("datetime"); tx = els.nth(i).inner_text(); modo = els.nth(i).get_attribute("data-local") or ""
        if "T" in dt and modo in ("fecha-hora", "segundos", "hora", "hora-seg", ""):
            d = datetime.fromisoformat(dt.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Santiago"))
            return f"{n} marcas de hora · ej. UTC {dt[11:16]} → muestra «{tx.strip()[:22]}» · esperado Santiago {d:%H:%M} · ok {d.strftime('%H:%M') in tx}"
    return f"{n} marcas de hora (sin hora convertible)"
R = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 950}, timezone_id="America/Santiago", locale="es-CL")
    # doble factor: sin código no entra
    pg0, paso = login(ctx, "qa.nucleo", codigo=False)
    pg0.goto(B + "/"); R["2fa_sin_codigo"] = {"pide_codigo": paso, "url_tras_intentar_entrar": pg0.url.replace(B, "")[:60]}
    pg0.goto(B + "/ingresar/codigo"); pg0.fill("input[name=codigo]", "000000"); pg0.click("button.btn"); pg0.wait_for_load_state()
    R["2fa_codigo_malo"] = {"rechaza": "incorrecto" in txt(pg0).lower(), "url": pg0.url.replace(B, "")[:40]}
    pg0.close(); ctx.clear_cookies()
    t0 = time.time(); pg, paso = login(ctx, "qa.nucleo"); R["login"] = {"pide_codigo": paso, "url_final": pg.url.replace(B, "")[:40], "s": round(time.time() - t0, 2),
                                                                         "usuario_en_cabecera": "QA Certificación Núcleo" in txt(pg) or "qa.nucleo" in txt(pg)}
    csrf = pg.locator("input[name=_csrf]").first.get_attribute("value") if pg.locator("input[name=_csrf]").count() else None
    P = [("/", lambda pg: f"productividad {'roductividad' in txt(pg) or 'valor' in txt(pg).lower()} · riesgo en cabecera {pg.locator('header, .cabecera, .top, nav').first.inner_text().lower().count('riesgo') > 0}"),
         ("/riesgo/estado", None),
         ("/buscar?q=nodo-vault", lambda pg: f"encuentra nodo-vault {'nodo-vault' in txt(pg)}"),
         ("/automatizaciones", lambda pg: f"acciones listadas {pg.locator(SEL_AUT).count()} · bloqueadas marcadas {txt(pg).lower().count('bloquead')}"),
         (f"/automatizaciones/{T['auto']}", lambda pg: f"ficha acción «Encender» {'ncender' in txt(pg)}"),
         ("/tickets", lambda pg: f"filas {pg.locator('table tbody tr').count()} · FO-LAB {txt(pg).count('FO-LAB')}"),
         (f"/tickets/{T['ticket']}", lambda pg: f"{T['ticket_num']} visible {T['ticket_num'] in txt(pg)} · estado exitoso {'xitoso' in txt(pg)}"),
         (f"/tickets/{T['ticket']}/estado", lambda pg: txt(pg)[:80]),
         ("/inventario", ch_inv),
         (f"/inventario/{T['encendida']}", ch_ficha("nodo-vault")),
         (f"/inventario/{T['apagada']}", ch_ficha("srv-app-01")),
         (f"/inventario/{T['encendida']}/consola", lambda pg: f"terminal {pg.locator('.xterm, #terminal, canvas, iframe').count() > 0}"),
         ("/aprobaciones/trazabilidad", lambda pg: f"filas {pg.locator('table tbody tr').count()}"),
         ("/monitoreo/eventos-aap", lambda pg: f"filas {pg.locator('table tbody tr').count()}"),
         ("/cola", lambda pg: f"lanzados/recientes {txt(pg).count('Lanzado')} · latido {'latido' in txt(pg).lower() or 'proceso' in txt(pg).lower()}"),
         ("/configuracion/usuarios", lambda pg: f"qa.lector listado {'qa.lector' in txt(pg) or 'QA Lector' in txt(pg)} · grupos {txt(pg).count('Dueño')}"),
         ("/configuracion/pruebas", lambda pg: f"certificada {txt(pg).lower().count('certificada')} · sin probar {txt(pg).lower().count('sin probar')}"),
         ("/admin/catalogo", lambda pg: f"filas {pg.locator('table tbody tr').count()}"),
         ("/auditoria", lambda pg: f"ingreso de qa.nucleo registrado {'qa.nucleo' in txt(pg)}"),
         ("/cuenta/doble-factor", lambda pg: f"2FA activo {'activ' in txt(pg).lower()}"),
         ("/admin/", lambda pg: f"datos demo ofrecido {'demo' in txt(pg).lower()}"),
         ]
    for ruta, ch in P:
        try: R[ruta] = medir(pg, ruta, ch)
        except Exception as x: R[ruta] = {"error": str(x)[:200]}
    R["hora_local"] = ch_hora(pg.goto(B + "/tickets") and pg)
    # ver como otro usuario
    try:
        pg.goto(B + "/configuracion/usuarios"); cs = pg.locator("input[name=_csrf]").first.get_attribute("value")
        r = pg.request.post(B + f"/ver-como/{T['lector']}", form={"_csrf": cs}); pg.goto(B + "/")
        R["ver_como"] = {"post": r.status, "muestra_lector": "QA Lector" in txt(pg) or "qa.lector" in txt(pg) or "Viendo como" in txt(pg)}
        pg.goto(B + "/ver-como/salir"); pg.goto(B + "/"); R["ver_como"]["volvio"] = "QA Certificación Núcleo" in txt(pg)
    except Exception as x: R["ver_como"] = {"error": str(x)[:160]}
    # móvil 390 px
    m = b.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, timezone_id="America/Santiago")
    m.add_cookies(ctx.cookies()); pm = m.new_page(); R["movil"] = {}
    for ruta, _ in P:
        if ruta.endswith("/estado"): continue
        try: R["movil"][ruta] = movil(pm, ruta)
        except Exception as x: R["movil"][ruta] = str(x)[:100]
    # perfil lector: sin escritura ni administración
    lc = b.new_context(ignore_https_errors=True); pl, _ = login(lc, "qa.lector")
    rr = {}
    for ruta in ("/", "/inventario", "/configuracion/usuarios", "/admin/catalogo", "/admin/usuarios"):
        r = pl.goto(B + ruta); rr[ruta] = r.status if r else None
    cs = pl.locator("input[name=_csrf]").first.get_attribute("value") if pl.locator("input[name=_csrf]").count() else ""
    r = pl.request.post(B + f"/inventario/{T['apagada']}/ejecutar/{T['auto']}", form={"_csrf": cs or "", "motivo": "prueba de rol lector (no debe lanzar)"})
    rr["POST ejecutar como lector"] = [r.status, r.text()[:120]]
    R["lector"] = rr
    b.close()
for k, v in R.items():
    print(k, "→", json.dumps(v, ensure_ascii=False))
PY
python3 /tmp/n3.py "$TG" 2>&1 | grep -v -i -E "clave|secret|totp\"" | tail -60
rm -f /tmp/qa-n3.json /tmp/n3.py
