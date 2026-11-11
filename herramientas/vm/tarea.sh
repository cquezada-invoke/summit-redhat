cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-60
python3 faceops/aap/configurar.py >/dev/null 2>&1; echo "configurar: $?"
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 15
for i in $(seq 1 20); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done
EX="sudo podman exec -i -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3"
cat > /tmp/kc-caso.py <<'PYC'
import json, sys, time
caso = json.loads(sys.argv[1])
from app import create_app
app = create_app()
with app.app_context():
    from app.aap import AAP
    from app.bd import q1
    from app.operacion import actualizar_tickets, resolver_aprobacion, solicitar_en_equipo
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); u = q1("SELECT * FROM usuarios WHERE id=1"); aap = AAP.de_cliente(cl)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], caso["auto"])
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cl["id"], caso["equipo"])
    out = {"caso": caso["caso"]}
    try:
        t = solicitar_en_equipo(u, cl, a, dict(e), dict(caso["params"]), f"Plan de prueba {caso['caso']} de «{caso['auto']}» (regla de prueba)", None, prueba=True)
    except Exception as x:
        print("RES", json.dumps(dict(out, estado="rechazado", error=str(x)[:300]), ensure_ascii=False)); sys.exit(0)
    out["ticket"] = t["numero"]; fin = time.time() + caso.get("min", 8) * 60
    while time.time() < fin:
        time.sleep(8)
        try: actualizar_tickets()
        except Exception: pass
        x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
        if x["estado"] == "pendiente_aprobacion":
            try: resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)")
            except Exception as er: out["aprob"] = str(er)[:120]
        if x["estado"] in ("exitoso", "fallido", "rechazado", "cancelado", "vencido", "error"): break
    x = q1("SELECT * FROM tickets WHERE id=?", t["id"]); out["estado"] = x["estado"]
    try: out["stats"] = (aap.get(f"jobs/{x['aap_job_id']}/").get("artifacts") or {}).get("faceops_servicio") if x.get("aap_job_id") else None
    except Exception: out["stats"] = None
    if x["estado"] != "exitoso":
        r = (x["resultado"] or ""); i = r.find("fatal:"); out["fallo"] = r[i:i + 260] if i >= 0 else r[-260:]
    print("RES", json.dumps(out, ensure_ascii=False, default=str), flush=True)

