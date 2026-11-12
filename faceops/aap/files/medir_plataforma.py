#!/usr/bin/env python3
"""Plataforma · Medir salud: lee, SIN agente y sin cambiar nada, los indicadores propios de una plataforma desde su máquina
(AAP entra por SSH y corre este script con python3 del sistema). Usa solo las APIs locales de la plataforma y comandos de
lectura. Nunca imprime secretos: si una herramienta necesita la clave local (Redis), la lee del archivo de configuración y la
pasa por variable de entorno, sin mostrarla.

Uso: medir_plataforma.py <código> [puerto]
Salida (JSON): {"valores": {clave: número}, "texto": {clave: texto}, "componentes": [{"n", "ok", "d"}], "notas": [...]}"""
import glob
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

V, T, C, N = {}, {}, [], []


def sh(cmd, t=60, env=None):
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=t, env=env)
        return r.returncode, (r.stdout or "") + ("" if r.returncode == 0 else (r.stderr or ""))
    except Exception as x:  # noqa: BLE001
        return 99, str(x)


def http(url, t=6, hdr=None):
    """(código, cuerpo) de un GET local; 0 si no responde."""
    try:
        ctx = ssl._create_unverified_context()   # endpoints locales con certificados propios: solo se lee el estado
        req = urllib.request.Request(url, headers=hdr or {})
        with urllib.request.urlopen(req, timeout=t, context=ctx) as r:
            return r.status, r.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as x:
        try:
            return x.code, x.read(200_000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return x.code, ""
    except Exception:  # noqa: BLE001
        return 0, ""


def puerto(p, h="127.0.0.1", t=3):
    try:
        with socket.create_connection((h, int(p)), timeout=t):
            return True
    except OSError:
        return False


def activo(*unidades):
    """1 si alguna unidad systemd (o instancia «x@») está activa; None si ninguna existe."""
    vistas = False
    for u in unidades:
        if u.endswith("@"):
            _, o = sh(f"systemctl list-units --all --plain --no-legend '{u}*'")
            for l_ in o.splitlines():
                vistas = True
                if " active " in f" {l_} ":
                    return 1
            continue
        rc, o = sh(["systemctl", "is-active", u])
        if o.strip() in ("active", "activating", "reloading"):
            return 1
        rc2, o2 = sh(["systemctl", "cat", u])
        vistas = vistas or rc2 == 0
    return 0 if vistas else None


def libre_pct(ruta):
    try:
        st = shutil.disk_usage(ruta)
        V["disco_gb"] = round(st.total / 1e9, 1)
        return round(100 * st.free / st.total, 1)
    except OSError:
        return None


def comp(n, ok, d=""):
    C.append({"n": n, "ok": ok, "d": d})


def cert_dias(rutas):
    """Días del certificado más próximo a vencer entre los archivos PEM dados (openssl del sistema)."""
    dias = []
    for r in rutas:
        rc, o = sh(["openssl", "x509", "-enddate", "-noout", "-in", r], 10)
        m = re.search(r"notAfter=(.+)", o or "")
        if rc == 0 and m:
            rc2, ep = sh(["date", "-d", m.group(1).strip(), "+%s"], 5)
            if rc2 == 0 and ep.strip().isdigit():
                dias.append(int((int(ep.strip()) - time.time()) // 86400))
    return min(dias) if dias else None


def unidad_exec(u):
    _, o = sh(["systemctl", "show", u, "-p", "ExecStart", "--value"])
    return o


# ───────────────────────────────────────────── por plataforma
def data_lake(_p):
    V["servicio_activo"] = activo("lake-s3")
    ex = unidad_exec("lake-s3")
    m = re.search(r"-dir=(\S+)", ex)
    d = (m.group(1) if m else "/srv/datalake/s3")
    T["directorio"] = d
    c9000, _ = http("http://127.0.0.1:9000/")
    V["s3_responde"] = 1 if c9000 in (200, 403) else 0
    V["s3_anonimo"] = 1 if c9000 == 200 else (0 if c9000 == 403 else None)   # 200 sin credencial = lista buckets a cualquiera
    cm, bm = http("http://127.0.0.1:9333/cluster/status")
    cv, _ = http("http://127.0.0.1:18080/status")
    cf, bf = http("http://127.0.0.1:8888/", hdr={"Accept": "application/json"})
    comp("master", cm == 200, ":9333"); comp("volumen", cv == 200, ":18080"); comp("filer", cf == 200, ":8888"); comp("s3", V["s3_responde"] == 1, ":9000")
    V["comp_ok"], V["comp_total"] = sum(1 for x in C if x["ok"]), len(C)
    try:
        V["master_lider"] = 1 if json.loads(bm).get("IsLeader") or json.loads(bm).get("Leader") else 0
    except ValueError:
        V["master_lider"] = 0 if cm else None
    cd, bd = http("http://127.0.0.1:9333/dir/status")
    try:
        topo = json.loads(bd).get("Topology") or {}
        nodos = [n for dc in topo.get("DataCenters") or [] for r in dc.get("Racks") or [] for n in r.get("DataNodes") or []]
        V["nodos_volumen"] = len(nodos)
        V["volumenes"] = sum(int(n.get("Volumes") or 0) for n in nodos)
        V["volumenes_libres"] = int(topo.get("Free") or 0)
    except ValueError:
        pass
    cvs, bvs = http("http://127.0.0.1:9333/vol/status")
    try:   # cada volumen con sus copias frente a su política de réplica (xyz → x+y+z+1 copias)
        copias, req = {}, {}
        def recorrer(o):
            if isinstance(o, dict):
                if "Id" in o and "ReplicaPlacement" in o:
                    rp = o.get("ReplicaPlacement") or {}
                    n_ = 1 + sum(int(rp.get(k) or 0) for k in ("SameRackCount", "DiffRackCount", "DiffDataCenterCount", "node", "rack", "dc"))
                    copias[o["Id"]] = copias.get(o["Id"], 0) + 1
                    req[o["Id"]] = max(req.get(o["Id"], 1), n_)
                for v_ in o.values():
                    recorrer(v_)
            elif isinstance(o, list):
                for v_ in o:
                    recorrer(v_)
        recorrer(json.loads(bvs))
        if copias:
            V["vol_subreplicados"] = sum(1 for k in copias if copias[k] < req.get(k, 1))
    except ValueError:
        pass
    cb, bb = http("http://127.0.0.1:8888/buckets/?limit=1000", hdr={"Accept": "application/json"})
    buckets = []
    try:
        buckets = [e["FullPath"].rsplit("/", 1)[-1] for e in (json.loads(bb).get("Entries") or []) if e.get("FullPath")]
        V["buckets"] = len(buckets)
        T["buckets"] = ", ".join(buckets[:20])
    except ValueError:
        pass
    cc, conf = http("http://127.0.0.1:8888/etc/seaweedfs/filer.conf")
    if cb == 200:
        con_ttl = set()
        try:   # reglas por ruta (fs.configure): las que fijan TTL sobre /buckets/<b>
            for loc in (json.loads(conf).get("locations") or []) if cc == 200 else []:
                if loc.get("ttl"):
                    for b in buckets:
                        if (loc.get("locationPrefix") or "").rstrip("/") in (f"/buckets/{b}", "/buckets"):
                            con_ttl.add(b)
        except ValueError:
            pass
        V["buckets_ttl"] = len(con_ttl)
    _, ps = sh("ps -eo args")
    V["replica_sitio"] = 1 if re.search(r"weed\S*\s+(filer\.sync|filer\.remote\.sync|filer\.backup)", ps) else 0
    lp = libre_pct(d if os.path.exists(d) else "/")
    if lp is not None:
        V["espacio_libre_pct"] = lp
    rc, ov = sh(["/usr/local/bin/weed", "version"], 10)
    m = re.search(r"version\s+(\S+)", ov or "")
    if m:
        T["version"] = m.group(1)


def kafka(_p):
    V["servicio_activo"] = activo("kafka", "amq-streams")
    ex = unidad_exec("kafka") or unidad_exec("amq-streams")
    m = re.search(r"(\S+)/bin/kafka-server-start\.sh\s+(\S+)", ex)
    home = m.group(1) if m else next(iter(sorted(glob.glob("/opt/*kafka*/bin/kafka-topics.sh"))), "").rsplit("/bin/", 1)[0]
    props = m.group(2) if m else next(iter(glob.glob(f"{home}/config/kraft/server.properties") + glob.glob(f"{home}/config/server.properties")), "")
    if not home or not os.path.exists(f"{home}/bin/kafka-topics.sh"):
        N.append("no se encontraron las herramientas de Kafka en la máquina")
        return
    T["kafka_home"] = home
    cfg = {}
    try:
        for l_ in open(props, encoding="utf-8", errors="replace"):
            if "=" in l_ and not l_.lstrip().startswith("#"):
                k, v = l_.split("=", 1)
                cfg[k.strip()] = v.strip()
    except OSError:
        pass
    lst = cfg.get("advertised.listeners") or cfg.get("listeners") or "PLAINTEXT://localhost:9092"
    mapa = dict(x.split(":", 1) for x in (cfg.get("listener.security.protocol.map") or "").split(",") if ":" in x)
    protos = [mapa.get(x.split("://")[0], x.split("://")[0]) for x in lst.split(",") if "://" in x and "CONTROLLER" not in x.split("://")[0]]
    V["listener_seguro"] = 0 if any(p_ == "PLAINTEXT" for p_ in protos) else 1
    hp = next((x.split("://", 1)[1] for x in lst.split(",") if "://" in x and "CONTROLLER" not in x.split("://")[0]), "localhost:9092")
    host, _, prt = hp.rpartition(":")
    boot = f"{'127.0.0.1' if host in ('', '0.0.0.0') else host}:{prt}"
    T["bootstrap"] = boot
    comp("broker", puerto(prt, '127.0.0.1' if host in ('', '0.0.0.0') else host), f":{prt}")
    votos = [x for x in (cfg.get("controller.quorum.voters") or "").split(",") if x]
    comp("controlador", bool(votos) or None, f"{len(votos)} votante(s) KRaft" if votos else "ZooKeeper o sin datos")
    V["brokers_esperados"] = max(1, len([x for x in votos])) if (cfg.get("process.roles") or "").find("broker") >= 0 and votos else 1
    env = dict(os.environ, KAFKA_HEAP_OPTS="-Xmx256m")
    b = f"{home}/bin"
    rc, o = sh([f"{b}/kafka-broker-api-versions.sh", "--bootstrap-server", boot], 90, env)
    V["brokers_arriba"] = len(set(re.findall(r"\(id:\s*(\d+)", o))) if rc == 0 else 0
    rc, o = sh([f"{b}/kafka-topics.sh", "--bootstrap-server", boot, "--list"], 90, env)
    if rc == 0:
        V["topicos"] = len([x for x in o.splitlines() if x.strip() and not x.startswith("__")])
    rc, o = sh([f"{b}/kafka-topics.sh", "--bootstrap-server", boot, "--describe"], 90, env)
    if rc == 0:
        V["particiones"] = len(re.findall(r"\bPartition:\s*\d+", o))
    for k, op in (("particiones_subreplicadas", "--under-replicated-partitions"), ("particiones_offline", "--unavailable-partitions")):
        rc, o = sh([f"{b}/kafka-topics.sh", "--bootstrap-server", boot, "--describe", op], 90, env)
        if rc == 0:
            V[k] = len(re.findall(r"\bPartition:\s*\d+", o))
    rc, o = sh([f"{b}/kafka-consumer-groups.sh", "--bootstrap-server", boot, "--describe", "--all-groups"], 120, env)
    if rc == 0:
        lag, grupos = 0, set()
        for l_ in o.splitlines():
            p_ = l_.split()
            if len(p_) >= 6 and p_[0] != "GROUP" and re.fullmatch(r"\d+|-", p_[5] or ""):
                grupos.add(p_[0])
                lag += int(p_[5]) if p_[5].isdigit() else 0
        V["lag_total"], V["grupos"] = lag, len(grupos)
    ld = (cfg.get("log.dirs") or cfg.get("log.dir") or "/tmp/kafka-logs").split(",")[0]
    lp = libre_pct(ld if os.path.exists(ld) else "/")
    if lp is not None:
        V["espacio_libre_pct"] = lp
    m = re.search(r"kafka_[\d.]+-([\d.]+)", home) or re.search(r"kafka[-_]?([\d.]+)", home)
    if m:
        T["version"] = m.group(1)


def keycloak(p):
    V["servicio_activo"] = activo("keycloak", "rhbk")
    pt = int(p or 8080)
    V["puerto"] = pt
    c, b = http(f"http://127.0.0.1:{pt}/realms/master/.well-known/openid-configuration")
    V["oidc_responde"] = 1 if c == 200 else 0
    comp("http", c == 200, f":{pt}")
    ok = None
    for u in (f"http://127.0.0.1:9000/health/ready", f"http://127.0.0.1:{pt}/health/ready"):
        ch, bh = http(u)
        if ch in (200, 503):
            ok = 1 if ch == 200 and '"UP"' in bh else 0
            break
    if ok is not None:
        V["health_listo"] = ok
    comp("health", ok == 1 if ok is not None else None, "/health/ready")
    ca, _ = http(f"http://127.0.0.1:{pt}/admin/")
    comp("admin", ca in (200, 302, 303) or None, "/admin/")
    conf = ""
    for f_ in glob.glob("/opt/*keycloak*/conf/keycloak.conf") + glob.glob("/opt/rhbk*/conf/keycloak.conf") + glob.glob("/etc/keycloak/keycloak.conf"):
        try:
            conf += open(f_, encoding="utf-8", errors="replace").read()
        except OSError:
            pass
    ex = unidad_exec("keycloak") + unidad_exec("rhbk")
    _, envs = sh("systemctl show keycloak rhbk -p Environment --value")
    todo = conf + " " + ex + " " + envs
    V["https"] = 1 if re.search(r"https-certificate-file|https-key-store|KC_HTTPS_CERTIFICATE", todo) or puerto(8443) else 0
    V["metricas"] = 1 if re.search(r"metrics-enabled\s*=\s*true|--metrics-enabled(=true)?\b|KC_METRICS_ENABLED=true", todo) else 0
    for f_ in glob.glob("/opt/*keycloak*/version.txt") + glob.glob("/opt/rhbk*/version.txt"):
        try:
            T["version"] = open(f_).read().strip()[:60]
        except OSError:
            pass
    if c == 200:
        try:
            T["emisor"] = json.loads(b).get("issuer", "")[:120]
        except ValueError:
            pass


def jboss(_p):
    V["servicio_activo"] = activo("eap8-standalone", "eap7-standalone", "jboss-eap", "wildfly", "jboss")
    homes = sorted(set(glob.glob("/opt/*eap*") + glob.glob("/opt/*jboss*") + glob.glob("/opt/*wildfly*")))
    cli = next((f"{h}/bin/jboss-cli.sh" for h in homes if os.path.exists(f"{h}/bin/jboss-cli.sh")), "")
    if not cli:
        N.append("no se encontró jboss-cli.sh")
    else:
        T["jboss_home"] = cli.rsplit("/bin/", 1)[0]
        env = dict(os.environ, JAVA_OPTS="-Xmx128m")
        rc, o = sh([cli, "--connect", "--output-json", "--commands=:read-attribute(name=server-state)"], 90, env)
        V["server_running"] = 1 if rc == 0 and '"running"' in o else 0
        comp("servidor", V["server_running"] == 1, "server-state")
        rc, o = sh([cli, "--connect", "--output-json", "--commands=/deployment=*:read-attribute(name=status)"], 90, env)
        if rc == 0:
            V["despliegues"] = len(re.findall(r'"result"\s*:\s*"(OK|FAILED|STOPPED)"', o))
            V["despliegues_fallidos"] = len(re.findall(r'"result"\s*:\s*"FAILED"', o))
        rc, o = sh([cli, "--connect", "--output-json", "--commands=/core-service=platform-mbean/type=memory:read-attribute(name=heap-memory-usage)"], 90, env)
        m1, m2 = re.search(r'"used"\s*:\s*(\d+)', o or ""), re.search(r'"max"\s*:\s*(\d+)', o or "")
        if rc == 0 and m1 and m2 and int(m2.group(1)) > 0:
            V["heap_pct"] = round(100 * int(m1.group(1)) / int(m2.group(1)), 1)
            V["heap_max_mb"] = round(int(m2.group(1)) / 1048576)
        rc, o = sh([cli, "--connect", "--output-json", "--commands=/subsystem=datasources:read-children-names(child-type=data-source)"], 90, env)
        if rc == 0:
            try:
                V["datasources"] = len(json.loads(o).get("result") or [])
            except ValueError:
                pass
        rc, o = sh([cli, "--connect", "--output-json", "--commands=:read-attribute(name=product-version)"], 60, env)
        m = re.search(r'"result"\s*:\s*"([^"]+)"', o or "")
        if m:
            T["version"] = m.group(1)
    c, _ = http("http://127.0.0.1:8080/")
    V["http_responde"] = 1 if 0 < c < 500 else 0
    comp("http", V["http_responde"] == 1, ":8080")
    _, ss = sh("ss -ltnH")
    esc = [l_.split()[3] for l_ in ss.splitlines() if len(l_.split()) > 3 and l_.split()[3].endswith(":9990")]
    if esc:
        V["consola_local"] = 0 if any(x.startswith(("0.0.0.0", "*", "[::]")) for x in esc) else 1
    comp("administración", bool(esc) or None, ", ".join(esc) or "9990 cerrado")


def redis(_p):
    V["servicio_activo"] = activo("redis", "valkey")
    cli = shutil.which("redis-cli") or shutil.which("valkey-cli")
    if not cli:
        N.append("no se encontró redis-cli ni valkey-cli")
        return
    env = dict(os.environ)
    rc, o = sh([cli, "PING"], 10, env)
    if "PONG" not in o and "AUTH" in o.upper():
        V["requiere_auth"] = 1
        for f_ in ("/etc/redis/redis.conf", "/etc/redis.conf", "/etc/valkey/valkey.conf"):
            try:
                m = re.search(r"^\s*requirepass\s+(\S+)", open(f_, encoding="utf-8", errors="replace").read(), re.M)
            except OSError:
                m = None
            if m:
                env["REDISCLI_AUTH"] = m.group(1).strip('"')   # la clave local solo viaja por el entorno: no se imprime
                break
        rc, o = sh([cli, "PING"], 10, env)
    else:
        V["requiere_auth"] = 0 if "PONG" in o else None
    V["ping"] = 1 if "PONG" in o else 0
    comp("servidor", V["ping"] == 1, ":6379")
    rc, o = sh([cli, "INFO"], 15, env)
    i = dict(l_.split(":", 1) for l_ in o.splitlines() if ":" in l_ and not l_.startswith("#"))
    if i:
        num = lambda k: float(i.get(k, "0").strip() or 0)   # noqa: E731
        T["version"] = (i.get("redis_version") or i.get("valkey_version") or "").strip()
        T["rol"] = i.get("role", "").strip()
        V["clientes"], V["clientes_bloqueados"] = int(num("connected_clients")), int(num("blocked_clients"))
        V["memoria_mb"] = round(num("used_memory") / 1048576, 1)
        mx = num("maxmemory")
        V["maxmemory_conf"] = 1 if mx > 0 else 0
        if mx > 0:
            V["memoria_pct"] = round(100 * num("used_memory") / mx, 1)
        ok = i.get("rdb_last_bgsave_status", "ok").strip() == "ok" and i.get("aof_last_write_status", "ok").strip() == "ok"
        V["persistencia_ok"] = 1 if ok else 0
        comp("persistencia", ok, "RDB " + i.get("rdb_last_bgsave_status", "—").strip() + (" · AOF " + i.get("aof_last_write_status", "").strip() if i.get("aof_enabled", "0").strip() == "1" else ""))
        V["claves"] = sum(int(m.group(1)) for m in re.finditer(r"^db\d+:keys=(\d+)", o, re.M))


def postgresql(_p):
    V["servicio_activo"] = activo("postgresql", "postgresql-16", "postgresql-15", "postgresql-13")
    rc, o = sh("pg_isready -h /var/run/postgresql || pg_isready", 10)
    V["responde"] = 1 if rc == 0 else 0
    comp("servidor", rc == 0, "pg_isready")
    def q(sql):
        rc_, o_ = sh(["runuser", "-u", "postgres", "--", "psql", "-XAtqc", sql], 30)
        return o_.strip() if rc_ == 0 else None
    if V["responde"]:
        x = q("select count(*), current_setting('max_connections') from pg_stat_activity")
        if x and "|" in x:
            a, b = x.split("|")
            V["conexiones"], V["max_conexiones"] = int(a), int(b)
            V["conexiones_pct"] = round(100 * int(a) / max(1, int(b)), 1)
        x = q("select current_setting('archive_mode')")
        if x is not None:
            V["archivado"] = 1 if x in ("on", "always") else 0
        comp("archivado", V.get("archivado") == 1, x or "—")
        x = q("select count(*), coalesce(max(extract(epoch from replay_lag)),0) from pg_stat_replication")
        if x and "|" in x:
            n_, lag = x.split("|")
            V["replicas"] = int(n_)
            if int(n_):
                V["replica_lag_s"] = round(float(lag or 0), 1)
            comp("replicación", True if int(n_) else None, f"{n_} réplica(s)")
        x = q("select round(100*max(age(datfrozenxid))/2000000000.0,1) from pg_database")
        if x:
            V["xid_pct"] = float(x)
        x = q("select count(*) from pg_stat_activity where xact_start < now() - interval '5 minutes' and state <> 'idle'")
        if x is not None:
            V["tx_largas"] = int(x)
        x = q("select count(*), round(sum(pg_database_size(datname))/1048576.0) from pg_database where not datistemplate")
        if x and "|" in x:
            V["bases"], V["tamano_mb"] = int(x.split("|")[0]), float(x.split("|")[1] or 0)
        x = q("show server_version")
        if x:
            T["version"] = x.split()[0]


def activemq(_p):
    V["servicio_activo"] = activo("artemis", "amq-broker", "activemq")
    V["puerto_ok"] = 1 if puerto(61616) else 0
    comp("acceptor", V["puerto_ok"] == 1, ":61616")
    c, b = http("http://127.0.0.1:8161/metrics")
    comp("consola", c in (200, 401, 302) or None, ":8161")
    if c == 200 and "artemis_" in b:
        colas, msg, cons, sin = 0, 0, 0, 0
        por = {}
        for m in re.finditer(r'^artemis_(message_count|consumer_count)\{([^}]*)\}\s+([0-9.eE+-]+)', b, re.M):
            lb = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
            k = lb.get("queue") or lb.get("address")
            por.setdefault(k, {})[m.group(1)] = float(m.group(3))
        for k, x in por.items():
            if k and not k.startswith(("$", "activemq.")):
                colas += 1
                msg += x.get("message_count", 0)
                cons += x.get("consumer_count", 0)
                sin += 1 if x.get("message_count", 0) > 0 and x.get("consumer_count", 0) == 0 and k not in ("DLQ", "ExpiryQueue") else 0
        V["colas"], V["mensajes"], V["consumidores"], V["colas_sin_consumidor"] = colas, int(msg), int(cons), sin
        V["dlq_mensajes"] = int((por.get("DLQ") or {}).get("message_count", 0))
        mu = re.search(r"^artemis_address_memory_usage_percentage\S*\s+([0-9.eE+-]+)", b, re.M)
        if mu:
            V["memoria_pct"] = round(float(mu.group(1)) * (100 if float(mu.group(1)) <= 1 else 1), 1)
    else:
        N.append("el broker no publica /metrics en la consola (:8161): DLQ y colas quedan sin medir")


def nginx(_p):
    V["servicio_activo"] = activo("nginx")
    rc, o = sh(["nginx", "-t"], 20)
    V["config_ok"] = 1 if rc == 0 else 0
    comp("configuración", rc == 0, "nginx -t")
    c80, _ = http("http://127.0.0.1/")
    c443, _ = http("https://127.0.0.1/")
    V["http_responde"] = 1 if c80 or c443 else 0
    comp("servidor", V["http_responde"] == 1, ":80" if c80 else (":443" if c443 else "sin respuesta"))
    rc, o = sh(["nginx", "-T"], 20)
    if rc == 0:
        certs = sorted(set(re.findall(r"^\s*ssl_certificate\s+([^;]+);", o, re.M)))
        V["certificados"] = len(certs)
        d = cert_dias([x.strip() for x in certs])
        if d is not None:
            V["cert_dias_min"] = d
        comp("tls", (d is None) or d >= 30 if certs else None, f"{len(certs)} certificado(s)")
        V["upstreams"] = len(re.findall(r"^\s*upstream\s+\S+\s*\{", o, re.M))
        V["sitios"] = len(set(re.findall(r"^\s*server_name\s+([^;]+);", o, re.M)))
    rc, o = sh("nginx -v 2>&1", 5)
    m = re.search(r"nginx/(\S+)", o)
    if m:
        T["version"] = m.group(1)


def vault(_p):
    V["servicio_activo"] = activo("vault")
    conf = ""
    for f_ in glob.glob("/etc/vault.d/*.hcl") + glob.glob("/etc/vault/*.hcl"):
        try:
            conf += open(f_, encoding="utf-8", errors="replace").read()
        except OSError:
            pass
    V["tls"] = 0 if re.search(r'tls_disable\s*=\s*"?(1|true)', conf) else (1 if conf else None)
    st = re.search(r'storage\s+"(\w+)"', conf)
    if st:
        T["almacenamiento"] = st.group(1)
        V["raft"] = 1 if st.group(1) == "raft" else 0
    esq = "https" if V.get("tls") == 1 else "http"
    c, b = http(f"{esq}://127.0.0.1:8200/v1/sys/health?standbyok=true&sealedcode=200&uninitcode=200")
    comp("servidor", c == 200, ":8200")
    try:
        h = json.loads(b)
        V["inicializado"] = 1 if h.get("initialized") else 0
        V["sellado"] = 1 if h.get("sealed") else 0
        T["version"] = h.get("version", "")
        comp("almacenamiento", not h.get("sealed"), "desellado" if not h.get("sealed") else "sellado")
    except ValueError:
        pass


def satellite(_p):
    us = ["foreman", "httpd", "postgresql", "pulpcore-api", "pulpcore-content", "redis", "tomcat", "foreman-proxy", "dynflow-sidekiq@"]
    ok = [u for u in us if activo(u) == 1]
    V["serv_ok"], V["serv_total"] = len(ok), len(us)
    for u in us:
        comp(u.rstrip("@"), u in ok, "")
    lp = libre_pct("/var/lib/pulp" if os.path.exists("/var/lib/pulp") else "/")
    if lp is not None:
        V["espacio_libre_pct"] = lp
    x = sh(["runuser", "-u", "postgres", "--", "psql", "-XAtqd", "foreman", "-c", "select count(*) from foreman_tasks_tasks where state='paused'"], 30)
    if x[0] == 0 and x[1].strip().isdigit():
        V["tareas_pausadas"] = int(x[1].strip())
    d = cert_dias(glob.glob("/etc/pki/katello/certs/katello-apache.crt") + glob.glob("/etc/foreman-installer/custom-certs/*.crt"))
    if d is not None:
        V["cert_dias_min"] = d
    rc, o = sh("rpm -q --qf '%{VERSION}' satellite", 10)
    if rc == 0:
        T["version"] = o.strip()


def idm(_p):
    rc, o = sh(["ipactl", "status"], 60)
    est = re.findall(r"^(\S.*?) Service:\s*(\w+)", o, re.M)
    if est:
        V["serv_ok"] = sum(1 for _, s in est if s.upper() == "RUNNING")
        V["serv_total"] = len(est)
        for n, s in est:
            comp(n, s.upper() == "RUNNING", s)
    V["ldap_ok"] = 1 if sh(["ldapsearch", "-x", "-LLL", "-H", "ldap://127.0.0.1", "-s", "base", "-b", "", "namingContexts"], 15)[0] == 0 else 0
    rc, o = sh(["getcert", "list"], 30)
    if rc == 0:
        exps = re.findall(r"expires:\s*(.+)", o)
        V["certificados"] = len(exps)
        V["cert_problemas"] = len([s for s in re.findall(r"status:\s*(\S+)", o) if s != "MONITORING"])
        dias = []
        for x in exps:
            r2, ep = sh(["date", "-d", x.strip(), "+%s"], 5)
            if r2 == 0 and ep.strip().isdigit():
                dias.append(int((int(ep.strip()) - time.time()) // 86400))
        if dias:
            V["cert_dias_min"] = min(dias)


def ds389(_p):
    V["servicio_activo"] = activo("dirsrv@")
    inst = [os.path.basename(x)[len("slapd-"):] for x in glob.glob("/etc/dirsrv/slapd-*")]
    T["instancias"] = ", ".join(inst)
    V["instancias"] = len(inst)
    rc, o = sh(["ldapsearch", "-x", "-LLL", "-H", "ldap://127.0.0.1", "-s", "base", "-b", "", "namingContexts"], 15)
    V["ldap_ok"] = 1 if rc == 0 else 0
    if rc == 0:
        V["sufijos"] = len(re.findall(r"^namingContexts:", o, re.M))
    comp("ldap", rc == 0, ":389")
    V["ldaps_ok"] = 1 if puerto(636) else 0
    comp("ldaps", V["ldaps_ok"] == 1, ":636")
    lp = libre_pct("/var/lib/dirsrv" if os.path.exists("/var/lib/dirsrv") else "/")
    if lp is not None:
        V["espacio_libre_pct"] = lp


MEDIR = {"data-lake": data_lake, "kafka": kafka, "keycloak": keycloak, "jboss-eap": jboss, "redis": redis, "postgresql": postgresql,
         "activemq": activemq, "nginx": nginx, "vault": vault, "satellite": satellite, "red-hat-idm": idm, "389-directory-server": ds389}


def main():
    cod = sys.argv[1] if len(sys.argv) > 1 else ""
    if cod not in MEDIR:
        print(json.dumps({"error": f"plataforma sin medición por SSH: {cod}"}))
        sys.exit(2)
    try:
        MEDIR[cod](sys.argv[2] if len(sys.argv) > 2 else "")
    except Exception as x:  # noqa: BLE001  una medición parcial vale más que ninguna
        N.append(f"medición incompleta: {type(x).__name__}")
    print(json.dumps({"valores": {k: v for k, v in V.items() if v is not None}, "texto": T, "componentes": C, "notas": N}, ensure_ascii=False))


if __name__ == "__main__":
    main()
