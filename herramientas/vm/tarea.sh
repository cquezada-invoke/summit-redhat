#!/bin/bash
# Certificación Núcleo · cierre: panel tras d4ccca74, lista de servidores (encendidos y apagados), consolas web de productos y evidencia de las acciones certificadas. Sin secretos.
cd ~/summit-redhat && git fetch -q origin main
for i in $(seq 1 40); do D=$(sudo podman exec faceops cat /opt/faceops/app/version.json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["commit"])'); git merge-base --is-ancestor d4ccca74 "$D" 2>/dev/null && break; sleep 20; done
echo "desplegado: ${D:0:8} · incluye d4ccca74: $(git merge-base --is-ancestor d4ccca74 "$D" 2>/dev/null && echo sí || echo no)"
cat > /tmp/n8c.py <<'PY'
from app import create_app
from app.bd import q, q1
from app.pruebas import estado
create_app()
for n in ("Infra · Encender o apagar equipo", "FaceOps · Apagar equipo", "FaceOps · Vincular máquina a la bóveda", "Plataforma · Controlar servicio", "Sistema · Quitar agente",
          "Sistema · Inventariar máquina", "Sistema · Paquetes · inventario y respaldo", "FaceOps · Vincular máquina on-premise"):
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=1 AND nombre_aap=?", n); e = estado(dict(a, cliente_id=1)); u = e.get("ultima") or {}
    print("EVID", n, "|", e["estado"], e["huella"], "|", (u.get("fecha") or "")[:10], "|", (u.get("plan") or "")[:50], "|", (u.get("tickets") or "")[:90])
PY
sudo podman cp /tmp/n8c.py faceops:/tmp/n8c.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/n8c.py 2>&1 | grep "^EVID"; sudo podman exec faceops rm -f /tmp/n8c.py
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
    for ruta in ("/", "/inventario", "/inventario?forma=virtual"):
        ts = []
        for i in range(4):
            pg.goto(B + ruta, wait_until="load"); ts.append(pg.evaluate(NAV))
        print("MEDIDA", ruta, "· dcl", [round(t["dcl"]) for t in ts], "· mediana", round(statistics.median(t["dcl"] for t in ts[1:])), "· errores", errs[:2]); errs.clear()
    body = pg.locator("body").inner_text()
    print("LISTA virtuales: nodo-vault", "nodo-vault" in body, "· nodo-jboss-qa (apagada)", "nodo-jboss-qa" in body, "· filas", pg.locator("table tbody tr").count(),
          "· marcas encendido/apagado", pg.locator("[title*='ncendid'], [title*='pagad'], .on, .off, .energia").count())
    pg.goto(B + "/inventario/26"); hs = sorted({a.get_attribute("href") for a in pg.locator("a[href*='/consola-web/']").all()})
    print("CONSOLAS WEB en ficha de nodo-vault:", hs[:4])
    for h in hs[:2]:
        errs.clear(); t0 = time.time(); r = pg.goto(B0 + h if h.startswith("/") else h, wait_until="load"); pg.wait_for_timeout(3000)
        print("  abre", h, "→", r.status if r else None, f"{time.time() - t0:.1f}s", "· título", pg.title()[:50], "· errores", errs[:2])
    b.close()
PY
python3 /tmp/n8.py 2>&1 | tail -20; rm -f /tmp/qa-n8.json /tmp/n8.py
