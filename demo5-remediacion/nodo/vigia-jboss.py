#!/usr/bin/python3
# Demo 5 · vigía de nodo-jboss: avisa a Event-Driven Ansible cuando
#   - el servicio de JBoss se detiene                 → jboss_detenido  (riesgo bajo)
#   - el directorio de logs supera el umbral          → logs_llenos     (riesgo bajo)
#   - JBoss está arriba pero la app responde con error → app_con_error   (riesgo alto)
# Solo observa; nunca corrige. La corrección la decide EDA y la ejecuta AAP.
import json, os, shutil, socket, subprocess, time, urllib.error, urllib.request

EDA = os.environ.get("EDA_URL", "http://192.168.122.1:5001/endpoint")
LOGS = os.environ.get("LOGS", "/var/log/app-jboss")
UMBRAL = int(os.environ.get("UMBRAL", "80"))
SERVIDOR = socket.gethostname().split(".")[0]
SEGUIDAS = {"jboss_detenido": 3, "logs_llenos": 2, "app_con_error": 5}   # chequeos seguidos (cada 5 s) antes de avisar
ESPERA = {"jboss_detenido": 150, "logs_llenos": 150, "app_con_error": 600}  # segundos sin repetir el mismo aviso


def servicio():
    return subprocess.run(["systemctl", "is-active", "eap8-standalone"], capture_output=True, text=True).stdout.strip()


def salud():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/app-jboss/salud", timeout=4) as r:
            return r.status, r.read(200).decode(errors="replace").strip()
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode(errors="replace").strip()
    except Exception as e:
        return 0, str(e)[:120]


def uso_logs():
    try:
        u = shutil.disk_usage(LOGS)
        return round(u.used * 100 / u.total)
    except OSError:
        return 0


def avisar(tipo, detalle):
    ev = dict(tipo=tipo, servidor=SERVIDOR, hora=time.strftime("%Y-%m-%d %H:%M:%S"), **detalle)
    req = urllib.request.Request(EDA, data=json.dumps(ev).encode(), headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
        print("evento enviado", ev, flush=True)
        return True
    except Exception as e:
        print("EDA no responde:", e, flush=True)
        return False


cuenta = {t: 0 for t in SEGUIDAS}
ultimo = {t: 0.0 for t in SEGUIDAS}
activo_desde = time.time()
estado_previo = "active"
while True:
    time.sleep(5)
    s = servicio()
    if s == "active" and estado_previo != "active":
        activo_desde = time.time()
    estado_previo = s
    actuales = {}
    if s != "active":
        actuales["jboss_detenido"] = {"servicio": s}
    elif time.time() - activo_desde > 90:          # tras un arranque, JBoss tarda en desplegar la app: no es error
        codigo, texto = salud()
        if codigo != 200:
            actuales["app_con_error"] = {"http": codigo, "respuesta": texto}
    p = uso_logs()
    if p >= UMBRAL:
        actuales["logs_llenos"] = {"ruta": LOGS, "uso_pct": p, "umbral_pct": UMBRAL}
    for t in SEGUIDAS:
        cuenta[t] = cuenta[t] + 1 if t in actuales else 0
        if cuenta[t] >= SEGUIDAS[t] and time.time() - ultimo[t] > ESPERA[t]:
            if avisar(t, actuales[t]):
                ultimo[t] = time.time()
