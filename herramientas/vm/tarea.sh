#!/bin/bash
# Etapa 1 · bloqueo de controles de otras etapas: Playwright por pantalla y rechazo en el servidor. Sin secretos.
SHA=30308415
for i in $(seq 1 120); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); [ -n "$D" ] && git -C ~/summit-redhat merge-base --is-ancestor $SHA $D 2>/dev/null && break; sleep 10; done
echo "desplegado: $(cut -c1-8 ~/faceops-autodeploy/desplegado)"
cat > /tmp/e1bq.py <<'PWPY'
"""Bloqueo de controles de otras etapas en las pantallas de la Etapa 1: lista por pantalla y verificación de clic y servidor."""
import json, sys, time
from collections import Counter
from playwright.sync_api import sync_playwright
D = json.load(open(sys.argv[1])); B = D["base"]
P = [("Panel", "/lab/"), ("Buscador", "/lab/buscar?q=nodo-vault"), ("Acciones", "/lab/automatizaciones"), ("Ficha de acción (Etapa 1)", f"/lab/automatizaciones/{D['auto_id']}"),
     ("Ficha de acción (otra etapa)", f"/lab/automatizaciones/{D['auto_otra']}"), ("Tickets", "/lab/tickets"), ("Ticket", f"/lab/tickets/{D['ticket_id']}"),
     ("Servidores virtuales", "/lab/inventario?forma=virtual"), ("Servidores físicos", "/lab/inventario?forma=fisico"),
     ("Ficha servidor nodo-jboss", "/lab/inventario/6"), ("Ficha servidor nodo-vault", f"/lab/inventario/{D['eq_on']}"), ("Ficha servidor apagada", f"/lab/inventario/{D['eq_off']}"),
     ("Aprobaciones pendientes", "/lab/monitoreo/aprobaciones-pendientes"), ("Trazabilidad", "/lab/aprobaciones/trazabilidad"), ("Eventos de AAP", "/lab/monitoreo/eventos-aap"),
     ("Cola", "/lab/cola"), ("Usuarios", "/lab/configuracion/usuarios"), ("Usuario", f"/lab/configuracion/usuarios/{D['lector_id']}"), ("Pruebas", "/lab/configuracion/pruebas"),
     ("Auditoría", "/lab/auditoria"), ("Doble factor", "/lab/cuenta/doble-factor"), ("Catálogo admin", "/admin/catalogo"), ("Panel admin", "/admin/")]
JS = """() => [...document.querySelectorAll('.bloq-etapa')].map(e => {const t=(e.getAttribute('aria-label')||e.title&&e.textContent.trim()||e.textContent||e.value||e.getAttribute('href')||e.className||'').toString().replace(/\\s+/g,' ').trim();
  const s=e.closest('[id]'); return [e.dataset.bloqGlosa, (e.textContent||'').replace(/\\s+/g,' ').trim().slice(0,40) || (e.getAttribute('href')||'').slice(0,40) || e.className.split(' ').slice(0,2).join('.'), s?s.id:'']})"""
