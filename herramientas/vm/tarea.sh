#!/bin/bash
# Certificación Núcleo · alta manual de un equipo existente (de punta a punta: bóveda + host en AAP + equipo en FaceOps) y consola SSH
# por la URL pública. El equipo de prueba (qa-alta-01, credencial ficticia) se retira al final: host de AAP, secreto y fila. Sin secretos.
echo "desplegado: $(sudo podman exec faceops cat /opt/faceops/app/version.json 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["commit"][:8], d["fecha"])')"
sudo cat /tmp/qa-nucleo.json > /tmp/qa-n6.json; chmod 600 /tmp/qa-n6.json
cat > /tmp/n6v.py <<'PY'
import json, os, sys, requests
from app import create_app
from app.bd import q1, ej, auditar
app = create_app()
with app.app_context():
    from app.aap import AAP
    from app import boveda
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); aap = AAP.de_cliente(cl)
    e = q1("SELECT id, nombre, estado, inventario, aap_host_id, ip FROM equipos WHERE cliente_id=? AND nombre='qa-alta-01'", cl["id"])
    h = (aap.get("hosts/", name="qa-alta-01").get("results") or [])
    s = boveda.leer("maquinas/qa-alta-01") or {}
    print("VERIF equipo:", {k: e[k] for k in ("id", "estado", "inventario", "aap_host_id")} if e else None,
          "· host AAP:", [(x["id"], x["summary_fields"]["inventory"]["name"], "password" in (x.get("variables") or "")) for x in h],
          "· secreto en bóveda:", sorted(s.keys()) if isinstance(s, dict) else bool(s))
    if sys.argv[1:] == ["limpiar"]:
        for x in h: aap._r("DELETE", f"hosts/{x['id']}/")
        try:
            r = requests.delete(f"{os.environ['FACEOPS_VAULT_ADDR']}/v1/faceops/metadata/maquinas/qa-alta-01", headers={"X-Vault-Token": boveda._token()}, timeout=5); bv = r.status_code
        except Exception as x: bv = type(x).__name__
        if e: ej("DELETE FROM equipos WHERE id=?", e["id"])
        auditar("Claude (certificación Núcleo)", cl["id"], "retirar equipo de prueba", "qa-alta-01", {"host_aap": len(h), "boveda": bv})
        print("LIMPIEZA host AAP borrados:", len(h), "· bóveda:", bv, "· fila FaceOps:", bool(e), "→ queda:", q1("SELECT COUNT(*) n FROM equipos WHERE nombre='qa-alta-01'")["n"])
PY
sudo podman cp /tmp/n6v.py faceops:/tmp/n6v.py
cat > /tmp/n6.py <<'PY'
import base64, hashlib, hmac, json, re, secrets, struct, time
from playwright.sync_api import sync_playwright
B0 = "http://192.168.122.1:8110"; B = B0 + "/lab"; BP = "https://faceops.34.176.42.56.sslip.io:8843/faceops"; C = json.load(open("/tmp/qa-n6.json"))
def totp(sec):
    k = base64.b32decode(sec.upper() + "=" * (-len(sec) % 8)); m = hmac.new(k, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    o = m[-1] & 15; return "%06d" % ((struct.unpack(">I", m[o:o + 4])[0] & 0x7fffffff) % 1000000)
CSRF = lambda pg: (re.search(r"_csrf'?\"?\s*[,:]\s*\"([^\"]+)\"", pg.content()) or [None, None])[1]
def login(ctx, base, lento=False, usu="qa.nucleo"):
    pg = ctx.new_page(); pg.goto(base + "/ingresar"); pg.fill("input[name=usuario]", usu); pg.fill("input[name=clave]", C[usu]["clave"]); pg.click("button.btn"); pg.wait_for_load_state()
    if lento: pg.wait_for_timeout(65000)
    pg.fill("input[name=codigo]", totp(C[usu]["totp"])); pg.click("button.btn"); pg.wait_for_load_state(); return pg
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(ignore_https_errors=True); pg = login(ctx, B0)
    pg.goto(B + "/inventario"); cs = CSRF(pg)
    t0 = time.time()
    r = pg.request.post(B + "/equipos/agregar/inventario", data=json.dumps({"_csrf": cs, "marca": "linux-ssh", "tipo": "servidor", "nombre": "qa-alta-01", "ip": "192.0.2.10",
                        "auth": "clave", "usuario": "qa", "secreto": secrets.token_urlsafe(12), "forma": "virtual", "ambiente": "desarrollo"}), headers={"Content-Type": "application/json"})
    print("ALTA", r.status, f"{time.time() - t0:.1f}s", r.text()[:200].replace("secreto", "s.").replace("clave", "c."))
    if r.status == 200:
        u = r.json()["url"]; pg.goto(B0 + u if u.startswith("/") else u); print("ficha del nuevo equipo:", pg.url.replace(B0, ""), "· nombre visible", "qa-alta-01" in pg.locator("body").inner_text())
        pg.goto(B + "/inventario?q=qa-alta-01"); print("aparece en inventario:", "qa-alta-01" in pg.locator("body").inner_text())
    r = pg.request.post(B + "/equipos/agregar/inventario", data=json.dumps({"_csrf": cs, "marca": "linux-ssh", "nombre": "qa-alta-01", "ip": "192.0.2.10", "auth": "clave", "usuario": "qa", "secreto": "x"}), headers={"Content-Type": "application/json"})
    print("REPETIR (duplicado)", r.status, r.text()[:120])
    r = pg.request.post(B + "/equipos/agregar/inventario", data=json.dumps({"_csrf": cs, "marca": "linux-ssh", "nombre": "QA mal", "ip": "192.0.2.11", "auth": "clave", "usuario": "qa", "secreto": "x"}), headers={"Content-Type": "application/json"})
    print("NOMBRE INVÁLIDO", r.status, r.text()[:120])
    lc = b.new_context(ignore_https_errors=True); pl = login(lc, B0, usu="qa.lector"); pl.goto(B + "/inventario/6"); csl = CSRF(pl)
    r = pl.request.post(B + "/inventario/6/plataforma/jboss/servicio", form={"_csrf": csl or "", "accion": "encender", "nombre": "JBoss EAP"}, headers={"X-Requested-With": "fetch"})
    print("LECTOR intenta encender JBoss:", r.status, r.text()[:80].replace(chr(10), " "))
    r = pl.request.post(B + "/equipos/agregar/inventario", data=json.dumps({"_csrf": csl, "marca": "linux-ssh", "nombre": "qa-alta-02", "ip": "192.0.2.12", "auth": "clave", "usuario": "qa", "secreto": "x"}), headers={"Content-Type": "application/json"})
    print("LECTOR intenta alta manual:", r.status, r.text()[:80])
    b.close()
PY
python3 /tmp/n6.py 2>&1 | sed -E 's/("secreto": *")[^"]*/\1***/g' | tail -20
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/n6v.py 2>&1 | grep "^VERIF"
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/n6v.py limpiar 2>&1 | grep -E "^VERIF|^LIMPIEZA"
sudo podman exec faceops rm -f /tmp/n6v.py; rm -f /tmp/qa-n6.json /tmp/n6.py
