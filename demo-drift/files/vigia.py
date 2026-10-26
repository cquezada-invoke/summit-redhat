#!/usr/bin/python3
# Vigía: lee auditd, identifica quién cambió archivos vigilados y avisa al panel de cumplimiento.
# También vigila los paquetes (rpm): instalaciones, desinstalaciones y cambios de versión, con quién los hizo (dnf history).
import json, os, pwd, re, socket, subprocess, time, urllib.request, uuid
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


HOME_PROF = 3  # /home/<usuario>/<algo>/<algo>: solo los dos primeros niveles de cada home


def en_home(path):
    """Cambios en /home que interesan: visibles (sin partes ocultas) y a poca profundidad."""
    if not path.startswith("/home/"):
        return False
    partes = [x for x in path[len("/home/"):].split("/") if x]
    return 1 < len(partes) <= HOME_PROF and not any(x.startswith(".") for x in partes)


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
    if not sc or sc.get("key") not in ("drift", "comandos", "home"):
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
        if temporal(nombre):
            continue
        if sc.get("key") == "home" and not en_home(nombre):
            continue  # historial, caché y archivos ocultos de la sesión no cuentan
        archivos.append(nombre)
    if not archivos:
        return
    tipo = "home" if sc.get("key") == "home" else "drift"
    titulo = next((r.get("proctitle", "") for r in registros if r["type"] == "PROCTITLE"), "")
    lote = lotes.setdefault(f"{auid}|{tipo}", {"usuario": quien, "tipo": tipo, "archivos": set(), "comandos": [],
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


def enviar(clave, lote):
    auid = clave.split("|")[0]
    evento = {
        "tipo": lote.get("tipo", "drift"),
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


# ---------- paquetes ----------
RPMDB = next((p for p in ("/var/lib/rpm/rpmdb.sqlite", "/var/lib/rpm/Packages") if os.path.exists(p)), None)
RX_NEVRA = re.compile(r"^(?P<n>.+)-(?P<v>[^-]+)-(?P<r>[^-]+)\.(?P<a>[^.]+)$")
paquetes = {"lista": None, "mtime": 0, "cambio": 0}


def lista_paquetes():
    out = subprocess.run(["rpm", "-qa", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n"],
                         capture_output=True, text=True).stdout.split()
    d = {}
    for p in out:
        m = RX_NEVRA.match(p)
        if m and m["n"] != "gpg-pubkey":
            d[f'{m["n"]}.{m["a"]}'] = p
    return d


def ultima_transaccion():
    """Quién y con qué comando hizo el último cambio de paquetes (dnf history)."""
    try:
        out = subprocess.run(["dnf", "-q", "history", "info", "last"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    quien = re.search(r"^User\s*:.*?<([^>]+)>", out, re.M)
    cmd = re.search(r"^Command Line\s*:\s*(.*)$", out, re.M)
    return (quien.group(1) if quien else ""), (cmd.group(1).strip() if cmd else "")


def revisar_paquetes():
    if not RPMDB:
        return
    try:
        mt = os.stat(RPMDB).st_mtime
    except OSError:
        return
    ahora = time.time()
    if paquetes["lista"] is None:
        paquetes.update(lista=lista_paquetes(), mtime=mt)
        return
    if mt != paquetes["mtime"]:
        paquetes.update(mtime=mt, cambio=ahora)  # espera a que termine la transacción
        return
    if not paquetes["cambio"] or ahora - paquetes["cambio"] < 4:
        return
    paquetes["cambio"] = 0
    antes, despues = paquetes["lista"], lista_paquetes()
    paquetes["lista"] = despues
    cambios = [f"paquete instalado: {despues[k]}" for k in sorted(despues) if k not in antes]
    cambios += [f"paquete desinstalado: {antes[k]}" for k in sorted(antes) if k not in despues]
    cambios += [f"paquete actualizado: {antes[k]} → {despues[k]}" for k in sorted(despues) if k in antes and antes[k] != despues[k]]
    if not cambios:
        return
    quien, cmd = ultima_transaccion()
    if not quien or quien in IGNORAR:  # lo hizo la automatización (ansible): no es un cambio manual
        return
    evento = {"tipo": "paquetes", "id": uuid.uuid4().hex[:10], "host": socket.gethostname().split(".")[0],
              "usuario": quien, "como": "root", "origen_ip": "dnf/rpm", "terminal": "",
              "comandos": [f"{datetime.now().strftime('%H:%M:%S')}  dnf {cmd}"] if cmd else [],
              "archivos": cambios[:40], "hora": datetime.now().astimezone().isoformat(timespec="seconds")}
    try:
        req = urllib.request.Request(PANEL, data=json.dumps(evento).encode(), headers={"Content-Type": "application/json"})
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
    ultimo_rpm = 0
    for line in seguir(LOG):
        ahora = time.time()
        if ahora - ultimo_rpm > 2:
            ultimo_rpm = ahora
            revisar_paquetes()
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
