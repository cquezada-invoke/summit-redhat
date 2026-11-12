#!/bin/bash
# Certificación Núcleo · cola durable y aviso global con AAP caído/arriba (de punta a punta, como usuario de prueba por la consola).
# AAP no se toca: se corta SOLO la salida de FaceOps hacia AAP (regla de firewall atada al cgroup del contenedor faceops),
# con retiro garantizado (trap + seguro a los 10 min). Acción: encender JBoss ya encendido en nodo-jboss (sin cambios, con aprobación).
CG=$(sudo podman inspect -f '{{.State.CgroupPath}}' faceops 2>/dev/null | sed 's#^/##'); NM=$(sudo podman inspect -f '{{.HostConfig.NetworkMode}}' faceops)
echo "faceops red: $NM · cgroup: ${CG:-?}"
echo "desplegado: $(sudo podman exec faceops cat /opt/faceops/app/version.json 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["commit"][:8], d["fecha"])')"
REGLA="OUTPUT -p tcp -d 127.0.0.1 --dport 443 -m cgroup --path $CG -j REJECT --reject-with tcp-reset"
quitar() { while sudo iptables -C $REGLA 2>/dev/null; do sudo iptables -D $REGLA; done; }
trap quitar EXIT
ping_fo() { sudo podman exec faceops python3 -c "import requests,urllib3;urllib3.disable_warnings()
try: print(requests.get('https://127.0.0.1/api/controller/v2/ping/',verify=False,timeout=4).status_code)
except Exception as e: print(type(e).__name__)" 2>/dev/null; }
echo "antes · FaceOps→AAP: $(ping_fo) · anfitrión→AAP: $(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 https://127.0.0.1/api/controller/v2/ping/)"
cat > /tmp/corte.sh <<SH
#!/bin/bash
# corta (on) o devuelve (off) la salida FaceOps→AAP
case "\$1" in on) sudo iptables -I $REGLA; (sleep 600; while sudo iptables -C $REGLA 2>/dev/null; do sudo iptables -D $REGLA; done) >/dev/null 2>&1 & ;; off) while sudo iptables -C $REGLA 2>/dev/null; do sudo iptables -D $REGLA; done ;; esac
SH
chmod +x /tmp/corte.sh
sudo cat /tmp/qa-nucleo.json > /tmp/qa-n5.json; chmod 600 /tmp/qa-n5.json
cat > /tmp/n5.py <<'PY'
import base64, hashlib, hmac, json, re, struct, subprocess, time
from playwright.sync_api import sync_playwright
B0 = "http://192.168.122.1:8110"; B = B0 + "/lab"; C = json.load(open("/tmp/qa-n5.json"))
def totp(sec):
    k = base64.b32decode(sec.upper() + "=" * (-len(sec) % 8)); m = hmac.new(k, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    o = m[-1] & 15; return "%06d" % ((struct.unpack(">I", m[o:o + 4])[0] & 0x7fffffff) % 1000000)
CSRF = lambda pg: (re.search(r"_csrf'?\"?\s*[,:]\s*\"([^\"]+)\"", pg.content()) or [None, None])[1]
T0 = time.time(); log = lambda *a: print(f"[{time.time() - T0:6.1f}s]", *a, flush=True)
def ping_fo():
    return subprocess.run("sudo podman exec faceops python3 -c \"import requests,urllib3;urllib3.disable_warnings()\ntry: print(requests.get('https://127.0.0.1/api/controller/v2/ping/',verify=False,timeout=4).status_code)\nexcept Exception as e: print(type(e).__name__)\"", shell=True, capture_output=True, text=True).stdout.strip()
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(ignore_https_errors=True); pg = ctx.new_page()
    pg.goto(B0 + "/ingresar"); pg.fill("input[name=usuario]", "qa.nucleo"); pg.fill("input[name=clave]", C["qa.nucleo"]["clave"]); pg.click("button.btn"); pg.wait_for_load_state()
    pg.fill("input[name=codigo]", totp(C["qa.nucleo"]["totp"])); pg.click("button.btn"); pg.wait_for_load_state()
    pg.goto(B + "/inventario/6"); log("ingreso ok · aviso AAP antes del corte:", pg.locator(".aviso-aap").count())
    subprocess.run(["/tmp/corte.sh", "on"]); log("CORTE FaceOps→AAP aplicado · ping desde FaceOps:", ping_fo())
    aviso = None
    for i in range(36):
        pg.goto(B + "/inventario"); 
        if pg.locator(".aviso-aap").count(): aviso = pg.locator(".aviso-aap").first.inner_text(); break
        pg.wait_for_timeout(5000)
    log("aviso global visible:", bool(aviso), "·", (aviso or "")[:140])
    otras = {}
    for r_ in ("/", "/tickets", "/cola", "/automatizaciones"):
        pg.goto(B + r_); otras[r_] = pg.locator(".aviso-aap").count()
    log("aviso en otras pantallas:", otras)
    pg.goto(B + "/inventario/6"); cs = CSRF(pg)
    tk = None
    for cod in ("jboss", "jboss-eap", "eap"):
        r = pg.request.post(B + f"/inventario/6/plataforma/{cod}/servicio", form={"_csrf": cs, "accion": "encender", "nombre": "JBoss EAP"}, headers={"X-Requested-With": "fetch"})
        log("solicitud", cod, r.status, r.text()[:160])
        if r.status == 200 and "id" in r.text(): tk = r.json(); break
    if not tk: raise SystemExit("sin ticket")
    tid = tk["id"]
    def est():
        r = pg.request.get(B + f"/tickets/{tid}/estado"); return r.json() if r.status == 200 else {"http": r.status}
    log("ticket", tk["numero"], "creado con AAP caído · estado:", est())
    pg.wait_for_timeout(45000)
    e1 = est(); pg.goto(B + "/cola"); cola_txt = pg.locator("body").inner_text()
    log("tras 45 s con AAP caído · estado:", e1, "· en /cola:", tk["numero"] in cola_txt, "· motivo visible:", "no disponible" in cola_txt.lower())
    pg.goto(B + f"/tickets/{tid}"); log("ficha del ticket muestra cola:", re.findall(r"En cola[^\n]{0,80}", pg.locator("body").inner_text())[:2])
    subprocess.run(["/tmp/corte.sh", "off"]); log("CORTE retirado · ping desde FaceOps:", ping_fo())
    st = None
    for i in range(60):
        e_ = est(); st = e_.get("estado")
        if st not in ("en_cola", None): break
        pg.wait_for_timeout(5000)
    log("tras volver AAP · estado:", e_)
    if st == "pendiente_aprobacion":
        for i in range(12):
            pg.goto(B + f"/tickets/{tid}"); cs = CSRF(pg)
            r = pg.request.post(B + f"/tickets/{tid}", form={"_csrf": cs, "accion": "aprobar", "comentario": "Aprobado por Claude (regla de Cristian) · certificación Núcleo"})
            pg.wait_for_timeout(3000); e_ = est()
            if e_.get("estado") != "pendiente_aprobacion": break
            pg.wait_for_timeout(5000)
        log("aprobado desde FaceOps (HTTP", r.status, ") · estado:", e_)
    for i in range(72):
        e_ = est()
        if e_.get("estado") in ("exitoso", "fallido", "error", "cancelado", "rechazado"): break
        pg.wait_for_timeout(5000)
    log("final:", e_)
    for i in range(24):
        pg.goto(B + "/inventario")
        if not pg.locator(".aviso-aap").count(): break
        pg.wait_for_timeout(5000)
    log("aviso global tras volver AAP:", pg.locator(".aviso-aap").count())
    print("RESUMEN " + json.dumps({"ticket": tk["numero"], "final": e_.get("estado"), "job": e_.get("job")}))
    b.close()
PY
python3 /tmp/n5.py 2>&1 | grep -v -i -E "clave|secret" | tail -40
/tmp/corte.sh off; rm -f /tmp/qa-n5.json /tmp/n5.py
echo "después · FaceOps→AAP: $(ping_fo) · regla presente: $(sudo iptables -C $REGLA 2>/dev/null && echo SI || echo no)"
