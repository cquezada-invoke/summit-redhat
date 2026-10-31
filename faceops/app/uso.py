"""Uso de cada máquina repartido entre sus segmentos de negocio. Cada hora FaceOps lanza «FaceOps · Uso por segmento»
(solo lectura) y guarda lo medido. Con dos mediciones seguidas calcula, por familia de proceso, la CPU promedio del
período; luego la reparte entre los segmentos que usan esa plataforma en esta máquina según su arquitectura:
  · proceso dedicado a un segmento → CPU y memoria exactas
  · nginx compartido → por bytes servidos de cada contexto (red exacta)
  · JBoss compartido → por tiempo de proceso de cada despliegue (CPU estimada); la memoria del JVM queda «compartida»
  · otro proceso compartido → partes iguales (estimado)"""
import json
import time
from datetime import datetime
from urllib.parse import urlparse

from .bd import ahora, auditar, con, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS uso_muestras (id INTEGER PRIMARY KEY, cliente_id INTEGER, equipo TEXT, ts INTEGER, datos TEXT);
CREATE INDEX IF NOT EXISTS uso_muestras_eq ON uso_muestras(cliente_id, equipo, ts);
CREATE TABLE IF NOT EXISTS uso_trabajos (id INTEGER PRIMARY KEY, cliente_id INTEGER, job INTEGER, creado TEXT, estado TEXT DEFAULT 'en curso');
"""
PLANTILLA = "FaceOps · Uso por segmento"
FAMILIA = {"nginx": "nginx", "jboss": "jboss", "postgres": "postgres", "postgresql": "postgres", "redis": "redis", "valkey": "redis",
           "activemq": "artemis", "artemis": "artemis", "kafka": "kafka", "keycloak": "keycloak", "httpd": "httpd", "apache": "httpd"}
NOMBRE = {"nginx": "Nginx", "jboss": "JBoss EAP", "postgres": "PostgreSQL", "redis": "Redis/Valkey", "artemis": "Artemis", "kafka": "Kafka",
          "keycloak": "Keycloak", "httpd": "Apache"}


def iniciar():
    con().executescript(ESQUEMA)


# ------------------------------------------------------------------ medición (AAP)
def lanzar(cliente):
    """Lanza la medición con los mismos inventarios y credenciales que la recolección del cliente."""
    from .aap import AAP, ErrorAAP
    aap = AAP.de_cliente(cliente)
    r = aap.get("job_templates/", name=PLANTILLA).get("results", [])
    if not r:
        raise ErrorAAP(f"No existe la plantilla «{PLANTILLA}» en AAP")
    ult = q1("SELECT MAX(ts) t FROM uso_muestras WHERE cliente_id=?", cliente["id"])
    desde = (ult or {}).get("t") or int(time.time()) - 3600
    inventarios = {i["name"]: i["id"] for i in aap.inventarios()}
    jobs = []
    for inv, pasos in j(cliente["recoleccion"], {}).items():
        if inv.startswith("_") or inv not in inventarios:
            continue
        for p in pasos:
            cred = aap.credencial(p["credencial"]) if p.get("credencial") else None
            job = aap.lanzar("job", r[0]["id"], extra_vars={"uso_desde": desde}, limite=p.get("limite") or None,
                             inventario=inventarios[inv], credenciales=[cred] if cred else None)
            ej("INSERT INTO uso_trabajos(cliente_id, job, creado) VALUES (?,?,?)", cliente["id"], job, ahora())
            jobs.append(job)
    return jobs


def recoger(cliente):
    """Guarda lo que midieron los trabajos terminados."""
    from .aap import AAP
    pend = q("SELECT * FROM uso_trabajos WHERE cliente_id=? AND estado='en curso'", cliente["id"])
    if not pend:
        return 0
    aap, n = AAP.de_cliente(cliente), 0
    for t in pend:
        jb = aap.get(f"jobs/{t['job']}/")
        if jb.get("status") in ("pending", "waiting", "running", "new"):
            continue
        for equipo, d in ((jb.get("artifacts") or {}).get("faceops_uso") or {}).items():
            ej("INSERT INTO uso_muestras(cliente_id, equipo, ts, datos) VALUES (?,?,?,?)", cliente["id"], equipo, int(d.get("ts") or time.time()),
               json.dumps(d))
            n += 1
        ej("UPDATE uso_trabajos SET estado=? WHERE id=?", jb.get("status") or "terminado", t["id"])
    ej("DELETE FROM uso_muestras WHERE cliente_id=? AND ts < ?", cliente["id"], int(time.time()) - 15 * 86400)
    return n


# ------------------------------------------------------------------ reparto por segmento
def _contexto(url):
    p = urlparse(url or "").path.strip("/")
    return "/" + p.split("/")[0] if p else "/"


def asignacion(cliente_id, equipo):
    """{familia: [(segmento, contexto)]} según los manifiestos aprobados que ponen componentes en este equipo."""
    from .arquitectura import vigentes
    out = {}
    for m in [r["m"] for r in vigentes(cliente_id).values()]:
        ctx = _contexto((m.get("entrada") or {}).get("url"))
        for c in m.get("componentes") or []:
            if equipo in (c.get("equipos") or []) or equipo.startswith(f"srv-{m.get('codigo')}-{c.get('id')}-"):
                fam = FAMILIA.get((c.get("producto") or "").lower())
                if fam:
                    out.setdefault(fam, []).append((m.get("segmento"), c.get("contexto") or ctx))
    return out


def por_segmento(cliente_id, e):
    """{segmento: {cpu_pct, cpu_tipo, mem_mb, mem_tipo, red_bytes, req, sesiones, pool, plataformas}} del último período medido."""
    ms = q("SELECT ts, datos FROM uso_muestras WHERE cliente_id=? AND equipo=? ORDER BY ts DESC LIMIT 2", cliente_id, e["nombre"])
    if not ms:
        return {"medido": False}
    b = json.loads(ms[0]["datos"])
    a = json.loads(ms[1]["datos"]) if len(ms) > 1 else None
    seg = {}

    def s_(n):
        return seg.setdefault(n, {"cpu_pct": 0.0, "cpu_tipo": set(), "mem_mb": 0.0, "mem_tipo": set(), "red_bytes": 0, "req": 0,
                                  "sesiones": 0, "pool": 0, "plataformas": []})
    horas = max((b["ts"] - (a["ts"] if a else b.get("desde", b["ts"] - 3600))) / 3600, 1 / 60)
    ncpu = b.get("ncpu") or 1
    for fam, duenos in asignacion(cliente_id, e["nombre"]).items():
        pb = (b.get("procesos") or {}).get(fam)
        if not pb:
            continue
        pa = (a.get("procesos") or {}).get(fam) if a else None
        cpu = None
        if pa and pb["cpu_s"] >= pa["cpu_s"]:
            cpu = (pb["cpu_s"] - pa["cpu_s"]) / max(b["ts"] - a["ts"], 1) / ncpu * 100
        nombres = list(dict.fromkeys(d[0] for d in duenos))
        pesos = {}
        if len(nombres) == 1:
            pesos, tipo_cpu, tipo_mem = {nombres[0]: 1.0}, "exacta", "exacta"
        elif fam == "nginx":
            ng = b.get("nginx") or {}
            v = {n: sum(ng.get(cx, {}).get("bytes", 0) for s2, cx in duenos if s2 == n) for n in nombres}
            pesos, tipo_cpu, tipo_mem = ({n: x / sum(v.values()) for n, x in v.items()} if sum(v.values()) else {}), "estimada por tráfico", "compartida"
        elif fam == "jboss":
            apps = ((b.get("jboss") or {}).get("apps") or {}).values()
            apps_a = {x.get("contexto"): x for x in (((a or {}).get("jboss") or {}).get("apps") or {}).values()}
            v = {}
            for n in nombres:
                cxs = {cx for s2, cx in duenos if s2 == n}
                v[n] = sum(max(x["tiempo_ms"] - apps_a.get(x["contexto"], {}).get("tiempo_ms", 0), 0) for x in apps if x["contexto"] in cxs)
            pesos, tipo_cpu, tipo_mem = ({n: x / sum(v.values()) for n, x in v.items()} if sum(v.values()) else {}), "estimada por tiempo de proceso", "compartida"
        else:
            pesos, tipo_cpu, tipo_mem = {n: 1 / len(nombres) for n in nombres}, "estimada en partes iguales", "compartida"
        if not pesos:
            pesos, tipo_cpu = {n: 1 / len(nombres) for n in nombres}, "estimada en partes iguales"
        for n in nombres:
            x = s_(n)
            x["plataformas"].append(NOMBRE.get(fam, fam))
            if cpu is not None:
                x["cpu_pct"] += cpu * pesos.get(n, 0)
                x["cpu_tipo"].add(tipo_cpu)
            if tipo_mem == "exacta":
                x["mem_mb"] += pb["rss_mb"]
                x["mem_tipo"].add("exacta")
            else:
                x["mem_tipo"].add("compartida")
            cxs = {cx for s2, cx in duenos if s2 == n}
            if fam == "nginx":
                for cx in cxs:
                    x["red_bytes"] += (b.get("nginx") or {}).get(cx, {}).get("bytes", 0)
                    x["req"] += (b.get("nginx") or {}).get(cx, {}).get("req", 0)
            if fam == "jboss":
                for ap in ((b.get("jboss") or {}).get("apps") or {}).values():
                    if ap["contexto"] in cxs:
                        x["sesiones"] += ap["sesiones"]
                x["pool"] += sum(p_["activas"] for p_ in ((b.get("jboss") or {}).get("pools") or {}).values()) if len(nombres) == 1 else 0
    for x in seg.values():
        x["cpu_pct"] = round(x["cpu_pct"], 1) if x["cpu_tipo"] else None
        x["cpu_tipo"] = " · ".join(sorted(x["cpu_tipo"])) or "sin dos mediciones aún"
        x["mem_tipo"] = "exacta" if x["mem_tipo"] == {"exacta"} else ("compartida" if "compartida" in x["mem_tipo"] and not x["mem_mb"] else "parcial")
        x["red_h"] = round(x["red_bytes"] / horas)
        x["req_h"] = round(x["req"] / horas)
    return {"medido": True, "fecha": datetime.fromtimestamp(b["ts"]).strftime("%Y-%m-%d %H:%M"), "segmentos": seg,
            "jboss_estadisticas": (b.get("jboss") or {}).get("estadisticas")}
