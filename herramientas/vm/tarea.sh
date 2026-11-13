#!/bin/bash
# Etapa 1 · checklist de pantallas con Playwright headless (usuario de prueba qa.nucleo). Sin secretos en la salida.
SHA=1b8f72eb; for i in $(seq 1 120); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); [ -n "$D" ] && git -C ~/summit-redhat merge-base --is-ancestor $SHA $D 2>/dev/null && break; sleep 10; done; sleep 30
echo "desplegado: $(cut -c1-8 ~/faceops-autodeploy/desplegado) · $(cut -c1-120 ~/faceops-autodeploy/estado)"
cat > /tmp/e1pw.py <<'PWPY'
import json, sys, time, base64, hashlib, hmac, struct
from playwright.sync_api import sync_playwright
D = json.load(open(sys.argv[1]))
def totp(s, d=0):
    k = base64.b32decode(s + "=" * (-len(s) % 8)); h = hmac.new(k, struct.pack(">Q", int(time.time()) // 30 + d), hashlib.sha1).digest(); o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)
def flash(pg):
    try: return pg.evaluate("(()=>{const f=document.querySelectorAll('.flash,.alerta,.error,.msg');return [...f].map(x=>x.textContent.trim()).join(' / ').slice(0,120)})()")
    except Exception: return ""
with sync_playwright() as p:
    br = p.chromium.launch()
    def paso(pg, n):
        print("PASO", n, pg.url.replace(D["publica"], "")[:60], "|", flash(pg), flush=True)
    def ingresar(pg):
        pg.goto(D["publica"] + "/ingresar", wait_until="domcontentloaded")
        pg.fill("input[name=usuario]", "qa.nucleo"); pg.fill("input[name=clave]", D["clave"])
        with pg.expect_navigation(): pg.locator("form button").first.click()
    def codigo(pg, cod):
        pg.fill("input[name=codigo]", cod)
        with pg.expect_navigation(): pg.locator("form button").first.click()
    t0 = time.time()
    c = br.new_context(ignore_https_errors=True); pg = c.new_page(); ingresar(pg); paso(pg, "login 1"); en_codigo = "/ingresar/codigo" in pg.url
    pg.goto(D["publica"] + "/lab/", wait_until="domcontentloaded"); paso(pg, "lab sin código"); fuera = "/ingresar" in pg.url
    c.close()
    c = br.new_context(ignore_https_errors=True); pg = c.new_page(); ingresar(pg); paso(pg, "login 2")
    rech = None
    if "/ingresar/codigo" in pg.url:
        codigo(pg, "000000" if totp(D["totp"]) != "000000" else "111111"); paso(pg, "código malo"); rech = "/ingresar" in pg.url
    c.close()
    c = br.new_context(ignore_https_errors=True); pg = c.new_page(); ingresar(pg); paso(pg, "login 3"); ok = None
    if "/ingresar/codigo" in pg.url:
        codigo(pg, totp(D["totp"])); paso(pg, "código bueno"); ok = "/ingresar" not in pg.url
    print(f"DOBLE_FACTOR pide código {en_codigo} · sin código no entra {fuera} · código erróneo rechazado {rech} · código correcto entra {ok}")
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
tk = (q1("SELECT id FROM tickets WHERE cliente_id=? AND estado='exitoso' AND aap_job_tipo='workflow' ORDER BY id DESC LIMIT 1", cid) or q1("SELECT id FROM tickets WHERE cliente_id=? AND estado='exitoso' ORDER BY id DESC LIMIT 1", cid))["id"]
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
