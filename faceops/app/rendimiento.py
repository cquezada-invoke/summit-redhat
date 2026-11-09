"""Prueba de rendimiento del canal de un segmento de negocio (no invasiva, por tiempo, en vivo). Cada tramo entre cajas se mide
desde su máquina de origen (AAP abre y cierra conexiones TCP al puerto del destino, sin datos de negocio; no es ping ICMP) y la
entrada de la aplicación con peticiones HTTP GET desde FaceOps. Dura los segundos que se pidan (máximo 120), se puede pausar,
reanudar y detener, y las muestras llegan en vivo (eventos del trabajo en AAP). Un tramo es cuello de botella si su promedio
supera el umbral de rendimiento (200 ms por defecto, editable)."""
import json
import statistics
import threading
import time
from collections import defaultdict

from .bd import ahora, ajuste, auditar, ej, j, q1

PLANTILLA = "FaceOps · Rendimiento del canal"
MAX_SEG = 120
UMBRAL_DEF = 200.0


def umbral(cliente_id, nuevo=None):
    """Umbral de rendimiento (ms) para toda la aplicación: sobre él un tramo es cuello de botella."""
    k = f"umbral_rendimiento_ms_{cliente_id}"
    if nuevo is not None:
        v = max(1.0, min(float(nuevo), 60000.0))
        ajuste(k, str(v))
        return v
    try:
        return float(ajuste(k) or UMBRAL_DEF)
    except ValueError:
        return UMBRAL_DEF


def _tabla():
    ej("CREATE TABLE IF NOT EXISTS rendimiento_canal (id INTEGER PRIMARY KEY, cliente_id INTEGER, segmento TEXT, cuando TEXT, por TEXT, "
       "estado TEXT, jobs TEXT DEFAULT '[]', tramos TEXT DEFAULT '{}', entrada TEXT DEFAULT '{}', resultado TEXT DEFAULT '{}', fin TEXT)")
    from .bd import q as _q
    cols = {r["name"] for r in _q("PRAGMA table_info(rendimiento_canal)")}
    for c, t in (("duracion", "INTEGER DEFAULT 30"), ("inicio_ts", "REAL"), ("pausado_acum", "REAL DEFAULT 0"), ("pausa_desde", "REAL"),
                 ("ultimos", "TEXT DEFAULT '{}'"), ("muestras", "TEXT DEFAULT '{}'"), ("flujos", "TEXT DEFAULT '[]'")):
        if c not in cols:
            ej(f"ALTER TABLE rendimiento_canal ADD COLUMN {c} {t}")


def _stats(ms, perdidas=0):
    if not ms:
        return {"n": 0, "perdidas": perdidas}
    s = sorted(ms)
    return {"n": len(s), "min": round(s[0], 2), "prom": round(statistics.mean(s), 2), "p95": round(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))], 2),
            "max": round(s[-1], 2), "jitter": round(statistics.pstdev(s), 2), "perdidas": perdidas}


def transcurrido(r):
    if not r.get("inicio_ts"):
        return 0
    hasta = r["pausa_desde"] if r["estado"] == "pausado" and r.get("pausa_desde") else time.time()
    return max(0.0, hasta - r["inicio_ts"] - (r.get("pausado_acum") or 0))


def _lanzar_jobs(cliente, flujos, eqs, segundos):
    from .aap import AAP, ErrorAAP
    from .operacion import acceso_equipo
    aap = AAP.de_cliente(cliente)
    r = aap.get("job_templates/", name=PLANTILLA).get("results", [])
    if not r:
        raise ErrorAAP(f"Falta la plantilla «{PLANTILLA}» en AAP")
    por_nombre = {e["nombre"]: e for e in eqs}
    grupos = defaultdict(list)
    for f in flujos:
        try:
            inv, _, creds = acceso_equipo(aap, cliente, por_nombre[f["desde"]])
        except (ErrorAAP, KeyError):
            continue
        grupos[(inv, tuple(creds))].append(f)
    return [aap.lanzar("job", r[0]["id"], extra_vars={"faceops_flujos": fl, "fo_segundos": int(segundos) + 3},
                       limite=":".join(sorted({f["desde"] for f in fl})), inventario=inv, credenciales=list(creds)) for (inv, creds), fl in grupos.items()]


def _entrada(app_, rid, url):
    """La entrada HTTP, una petición por segundo mientras la prueba corre (respeta pausa y detención)."""
    import requests
    with app_.app_context():
        while True:
            r = q1("SELECT * FROM rendimiento_canal WHERE id=?", rid)
            if not r or r["estado"] in ("detenido", "listo"):
                return
            if r["estado"] in ("pausado", "preparando"):
                time.sleep(1)
                continue
            if transcurrido(r) >= (r["duracion"] or 30):
                return
            ms, err = None, 0
            try:
                x = requests.get(url, timeout=5, verify=False, headers={"User-Agent": "FaceOps-rendimiento/1.0"})
                ms = round(x.elapsed.total_seconds() * 1000, 2)
            except requests.RequestException:
                err = 1
            e = j(q1("SELECT entrada FROM rendimiento_canal WHERE id=?", rid)["entrada"], {})
            if ms is not None:
                e.setdefault("ms", []).append(ms)
            e["perdidas"] = e.get("perdidas", 0) + err
            e["url"] = url
            ej("UPDATE rendimiento_canal SET entrada=? WHERE id=?", json.dumps(e), rid)
            time.sleep(1)


