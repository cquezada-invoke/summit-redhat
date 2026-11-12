#!/bin/bash
# Diagnóstico: qué muestra la página de ingreso pública (sin credenciales).
python3 - <<'PY'
from playwright.sync_api import sync_playwright
B = "https://faceops.34.176.42.56.sslip.io:8843/faceops"
with sync_playwright() as p:
    b = p.chromium.launch(); c = b.new_context(ignore_https_errors=True); pg = c.new_page()
    for u in ("/ingresar", "/"):
        r = pg.goto(B + u); print(u, "→", r.status if r else None, pg.url, "|", pg.title())
        print("  inputs:", [ (i.get_attribute("name"), i.get_attribute("type")) for i in pg.locator("input").all()][:8])
        print("  texto:", pg.locator("body").inner_text()[:300].replace("\n", " | "))
    b.close()
PY
curl -sk -o /dev/null -w 'curl /ingresar %{http_code} %{redirect_url}\n' https://faceops.34.176.42.56.sslip.io:8843/faceops/ingresar
