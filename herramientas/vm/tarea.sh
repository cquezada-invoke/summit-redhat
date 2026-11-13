#!/bin/bash
# Etapa 1 · checklist de pantallas con Playwright headless (usuario de prueba qa.nucleo). Sin secretos en la salida.
SHA=2b439326; for i in $(seq 1 120); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); [ -n "$D" ] && git -C ~/summit-redhat merge-base --is-ancestor $SHA $D 2>/dev/null && break; sleep 10; done; sleep 30
echo "desplegado: $(cut -c1-8 ~/faceops-autodeploy/desplegado) · $(cut -c1-120 ~/faceops-autodeploy/estado)"
cat > /tmp/e1pw.py <<'PWPY'
import json, sys, time, base64, hashlib, hmac, struct

def ficha_test(br):
    c = br.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
    c.add_cookies([{"name": D["cookie_nombre"], "value": D["ck_nucleo"], "url": D["origen"]}])
    for nom, eid in D["fichas"]:
        pg = c.new_page(); errs = []
        pg.on("console", lambda m: errs.append(m.text[:120]) if m.type == "error" else None)
        pg.on("pageerror", lambda e_: errs.append("pageerror " + str(e_)[:120]))
        t0 = time.time(); r = pg.goto(D["base"] + f"/lab/inventario/{eid}", wait_until="domcontentloaded"); fria = round((time.time() - t0) * 1000)
        try: pg.wait_for_load_state("networkidle", timeout=8000)
        except Exception: pass
        t0 = time.time(); pg.reload(wait_until="domcontentloaded"); cal = round((time.time() - t0) * 1000)
        try: pg.wait_for_load_state("networkidle", timeout=8000)
        except Exception: pass
        info = pg.evaluate("""(()=>{const so=document.querySelector('.cajon-p[data-so]');
          const sk=document.getElementById('sk-cajon');
          const blo=so?[...so.children].concat([...so.querySelectorAll('.card,[data-pilar]')]).map(x=>(x.querySelector('h2,h3')||{}).textContent||x.className).filter(Boolean).map(x=>x.trim().slice(0,30)):[];
          const op=document.querySelector('#sk-op .op-card'),act=document.querySelector('#sk-act #actualizacion');
          const cajaE=document.querySelector('.eq-energia');const enB=[...document.querySelectorAll('[data-energia]')].map(b=>b.dataset.energia+(b.disabled?'(deshab)':''));return {energia_botones:enB, skills_en_energia:!!(cajaE&&cajaE.querySelector('#sk-btn')), skills_en_titulo:!!document.querySelector('.al-titulo #sk-btn'), so:!!so, so_salud:!!(so&&so.querySelector('.salud-res')), so_op:!!(so&&so.querySelector('.op-card')), so_act:!!(so&&so.querySelector('#actualizacion')),
            so_bloques:[...new Set(blo)].slice(0,14), skills:!!sk, sk_op:!!op, sk_act:!!act,
            op_n:op?op.querySelectorAll('tr').length:0, op_bloq:op?op.querySelectorAll('.bloq,.bloq-etapa,[disabled]').length:0,
            act_bloq:act?(act.querySelectorAll('.bloq-etapa,[disabled]').length+'/'+act.querySelectorAll('button,a').length):'',
            glosa:(document.body.innerText.match(/Etapa \\d · aún no liberada/)||[''])[0],
            hscroll: document.documentElement.scrollWidth>document.documentElement.clientWidth}})()""")
        # abrir Skills y ver los grupos visibles
        vis = ""
        try:
            if pg.query_selector("#sk-btn"):
                pg.click("#sk-btn"); pg.wait_for_timeout(500)
                vis = pg.evaluate("[...document.querySelectorAll('#sk-cajon .sk-grupo')].filter(g=>!g.hidden).map(g=>g.querySelector('summary').childNodes[0].textContent.trim()).join(' | ')")
                vis += " · inventariar en Operaciones: " + str(pg.evaluate("!!document.querySelector('#sk-cajon [data-g=operaciones] .sk-inv .sk-inv-ir')"))
                if D.get("inv_click") == nom:
                    pg.click("#sk-cajon .sk-inv-ir")
                    pg.wait_for_function("(()=>{const x=document.querySelector('#sk-cajon .sk-inv .sk-tk');return x&&/FO-|^$/.test(x.textContent)&&x.textContent!=='creando…'})()", timeout=60000)
                    pg.wait_for_timeout(1500)
                    vis += " · clic Inventariar → «" + pg.evaluate("document.querySelector('#sk-cajon .sk-inv .sk-tk').textContent") + "» " + pg.evaluate("[...document.querySelectorAll('.aviso,.toast,.fx-aviso')].map(x=>x.textContent).join(' ').slice(0,120)")
        except Exception as x: vis = "error " + str(x)[:80]
        lv = ""
        try:
            rutas = pg.evaluate("[...document.querySelectorAll('.cajon-p[data-so] .log-ver')].map(a=>[a.dataset.log,a.dataset.logN])")
            pref = [r for r in rutas if r[0] in ("/var/log/messages", "journal", "evento:System")] or rutas[:1]
            for ru, nm in pref[:2]:
                if pg.evaluate("document.getElementById('sk-cajon')&&!document.getElementById('sk-cajon').hidden"):
                    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
                pg.evaluate("([r,n])=>fxVerLog(r,n)", [ru, nm]); t0 = time.time()
                pg.wait_for_function("(()=>{const t=document.querySelector('#log-dlg .lg-estado').textContent;return /línea|No se|AAP no|error|falló/i.test(t)&&!/Pidiendo|leyendo/i.test(t)})()", timeout=200000)
                st = pg.evaluate("[document.querySelector('#log-dlg').open, document.querySelector('#log-dlg .lg-estado').textContent.slice(0,140), document.querySelectorAll('#log-dlg .lg-cuerpo').length, document.querySelector('#log-dlg .lg-cuerpo').textContent.split('\\n').length, !document.querySelector('#log-dlg .lg-mas').hidden, Math.abs(document.querySelector('#log-dlg .lg-cuerpo').scrollHeight-document.querySelector('#log-dlg .lg-cuerpo').clientHeight-document.querySelector('#log-dlg .lg-cuerpo').scrollTop)<5]")
                pags = len(c.pages)
                lv += f" [{ru}: {round(time.time()-t0)} s · abierto {st[0]} · «{st[1]}» · filas {st[3]} · más {st[4]} · autoscroll {st[5]} · pestañas {pags}]"
                if ru in ("/var/log/messages", "journal"):
                    pg.fill("#log-dlg .lg-busca", "systemd"); pg.click("#log-dlg .lg-buscar"); t0 = time.time()
                    pg.wait_for_function("(()=>{const t=document.querySelector('#log-dlg .lg-estado').textContent;return /con «systemd»|No se|error/i.test(t)})()", timeout=200000)
                    lv += f" [búsqueda {round(time.time()-t0)} s · «{pg.evaluate('document.querySelector(\'#log-dlg .lg-estado\').textContent.slice(0,100)')}» · marcas {pg.evaluate('document.querySelectorAll(\'#log-dlg mark\').length')}]"
                pg.evaluate("document.getElementById('log-dlg').close()")
        except Exception as x:
            lv += " [visor: " + str(x)[:160] + "]"
        fis = ""
        if nom.startswith("física"):
            fis = pg.evaluate("""(()=>{const c=document.getElementById('componentes');const n=c?[...c.querySelectorAll('.comp b')].map(b=>b.textContent.trim()).filter(Boolean):[];
              const h=document.getElementById('hardware');return {plataformas:n.slice(0,6), vacio:c?(c.querySelector('p.sub')||{}).textContent||'':'sin bloque',
              bmc_ssh:!!(h&&h.querySelector('.bmc-ssh')), bmc_web:h&&h.querySelector('.bmc-web')?(h.querySelector('.bmc-web').disabled?'deshabilitado: '+h.querySelector('.bmc-web').title:'enlace'):'no'}})()""")
            if nom == "física 97" and fis.get("bmc_ssh"):
                with c.expect_page() as np_:
                    pg.click("#hardware .bmc-ssh")
                p2 = np_.value
                try:
                    p2.wait_for_load_state("domcontentloaded", timeout=30000); p2.wait_for_timeout(9000)
                    fis["consola_bmc"] = p2.evaluate("({url: location.pathname, canvas: document.querySelectorAll('canvas').length, error: ((document.querySelector('.notification, .error, .client-status-modal')||{}).textContent||'').trim().slice(0,100)})")
                except Exception as x_:
                    fis["consola_bmc"] = "error " + str(x_)[:100]
                p2.close()
        print("FICHA", nom, "FIS", json.dumps(fis, ensure_ascii=False), r.status if r else 0, f"fría {fria} ms caliente {cal} ms", json.dumps(info, ensure_ascii=False), "· grupos Skills:", vis[:300], "· errores:", errs[:3], "· LOGS:", lv, flush=True)
        pg.close()
    c.close()
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
    ficha_test(br)
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
def _id(sql, *a):
    r = q1(sql, cid, *a); return r["id"] if r else None