def iniciar(usuario, cliente, segmento, eqs, segundos):
    from flask import current_app
    from .arquitectura import asignacion, flujos_expandidos, vigente
    from .topologia import clave_flujo, flujos_verificables
    _tabla()
    segundos = max(1, min(int(segundos or 30), MAX_SEG))
    arq = vigente(cliente["id"], segmento)
    if not arq:
        raise ValueError("El segmento no tiene arquitectura aprobada: no hay canal que medir")
    m = arq["m"]
    pares = {(f["desde"], f["hacia"]) for f in flujos_expandidos(m, asignacion(m, eqs)) if not f["desde"].startswith("x:")}
    flujos = [f for f in flujos_verificables(cliente, eqs) if any(f["clave"].startswith(clave_flujo(a, b, "")) for a, b in pares)]
    en_ = (m.get("entrada") or {}).get("url") or ""
    if not flujos and not en_:
        raise ValueError("No hay tramos medibles: los equipos de origen deben estar encendidos y con datos del recolector")
    rid = ej("INSERT INTO rendimiento_canal(cliente_id, segmento, cuando, por, estado, duracion, inicio_ts, tramos, flujos, entrada) VALUES (?,?,?,?,?,?,?,?,?,?)",
             cliente["id"], segmento, ahora(), usuario["usuario"], "preparando" if flujos else "corriendo", segundos, None if flujos else time.time(),
             json.dumps({f["clave"]: {"desde": f["desde"], "ip": f["ip"], "puerto": f["puerto"]} for f in flujos}), json.dumps(flujos),
             json.dumps({"url": en_} if en_ else {}))
    jobs = _lanzar_jobs(cliente, flujos, eqs, segundos) if flujos else []
    ej("UPDATE rendimiento_canal SET jobs=? WHERE id=?", json.dumps(jobs), rid)
    if en_:
        threading.Thread(target=_entrada, args=(current_app._get_current_object(), rid, en_), daemon=True).start()
    auditar(usuario["usuario"], cliente["id"], "prueba de rendimiento del canal", segmento, {"segundos": segundos, "tramos": len(flujos), "trabajos": jobs})
    return rid


def _cancelar(cliente, jobs):
    from .aap import AAP, ErrorAAP
    aap = AAP.de_cliente(cliente)
    for jid in jobs:
        try:
            aap.post(f"jobs/{jid}/cancel/")
        except ErrorAAP:
            pass


def controlar(usuario, cliente, segmento, eqs, accion):
    _tabla()
    r = q1("SELECT * FROM rendimiento_canal WHERE cliente_id=? AND segmento=? ORDER BY id DESC LIMIT 1", cliente["id"], segmento)
    if not r or r["estado"] not in ("corriendo", "pausado", "preparando"):
        raise ValueError("No hay una prueba en curso")
    if r["estado"] == "preparando" and accion != "detener":
        raise ValueError("AAP aún está arrancando la medición: espera unos segundos o detenla")
    if accion == "pausar" and r["estado"] == "corriendo":
        _ingerir(cliente, r)
        _cancelar(cliente, j(r["jobs"], []))
        ej("UPDATE rendimiento_canal SET estado='pausado', pausa_desde=? WHERE id=?", time.time(), r["id"])
    elif accion == "reanudar" and r["estado"] == "pausado":
        resto = max(1, int((r["duracion"] or 30) - transcurrido(r)))
        jobs = j(r["jobs"], []) + (_lanzar_jobs(cliente, j(r["flujos"], []), eqs, resto) if j(r["flujos"], []) else [])
        ej("UPDATE rendimiento_canal SET estado='corriendo', pausado_acum=COALESCE(pausado_acum,0)+?, pausa_desde=NULL, jobs=? WHERE id=?",
           time.time() - (r["pausa_desde"] or time.time()), json.dumps(jobs), r["id"])
    elif accion == "detener":
        _ingerir(cliente, r)
        _cancelar(cliente, j(r["jobs"], []))
        if r["estado"] == "pausado":
            ej("UPDATE rendimiento_canal SET pausado_acum=COALESCE(pausado_acum,0)+?, pausa_desde=NULL WHERE id=?",
               time.time() - (r["pausa_desde"] or time.time()), r["id"])
        _cerrar(cliente, q1("SELECT * FROM rendimiento_canal WHERE id=?", r["id"]), "detenido")
    auditar(usuario["usuario"], cliente["id"], f"prueba de rendimiento: {accion}", segmento, {"id": r["id"]})


