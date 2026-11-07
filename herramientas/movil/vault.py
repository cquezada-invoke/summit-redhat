"""Abre la consola de Vault de un equipo a través de FaceOps y reporta qué carga (uso: python3 vault.py <cookie> <eid>)."""
import sys
from playwright.sync_api import sync_playwright
BASE = "https://faceops.34.176.42.56.sslip.io:8843/faceops"
with sync_playwright() as p:
    b = p.chromium.launch(args=["--ignore-certificate-errors"])
    c = b.new_context(viewport={"width": 1300, "height": 900}, ignore_https_errors=True)
    c.add_cookies([{"name": "session", "value": sys.argv[1], "url": BASE + "/"}])
    pg = c.new_page(); malas = []
    pg.on("response", lambda r: malas.append(f"{r.status} {r.url[:140]}") if r.status >= 400 else None)
    pg.on("pageerror", lambda e: malas.append("JS " + str(e)[:160]))
    pg.goto(f"{BASE}/inventario/{sys.argv[2]}/consola-web/vault/", wait_until="networkidle", timeout=60000); pg.wait_for_timeout(3000)
    print("URL", pg.url); print("TEXTO", pg.inner_text("body")[:400].replace("\n", " | "))
    for m in malas[:15]: print("  ", m)
    b.close()
