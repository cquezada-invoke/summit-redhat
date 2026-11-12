#!/usr/bin/python3
"""Autocuidado del anfitrión del laboratorio (AAP, sus máquinas virtuales y FaceOps) · herramienta única del anfitrión.

La instalan «aap/autocuidado_configurar.py» (desde configurar.py) en /usr/local/sbin/faceops-autocuidado (root, 0755) y la usan:
  · los playbooks «Autocuidado · …» de AAP: el usuario aap-kvm la llama con sudo, y SOLO a ella (sudoers acotado);
  · el vigía externo (faceops-autocuidado-vigia.timer, cada 2 min) que actúa SOLO si AAP no responde;
  · el exportador de Prometheus (faceops-autocuidado-exportador.service, 127.0.0.1:9469).
Cada operación es segura por diseño: lista blanca de rutas y patrones, nunca volúmenes, discos de VM, bases ni datos de
usuario; contenedores de la plataforma protegidos; modo «evaluar» sin cambios. Salida: una línea JSON por stdout. Toda
acción que cambia algo queda en /var/lib/faceops/autocuidado/registro.jsonl (FaceOps la muestra y la vuelve ticket).
Solo biblioteca estándar de Python 3.9 (RHEL 9). No imprime tokens ni claves."""
import argparse
import datetime as dt
import fcntl
import fnmatch
import glob
import gzip
import json
import os
import pwd
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.0"
CONF = "/etc/faceops-autocuidado.conf"
DIR = "/var/lib/faceops/autocuidado"
REGISTRO = f"{DIR}/registro.jsonl"
ESTADO_VIGIA = f"{DIR}/vigia.json"
PAUSA = f"{DIR}/pausa"   # mantenimiento: hasta esa hora (epoch) el vigía solo observa («faceops-autocuidado pausar --minutos N»)
CANDADO = "/run/faceops-autocuidado.lock"
DEFECTO = {
    "aap_usuario": "cquezada",
    "aap_dir": "/home/cquezada/aap",
    "aap_api": "https://127.0.0.1/api/controller/v2/ping/",
    "faceops_url": "http://192.168.122.1:8110/ingresar",
    "faceops_bd": "/var/lib/faceops/faceops.db",
    "faceops_env": "/etc/faceops.env",
    "repo": "/home/cquezada/summit-redhat",
    "autodeploy_dir": "/home/cquezada/faceops-autodeploy",
    "respaldos": "/var/backups/faceops-autocuidado",
    "respaldos_conservar": 7,
    "certificados": [{"servicio": "aap", "host": "127.0.0.1", "puerto": 443, "sni": ""},
                     {"servicio": "faceops", "host": "127.0.0.1", "puerto": 8843, "sni": "faceops.34.176.42.56.sslip.io"}],
    # artefactos de trabajos y del runner: solo directorios/archivos que calzan con el patrón y más antiguos que «dias»
    "artefactos": [{"ruta": "/tmp", "patron": "awx_*", "dias": 2},
                   {"ruta": "/home/cquezada/aap/controller/data/job_execution", "patron": "awx_*", "dias": 2},
                   {"ruta": "/home/cquezada/actions-runner/_work/_temp", "patron": "*", "dias": 1},
                   {"ruta": "/home/cquezada/vmrun/wtx", "patron": "*", "dias": 1},
                   {"ruta": "/var/lib/systemd/coredump", "patron": "core.*", "dias": 1}],
    "logs_rotados": ["/var/log", "/home/cquezada/aap"],
    "journal_tamano": "500M", "journal_dias": 7,
    "vigia_umbral_disco": 95, "vigia_emergencia_disco": 98, "vigia_gracia_min": 15, "vigia_caida_min": 4,
    "vigia_reinicios_max_dia": 4, "vigia_pausa_reinicio_min": 15,
    "exportador": "127.0.0.1:9469",
}
# Contenedores de la plataforma: nunca se eliminan (podrían no volver a crearse); solo se reinician con su unidad
PROTEGIDOS = ["automation-*", "receptor*", "postgresql*", "redis*", "summit-*", "faceops*", "gitea*", "vault*"]
# Orden de arranque de AAP containerized: base de datos y caché → gateway → controller → receptor → EDA → hub
ORDEN_AAP = ["postgresql", "redis-unix", "redis-tcp", "automation-gateway-proxy", "automation-gateway", "automation-controller-rsyslog",
             "automation-controller-task", "automation-controller-web", "receptor", "automation-eda-api", "automation-eda-daphne",
             "automation-eda-web", "automation-eda-scheduler", "automation-eda-worker", "automation-eda-activation-worker",
             "automation-hub-api", "automation-hub-content", "automation-hub-worker", "automation-hub-web"]
BASE_AAP = ("postgresql", "redis-unix", "redis-tcp")
# Lo que sostiene al propio trabajo de AAP: un playbook lanzado por AAP no puede reiniciarlo (moriría con él); lo hace el vigía
PROPIOS = ("automation-controller-task", "receptor", "postgresql", "redis-unix", "redis-tcp")
FS_REALES = ("xfs", "ext4", "ext3", "btrfs", "vfat")


# ── utilidades ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def conf():
    c = dict(DEFECTO)
    try:
        with open(CONF) as f:
            c.update(json.load(f))
    except (OSError, ValueError):
        pass
    return c


def ahora():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(argv, timeout=120, entrada=None):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=entrada)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: no existe"
    except subprocess.TimeoutExpired:
        return 124, "", "tiempo agotado"


