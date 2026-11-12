#!/usr/bin/python3
# Al encender la VM, las activaciones de Event-Driven Ansible quedan en "error": al arrancar bajan la imagen del
# decision environment desde el registro de Automation Hub (pasando por el gateway), y mientras Hub no termina de
# levantar el registro responde 503 ("received unexpected HTTP status: 503 Service Unavailable"). Con la política
# on-failure agotan sus reintentos antes de que Hub esté listo y no vuelven solas.
# Este script espera a que la API de EDA y el registro de cada decision environment respondan (sin 5xx, varias
# veces seguidas), reinicia las activaciones que no estén "running" y verifica hasta que todas queden arriba,
# reintentando las que vuelvan a caer. Lo usa summit-eda.service (herramientas/summit-eda.service).
import json, ssl, base64, glob, re, os, sys, time, urllib.request, urllib.error
from urllib.parse import urlsplit

PLAZO = int(os.environ.get("EDA_PLAZO", "2400"))  # segundos máximos en total
T0 = time.time()
CL = re.search(r"^gateway_admin_password=(.*)$", open(glob.glob(os.path.expanduser("~/aap/*/inventory-demo"))[0]).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context(); AU = "Basic " + base64.b64encode(f"admin:{CL}".encode()).decode()

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
def queda(): return PLAZO - (time.time() - T0)

def eda(m, p, d=None):
    r = urllib.request.Request("https://localhost/api/eda/v1/" + p, method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Authorization": AU, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, context=CTX, timeout=30) as x:
        b = x.read(); return json.loads(b) if b else {}

def codigo(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), context=CTX, timeout=15) as x: return x.status
    except urllib.error.HTTPError as e: return e.code
    except Exception: return 0

def esperar(desc, ok, seguidas=3, pausa=10):
    n = 0
    while queda() > 0:
        n = n + 1 if ok() else 0
        if n >= seguidas: log("listo:", desc); return True
        time.sleep(pausa)
    log("sin respuesta a tiempo:", desc); return False

def activaciones(): return eda("GET", "activations/?page_size=100")["results"]

def eda_ok():
    try: activaciones(); return True
    except Exception: return False

if not esperar("API de EDA", eda_ok): sys.exit("EDA no responde")

# Registros de los decision environments en uso (Hub responde 401 sin credenciales cuando está listo; 5xx o 0 = no listo)
usados = {a["decision_environment_id"] for a in activaciones()}
regs = set()
for d in eda("GET", "decision-environments/?page_size=100")["results"]:
    if d["id"] in usados and d.get("image_url"):
        h = d["image_url"].split("/")[0]
        if "." in h or ":" in h: regs.add(h)
for h in sorted(regs):
    esperar(f"registro {h}", lambda h=h: 200 <= codigo(f"https://{h}/v2/") < 500 and codigo("https://localhost/api/galaxy/pulp/api/v3/status/") == 200)

ronda = 0
while queda() > 0:
    malas = [a for a in activaciones() if a["is_enabled"] and a["status"] != "running"]
    if not malas:
        log("todas las activaciones habilitadas están running"); break
    if ronda and all(a["status"] in ("starting", "pending") for a in malas):
        time.sleep(20); continue
    ronda += 1
    for a in malas:
        if a["status"] in ("starting", "pending"): continue
        try:
            eda("POST", f"activations/{a['id']}/restart/", {}); log(f"ronda {ronda} · reiniciada:", a["name"], f"(estaba {a['status']})")
        except urllib.error.HTTPError as e:
            log(f"ronda {ronda} · no se pudo reiniciar:", a["name"], e.code)
        time.sleep(3)  # escalonado: no bajar la misma imagen 12 veces a la vez
    time.sleep(90)
else:
    log("plazo agotado; siguen sin running:", ", ".join(a["name"] for a in activaciones() if a["is_enabled"] and a["status"] != "running"))
    sys.exit(1)
