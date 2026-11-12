#!/bin/bash
# Certificación Núcleo · cierre: panel tras d4ccca74, lista de servidores (encendidos y apagados), consolas web de productos y evidencia de las acciones certificadas. Sin secretos.
sudo cat /tmp/qa-nucleo.json > /tmp/qa-n8.json; chmod 600 /tmp/qa-n8.json
cat > /tmp/n8.py <<'PY'
import base64, hashlib, hmac, json, statistics, struct, time
from playwright.sync_api import sync_playwright
B0 = "http://192.168.122.1:8110"; B = B0 + "/lab"; C = json.load(open("/tmp/qa-n8.json"))
def totp(sec):
    k = base64.b32decode(sec.upper() + "=" * (-len(sec) % 8)); m = hmac.new(k, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    o = m[-1] & 15; return "%06d" % ((struct.unpack(">I", m[o:o + 4])[0] & 0x7fffffff) % 1000000)
NAV = "(() => {const n = performance.getEntriesByType('navigation')[0]; return {ttfb: n.responseStart - n.requestStart, dcl: n.domContentLoadedEventEnd - n.startTime}})()"
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 950}); pg = ctx.new_page()
    pg.goto(B0 + "/ingresar"); pg.fill("input[name=usuario]", "qa.nucleo"); pg.fill("input[name=clave]", C["qa.nucleo"]["clave"]); pg.click("button.btn"); pg.wait_for_load_state()
    pg.fill("input[name=codigo]", totp(C["qa.nucleo"]["totp"])); pg.click("button.btn"); pg.wait_for_load_state()
    errs = []; pg.on("console", lambda m: errs.append(m.text[:100]) if m.type == "error" else None)
    for q_ in ("nodo-vault", "nodo-jboss-qa", "dc1-ad01"):
        for ruta in (f"/inventario?q={q_}", f"/inventario?forma=virtual&q={q_}", f"/inventario?forma=fisico&q={q_}"):
            pg.goto(B + ruta); t = pg.locator("body").inner_text()
            filas = [r.inner_text().replace(chr(10), " ")[:90] for r in pg.locator("table tbody tr").all() if q_ in r.inner_text()]
            print("BUSCA", ruta, "· en página", q_ in t, "· fila:", filas[:1])
    b.close()
PY
python3 /tmp/n8.py 2>&1 | tail -20; rm -f /tmp/qa-n8.json /tmp/n8.py