def como_usuario(c, argv, timeout=120):
    """Ejecuta como el dueño de AAP (podman sin root y su systemd de usuario)."""
    u = pwd.getpwnam(c["aap_usuario"])
    env = ["env", f"XDG_RUNTIME_DIR=/run/user/{u.pw_uid}", f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{u.pw_uid}/bus", f"HOME={u.pw_dir}"]
    return sh(["runuser", "-u", c["aap_usuario"], "--", *env, *argv], timeout)


def registrar(accion, origen, resultado, detalle=None):
    os.makedirs(DIR, exist_ok=True)
    linea = {"cuando": ahora(), "accion": accion, "origen": origen, "resultado": resultado, "detalle": detalle or {}}
    try:
        if os.path.exists(REGISTRO) and os.path.getsize(REGISTRO) > 2_000_000:
            os.replace(REGISTRO, REGISTRO + ".1")
        with open(REGISTRO, "a") as f:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
        os.chmod(REGISTRO, 0o644)
    except OSError:
        pass   # disco lleno: la acción sigue; el resultado sale igual por stdout
    return linea


def candado():
    f = open(CANDADO, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        salir({"ok": False, "error": "otra acción de autocuidado está en curso; reintenta en unos minutos"}, 3)
    return f


def salir(d, rc=0):
    print(json.dumps(d, ensure_ascii=False, default=str), flush=True)
    sys.exit(rc)


def montajes():
    out = []
    with open("/proc/mounts") as f:
        for l in f:
            p = l.split()
            if len(p) < 4 or p[2] not in FS_REALES or p[1].startswith(("/run", "/sys", "/proc", "/boot/efi")) or "ro" in p[3].split(","):
                continue
            if any(m["montaje"] == p[1] for m in out):
                continue
            try:
                s = os.statvfs(p[1])
            except OSError:
                continue
            tot, libre = s.f_blocks * s.f_frsize, s.f_bavail * s.f_frsize
            ino = s.f_files or 0
            out.append({"montaje": p[1], "dispositivo": p[0], "total_gb": round(tot / 2**30, 1), "libre_gb": round(libre / 2**30, 2),
                        "uso_pct": round(100 * (1 - libre / tot), 1) if tot else 0.0,
                        "inodos_pct": round(100 * (1 - (s.f_favail / ino)), 1) if ino else 0.0})
    return out


def uso_raiz():
    return next((m["uso_pct"] for m in montajes() if m["montaje"] == "/"), 0.0)


def libre_bytes(ruta="/"):
    s = os.statvfs(ruta)
    return s.f_bavail * s.f_frsize


def memoria():
    m = {}
    with open("/proc/meminfo") as f:
        for l in f:
            k, v = l.split(":", 1)
            m[k] = int(v.split()[0]) * 1024
    tot, disp = m.get("MemTotal", 1), m.get("MemAvailable", 0)
    st, sf = m.get("SwapTotal", 0), m.get("SwapFree", 0)
    return {"memoria_pct": round(100 * (1 - disp / tot), 1), "swap_pct": round(100 * (1 - sf / st), 1) if st else 0.0}


def carga():
    l1, l5, l15 = os.getloadavg()
    n = os.cpu_count() or 1
    return {"carga15": round(l15, 2), "cpus": n, "carga15_por_cpu": round(l15 / n, 2)}


# ── máquinas virtuales (libvirt) ───────────────────────────────────────────────────────────────────────────────────────
def vms():
    rc, out, _ = sh(["virsh", "-c", "qemu:///system", "list", "--all", "--name"], 30)
    if rc != 0:
        return None
    res = []
    for n in [x.strip() for x in out.splitlines() if x.strip()]:
        _, est, _ = sh(["virsh", "-c", "qemu:///system", "domstate", n, "--reason"], 15)
        _, info, _ = sh(["virsh", "-c", "qemu:///system", "dominfo", n], 15)
        est = est.strip()
        estado = est.split(" (")[0].strip()
        razon = (re.search(r"\((.*)\)", est) or [None, ""])[1]
        res.append({"vm": n, "estado": estado, "razon": razon, "autoinicio": bool(re.search(r"Autostart:\s+enable", info))})
    return res


def pausada_por_io(v):
    return v["estado"] == "paused" and v["razon"].lower().replace("-", " ") in ("i/o error", "ioerror", "io error", "unknown")


def caida(v):
    return v["estado"] == "crashed" or (v["estado"] == "shut off" and v["razon"] == "crashed") or \
        (v["autoinicio"] and v["estado"] == "shut off" and v["razon"] not in ("shutdown", "destroyed", "saved"))


def recuperar_vms(origen, evaluar=False, solo_pausadas=False):
    lista = vms()
    if lista is None:
        return {"ok": False, "error": "libvirt no responde (virsh)"}
    raiz = uso_raiz()
    hechas, omitidas = [], []
    for v in lista:
        if pausada_por_io(v):
            if raiz >= 95:   # sin espacio la máquina volvería a pausarse: primero liberar disco
                omitidas.append({"vm": v["vm"], "motivo": f"disco raíz al {raiz} %: libera espacio antes de reanudar"})
                continue
            if not evaluar:
                rc, _, err = sh(["virsh", "-c", "qemu:///system", "resume", v["vm"]], 60)
                hechas.append({"vm": v["vm"], "accion": "reanudar", "ok": rc == 0, "error": err.strip()[:200] if rc else ""})
            else:
                hechas.append({"vm": v["vm"], "accion": "reanudar (evaluar)"})
        elif v["estado"] == "paused":
            omitidas.append({"vm": v["vm"], "motivo": f"pausada a propósito ({v['razon'] or 'sin razón'}): no se toca"})
        elif not solo_pausadas and caida(v):
            if not evaluar:
                rc, _, err = sh(["virsh", "-c", "qemu:///system", "start", v["vm"]], 90)
                hechas.append({"vm": v["vm"], "accion": "encender", "ok": rc == 0, "error": err.strip()[:200] if rc else ""})
            else:
                hechas.append({"vm": v["vm"], "accion": "encender (evaluar)"})
    ok = all(h.get("ok", True) for h in hechas)
    r = {"ok": ok, "modo": "evaluar" if evaluar else "aplicar", "acciones": hechas, "omitidas": omitidas, "disco_raiz_pct": raiz}
    if hechas and not evaluar:
        registrar("recuperar-vms", origen, "ok" if ok else "con errores", r)
    return r


# ── contenedores de AAP (podman sin root del usuario de AAP) ───────────────────────────────────────────────────────────
def contenedores(c):
    rc, out, err = como_usuario(c, ["podman", "ps", "-a", "--format", "json"], 60)
    if rc != 0:
        return None
    try:
        lista = json.loads(out or "[]")
    except ValueError:
        return None
    res = []
    for x in lista:
        n = (x.get("Names") or [""])[0]
        if not any(fnmatch.fnmatch(n, p) for p in ("automation-*", "receptor*", "postgresql*", "redis*")):
            continue
        st = (x.get("State") or "").lower()
        texto = x.get("Status") or ""
        res.append({"nombre": n, "estado": st, "salud": "unhealthy" if "unhealthy" in texto else ("starting" if "starting" in texto else
                    ("healthy" if "healthy" in texto else "")), "reinicios": int(x.get("Restarts") or 0), "texto": texto[:80]})
    return res


def malo(x):
    return x["estado"] not in ("running",) or x["salud"] == "unhealthy"


def orden(n):
    for i, p in enumerate(ORDEN_AAP):
        if n == p or n.startswith(p + "-") or (p.endswith("worker") and n.startswith(p)):
            return i
    return len(ORDEN_AAP)


def api_aap(c, timeout=6):
    try:
        with urllib.request.urlopen(urllib.request.Request(c["aap_api"]), context=ssl._create_unverified_context(), timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return 0


def reiniciar_aap(c, origen, evaluar=False, todos=False, sin_propios=False):
    lista = contenedores(c)
    if lista is None:
        return {"ok": False, "error": f"no se pudo listar los contenedores de {c['aap_usuario']} (podman)"}
    malos = [x for x in lista if malo(x)]
    if todos:
        objetivo = list(lista)
    elif any(x["nombre"].startswith(BASE_AAP) for x in malos):   # cayó la base: todo lo que depende de ella se reinicia detrás
        objetivo = list(lista)
    else:
        objetivo = malos
    objetivo.sort(key=lambda x: orden(x["nombre"]))
    para_vigia = []
    if sin_propios:
        para_vigia = [x["nombre"] for x in objetivo if x["nombre"].startswith(PROPIOS)]
        objetivo = [x for x in objetivo if not x["nombre"].startswith(PROPIOS)]
    hechos = []
    if evaluar:
        return {"ok": True, "modo": "evaluar", "malos": malos, "reiniciaria": [x["nombre"] for x in objetivo], "para_vigia": para_vigia, "api": api_aap(c)}
    if not objetivo and not para_vigia:
        return {"ok": True, "modo": "aplicar", "reiniciados": [], "api": api_aap(c), "nota": "todos los componentes de AAP están sanos: nada que reiniciar"}
    if not objetivo and para_vigia:
        return {"ok": False, "error": "los componentes caídos sostienen al propio AAP (" + ", ".join(para_vigia) + "): los reinicia el vigía externo", "para_vigia": para_vigia}
    if uso_raiz() >= 99:
        return {"ok": False, "error": "disco raíz lleno: libera espacio antes de reiniciar AAP (los contenedores no arrancarían)"}
    for x in objetivo:
        rc, _, err = como_usuario(c, ["systemctl", "--user", "restart", x["nombre"] + ".service"], 180)
        via = "systemd"
        if rc != 0:   # sin unidad de systemd: reinicio directo del contenedor
            rc, _, err = como_usuario(c, ["podman", "restart", "-t", "30", x["nombre"]], 180)
            via = "podman"
        hechos.append({"contenedor": x["nombre"], "via": via, "ok": rc == 0, "error": err.strip()[-200:] if rc else ""})
        if x["nombre"].startswith(BASE_AAP):
            time.sleep(10)
    codigo = 0
    for _ in range(36):   # hasta 6 min a que la API de AAP vuelva
        codigo = api_aap(c)
        if codigo == 200:
            break
        time.sleep(10)
    despues = contenedores(c) or []
    siguen = [x["nombre"] for x in despues if malo(x) and x["nombre"] in {h["contenedor"] for h in hechos}]
    r = {"ok": all(h["ok"] for h in hechos) and codigo == 200 and not siguen and not para_vigia, "modo": "aplicar", "reiniciados": hechos,
         "api": codigo, "malos_antes": [x["nombre"] for x in malos], "siguen_mal": siguen, "para_vigia": para_vigia}
    if hechos:
        registrar("reiniciar-aap", origen, "ok" if r["ok"] else "con errores", r)
    return r


DESPACHADOR = ("receptor", "automation-controller-task")   # malla de ejecución y despachador de trabajos, en ese orden


def despachador_atascado(c):
    """(atascado, motivo, datos): una instancia sin latido > 5 min, o trabajos pendientes > 10 min con capacidad libre."""
    d = capacidad_aap(c)
    if not d:
        return False, "", d
    viejo = {k: v for k, v in (d.get("latidos") or {}).items() if v > 300}
    if viejo:
        return True, "instancia sin latido: " + ", ".join(f"{k} hace {v // 60} min" for k, v in viejo.items()), d
    if d.get("pendientes_antiguos", 0) > 0 and d.get("capacidad_libre", 0) > 0:
        return True, f"{d['pendientes_antiguos']} trabajos pendientes hace más de 10 min con capacidad libre ({d['capacidad_libre']})", d
    return False, "", d


def reiniciar_despachador(c, origen, evaluar=False, diferido=False):
    """Reinicia receptor y automation-controller-task (lo que despacha y ejecuta los trabajos de AAP) y espera el latido.
    Desde AAP va diferido (systemd-run en 45 s): el propio trabajo termina antes de que se reinicie quien lo ejecuta."""
    atascado, motivo, d = despachador_atascado(c)
    if evaluar:
        return {"ok": True, "modo": "evaluar", "atascado": atascado, "motivo": motivo, "latidos": d.get("latidos"),
                "pendientes_antiguos": d.get("pendientes_antiguos"), "capacidad_libre": d.get("capacidad_libre")}
    if diferido:
        rc, _, err = sh(["systemd-run", "--unit", f"faceops-autocuidado-despachador-{int(time.time())}", "--on-active=45",
                         "-p", "Restart=on-failure", "-p", "RestartSec=30",
                         "/usr/local/sbin/faceops-autocuidado", "reiniciar-despachador", "--origen", "aap"], 30)
        r = {"ok": rc == 0, "modo": "diferido", "en_segundos": 45, "motivo": motivo or "pedido sin atasco detectado", "error": err.strip()[-200:] if rc else ""}
        registrar("reiniciar-despachador (programado)", origen, "ok" if r["ok"] else "con errores", r)
        return r
    if uso_raiz() >= 99:
        return {"ok": False, "error": "disco raíz lleno: libera espacio antes de reiniciar el despachador"}
    hechos = []
    for n in DESPACHADOR:
        rc, _, err = como_usuario(c, ["systemctl", "--user", "restart", n + ".service"], 180)
        via = "systemd"
        if rc != 0:
            rc, _, err = como_usuario(c, ["podman", "restart", "-t", "30", n], 180)
            via = "podman"
        hechos.append({"contenedor": n, "via": via, "ok": rc == 0, "error": err.strip()[-200:] if rc else ""})
    latido = None
    for _ in range(30):   # hasta 5 min a que la instancia vuelva a reportar latido
        time.sleep(10)
        lat = (capacidad_aap(c).get("latidos") or {})
        if lat and max(lat.values()) < 120:
            latido = max(lat.values())
            break
    r = {"ok": all(h["ok"] for h in hechos) and latido is not None, "modo": "aplicar", "motivo": motivo, "reiniciados": hechos,
         "latido_s": latido}
    registrar("reiniciar-despachador", origen, "ok" if r["ok"] else "con errores", r)
    return r


# ── disco ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _viejo(ruta, dias):
    try:
        return time.time() - os.lstat(ruta).st_mtime > dias * 86400
    except OSError:
        return False


def _tamano(ruta):
    tot = 0
    if os.path.islink(ruta) or os.path.isfile(ruta):
        try:
            return os.lstat(ruta).st_size
        except OSError:
            return 0
    for raiz, _, archivos in os.walk(ruta, onerror=lambda e: None):
        for a in archivos:
            try:
                tot += os.lstat(os.path.join(raiz, a)).st_size
            except OSError:
                pass
    return tot


def _borrar(ruta):
    if os.path.isdir(ruta) and not os.path.islink(ruta):
        shutil.rmtree(ruta, ignore_errors=True)
    else:
        try:
            os.unlink(ruta)
        except OSError:
            pass


def _podman_prune(argv_pre, profundo, evaluar):
    """Imágenes colgantes (o sin uso, en profundo) y contenedores detenidos NO protegidos con más de 24 h. Nunca volúmenes."""
    det = {}
    rc, out, _ = sh(argv_pre + ["podman", "ps", "-a", "--filter", "status=exited", "--filter", "status=created", "--format", "json"], 60)
    borrables = []
    try:
        for x in json.loads(out or "[]") if rc == 0 else []:
            n = (x.get("Names") or [""])[0]
            fin = max(float(x.get("ExitedAt") or 0), float(x.get("Created") or 0))   # «created» sin salir trae ExitedAt negativo
            if any(fnmatch.fnmatch(n, p) for p in PROTEGIDOS) or (time.time() - fin < 86400):
                continue
            borrables.append(n)
    except (ValueError, TypeError):
        pass
    det["contenedores"] = borrables
    if not evaluar and borrables:
        sh(argv_pre + ["podman", "rm", *borrables], 300)
    if evaluar:
        rc, out, _ = sh(argv_pre + ["podman", "images", "-f", "dangling=true", "-q"], 60)
        det["imagenes"] = len([x for x in out.split() if x])
    else:
        sh(argv_pre + ["podman", "image", "prune", "-f"] + (["-a"] if profundo else []), 900)
    return det


def liberar_disco(c, origen, modo="seguro"):
    evaluar = modo == "evaluar"
    profundo = modo == "profundo"
    antes = {m["montaje"]: m["libre_gb"] for m in montajes()}
    pasos = []

    def paso(nombre, fn):
        l0 = libre_bytes("/")
        try:
            d = fn() or {}
            ok = True
        except Exception as x:  # noqa: BLE001  un paso que falla no frena los demás
            d, ok = {"error": f"{type(x).__name__}: {x}"[:200]}, False
        pasos.append({"paso": nombre, "ok": ok, "liberado_mb": round((libre_bytes("/") - l0) / 2**20, 1), **d})

    def journal():
        if evaluar:
            rc, out, _ = sh(["journalctl", "--disk-usage"], 30)
            return {"uso": (re.search(r"take up ([\d.]+\w)", out) or [None, "?"])[1]}
        tam = "200M" if profundo else c["journal_tamano"]
        sh(["journalctl", f"--vacuum-size={tam}", f"--vacuum-time={c['journal_dias']}d"], 300)
        return {"tope": tam}

    def podman_root():
        return _podman_prune([], profundo, evaluar)

    def podman_aap():
        u = pwd.getpwnam(c["aap_usuario"])
        pre = ["runuser", "-u", c["aap_usuario"], "--", "env", f"XDG_RUNTIME_DIR=/run/user/{u.pw_uid}", f"HOME={u.pw_dir}"]
        return _podman_prune(pre, False, evaluar)   # en AAP nunca «-a»: las imágenes de los entornos de ejecución se reutilizan

    def logs():
        dias = 1 if profundo else 3
        n, b = 0, 0
        for base in c["logs_rotados"]:
            for raiz, dirs, archivos in os.walk(base, onerror=lambda e: None):
                if "/containers/storage" in raiz or "/postgresql" in raiz or "/data/" in raiz + "/":
                    dirs[:] = []
                    continue
                for a in archivos:
                    if re.search(r"(\.gz|\.xz|\.bz2|\.zst|[.-]\d+|\.old)$", a) and ("log" in raiz.lower() or "log" in a.lower()):
                        p = os.path.join(raiz, a)
                        if _viejo(p, dias):
                            n, b = n + 1, b + _tamano(p)
                            if not evaluar:
                                _borrar(p)
        return {"archivos": n, "mb": round(b / 2**20, 1)}

    def artefactos():
        n, b = 0, 0
        for a in c["artefactos"]:
            for p in glob.glob(os.path.join(a["ruta"], a["patron"])):
                if _viejo(p, a["dias"]):
                    n, b = n + 1, b + _tamano(p)
                    if not evaluar:
                        _borrar(p)
        return {"elementos": n, "mb": round(b / 2**20, 1)}

    def paquetes():
        if evaluar:
            return {}
        sh(["dnf", "-q", "clean", "packages"], 300)
        return {}

    paso("journal del sistema", journal)
    paso("imágenes y contenedores detenidos (root)", podman_root)
    paso(f"imágenes y contenedores detenidos ({c['aap_usuario']})", podman_aap)
    paso("logs rotados (sistema y AAP)", logs)
    paso("artefactos de trabajos y del runner", artefactos)
    paso("caché de paquetes", paquetes)
    despues = montajes()
    r = {"ok": all(p["ok"] for p in pasos), "modo": modo, "pasos": pasos,
         "liberado_mb": round(sum(p["liberado_mb"] for p in pasos), 1),
         "disco": [{"montaje": m["montaje"], "uso_pct": m["uso_pct"], "libre_gb": m["libre_gb"], "libre_antes_gb": antes.get(m["montaje"])} for m in despues]}
    if not evaluar:
        registrar("liberar-disco", origen, "ok" if r["ok"] else "con errores", r)
    return r


# ── FaceOps y despliegue ───────────────────────────────────────────────────────────────────────────────────────────────
def faceops_responde(c, timeout=8):
    t0 = time.time()
    try:
        with urllib.request.urlopen(c["faceops_url"], timeout=timeout) as r:
            return r.status, round(time.time() - t0, 2)
    except urllib.error.HTTPError as e:
        return e.code, round(time.time() - t0, 2)
    except Exception:  # noqa: BLE001
        return 0, round(time.time() - t0, 2)


def autodeploy(c):
    d = {"timer_activo": sh(["systemctl", "is-active", "faceops-autodeploy.timer"], 10)[1].strip() == "active",
         "servicio": sh(["systemctl", "is-active", "faceops-autodeploy.service"], 10)[1].strip()}
    try:
        d["estado"] = open(os.path.join(c["autodeploy_dir"], "estado")).read().strip()[:300]
        dep = open(os.path.join(c["autodeploy_dir"], "desplegado")).read().strip()
    except OSError:
        dep = ""
    repo = c["repo"]
    rc, rem, _ = sh(["git", "-C", repo, "-c", f"safe.directory={repo}", "rev-parse", "origin/main"], 15)
    rem = rem.strip()
    d["atraso_s"] = 0
    if rc == 0 and dep and rem and dep != rem:
        rc2, out, _ = sh(["git", "-C", repo, "-c", f"safe.directory={repo}", "log", "--format=%ct", f"{dep}..{rem}"], 15)
        ts = [int(x) for x in out.split() if x.isdigit()] if rc2 == 0 else []
        d["atraso_s"] = int(time.time() - min(ts)) if ts else 0
        d["pendientes"] = len(ts)
    return d


def reiniciar_faceops(c, origen, evaluar=False, desplegar=True):
    cod0, t0 = faceops_responde(c)
    ad0 = autodeploy(c)
    if evaluar:
        return {"ok": True, "modo": "evaluar", "faceops": {"codigo": cod0, "segundos": t0}, "autodeploy": ad0}
    hechos = []
    if cod0 != 200:
        rc, _, err = sh(["systemctl", "restart", "faceops.service"], 180)
        hechos.append({"accion": "reiniciar faceops", "ok": rc == 0, "error": err.strip()[-200:] if rc else ""})
    if not ad0["timer_activo"]:
        rc, _, err = sh(["systemctl", "enable", "--now", "faceops-autodeploy.timer"], 60)
        hechos.append({"accion": "activar el timer del despliegue", "ok": rc == 0, "error": err.strip()[-200:] if rc else ""})
    if desplegar and ad0.get("atraso_s", 0) > 1800 and ad0.get("servicio") != "activating":
        rc, _, err = sh(["systemctl", "start", "--no-block", "faceops-autodeploy.service"], 30)
        hechos.append({"accion": "lanzar el despliegue pendiente", "ok": rc == 0, "error": err.strip()[-200:] if rc else ""})
    cod = cod0
    for _ in range(30):
        cod, t = faceops_responde(c)
        if cod == 200:
            break
        time.sleep(10)
    r = {"ok": all(h["ok"] for h in hechos) and cod == 200, "modo": "aplicar", "acciones": hechos, "faceops": {"antes": cod0, "despues": cod}}
    if hechos:
        registrar("reiniciar-faceops", origen, "ok" if r["ok"] else "con errores", r)
    return r


# ── respaldos de las bases de AAP y FaceOps ────────────────────────────────────────────────────────────────────────────
def respaldos_edad(c):
    out = {}
    for base in ("aap", "faceops"):
        fs = sorted(glob.glob(os.path.join(c["respaldos"], f"{base}-*.gz")), key=os.path.getmtime)
        out[base] = int(time.time() - os.path.getmtime(fs[-1])) if fs else None
    return out


def respaldar(c, origen, evaluar=False):
    os.makedirs(c["respaldos"], mode=0o700, exist_ok=True)
    raiz = uso_raiz()
    libre = libre_bytes(c["respaldos"])
    est = os.path.getsize(c["faceops_bd"]) if os.path.exists(c["faceops_bd"]) else 0
    if evaluar:
        return {"ok": True, "modo": "evaluar", "edad_s": respaldos_edad(c), "disco_pct": raiz, "libre_gb": round(libre / 2**30, 1)}
    if raiz >= 85 or libre < 3 * 2**30 + est:
        return {"ok": False, "error": f"sin espacio seguro para respaldar (disco al {raiz} %, libre {round(libre / 2**30, 1)} GB): libera disco primero"}
    marca = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    hechos = []
    # FaceOps (SQLite): copia consistente con la API de respaldo, comprimida
    dst = os.path.join(c["respaldos"], f"faceops-{marca}.db.gz")
    try:
        tmp = dst[:-3] + ".tmp"
        src = sqlite3.connect(f"file:{c['faceops_bd']}?mode=ro", uri=True, timeout=30)
        bk = sqlite3.connect(tmp)
        src.backup(bk)
        bk.close()
        src.close()
        with open(tmp, "rb") as fi, gzip.open(dst, "wb", 6) as fo:
            shutil.copyfileobj(fi, fo)
        os.unlink(tmp)
        hechos.append({"base": "faceops", "ok": True, "archivo": dst, "mb": round(os.path.getsize(dst) / 2**20, 1)})
    except Exception as x:  # noqa: BLE001
        hechos.append({"base": "faceops", "ok": False, "error": f"{type(x).__name__}: {x}"[:200]})
    # AAP (PostgreSQL del contenedor): pg_dumpall comprimido; nunca a medias (si falla, se borra)
    dst = os.path.join(c["respaldos"], f"aap-{marca}.sql.gz")
    u = pwd.getpwnam(c["aap_usuario"])
    try:
        with gzip.open(dst, "wb", 6) as fo:
            p = subprocess.Popen(["runuser", "-u", c["aap_usuario"], "--", "env", f"XDG_RUNTIME_DIR=/run/user/{u.pw_uid}", f"HOME={u.pw_dir}",
                                  "podman", "exec", "postgresql", "pg_dumpall", "--clean", "--if-exists"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            shutil.copyfileobj(p.stdout, fo)
            err = p.stderr.read().decode(errors="replace")
            rc = p.wait(timeout=1800)
        if rc != 0:
            raise RuntimeError(err.strip()[-200:] or f"pg_dumpall código {rc}")
        hechos.append({"base": "aap", "ok": True, "archivo": dst, "mb": round(os.path.getsize(dst) / 2**20, 1)})
    except Exception as x:  # noqa: BLE001
        _borrar(dst)
        hechos.append({"base": "aap", "ok": False, "error": f"{type(x).__name__}: {x}"[:200]})
    for base in ("aap", "faceops"):   # retención
        fs = sorted(glob.glob(os.path.join(c["respaldos"], f"{base}-*.gz")), key=os.path.getmtime)
        for f in fs[:-int(c["respaldos_conservar"])]:
            _borrar(f)
    for f in glob.glob(os.path.join(c["respaldos"], "*")):
        os.chmod(f, 0o600)
    r = {"ok": all(h["ok"] for h in hechos), "modo": "aplicar", "respaldos": hechos, "edad_s": respaldos_edad(c)}
    registrar("respaldar", origen, "ok" if r["ok"] else "con errores", r)
    return r


# ── certificados ───────────────────────────────────────────────────────────────────────────────────────────────────────
def certificados(c):
    out = []
    for x in c["certificados"]:
        d = {"servicio": x["servicio"], "dias": None}
        try:
            ctx = ssl._create_unverified_context()
            with socket.create_connection((x["host"], x["puerto"]), timeout=5) as s:
                with ctx.wrap_socket(s, server_hostname=x.get("sni") or None) as t:
                    der = t.getpeercert(binary_form=True)
            pem = ssl.DER_cert_to_PEM_cert(der)
            rc, fin, _ = sh(["openssl", "x509", "-noout", "-enddate"], 10, entrada=pem)
            fecha = dt.datetime.strptime(fin.strip().split("=", 1)[1], "%b %d %H:%M:%S %Y %Z")
            d["dias"] = round((fecha - dt.datetime.utcnow()).total_seconds() / 86400, 1)
            d["vence"] = fecha.strftime("%Y-%m-%d")
        except Exception as e:  # noqa: BLE001
            d["error"] = type(e).__name__
        out.append(d)
    return out


def renovar_cert(c, origen, evaluar=False):
    antes = certificados(c)
    if evaluar:
        return {"ok": True, "modo": "evaluar", "certificados": antes}
    hechos = []
    # FaceOps público (Let's Encrypt): certbot renueva solo lo que vence en < 30 días y recarga el proxy
    rc, _, err = sh(["systemctl", "start", "faceops-publico-cert.service"], 600)
    hechos.append({"servicio": "faceops", "accion": "certbot renew + recarga del proxy", "ok": rc == 0, "error": err.strip()[-200:] if rc else ""})
    despues = certificados(c)
    aap_ = next((x for x in despues if x["servicio"] == "aap"), {})
    propuesta = ""
    if aap_.get("dias") is not None and aap_["dias"] < 30:   # los de AAP se regeneran con el instalador: requiere ventana y persona
        propuesta = "El certificado de AAP vence en menos de 30 días: se regenera con el instalador de AAP (ventana de mantenimiento, con aprobación)"
    r = {"ok": all(h["ok"] for h in hechos), "modo": "aplicar", "acciones": hechos, "antes": antes, "despues": despues, "propuesta": propuesta}
    registrar("renovar-cert", origen, "ok" if r["ok"] else "con errores", r)
    return r


# ── capacidad de AAP y cola de FaceOps (para el exportador) ────────────────────────────────────────────────────────────
def _token(c):
    try:
        for l in open(c["faceops_env"]):
            if l.startswith("FACEOPS_AAP_TOKEN="):
                return l.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def capacidad_aap(c):
    tok = _token(c)
    if not tok:
        return {}
    base = c["aap_api"].split("/api/")[0] + "/api/controller/v2/"
    ctx = ssl._create_unverified_context()

    def get(p):
        req = urllib.request.Request(base + p, headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, context=ctx, timeout=6) as r:
            return json.loads(r.read())
    d = {}
    try:
        gs = [x for x in get("instance_groups/?page_size=50").get("results", []) if not x.get("is_container_group")]
        d["capacidad_libre"] = sum(max(0, (x.get("capacity") or 0) - (x.get("consumed_capacity") or 0)) for x in gs)
        d["grupos_con_capacidad"] = sum(1 for x in gs if (x.get("capacity") or 0) > 0)
        hace = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        d["pendientes"], d["pendientes_antiguos"] = 0, 0
        for ep in ("jobs", "project_updates", "inventory_updates", "system_jobs"):   # ejecutables: un workflow «running» esperando aprobación no cuenta
            d["pendientes"] += get(f"{ep}/?status__in=pending,waiting&page_size=1").get("count", 0)
            d["pendientes_antiguos"] += get(f"{ep}/?status__in=pending,waiting&created__lt={hace}&page_size=1").get("count", 0)
        d["latidos"] = {}
        for i in get("instances/?page_size=50").get("results", []):
            if i.get("enabled") and i.get("node_type") in ("hybrid", "control", "execution") and i.get("last_seen"):
                visto = dt.datetime.fromisoformat(i["last_seen"].replace("Z", "+00:00"))
                d["latidos"][i["hostname"]] = int((dt.datetime.now(dt.timezone.utc) - visto).total_seconds())
    except Exception:  # noqa: BLE001
        pass
    return d


def cola_faceops(c):
    try:
        cx = sqlite3.connect(f"file:{c['faceops_bd']}?mode=ro", uri=True, timeout=3)
        cx.row_factory = sqlite3.Row
        t = time.time()
        d = {"pendientes": cx.execute("SELECT COUNT(*) FROM cola_acciones WHERE estado IN ('pendiente','en_curso')").fetchone()[0],
             "atascados": cx.execute("SELECT COUNT(*) FROM cola_acciones WHERE estado IN ('pendiente','en_curso') AND COALESCE(creado_ts,0) < ?",
                                     (t - 900,)).fetchone()[0],
             "fallidos": cx.execute("SELECT COUNT(*) FROM cola_acciones WHERE estado='fallido'").fetchone()[0]}
        lat = cx.execute("SELECT valor FROM ajustes WHERE clave='cola_latido'").fetchone()
        d["latido_s"] = round(t - float(lat[0])) if lat and lat[0] else None
        cx.close()
        return d
    except Exception:  # noqa: BLE001  (sin la tabla aún o base ocupada)
        return {}


# ── estado y métricas ──────────────────────────────────────────────────────────────────────────────────────────────────
def estado(c, ligero=False):
    d = {"version": VERSION, "cuando": ahora(), "disco": montajes(), **memoria(), **carga()}
    d["vms"] = vms()
    d["aap_api"] = api_aap(c, 5)
    d["contenedores"] = contenedores(c)
    cod, t = faceops_responde(c, 8)
    d["faceops"] = {"codigo": cod, "segundos": t}
    d["autodeploy"] = autodeploy(c)
    d["respaldos_edad_s"] = respaldos_edad(c)
    d["certificados"] = certificados(c)
    if not ligero:
        d["aap_capacidad"] = capacidad_aap(c)
        d["cola_faceops"] = cola_faceops(c)
    try:
        d["vigia"] = json.load(open(ESTADO_VIGIA))
    except (OSError, ValueError):
        d["vigia"] = {}
    return d


def _esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def metricas(c):
    e = estado(c)
    L = ["# HELP faceops_ac_info Exportador de autocuidado de FaceOps", "# TYPE faceops_ac_info gauge", f'faceops_ac_info{{version="{VERSION}"}} 1']

    def m(nombre, valor, **et):
        if valor is None:
            return
        lab = ",".join(f'{k}="{_esc(v)}"' for k, v in et.items())
        L.append(f"faceops_ac_{nombre}{{{lab}}} {float(valor)}" if lab else f"faceops_ac_{nombre} {float(valor)}")
    for x in e["disco"]:
        m("disco_uso_pct", x["uso_pct"], montaje=x["montaje"])
        m("disco_libre_gb", x["libre_gb"], montaje=x["montaje"])
        m("inodos_uso_pct", x["inodos_pct"], montaje=x["montaje"])
    m("memoria_uso_pct", e["memoria_pct"])
    m("swap_uso_pct", e["swap_pct"])
    m("carga15_por_cpu", e["carga15_por_cpu"])
    if e["vms"] is not None:
        m("libvirt_ok", 1)
        for v in e["vms"]:
            m("vm_pausada", 1 if pausada_por_io(v) else 0, vm=v["vm"], razon=v["razon"])
            m("vm_caida", 1 if caida(v) else 0, vm=v["vm"], razon=v["razon"])
            m("vm_encendida", 1 if v["estado"] == "running" else 0, vm=v["vm"])
    else:
        m("libvirt_ok", 0)
    m("aap_api_codigo", e["aap_api"])
    m("aap_api_ok", 1 if e["aap_api"] == 200 else 0)
    if e["contenedores"] is not None:
        for x in e["contenedores"]:
            m("contenedor_ok", 0 if malo(x) else 1, contenedor=x["nombre"], estado=x["estado"], salud=x["salud"] or "sin chequeo")
            m("contenedor_reinicios", x["reinicios"], contenedor=x["nombre"])
    m("faceops_codigo", e["faceops"]["codigo"])
    m("faceops_ok", 1 if e["faceops"]["codigo"] == 200 else 0)
    m("faceops_respuesta_segundos", e["faceops"]["segundos"])
    ad = e["autodeploy"]
    m("autodeploy_atraso_segundos", ad.get("atraso_s", 0))
    m("autodeploy_timer_activo", 1 if ad.get("timer_activo") else 0)
    m("autodeploy_error", 1 if (ad.get("estado") or "").split(" ", 1)[-1].startswith("error") else 0)
    for b, s in e["respaldos_edad_s"].items():
        m("respaldo_edad_segundos", s if s is not None else 10**9, base=b)
    for x in e["certificados"]:
        m("certificado_dias", x["dias"] if x["dias"] is not None else -1, servicio=x["servicio"])
    cap = e.get("aap_capacidad") or {}
    m("aap_capacidad_libre", cap.get("capacidad_libre"))
    m("aap_grupos_con_capacidad", cap.get("grupos_con_capacidad"))
    m("aap_trabajos_pendientes", cap.get("pendientes"))
    m("aap_trabajos_pendientes_antiguos", cap.get("pendientes_antiguos"))
    for h, v in (cap.get("latidos") or {}).items():
        m("aap_instancia_latido_segundos", v, instancia=h)
    cola = e.get("cola_faceops") or {}
    m("cola_pendientes", cola.get("pendientes"))
    m("cola_atascados", cola.get("atascados"))
    m("cola_fallidos", cola.get("fallidos"))
    m("cola_latido_segundos", cola.get("latido_s"))
    vg = e.get("vigia") or {}
    m("vigia_ultima_pasada_segundos", (time.time() - vg["ts"]) if vg.get("ts") else None)
    m("vigia_acciones_total", vg.get("acciones_total", 0))
    return "\n".join(L) + "\n"


def exportador(c):
    host, puerto = c["exportador"].rsplit(":", 1)
    cache = {"t": 0.0, "txt": ""}
    cerrojo = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.split("?")[0] not in ("/metrics", "/metricas"):
                self.send_response(404)
                self.end_headers()
                return
            with cerrojo:
                if time.time() - cache["t"] > 20:
                    try:
                        cache["txt"] = metricas(c)
                    except Exception as x:  # noqa: BLE001
                        cache["txt"] = f'faceops_ac_error{{tipo="{type(x).__name__}"}} 1\n'
                    cache["t"] = time.time()
                b = cache["txt"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass
    ThreadingHTTPServer((host, int(puerto)), H).serve_forever()


# ── vigía externo: SOLO lo crítico y SOLO si AAP no responde ───────────────────────────────────────────────────────────
def vigia(c):
    os.makedirs(DIR, exist_ok=True)
    try:
        st = json.load(open(ESTADO_VIGIA))
    except (OSError, ValueError):
        st = {}
    t = time.time()
    hoy = dt.date.today().isoformat()
    if st.get("dia") != hoy:
        st.update(dia=hoy, reinicios_hoy=0)
    st.setdefault("instalado", t)
    st.setdefault("acciones_total", 0)
    codigo = api_aap(c)
    raiz = uso_raiz()
    st.update(ts=t, cuando=ahora(), aap_api=codigo, disco_raiz_pct=raiz)
    if codigo == 200:
        st["aap_caido_desde"] = None
    elif not st.get("aap_caido_desde"):
        st["aap_caido_desde"] = t
    caido_min = (t - st["aap_caido_desde"]) / 60 if st.get("aap_caido_desde") else 0
    hecho = []
    with open("/proc/uptime") as f:
        arranque_min = float(f.read().split()[0]) / 60
    en_gracia = (t - st["instalado"]) / 60 < c["vigia_gracia_min"] or arranque_min < c["vigia_gracia_min"]
    try:
        pausa_hasta = float(open(PAUSA).read().strip() or 0)
    except (OSError, ValueError):
        pausa_hasta = 0
    if pausa_hasta > t:   # mantenimiento declarado por una persona: ni siquiera la emergencia de disco actúa
        en_gracia = True
        st["pausa_hasta"] = pausa_hasta
    emergencia = raiz >= c["vigia_emergencia_disco"]
    actuar = (codigo != 200 and caido_min >= c["vigia_caida_min"]) or emergencia
    if pausa_hasta > t:
        emergencia = False
    st["decision"] = (f"en mantenimiento hasta {dt.datetime.fromtimestamp(pausa_hasta).strftime('%H:%M')}: solo observa" if pausa_hasta > t else
                      "en gracia (recién instalado o recién encendido): solo observa" if en_gracia and not emergencia else
                      "AAP responde: todo lo demás lo atiende AAP con EDA" if not actuar else
                      f"disco raíz al {raiz} % (emergencia)" if codigo == 200 else f"AAP no responde hace {round(caido_min)} min: actúa el vigía")
    if actuar and (not en_gracia or emergencia):
        # 1. disco: sin espacio nada vuelve a arrancar
        if raiz >= c["vigia_umbral_disco"]:
            r = liberar_disco(c, "vigía", "seguro")
            hecho.append({"accion": "liberar-disco", "ok": r["ok"], "liberado_mb": r["liberado_mb"]})
            raiz = uso_raiz()
        # 2. máquinas pausadas por falta de disco (I/O)
        lista = vms() or []
        if any(pausada_por_io(v) for v in lista) and raiz < 95:
            r = recuperar_vms("vigía", solo_pausadas=True)
            hecho.append({"accion": "reanudar-vms", "ok": r["ok"], "vms": [a["vm"] for a in r["acciones"]]})
        # 3. contenedores de AAP (con pausa entre intentos y tope diario, para no entrar en un bucle de reinicios)
        if codigo != 200:
            ult = st.get("ultimo_reinicio") or 0
            if (t - ult) / 60 < c["vigia_pausa_reinicio_min"]:
                hecho.append({"accion": "reiniciar-aap", "omitida": f"último reinicio hace {round((t - ult) / 60)} min: espera a que AAP arranque"})
            elif st["reinicios_hoy"] >= c["vigia_reinicios_max_dia"]:
                hecho.append({"accion": "reiniciar-aap", "omitida": "tope diario de reinicios alcanzado: requiere una persona"})
            else:
                st["ultimo_reinicio"], st["reinicios_hoy"] = t, st["reinicios_hoy"] + 1
                r = reiniciar_aap(c, "vigía")
                hecho.append({"accion": "reiniciar-aap", "ok": r["ok"], "api": r.get("api"), "reiniciados": [x["contenedor"] for x in r.get("reiniciados", [])]})
    # 4. despachador de AAP atascado con la API respondiendo: ningún trabajo de AAP corre, así que AAP no puede curarlo
    if codigo == 200 and not en_gracia:
        atascado, motivo, _ = despachador_atascado(c)
        if not atascado:
            st["despachador_mal_desde"] = None
        else:
            st["despachador_mal_desde"] = st.get("despachador_mal_desde") or t
            st["decision"] = f"despachador de AAP atascado ({motivo})"
            ult = st.get("ultimo_reinicio") or 0
            if (t - st["despachador_mal_desde"]) / 60 < 2:
                pass   # se confirma en la pasada siguiente (evita reiniciar por un pico)
            elif (t - ult) / 60 < c["vigia_pausa_reinicio_min"]:
                hecho.append({"accion": "reiniciar-despachador", "omitida": f"último reinicio hace {round((t - ult) / 60)} min"})
            elif st["reinicios_hoy"] >= c["vigia_reinicios_max_dia"]:
                hecho.append({"accion": "reiniciar-despachador", "omitida": "tope diario de reinicios alcanzado: requiere una persona"})
            else:
                st["ultimo_reinicio"], st["reinicios_hoy"] = t, st["reinicios_hoy"] + 1
                r = reiniciar_despachador(c, "vigía")
                hecho.append({"accion": "reiniciar-despachador", "ok": r["ok"], "motivo": motivo, "latido_s": r.get("latido_s")})
                st["despachador_mal_desde"] = None
    st["ultima_actuacion"] = {"cuando": ahora(), "acciones": hecho} if hecho else st.get("ultima_actuacion")
    st["acciones_total"] = st["acciones_total"] + len([h for h in hecho if not h.get("omitida")])
    try:
        with open(ESTADO_VIGIA + ".tmp", "w") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(ESTADO_VIGIA + ".tmp", ESTADO_VIGIA)
        os.chmod(ESTADO_VIGIA, 0o644)
    except OSError:
        pass
    return {"ok": True, "decision": st["decision"], "acciones": hecho, "aap_api": codigo, "disco_raiz_pct": raiz}


# ── crecer el sistema de archivos tras ampliar el disco en GCP (solo crece; nunca achica) ──────────────────────────────
def crecer_fs(c, origen, evaluar=False):
    rc, out, _ = sh(["findmnt", "-n", "-o", "SOURCE,FSTYPE", "/"], 10)
    dev, fs = (out.split() + ["", ""])[:2]
    m = re.match(r"(/dev/(?:nvme\d+n\d+|[a-z]+))p?(\d+)$", dev)
    if not m or fs not in ("xfs", "ext4"):
        return {"ok": False, "error": f"raíz no soportada para crecer en línea ({dev} {fs})"}
    disco, part = m.group(1), m.group(2)
    antes = next((x for x in montajes() if x["montaje"] == "/"), {})
    if evaluar:
        return {"ok": True, "modo": "evaluar", "dispositivo": dev, "fs": fs, "total_gb": antes.get("total_gb")}
    rc1, o1, e1 = sh(["growpart", disco, part], 120)
    if rc1 not in (0, 1):   # 1 = NOCHANGE
        return {"ok": False, "error": (e1 or o1).strip()[-200:]}
    rc2, _, e2 = sh(["xfs_growfs", "/"] if fs == "xfs" else ["resize2fs", dev], 300)
    despues = next((x for x in montajes() if x["montaje"] == "/"), {})
    r = {"ok": rc2 == 0, "modo": "aplicar", "antes_gb": antes.get("total_gb"), "despues_gb": despues.get("total_gb"), "error": e2.strip()[-200:] if rc2 else ""}
    registrar("crecer-fs", origen, "ok" if r["ok"] else "con errores", r)
    return r


def identidad_gcp():
    def md(p):
        req = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/" + p, headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.read().decode()
    try:
        disco = json.loads(md("instance/disks/?recursive=true"))[0]
        return {"ok": True, "proyecto": md("project/project-id"), "zona": md("instance/zone").rsplit("/", 1)[-1],
                "instancia": md("instance/name"), "disco": disco.get("deviceName")}
    except Exception as x:  # noqa: BLE001
        return {"ok": False, "error": f"no es una VM de GCP o sin acceso a metadatos ({type(x).__name__})"}


# ── entrada ────────────────────────────────────────────────────────────────────────────────────────────────────────────
ACCIONES = ("estado", "metricas", "exportador", "vigia", "pausar", "liberar-disco", "recuperar-vms", "reiniciar-aap", "reiniciar-faceops",
            "respaldar", "renovar-cert", "crecer-fs", "identidad-gcp", "version", "reiniciar-despachador")


def main():
    if os.geteuid() != 0:
        salir({"ok": False, "error": "requiere root (sudo)"}, 2)
    ap = argparse.ArgumentParser(prog="faceops-autocuidado")
    ap.add_argument("accion", choices=ACCIONES)
    ap.add_argument("--modo", choices=("evaluar", "seguro", "profundo", "aplicar"), default="aplicar")
    ap.add_argument("--origen", choices=("aap", "vigía", "vigia", "manual", "prueba"), default="manual")
    ap.add_argument("--todos", action="store_true", help="reiniciar-aap: todos los contenedores en orden")
    ap.add_argument("--minutos", type=int, default=0, help="pausar: minutos de mantenimiento (0 = quitar la pausa; máx. 1440)")
    ap.add_argument("--solo-pausadas", action="store_true")
    ap.add_argument("--diferido", action="store_true", help="reiniciar-despachador desde AAP: se programa en 45 s")
    ap.add_argument("--sin-propios", action="store_true", help="reiniciar-aap desde AAP: no toca lo que sostiene al propio trabajo")
    a = ap.parse_args()
    c = conf()
    evaluar = a.modo == "evaluar"
    origen = "vigía" if a.origen == "vigia" else a.origen
    if a.accion == "version":
        salir({"ok": True, "version": VERSION})
    if a.accion == "estado":
        salir(estado(c))
    if a.accion == "metricas":
        sys.stdout.write(metricas(c))
        return
    if a.accion == "exportador":
        exportador(c)
        return
    if a.accion == "identidad-gcp":
        salir(identidad_gcp())
    if a.accion == "pausar":
        os.makedirs(DIR, exist_ok=True)
        hasta = time.time() + max(0, min(a.minutos, 1440)) * 60
        with open(PAUSA, "w") as f:
            f.write(str(hasta if a.minutos > 0 else 0))
        registrar("pausar", origen, "ok", {"minutos": a.minutos})
        salir({"ok": True, "vigia_en_pausa_hasta": dt.datetime.fromtimestamp(hasta).isoformat(timespec="minutes") if a.minutos > 0 else None})
    _cand = candado()  # noqa: F841  (las acciones que cambian algo no corren a la vez)
    if a.accion == "vigia":
        r = vigia(c)
    elif a.accion == "liberar-disco":
        r = liberar_disco(c, origen, a.modo if a.modo in ("evaluar", "seguro", "profundo") else "seguro")
    elif a.accion == "recuperar-vms":
        r = recuperar_vms(origen, evaluar, a.solo_pausadas)
    elif a.accion == "reiniciar-aap":
        r = reiniciar_aap(c, origen, evaluar, a.todos, a.sin_propios)
    elif a.accion == "reiniciar-faceops":
        r = reiniciar_faceops(c, origen, evaluar)
    elif a.accion == "reiniciar-despachador":
        r = reiniciar_despachador(c, origen, evaluar, a.diferido)
    elif a.accion == "respaldar":
        r = respaldar(c, origen, evaluar)
    elif a.accion == "renovar-cert":
        r = renovar_cert(c, origen, evaluar)
    else:
        r = crecer_fs(c, origen, evaluar)
    salir(r, 0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