PYC
sudo podman cp /tmp/kc-caso.py faceops:/tmp/kc-caso.py
cat > /tmp/kc-shot.py <<'PYS'
import base64, sys
from playwright.sync_api import sync_playwright
BASE = "https://faceops.34.176.42.56.sslip.io:8843/faceops"
ck = open("/tmp/kc-ck").read().strip()
with sync_playwright() as p:
    b = p.chromium.launch(args=["--ignore-certificate-errors"])
    c = b.new_context(viewport={"width": 1440, "height": 1000}, ignore_https_errors=True)
    c.add_cookies([{"name": "session", "value": ck, "url": BASE + "/"}])
    pg = c.new_page()
    r = pg.goto(BASE + "/lab/inventario/6", wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(1200)
    info = pg.evaluate("""() => { const out = [];
      document.querySelectorAll('.comp').forEach(cd => { const b = cd.querySelector('.comp-cab b'); if (!b || !/Keycloak|JBoss/.test(b.textContent)) return;
        const R = cd.getBoundingClientRect(); const fuera = [];
        cd.querySelectorAll('*').forEach(el => { const r = el.getBoundingClientRect(); if (r.width && (r.right > R.right + 1 || r.left < R.left - 1)) fuera.push(el.tagName + '.' + (el.className||'')); });
        const rq = cd.querySelector('.req-ram'); const et = cd.querySelector('.caja-energia .et');
        out.push({n: b.textContent.trim(), ancho: Math.round(R.width), fuera: fuera.slice(0, 5), req: rq ? rq.textContent.trim() : null, estado: et ? et.textContent.trim() : null,
                  btn_enc: (cd.querySelector('.ico-en.encender') || {}).title || null}); });
      const m = [...document.querySelectorAll('.disco-clic')].map(x => (x.disabled ? 'deshabilitado: ' : 'activo: ') + x.title);
      const mem = (document.querySelector('.uso-i:nth-child(2) .uso-t span') || {}).textContent;
      return {cards: out, lapices: m, memoria: mem}; }""")
    print("SHOT", r.status, info)
    el = pg.locator(".comp", has=pg.locator(".comp-cab b", has_text="Keycloak")).first
    el.scroll_into_view_if_needed()
    png = el.screenshot()
    sys.stdout.write("PNG " + sys.argv[1] + " " + base64.b64encode(png).decode() + "\n")
    b.close()

PYS
cat > /tmp/kc-consola.py <<'PYK'
import sys
from playwright.sync_api import sync_playwright
BASE = "https://faceops.34.176.42.56.sslip.io:8843/faceops"
nm, ck = open("/tmp/kc-ck").read().strip().split("\t")
with sync_playwright() as p:
    b = p.chromium.launch(args=["--ignore-certificate-errors"])
    c = b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    c.add_cookies([{"name": nm, "value": ck, "url": BASE + "/"}])
    pg = c.new_page(); errs = []
    pg.on("response", lambda r: errs.append(f"{r.status} {r.url[:110]}") if r.status >= 400 else None)
    r = pg.goto(BASE + "/lab/inventario/6/consola-web/keycloak/", wait_until="networkidle", timeout=90000)
    try:
        pg.wait_for_selector("text=/Welcome to|Realm settings|Clients|Manage realms|master/i", timeout=45000)
        ok = True
    except Exception:
        ok = False
    pg.wait_for_timeout(2500)
    txt = pg.inner_text("body")[:400].replace("\n", " | ")
    print("CONSOLA", "UI_ADMIN_OK" if ok else "UI_ADMIN_NO", "| url:", pg.url.split("?")[0][:140], "| login_form:", pg.locator("#kc-form-login").count(), "| texto:", txt[:300])
    print("ERRORES", errs[:8])
    sys.stdout.write("PNG consola " + __import__("base64").b64encode(pg.screenshot()).decode() + "\n")
    b.close()

PYK
galleta() { $EX - <<'PY' 2>/dev/null > /dev/null
from app import create_app
from app.bd import q1
a = create_app(); u = q1("SELECT * FROM usuarios WHERE id=1"); c = a.test_client()
with c.session_transaction() as s: s["uid"] = u["id"]; s["csrf"] = "kc-shot"
n_ = a.config.get("SESSION_COOKIE_NAME", "session")
ck_ = c.get_cookie(n_) if hasattr(c, "get_cookie") else None
v_ = ck_.value if ck_ else next(x.value for x in c.cookie_jar if x.name == n_)
open("/tmp/kc-ck", "w").write(n_ + "\t" + v_)
PY
sudo podman cp faceops:/tmp/kc-ck /tmp/kc-ck && sudo chown $(id -u) /tmp/kc-ck; sudo podman exec faceops rm -f /tmp/kc-ck; }
shot() { galleta; python3 /tmp/kc-shot.py "$1" 2>&1 | grep -E "^(SHOT|PNG|SIM)" | cut -c1-200000; rm -f /tmp/kc-ck; }
consola() { galleta; timeout 200 python3 /tmp/kc-consola.py 2>&1 | grep -E "^(CONSOLA|ERRORES|PNG)" | cut -c1-200000; rm -f /tmp/kc-ck; }
caso() { for i in $(seq 1 30); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done; sudo podman cp /tmp/kc-caso.py faceops:/tmp/kc-caso.py; $EX /tmp/kc-caso.py "$1" > /tmp/kc-res.txt 2>&1; if grep -q '^RES' /tmp/kc-res.txt; then grep '^RES' /tmp/kc-res.txt | cut -c1-900; else echo "SIN RES:"; grep -v -i "warn\|password\|secret\|token" /tmp/kc-res.txt | tail -4 | cut -c1-300; fi; }
kc() { echo "  kc8180=$(curl -s -o /dev/null -m 8 -w '%{http_code}' http://192.168.122.15:8180/sso/) app=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://192.168.122.15:8080/app-jboss/salud) lb=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://localhost:8090/app-jboss/salud)"; }
espera_kc() { for i in $(seq 1 30); do [ "$(curl -s -o /dev/null -m 8 -w '%{http_code}' http://192.168.122.15:8180/sso/)" = 302 ] && break; sleep 10; done; sleep 5; }
S="ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10 -i $HOME/.ssh/id_demo ansible@192.168.122.15"
ver() { $S 'sudo -n bash -c "ls /etc/containers/systemd/ /var/lib/keycloak/deshabilitado/ 2>&1 | tr \"\n\" \" \"; echo; grep -hE \"PublishPort|KC_HOSTNAME=\" /etc/containers/systemd/keycloak.container /var/lib/keycloak/deshabilitado/keycloak.container 2>/dev/null | tr \"\n\" \" \"; echo; systemctl is-active keycloak redis eap8-standalone | tr \"\n\" \" \"; echo"' 2>&1 | sed 's/^/  vm: /'; }
URLC="https://faceops.34.176.42.56.sslip.io:8843/faceops/lab/inventario/6/consola-web/keycloak"
echo "URL consola: $URLC"
A='"auto": "Plataforma · Controlar servicio", "equipo": "nodo-jboss"'; U='"keycloak", "rhbk"'
P="\"sp_unidades\": [$U], \"sp_plataforma\": \"Keycloak\", \"sp_puerto\": 8180, \"sp_url_consola\": \"$URLC\""
ver; kc
echo C1; caso "{$A, \"params\": {\"sp_unidades\": [$U], \"sp_accion\": \"deshabilitar\", \"sp_plataforma\": \"Keycloak\"}, \"caso\": \"C1\", \"min\": 8}" | tee /tmp/kc-r1; ver; kc
echo C2; caso "{$A, \"params\": {$P, \"sp_accion\": \"encender\"}, \"caso\": \"C2\", \"min\": 10}" | tee /tmp/kc-r2; espera_kc; ver; kc
$EX - <<'PY' 2>&1 | grep -v -i warn | tail -1
from app import create_app
from app.boveda import web
create_app(); d = web("nodo-jboss", "keycloak") or {}
print("BOVEDA keycloak:", d.get("sitio"), d.get("usuario"), "clave:", bool(d.get("clave")), "puerto:", d.get("puerto"))
PY
consola
echo C3; caso "{$A, \"params\": {$P, \"sp_accion\": \"encender\"}, \"caso\": \"C3\", \"min\": 6}" | tee /tmp/kc-r3; kc
echo C4; caso "{$A, \"params\": {$P, \"sp_accion\": \"reiniciar\"}, \"caso\": \"C4\", \"min\": 6}" | tee /tmp/kc-r4; espera_kc; kc
echo C5; caso "{$A, \"params\": {\"sp_unidades\": [$U], \"sp_accion\": \"apagar\", \"sp_plataforma\": \"Keycloak\"}, \"caso\": \"C5\", \"min\": 6}" | tee /tmp/kc-r5; sleep 5; ver; kc
echo C6; caso "{$A, \"params\": {$P, \"sp_accion\": \"encender\"}, \"caso\": \"C6\", \"min\": 6}" | tee /tmp/kc-r6; espera_kc; ver; kc
echo C7; caso "{$A, \"params\": {\"sp_unidades\": [\"eap8-standalone\", \"eap7-standalone\", \"jboss-eap\", \"wildfly\", \"jboss\"], \"sp_accion\": \"estado\", \"sp_plataforma\": \"JBoss\"}, \"caso\": \"C7\", \"min\": 6}" | tee /tmp/kc-r7
echo C8; caso "{$A, \"params\": {\"sp_unidades\": [\"tomcat\", \"jws6-tomcat\", \"jws5-tomcat\"], \"sp_accion\": \"encender\", \"sp_plataforma\": \"Tomcat\"}, \"caso\": \"C8\", \"min\": 6}" | tee /tmp/kc-r8
echo C9; caso "{$A, \"params\": {\"sp_unidades\": [\"redis\", \"valkey\"], \"sp_accion\": \"deshabilitar\", \"sp_plataforma\": \"Redis\"}, \"caso\": \"C9\", \"min\": 6}" | tee /tmp/kc-r9; ver
echo C10; $EX - <<'PY' 2>&1 | grep -v -i warn | tail -2 | tee /tmp/kc-r10
from app import create_app
from app.bd import q1
a = create_app(); u = q1("SELECT * FROM usuarios WHERE id=1"); c = a.test_client()
with c.session_transaction() as s: s["uid"] = u["id"]; s["csrf"] = "kc"
e2 = q1("SELECT id, ram_mb FROM equipos WHERE nombre='nodo-jboss2' AND estado!='retirado'"); n0 = q1("SELECT COUNT(*) n FROM tickets")["n"]
r = c.post(f"/lab/inventario/{e2['id']}/plataforma/keycloak/servicio", data={"_csrf": "kc", "accion": "encender", "nombre": "Keycloak"}, headers={"X-Requested-With": "fetch"})
print("C10", r.status_code, (r.get_json() or {}).get("error"), "| tickets nuevos:", q1("SELECT COUNT(*) n FROM tickets")["n"] - n0)
PY
# Certificación solo si cada caso dio lo esperado
python3 - <<'PY' > /tmp/kc-eval.json
import json, re
def r(n):
    try:
        l = [x for x in open(f"/tmp/kc-r{n}") if x.startswith("RES")][0]; return json.loads(l[4:])
    except Exception: return {}
R = {n: r(n) for n in range(1, 10)}
q = lambda n: str((R[n].get("stats") or {}).get("nodo-jboss", {}).get("quadlet", ""))
act = lambda n: str((R[n].get("stats") or {}).get("nodo-jboss", {}).get("activo", ""))
ok = {
 "C1": R[1].get("estado") == "exitoso" and "deshabilitado" in q(1),
 "C2": R[2].get("estado") == "exitoso" and "habilitado" in q(2) and act(2) == "active",
 "C3": R[3].get("estado") == "exitoso" and q(3) == "" and act(3) == "active",
 "C4": R[4].get("estado") == "exitoso" and act(4) == "active",
 "C5": R[5].get("estado") == "exitoso" and act(5) in ("inactive", "failed"),
 "C6": R[6].get("estado") == "exitoso" and act(6) == "active",
 "C7": R[7].get("estado") == "exitoso" and act(7) == "active",
 "C8": R[8].get("estado") == "fallido" and "No se encontró el servicio" in str(R[8].get("fallo")),
 "C9": R[9].get("estado") == "fallido" and "contenedor" in str(R[9].get("fallo")),
 "C10": "409" in open("/tmp/kc-r10").read() and "tickets nuevos: 0" in open("/tmp/kc-r10").read()}
tk = [R[n].get("ticket") for n in range(1, 10) if R[n].get("ticket")]
print(json.dumps({"ok": ok, "tickets": tk, "q": {f"C{n}": q(n) for n in range(1, 10)}}, ensure_ascii=False))
PY
cat /tmp/kc-eval.json
sudo podman cp /tmp/kc-eval.json faceops:/tmp/kc-eval.json
$EX - <<'PY' 2>&1 | grep -v -i warn | tail -3
import json
from app import create_app
from app.bd import q1
from app import pruebas
create_app(); ev = json.load(open("/tmp/kc-eval.json")); cid = q1("SELECT id FROM clientes WHERE codigo='lab'")["id"]
if all(ev["ok"].values()):
    casos = [{"id": k, "resultado": "ok", "evidencia": (ev["q"].get(k) or "ver ticket")} for k in ev["ok"]]
    pruebas.certificar(cid, "Plataforma · Controlar servicio", "planes-prueba.yml · Plataforma · Controlar servicio", casos, ev["tickets"], "aprobada", "Claude (regla de Cristian)")
else:
    casos = [{"id": k, "resultado": "ok" if v else "falla"} for k, v in ev["ok"].items()]
    pruebas.certificar(cid, "Plataforma · Controlar servicio", "planes-prueba.yml · Plataforma · Controlar servicio", casos, ev["tickets"], "rechazada", "Claude (regla de Cristian)")
a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Plataforma · Controlar servicio'", cid)
print("CERT controlar servicio:", pruebas.estado(dict(a))["estado"])
PY
echo FICHA-ENCENDER; $EX - <<'PY' 2>&1 | grep -v -i warn | tail -3
import time
from app import create_app
from app.bd import q1
from app.operacion import resolver_aprobacion, actualizar_tickets
a = create_app(); u = q1("SELECT * FROM usuarios WHERE id=1"); c = a.test_client()
with c.session_transaction() as s: s["uid"] = u["id"]; s["csrf"] = "kc"
r = c.post("/lab/inventario/6/plataforma/keycloak/servicio", data={"_csrf": "kc", "accion": "encender", "nombre": "Keycloak"}, headers={"X-Requested-With": "fetch"})
d = r.get_json() or {}; print("ficha encender", r.status_code, d.get("numero"), d.get("error"))
if d.get("id"):
    for k in range(60):
        try: actualizar_tickets()
        except Exception: pass
        t = q1("SELECT numero, estado FROM tickets WHERE id=?", d["id"])
        if t["estado"] == "pendiente_aprobacion":
            try: resolver_aprobacion(u, d["id"], True, "Aprobado por Claude (regla de Cristian)")
            except Exception as x: print("aprobar", str(x)[:100])
        if t["estado"] in ("exitoso", "fallido", "rechazado", "cancelado"): break
        time.sleep(8)
    print("ticket", t["numero"], t["estado"])
PY
espera_kc; ver; kc
consola
shot despues
shot simulado
sudo podman exec faceops rm -f /tmp/kc-caso.py /tmp/kc-eval.json; rm -f /tmp/kc-caso.py /tmp/kc-res.txt /tmp/kc-shot.py /tmp/kc-consola.py /tmp/kc-r* /tmp/kc-eval.json
