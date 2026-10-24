#!/usr/bin/python3
# Vigía del router: si la configuración en ejecución cambia (por ejemplo, alguien entra por consola),
# avisa a Event-Driven Ansible con un evento HTTP. Corre dentro del contenedor del equipo.
import hashlib, json, os, socket, subprocess, time, urllib.request

EDA = os.environ.get("EDA_URL", "http://172.20.20.1:5000/endpoint")
EQUIPO = socket.gethostname()


def config():
    out = subprocess.run(["vtysh", "-c", "show running-config"], capture_output=True, text=True).stdout
    return "\n".join(l for l in out.splitlines() if l.strip() and not l.startswith(("Building", "Current configuration", "frr version")))


def avisar(antes, ahora):
    quitado = sorted(set(antes.splitlines()) - set(ahora.splitlines()))
    agregado = sorted(set(ahora.splitlines()) - set(antes.splitlines()))
    ev = {"tipo": "cambio_config", "equipo": EQUIPO, "quitado": quitado[:20], "agregado": agregado[:20],
          "hora": time.strftime("%Y-%m-%d %H:%M:%S")}
    req = urllib.request.Request(EDA, data=json.dumps(ev).encode(), headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
        print("evento enviado", ev, flush=True)
    except Exception as e:
        print("EDA no responde:", e, flush=True)


base = config()
while True:
    time.sleep(3)
    ahora = config()
    if ahora and ahora != base:
        time.sleep(4)                      # agrupa cambios seguidos en un solo evento
        ahora = config()
        avisar(base, ahora)
        time.sleep(90)                     # deja que AAP restaure antes de tomar la nueva línea base
        base = config()