with sync_playwright() as p:
    br = p.chromium.launch(); c = br.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    c.add_cookies([{"name": D["cookie_nombre"], "value": D["ck_nucleo"], "url": D["origen"]}])
    errs = []
    for nom, r in P:
        pg = c.new_page(); pg.on("pageerror", lambda e: errs.append((r, str(e)[:120])))
        pg.goto(B + r, wait_until="domcontentloaded", timeout=60000)
        try: pg.wait_for_load_state("networkidle", timeout=8000)
        except Exception: pass
        pg.wait_for_timeout(600)
        b = pg.evaluate(JS)
        cnt = Counter((x[0], x[2] or "-") for x in b)
        print(f"PANTALLA {nom} | {r} | bloqueados {len(b)}")
        por = {}
        for g_, txt, sec in b: por.setdefault((g_, sec or "-"), []).append(txt)
        for (g_, sec), ts in sorted(por.items()):
            vis = [t for t in dict.fromkeys(ts) if t][:6]
            print(f"   {g_} · {('#' + sec) if sec != '-' else 'pantalla'}: {len(ts)} · {' | '.join(vis)}")
        if r == "/lab/inventario/6":
            u0 = pg.url
            al = pg.locator(".al-btn").first
            if al.count():
                al.click(force=True); pg.wait_for_timeout(500)
                print("   CLIC alertas: abre diálogo =", pg.evaluate("!!document.querySelector('#al-dlg[open]')"), "· aviso =", pg.evaluate("(document.querySelector('.fx-aviso')||{}).textContent||''")[:60])
            print("   glosa visible junto a Alertas:", pg.evaluate("(()=>{const b=document.querySelector('.al-btn');const n=b&&b.nextElementSibling;return n&&n.classList.contains('bloq-glosa')?n.textContent:''})()"))
            for sel, n in ((".btn-energ.prender", "Encender"), (".con-plat", "Consola web"), ("#hb-btn", "Skills")):
                e = pg.locator(sel).first
                print(f"   {n}: {'bloqueado' if e.count() and 'bloq-etapa' in (e.get_attribute('class') or '') else ('habilitado' if e.count() else 'no está')}")
        pg.close()
    print("ERRORES JS", errs[:5])
    # servidor: POST a rutas de otra etapa y acción de otra etapa desde la consola
    rq = c.request
    h = {"Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "fetch"}
    for ruta, datos in ((f"/lab/inventario/6/alertas/probar", "_csrf=x&aid=J01"), (f"/lab/inventario/6/habilidad", "_csrf=x&hid=parches"),
                        (f"/lab/inventario/6/ejecutar/{D['auto_otra']}", "_csrf=x"), (f"/lab/automatizaciones/{D['auto_otra']}", "_csrf=x&motivo=prueba")):
        x = rq.post(B + ruta, data=datos, headers=h, max_redirects=0)
        try: tx = x.json().get("error", "")
        except Exception: tx = x.text()[:80]
        print(f"SERVIDOR POST {ruta} → {x.status} {str(tx)[:90]}")
    br.close()

PWPY
cat > /tmp/e1prep2.py <<'PY'
import json, os
from app import create_app
app = create_app()
from app.bd import q1, q
from app.etapas import etapa_de_auto
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); cid = cl["id"]
un = q1("SELECT * FROM usuarios WHERE usuario='qa.nucleo'"); ul = q1("SELECT * FROM usuarios WHERE usuario='qa.lector'")
ser = app.session_interface.get_signing_serializer(app)
otra = next(a["id"] for a in q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1 AND nombre_aap='Seguridad · Revisar CVEs'", cid))
d = {"base": "https://127.0.0.1:8843/faceops", "origen": "https://127.0.0.1:8843", "cookie_nombre": app.config.get("SESSION_COOKIE_NAME") or "session",
     "ck_nucleo": ser.dumps({"uid": un["id"], "csrf": "x", "cliente_id": cid}), "eq_on": q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre='nodo-vault'", cid)["id"],
     "eq_off": q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre='nodo-jboss-qa'", cid)["id"], "ticket_id": q1("SELECT max(id) m FROM tickets WHERE cliente_id=? AND estado='exitoso'", cid)["m"],
     "auto_id": q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Infra · Encender o apagar equipo'", cid)["id"], "auto_otra": otra, "lector_id": ul["id"]}
fd = os.open("/tmp/e1bq.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, json.dumps(d).encode()); os.close(fd); print("PREP ok")
PY
sudo podman cp /tmp/e1prep2.py faceops:/tmp/e1prep2.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/e1prep2.py 2>&1 | grep -E "PREP|Error"
sudo podman cp faceops:/tmp/e1bq.json /tmp/e1bq.json; sudo chown $(id -u) /tmp/e1bq.json; chmod 600 /tmp/e1bq.json; sudo podman exec faceops rm -f /tmp/e1bq.json /tmp/e1prep2.py
timeout 1200 ~/pw/bin/python /tmp/e1bq.py /tmp/e1bq.json 2>&1 | tail -200
rm -f /tmp/e1bq.json /tmp/e1bq.py
