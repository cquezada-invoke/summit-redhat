#!/bin/bash
# Demo 1 en Windows · prueba de drift en nodo-windows, como lo haría un administrador a mano (vía AAP, comando ad hoc).
# Se ejecuta en la VM. No crea usuarios: usa la cuenta integrada "Guest" (deshabilitada) como "intruso" en Administradores.
#   probar-windows.sh admin      → agrega Guest al grupo Administradores
#   probar-windows.sh servicio   → cambia el inicio de W3SVC (IIS) de automático a manual (no lo detiene: la Demo 5 no se activa)
#   probar-windows.sh hora       → detiene el servicio W32Time
#   probar-windows.sh limpiar    → deja nodo-windows igual a la base aprobada de Git (plantilla "Drift · Windows", modo corregir;
#                                  el vigía no lo reporta como incidente)
#   probar-windows.sh estado     → muestra servicios críticos y miembros de Administradores
# Qué esperar en el panel (https://34.176.42.56:9443), a los ~15-40 s:
#   "Vigilar y corregir" → incidente "restablecido" con el antes/después (YAML) y Ansible lo deja igual a la base de Git.
#   "Solo registrar"    → incidente "registrado" con el commit en Git (rama main, nodo-windows/windows.yml); no se corrige:
#                         usar "limpiar" después (eso también queda registrado).
# Cómo revertir la integración completa: botón "Quitar" de nodo-windows en el panel (quita la tarea programada y la carpeta
# C:\ProgramData\SummitDrift); la plantilla "Drift · Windows" y la base en master pueden quedar.
set -e
ACCION=${1:-estado}
python3 - "$ACCION" <<'EOF'
import base64, glob, json, os, re, ssl, sys, time, urllib.parse, urllib.request
inv = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
clave = re.search(r"^gateway_admin_password=(.*)$", open(inv).read(), re.M).group(1).strip()
AUTH = "Basic " + base64.b64encode(f"admin:{clave}".encode()).decode()
CTX = ssl._create_unverified_context()

def aap(m, p, d=None):
    r = urllib.request.Request("https://localhost/api/controller/v2/" + p, method=m, data=json.dumps(d).encode() if d else None,
                               headers={"Content-Type": "application/json", "Authorization": AUTH})
    with urllib.request.urlopen(r, context=CTX, timeout=60) as x:
        b = x.read()
        return json.loads(b) if b else {}

uno = lambda rec, n: aap("GET", f"{rec}/?name=" + urllib.parse.quote(n))["results"][0]["id"]
INV, CRED = uno("inventories", "Servidores Windows"), uno("credentials", "Windows · ansible (WinRM)")
PASOS = {
    "admin":    [("win_user", "name=Guest groups=Administrators groups_action=add")],
    "servicio": [("win_service", "name=W3SVC start_mode=manual")],
    "hora":     [("win_service", "name=W32Time state=stopped")],
    "limpiar":  [],
    "estado":   [("win_service", "name=W3SVC"), ("win_service", "name=W32Time"), ("win_user", "name=Guest")],
}
accion = sys.argv[1]
if accion not in PASOS:
    sys.exit("uso: probar-windows.sh admin|servicio|hora|limpiar|estado")
if accion == "limpiar":
    jt = uno("job_templates", "Drift · Windows")
    j = aap("POST", f"job_templates/{jt}/launch/", {"extra_vars": {"modo": "corregir", "servidor": "nodo-windows",
                                                                   "solicitado_por": "probar-windows.sh"}})["id"]
    while (st := aap("GET", f"jobs/{j}/")["status"]) not in ("successful", "failed", "error", "canceled"):
        time.sleep(3)
    print(f"  Drift · Windows (corregir) job {j} → {st}")
for modulo, args in PASOS[accion]:
    j = aap("POST", "ad_hoc_commands/", {"inventory": INV, "credential": CRED, "limit": "nodo-windows",
                                         "module_name": modulo, "module_args": args})["id"]
    while (st := aap("GET", f"ad_hoc_commands/{j}/")["status"]) not in ("successful", "failed", "error", "canceled"):
        time.sleep(2)
    ev = [e for e in aap("GET", f"ad_hoc_commands/{j}/events/?page_size=50")["results"] if e["event"].startswith("runner_on_")]
    res = ev[-1]["event_data"].get("res", {}) if ev else {}
    if accion == "estado":
        if modulo == "win_service":
            print(f"  {res.get('name')}: {res.get('state')} · inicio {res.get('start_mode')}")
        else:
            print("  Guest en: " + ", ".join(res.get("groups", []) and [g.get("name") for g in res["groups"]] or ["(ningún grupo)"]))
    else:
        print(f"  {modulo} {args} → {st}{' (cambió)' if res.get('changed') else ''}")
if accion != "estado":
    print("Mira el panel: https://34.176.42.56:9443")
EOF