def _ingerir(cliente, r):
    """Lee de AAP las muestras nuevas (un evento por paso de cada equipo de origen)."""
    from .aap import AAP, ErrorAAP
    aap, ult, mu = AAP.de_cliente(cliente), j(r["ultimos"], {}), j(r["muestras"], {})
    for jid in j(r["jobs"], []):
        try:
            evs = aap.get(f"jobs/{jid}/job_events/", event="runner_item_on_ok", id__gt=ult.get(str(jid), 0), order_by="id", page_size=200).get("results", [])
        except ErrorAAP:
            continue
        for ev in evs:
            ult[str(jid)] = max(ult.get(str(jid), 0), ev["id"])
            try:
                d = json.loads(((ev.get("event_data") or {}).get("res") or {}).get("stdout") or "{}")
            except ValueError:
                continue
            for k, v in (d.get("m") or {}).items():
                x = mu.setdefault(k, {"ms": [], "perdidas": 0, "serie": []})
                x["ms"] = (x["ms"] + v)[-3000:]
                x["perdidas"] += (d.get("p") or {}).get(k, 0)
                if v:
                    x["serie"] = (x["serie"] + [round(statistics.mean(v), 2)])[-150:]
    ej("UPDATE rendimiento_canal SET ultimos=?, muestras=? WHERE id=?", json.dumps(ult), json.dumps(mu), r["id"])
    return mu


def _cerrar(cliente, r, estado_):
    for k, x in j(r["muestras"], {}).items():   # la medición también alimenta la latencia normal del mapa
        if x.get("ms"):
            med = round(statistics.median(x["ms"]), 3)
            ej("INSERT INTO flujos_latencia(cliente_id, clave, ms, en) VALUES (?,?,?,?)", cliente["id"], k, med, ahora())
            ej("UPDATE flujos SET estado='abierto', verificado=?, latencia_ms=?, latencia_en=? WHERE cliente_id=? AND clave=?", ahora(), med, ahora(), cliente["id"], k)
    ej("UPDATE rendimiento_canal SET estado=?, fin=? WHERE id=?", estado_, ahora(), r["id"])


def _ts(iso):
    from datetime import datetime
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def estado(cliente, segmento):
    """La última prueba del segmento, con sus muestras al día (lee AAP mientras corre)."""
    _tabla()
    r = q1("SELECT * FROM rendimiento_canal WHERE cliente_id=? AND segmento=? ORDER BY id DESC LIMIT 1", cliente["id"], segmento)
    if not r:
        return None
    if r["estado"] == "preparando":   # AAP arranca los trabajos (≈20 s): el reloj parte con la primera muestra
        mu = _ingerir(cliente, r)
        if any(x.get("ms") for x in mu.values()):
            ej("UPDATE rendimiento_canal SET estado='corriendo', inicio_ts=? WHERE id=?", time.time(), r["id"])
        elif time.time() - _ts(r["cuando"]) > 120:
            _cancelar(cliente, j(r["jobs"], []))
            ej("UPDATE rendimiento_canal SET estado='detenido', fin=? WHERE id=?", ahora(), r["id"])
        r = q1("SELECT * FROM rendimiento_canal WHERE id=?", r["id"])
    if r["estado"] == "corriendo":
        _ingerir(cliente, r)
        if transcurrido(r) >= (r["duracion"] or 30):
            _cancelar(cliente, j(r["jobs"], []))
            _cerrar(cliente, q1("SELECT * FROM rendimiento_canal WHERE id=?", r["id"]), "listo")
        r = q1("SELECT * FROM rendimiento_canal WHERE id=?", r["id"])
    um = umbral(cliente["id"])
    mu, tr = j(r["muestras"], {}), j(r["tramos"], {})
    tramos = {}
    for k, t in tr.items():
        x = mu.get(k) or {}
        g = _stats(x.get("ms") or [], x.get("perdidas", 0))
        tramos[k] = dict(t, **{"global": g, "serie": x.get("serie", [])[-60:], "cuello": bool(g.get("n") and g["prom"] > um)})
    e = j(r["entrada"], {})
    ms_e = [v for v in e.get("ms", []) if v is not None]
    entrada = {"url": e.get("url", ""), "global": _stats(ms_e, e.get("perdidas", 0)), "serie": ms_e[-60:]} if e.get("url") else {}
    if entrada:
        entrada["cuello"] = bool(ms_e and entrada["global"]["prom"] > um)
    return {"id": r["id"], "estado": r["estado"], "duracion": r["duracion"], "transcurrido": round(transcurrido(r), 1), "cuando": r["cuando"],
            "fin": r["fin"], "por": r["por"], "umbral": um, "tramos": tramos, "entrada": entrada}
