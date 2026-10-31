#!/usr/bin/env python3
"""FaceOps · medición de uso por aplicación (solo lectura). Corre en la máquina con python3 de la base del sistema.
Argumento: epoch desde el que se leen los access logs. Salida JSON:
  procesos: por familia (nginx, jboss, postgres, redis, artemis…): cpu_s acumulado, rss_mb, n
  nginx: peticiones y bytes por contexto (/app-jboss) desde «desde»
  jboss: por despliegue: contexto, sesiones activas, peticiones y tiempo de proceso acumulados; pool de cada datasource"""
import glob, json, os, re, subprocess, sys, time

desde = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] not in ("", "0") else time.time() - 3600
TCK = os.sysconf("SC_CLK_TCK")
PAG = os.sysconf("SC_PAGE_SIZE")


def familia(comm, cmd):
    c = comm.lower()
    if c.startswith("nginx"):
        return "nginx"
    if c.startswith("postgres") or c.startswith("postmaster"):
        return "postgres"
    if c.startswith("redis-server") or c.startswith("valkey-server"):
        return "redis"
    if c.startswith("httpd"):
        return "httpd"
    if c == "java":
        if "jboss-modules" in cmd:
            return "jboss"
        if "artemis" in cmd.lower():
            return "artemis"
        if "kafka" in cmd.lower():
            return "kafka"
        if "keycloak" in cmd.lower():
            return "keycloak"
        return "java"
    return None


procesos = {}
for d in glob.glob("/proc/[0-9]*"):
    try:
        st = open(f"{d}/stat").read()
        comm = st[st.index("(") + 1:st.rindex(")")]
        campos = st[st.rindex(")") + 2:].split()
        cmd = open(f"{d}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="ignore")
    except (OSError, ValueError):
        continue
    f = familia(comm, cmd)
    if not f:
        continue
    p = procesos.setdefault(f, {"cpu_s": 0.0, "rss_mb": 0.0, "n": 0})
    p["cpu_s"] += (int(campos[11]) + int(campos[12])) / TCK
    p["rss_mb"] += int(campos[21]) * PAG / 1048576
    p["n"] += 1
for p in procesos.values():
    p["cpu_s"], p["rss_mb"] = round(p["cpu_s"], 2), round(p["rss_mb"], 1)

# ---- nginx: peticiones y bytes por contexto (primer tramo de la ruta) desde «desde»
nginx = {}
MES = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
pat = re.compile(r'\[(\d+)/(\w+)/(\d+):(\d+):(\d+):(\d+) ([+-]\d{4})\] "(?:[A-Z]+) (\S+)[^"]*" (\d{3}) (\d+|-)')
from datetime import datetime, timedelta, timezone
for ruta in sorted(glob.glob("/var/log/nginx/access.log*")):
    if ruta.endswith(".gz") or os.path.getmtime(ruta) < desde:
        continue
    try:
        fh = open(ruta, errors="ignore")
        tam = os.path.getsize(ruta)
        if tam > 50 * 1048576:
            fh.seek(tam - 50 * 1048576)
        for linea in fh:
            m = pat.search(linea)
            if not m:
                continue
            dd, mo, yy, hh, mi, ss, tz, url, cod, by = m.groups()
            off = (int(tz[1:3]) * 60 + int(tz[3:])) * (1 if tz[0] == "+" else -1)
            t = datetime(int(yy), MES.get(mo, 1), int(dd), int(hh), int(mi), int(ss), tzinfo=timezone(timedelta(minutes=off))).timestamp()
            if t < desde:
                continue
            ctx = "/" + url.split("?")[0].strip("/").split("/")[0] if url.strip("/") else "/"
            x = nginx.setdefault(ctx, {"req": 0, "bytes": 0, "err": 0})
            x["req"] += 1
            x["bytes"] += int(by) if by.isdigit() else 0
            x["err"] += cod.startswith("5")
    except OSError:
        pass

# ---- JBoss: estadísticas de cada despliegue por su consola de administración local
jboss = {}
cli = (glob.glob("/opt/*/bin/jboss-cli.sh") + glob.glob("/opt/*/*/bin/jboss-cli.sh") + glob.glob("/opt/rh/*/*/bin/jboss-cli.sh"))
if cli and "jboss" in procesos:
    def cmd(c):
        try:
            r = subprocess.run([cli[0], "--connect", "--output-json", f"--command={c}"], capture_output=True, text=True, timeout=40)
            return json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
        except Exception:  # noqa: BLE001
            return {}
    est = cmd("/subsystem=undertow:read-attribute(name=statistics-enabled)")
    jboss["estadisticas"] = est.get("result")
    dep = cmd("/deployment=*/subsystem=undertow:read-resource(include-runtime=true,recursive=true)")
    apps = {}
    for it in dep.get("result") or []:
        r = it.get("result") or {}
        nombre = [a.get("deployment") for a in it.get("address", []) if "deployment" in a]
        nombre = nombre[0] if nombre else "?"
        req = tpo = 0
        for s in (r.get("servlet") or {}).values():
            req += int(s.get("request-count") or 0)
            tpo += int(s.get("total-request-time") or 0)
        apps[nombre] = {"contexto": r.get("context-root") or "", "sesiones": int(r.get("active-sessions") or 0), "req": req, "tiempo_ms": tpo}
    jboss["apps"] = apps
    ds = cmd("/subsystem=datasources/data-source=*/statistics=pool:read-resource(include-runtime=true)")
    jboss["pools"] = {[a.get("data-source") for a in it.get("address", []) if "data-source" in a][0]: {
        "activas": int((it.get("result") or {}).get("ActiveCount") or 0), "en_uso": int((it.get("result") or {}).get("InUseCount") or 0)}
        for it in (ds.get("result") or []) if any("data-source" in a for a in it.get("address", []))}

mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if l.split(":")[0] in ("MemTotal", "MemAvailable")}
print(json.dumps({"ts": round(time.time()), "desde": round(desde), "ncpu": os.cpu_count(), "mem_mb": round(mem.get("MemTotal", 0) / 1024),
                  "mem_disp_mb": round(mem.get("MemAvailable", 0) / 1024), "carga": os.getloadavg()[0],
                  "cpu_total_s": round(sum(float(x) for x in open("/proc/stat").readline().split()[1:8]) / TCK, 1),
                  "procesos": procesos, "nginx": nginx, "jboss": jboss}))
