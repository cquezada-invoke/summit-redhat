#!/bin/bash
SHA=2b439326; for i in $(seq 1 150); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); [ -n "$D" ] && git -C ~/summit-redhat merge-base --is-ancestor $SHA $D 2>/dev/null && break; sleep 10; done; echo "desplegado: $(cut -c1-8 ~/faceops-autodeploy/desplegado)"
# Etapa 1 · cola de acciones con AAP caído y vuelta, y aviso global en todas las pantallas. Solo se corta la salida de
# FaceOps hacia AAP (regla iptables por el usuario del proceso de FaceOps); el resto del lab sigue. Sin secretos.
fopy() { sudo podman exec -i -e FACEOPS_VIGIA=0 -w /opt/faceops -e PYTHONPATH=/opt/faceops faceops python3 - "$@" 2>/dev/null; }
AAPURL=$(fopy <<'Q' | tail -1
from app import create_app
create_app()
from app.bd import q1
print(q1("SELECT aap_url FROM clientes WHERE codigo='lab'")["aap_url"])
Q
)
H=$(echo "$AAPURL" | sed -E 's#https?://([^/:]+).*#\1#'); P=$(echo "$AAPURL" | grep -oE ':[0-9]+' | tr -d : ); P=${P:-443}
IPS=$(getent ahostsv4 "$H" | awk '{print $1}' | sort -u | tr '\n' ' '); [ -z "$IPS" ] && IPS=$H
UIDFO=$(sudo podman top faceops huser | awk 'NR>1{print $1}' | sort | uniq -c | sort -rn | awk 'NR==1{print $2}')
UIDN=$(id -u "$UIDFO" 2>/dev/null || echo "$UIDFO")
echo "AAP de FaceOps: $H:$P ($IPS) · usuario del proceso FaceOps en el anfitrión: $UIDN"
cortar() { for ip in $IPS; do sudo iptables -I OUTPUT -p tcp -d $ip --dport $P -m owner --uid-owner $UIDN -j REJECT --reject-with tcp-reset -m comment --comment e1-aap-caido; done; }
volver() { while sudo iptables -S OUTPUT | grep -q e1-aap-caido; do R=$(sudo iptables -S OUTPUT | grep e1-aap-caido | head -1 | sed 's/^-A /-D /'); sudo iptables $R; done; }
trap volver EXIT
cortar; T0=$(date +%s)
# ticket en cola mientras AAP no responde (encender la VM de prueba ya encendida: idempotente)
fopy <<'Q' | grep -E "^(TK|EST)"
import time, json
from app import create_app
create_app()
from app.bd import q1
from app.operacion import solicitar
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); u = q1("SELECT * FROM usuarios WHERE usuario='qa.nucleo'")
a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Infra · Encender o apagar equipo'", cl["id"])
t = solicitar(u, cl, a, "", {"en_equipo": "srv-e1prueba-01", "en_accion": "encender"}, "Etapa 1 · prueba de cola con AAP caído (encender la VM de prueba ya encendida)")
open("/tmp/e1-cola.txt", "w").write(str(t["id"]))
for i in range(24):
    x = q1("SELECT * FROM tickets WHERE id=?", t["id"]); c = q1("SELECT estado, motivo, intentos FROM cola_acciones WHERE ticket_id=? ORDER BY id DESC LIMIT 1", t["id"])
    if i in (0, 5, 23): print("EST", i * 5, "s", x["estado"], "| cola:", dict(c) if c else None)
    time.sleep(5)
print("TK", t["numero"], t["id"])
Q
# aviso global en las pantallas (Playwright, usuario qa.nucleo)
cat > /tmp/e1av.py <<'PY'
import json, sys, time
from playwright.sync_api import sync_playwright
D = json.load(open(sys.argv[1])); B = D["base"]
with sync_playwright() as p:
    br = p.chromium.launch(); c = br.new_context(ignore_https_errors=True); c.add_cookies([{"name": D["cookie_nombre"], "value": D["ck"], "url": D["origen"]}])
    pg = c.new_page(); out = []
    for r in ("/lab/", "/lab/tickets", "/lab/cola", "/lab/automatizaciones", "/lab/inventario", f"/lab/tickets/{D['tid']}"):
        t0 = time.time(); pg.goto(B + r, wait_until="domcontentloaded"); ms = round((time.time() - t0) * 1000)
        av = pg.evaluate("(()=>{const a=document.querySelector('.aviso-aap');return a?a.textContent.trim().slice(0,90):''})()")
        cola = pg.evaluate("document.body.innerText.includes('AAP no disponible') || document.body.innerText.includes('En cola')")
        out.append((r, ms, bool(av), cola))
    print("AVISO", sys.argv[2], out, (av or "")[:90]); br.close()
PY
mkpw() { fopy "$(cat /tmp/e1-cola-id 2>/dev/null)" <<'Q' > /tmp/e1av.json
import json, sys
from app import create_app
app = create_app()
from app.bd import q1
u = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")["id"]; cl = q1("SELECT id FROM clientes WHERE codigo='lab'")["id"]
print(json.dumps({"base": "https://127.0.0.1:8843/faceops", "origen": "https://127.0.0.1:8843", "cookie_nombre": app.config.get("SESSION_COOKIE_NAME") or "session",
                  "ck": app.session_interface.get_signing_serializer(app).dumps({"uid": u, "csrf": "x", "cliente_id": cl}), "tid": sys.argv[1]}))
Q
chmod 600 /tmp/e1av.json; }
sudo podman exec faceops cat /tmp/e1-cola.txt > /tmp/e1-cola-id 2>/dev/null; mkpw
timeout 300 ~/pw/bin/python /tmp/e1av.py /tmp/e1av.json "con AAP caído" 2>&1 | tail -2
echo "AAP cortado durante $(( $(date +%s) - T0 )) s"; volver; T1=$(date +%s); echo "AAP de vuelta"
# al volver: la cola lo lanza sola; se aprueba con la regla de Cristian y termina
fopy "$(cat /tmp/e1-cola-id)" <<'Q' | grep -E "^(VUELTA|FIN)"
import sys, time
from app import create_app
create_app()
from app.bd import q1
from app.operacion import resolver_aprobacion
u = q1("SELECT * FROM usuarios WHERE usuario='qa.nucleo'"); tid = int(sys.argv[1]); t0 = time.time(); lanzado = None
for _ in range(180):
    x = q1("SELECT * FROM tickets WHERE id=?", tid)
    if x["estado"] != "en_cola" and lanzado is None: lanzado = round(time.time() - t0); print("VUELTA lanzado en", lanzado, "s ·", x["estado"])
    if x["estado"] == "pendiente_aprobacion":
        try: resolver_aprobacion(u, tid, True, "Aprobado por Claude (regla de Cristian)")
        except Exception: pass
    if x["estado"] in ("exitoso", "fallido", "rechazado", "cancelado", "error"): break
    time.sleep(5)
print("FIN", x["numero"], x["estado"], (x["resultado"] or "").strip().splitlines()[-1:] )
Q
sleep 20; mkpw; timeout 300 ~/pw/bin/python /tmp/e1av.py /tmp/e1av.json "con AAP de vuelta" 2>&1 | tail -2
rm -f /tmp/e1av.json /tmp/e1av.py /tmp/e1-cola-id; sudo podman exec faceops rm -f /tmp/e1-cola.txt
