#!/usr/bin/python3
# Vigía: lee auditd, identifica quién cambió archivos vigilados y avisa al panel de cumplimiento.
import json, os, pwd, re, socket, time, urllib.request, uuid
from collections import deque
from datetime import datetime

LOG = "/var/log/audit/audit.log"
PANEL = os.environ.get("PANEL_URL", "http://192.168.122.1:8088/api/eventos")
IGNORAR = set(os.environ.get("IGNORAR_USUARIOS", "ansible").split(","))
VENTANA = 3  # segundos para agrupar cambios de una misma persona
HISTORIAL_SEG = 600  # comandos de los últimos 10 minutos que acompañan cada aviso
HISTORIAL_MAX = 6
SHELLS = {"bash", "sh", "zsh", "ksh"}

RX_SERIAL = re.compile(r"msg=audit\((\d+\.\d+):(\d+)\)")
RX_KV = re.compile(r'(\w+)=("([^"]*)"|\S+)')


def kv(line):
    return {m.group(1): m.group(3) if m.group(3) is not None else m.group(2) for m in RX_KV.finditer(line)}


def usuario(uid):
    try:
        return pwd.getpwuid(int(uid)).pw_name
    except (KeyError, ValueError):
        return str(uid)


def temporal(path):
    base = os.path.basename(path)
    return base.endswith((".swp", ".swx", "~", ".tmp")) or base == "4913" or base.startswith(("sed", ".#"))


def decodificar(proctitle):
    try:
        return bytes.fromhex(proctitle).replace(b"\0", b" ").decode(errors="replace").strip()
    except ValueError:
        return proctitle.strip('"')


sesiones = {}   # ses -> ip de origen
grupos = {}     # serial -> registros del evento
lotes = {}      # auid -> cambios acumulados
shells = {}     # pid de una shell de persona -> hora de inicio
historial = {}  # auid -> comandos recientes (hora, comando)


def registrar_comando(sc, registros, ts):
    # Solo comandos tecleados en una shell (hijos directos de la shell), no los internos de cada programa
    exe = sc.get("exe", "").strip('"')
    if os.path.basename(exe) in SHELLS:
        shells[sc.get("pid")] = ts
        return
    inicio = shells.get(sc.get("ppid"))
    if inicio is None or ts - inicio < 2:  # los primeros 2 s son el arranque de la sesión
        return
    titulo = next((r.get("proctitle", "") for r in registros if r["type"] == "PROCTITLE"), "")
    cmd = decodificar(titulo) if titulo else exe
    historial.setdefault(sc.get("auid"), deque(maxlen=30)).append((ts, cmd))


def procesar(registros, ts):
    sc = next((r for r in registros if r["type"] == "SYSCALL"), None)
    if not sc or sc.get("key") not in ("drift", "comandos"):
        return
    auid = sc.get("auid", "4294967295")
    if auid in ("4294967295", "-1"):
        return
    quien = usuario(auid)
    if quien in IGNORAR:
        return
    if sc.get("key") == "comandos":
        registrar_comando(sc, registros, ts)
        return
    cwd = next((r.get("cwd", "") for r in registros if r["type"] == "CWD"), "")
    archivos = []
    for r in registros:
        if r["type"] != "PATH" or r.get("nametype") == "PARENT":
            continue
        nombre = r.get("name", "")
        if not nombre or nombre == "(null)":
            continue
        if not nombre.startswith("/"):
            nombre = os.path.normpath(os.path.join(cwd, nombre))
        if not temporal(nombre):
            archivos.append(nombre)
    if not archivos:
        return
    titulo = next((r.get("proctitle", "") for r in registros if r["type"] == "PROCTITLE"), "")
    lote = lotes.setdefault(auid, {"usuario": quien, "archivos": set(), "comandos": [],
                                   "terminal": sc.get("tty", ""), "ses": sc.get("ses", ""),
                                   "como": usuario(sc.get("uid", "0")), "inicio": time.time()})
    lote["archivos"].update(archivos)
    cmd = decodificar(titulo) if titulo else sc.get("exe", "")
    if cmd and cmd not in lote["comandos"]:
        lote["comandos"].append(cmd)
    lote["ultimo"] = time.time()


def recientes(auid, respaldo):
    limite = time.time() - HISTORIAL_SEG
    cmds = [f"{datetime.fromtimestamp(t).strftime('%H:%M:%S')}  {c}" for t, c in historial.get(auid, []) if t >= limite]
    return cmds[-HISTORIAL_MAX:] or respaldo[:5]


def enviar(auid, lote):
    evento = {
        "id": uuid.uuid4().hex[:10],
        "host": socket.gethostname().split(".")[0],
        "usuario": lote["usuario"],
        "como": lote["como"],
        "origen_ip": sesiones.get(lote["ses"], "consola local"),
        "terminal": lote["terminal"],
        "comandos": recientes(auid, lote["comandos"]),
        "archivos": sorted(lote["archivos"]),
        "hora": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    try:
        req = urllib.request.Request(PANEL, data=json.dumps(evento).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except OSError as e:
        print("No se pudo avisar al panel:", e, flush=True)


def seguir(path):
    f = open(path, errors="replace")
    f.seek(0, os.SEEK_END)
    ino = os.fstat(f.fileno()).st_ino
    while True:
        line = f.readline()
        if line:
            yield line
            continue
        yield None
        try:
            if os.stat(path).st_ino != ino:
                f.close()
                f = open(path, errors="replace")
                ino = os.fstat(f.fileno()).st_ino
        except FileNotFoundError:
            pass
        time.sleep(0.3)


def main():
    for line in seguir(LOG):
        ahora = time.time()
        if line:
            m = RX_SERIAL.search(line)
            if m:
                d = kv(line)
                if d.get("type") in ("USER_START", "USER_LOGIN", "CRED_ACQ") and d.get("ses") and d.get("addr") not in (None, "?"):
                    sesiones[d["ses"]] = d["addr"]
                grupos.setdefault(m.group(2), {"t": ahora, "ts": float(m.group(1)), "r": []})["r"].append(d)
        for serial in [s for s, g in grupos.items() if ahora - g["t"] > 0.5]:
            g = grupos.pop(serial)
            procesar(g["r"], g["ts"])
        for auid in [a for a, l in lotes.items() if ahora - l["ultimo"] > VENTANA]:
            enviar(auid, lotes.pop(auid))
        if len(shells) > 500:  # limpia shells de más de 12 horas
            for pid in [p for p, t in shells.items() if ahora - t > 43200]:
                shells.pop(pid, None)


if __name__ == "__main__":
    main()