fichas = [(n, i) for n, i in [("RHEL virtual srv-e1prueba-01", _id("SELECT id FROM equipos WHERE cliente_id=? AND nombre='srv-e1prueba-01' AND estado!='retirado'")),
          ("RHEL virtual nodo-vault", eon),
          ("Windows srv-windows-iis-01", _id("SELECT id FROM equipos WHERE cliente_id=? AND nombre='srv-windows-iis-01' AND estado!='retirado'")),
          ("física 97", 97 if q1("SELECT 1 x FROM equipos WHERE id=97 AND cliente_id=?", cid) else None),
          ("física otra", _id("SELECT id FROM equipos WHERE cliente_id=? AND forma IN ('fisico','físico','física') AND estado!='retirado' AND id!=97 AND (categoria='servidor' OR categoria IS NULL) ORDER BY id LIMIT 1"))] if i]
clave = secrets.token_urlsafe(16) + "aA9!"
sec = un["totp_secreto"] if un["totp_activo"] and un["totp_secreto"] else nuevo_secreto()
ej("DELETE FROM ingresos_fallidos WHERE usuario='qa.nucleo' OR ip IN ('127.0.0.1','::1','')")
ej("UPDATE usuarios SET clave_hash=?, totp_secreto=?, totp_activo=1 WHERE id=?", generate_password_hash(clave), sec, un["id"])
d = {"base": os.environ.get("E1_BASE"), "publica": os.environ.get("E1_PUB"), "origen": os.environ.get("E1_ORIGEN"), "cookie_nombre": app.config.get("SESSION_COOKIE_NAME") or "session",
     "ck_nucleo": ser.dumps({"uid": un["id"], "csrf": "x", "cliente_id": cid}), "ck_lector": ser.dumps({"uid": ul["id"], "csrf": "x", "cliente_id": cid}),
     "fichas": fichas, "inv_click": "RHEL virtual nodo-vault", "eq_on": eon, "eq_off": eoff, "ticket_id": tk, "auto_id": au, "lector_id": ul["id"], "clave": clave, "totp": sec}
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
# BMC simulado: el prompt iBMC por SSH con la credencial de la bóveda (no se imprime)
CF=$(mktemp); chmod 600 $CF
sudo podman exec -i -e FACEOPS_VIGIA=0 -w /opt/faceops -e PYTHONPATH=/opt/faceops faceops python3 - 2>/dev/null <<'Q' > $CF
from app import create_app
create_app()
from app.bd import q1
from app.boveda import acceso
e = q1("SELECT nombre FROM equipos WHERE id=97")
print((acceso(e["nombre"] + "-bmc") or {}).get("clave", ""), end="")
Q
if [ -s $CF ]; then printf 'help\nipmcget -d version\nexit\n' | timeout 20 sshpass -f $CF ssh -tt -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR Administrator@192.168.122.1 2>&1 | tr -d '\r' | head -8 | sed 's/^/BMC| /'; else echo "BMC| sin credencial en la bóveda"; fi
rm -f $CF
