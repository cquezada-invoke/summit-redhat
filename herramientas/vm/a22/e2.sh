#!/bin/bash
# A22 · Etapa 2 (Monitoreo, salud y alertas): pantallas con Playwright + planes de sus acciones. Correr en el anfitrión del lab
# con el lab encendido: bash e2.sh > e2.out 2>&1. Pone la Etapa 2 en revisión (liberadas [1, 2]) mientras prueba y la deja
# como estaba al final. Usuario qa.nucleo. Sin secretos en la salida.
set -u
F="sudo podman exec -i -e FACEOPS_VIGIA=0 -w /opt/faceops -e PYTHONPATH=/opt/faceops faceops python3 -"
ANT=$($F 2>/dev/null <<'Q' | tail -1
from app import create_app; create_app()
from app.etapas import liberadas, fijar_liberadas
import json; print(json.dumps(sorted(liberadas()))); fijar_liberadas([1, 2])
Q
); echo "etapas liberadas antes: $ANT → [1, 2]"
trap '$F >/dev/null 2>&1 <<Q
from app import create_app; create_app()
from app.etapas import fijar_liberadas; import json; fijar_liberadas(json.loads("""$ANT"""))
Q
echo "etapas liberadas restauradas: $ANT"' EXIT
# --- pantallas (Playwright, cookie de sesión firmada como en la Etapa 1)
cat > /tmp/e2prep.py <<'PY'
import json, os
from app import create_app
app = create_app()
from app.bd import q1
cid = q1("SELECT id FROM clientes WHERE codigo='lab'")["id"]; u = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")
ser = app.session_interface.get_signing_serializer(app)
eqs = [r["id"] for r in [q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cid, n) for n in ("srv-e1prueba-01", "srv-windows-iis-01")] if r] + [97]
d = {"base": "https://127.0.0.1:8843/faceops", "origen": "https://127.0.0.1:8843", "cookie": app.config.get("SESSION_COOKIE_NAME") or "session",
     "ck": ser.dumps({"uid": u["id"], "csrf": "x", "cliente_id": cid}), "equipos": eqs}
fd = os.open("/tmp/e2pw.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, json.dumps(d).encode()); os.close(fd); print("PREP ok", eqs)
PY
cat > /tmp/e2pw.py <<'PY'
import json, sys, time
from playwright.sync_api import sync_playwright
D = json.load(open(sys.argv[1]))
RUTAS = ["/lab/alertas", "/lab/monitoreo/radar", "/lab/monitoreo/kpi-okr", "/lab/monitoreo/itil", "/lab/monitoreo/alertas", "/lab/autocuidado", "/lab/incidentes", "/lab/deudas"]
with sync_playwright() as p:
    br = p.chromium.launch(); c = br.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
    c.add_cookies([{"name": D["cookie"], "value": D["ck"], "url": D["origen"]}])
    for movil in (False, True):
        if movil: c = br.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844}); c.add_cookies([{"name": D["cookie"], "value": D["ck"], "url": D["origen"]}])
        for r in RUTAS:
            pg = c.new_page(); errs = []; pg.on("console", lambda m: errs.append(m.text[:100]) if m.type == "error" else None)
            pg.on("pageerror", lambda e: errs.append("pageerror " + str(e)[:100]))
            rs = pg.goto(D["base"] + r, wait_until="domcontentloaded"); t0 = time.time(); pg.reload(wait_until="domcontentloaded"); cal = round((time.time() - t0) * 1000)
            i = pg.evaluate("({chip:(document.querySelector('.etapa-chip')||{}).textContent||'', bloq:document.querySelectorAll('.bloq-etapa').length, glosa:(document.body.innerText.match(/Etapa \\d+ · [^\\n]*liberada/)||[''])[0], hs:document.documentElement.scrollWidth>document.documentElement.clientWidth, h1:((document.querySelector('h1')||{}).textContent||'').trim().slice(0,50)})")
            print("PANTALLA", "móvil" if movil else "escritorio", r, rs.status if rs else 0, f"{cal} ms", json.dumps(i, ensure_ascii=False), "errores:", errs[:2], flush=True); pg.close()
    c = br.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900}); c.add_cookies([{"name": D["cookie"], "value": D["ck"], "url": D["origen"]}])
    for eid in D["equipos"]:
        pg = c.new_page(); pg.goto(D["base"] + f"/lab/inventario/{eid}", wait_until="domcontentloaded"); pg.wait_for_timeout(1500)
        out = {}
        try:   # salud de la máquina: barra y flotante con remediación
            b = pg.query_selector(".sl-barra"); out["salud_barra"] = bool(b) and not b.get_attribute("class").count("bloq-etapa")
            if b: b.click(); pg.wait_for_timeout(800); out["salud_flotante"] = pg.evaluate("!!document.querySelector('#sl-dlg[open]')"); pg.keyboard.press("Escape")
        except Exception as x: out["salud_err"] = str(x)[:80]
        try:   # alertas: el cajón ya no bloquea activar/desactivar
            pg.click(".al-titulo .al-btn"); pg.wait_for_timeout(600)
            out["alertas"] = pg.evaluate("(()=>{const d=document.querySelector('dialog.al-dlg[open]');if(!d)return 'no abre';const b=[...d.querySelectorAll('button,input:not([type=hidden])')].filter(x=>!x.matches('.al-x'));return {controles:b.length,bloqueados:b.filter(x=>x.classList.contains('bloq-etapa')).length,glosa:!!d.querySelector('.bloq-glosa-cont')}})()")
        except Exception as x: out["alertas_err"] = str(x)[:80]
        out["eventos_bmc"] = pg.evaluate("(()=>{const x=document.getElementById('hw-eventos-bmc');return x?(x.classList.contains('bloq-cont')?'bloqueado':'visible'):'no aplica'})()")
        print("FICHA_E2", eid, json.dumps(out, ensure_ascii=False), flush=True); pg.close()
    br.close()
PY
sudo podman cp /tmp/e2prep.py faceops:/tmp/e2prep.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/e2prep.py 2>&1 | grep PREP
sudo podman cp faceops:/tmp/e2pw.json /tmp/e2pw.json; sudo chown $(id -u) /tmp/e2pw.json; chmod 600 /tmp/e2pw.json; sudo podman exec faceops rm -f /tmp/e2pw.json /tmp/e2prep.py
timeout 1500 ~/pw/bin/python /tmp/e2pw.py /tmp/e2pw.json; rm -f /tmp/e2pw.json
# --- planes de las acciones de la Etapa 2 (tickets reales, aprobados con la regla de Cristian): pendientes de su arnés propio,
#     ver planes-prueba.yml: Monitoreo · Configurar alertas del equipo, Responder alerta, Agente de monitoreo (Linux),
#     Exportador en Windows, Plataforma · Medir salud / Programar medición, Incidentes · Verificar error conocido,
#     Remediación · IIS en Windows, Remediación · Limpiar logs.
echo "fin e2 $(date +%H:%M)"
