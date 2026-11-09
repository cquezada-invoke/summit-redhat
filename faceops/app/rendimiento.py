"""Prueba de rendimiento del canal de un segmento de negocio (no invasiva): mide cada tramo entre cajas desde su máquina de
origen (conexiones TCP cortas, varias rondas de varias muestras, con pausa entre muestras) y la entrada HTTP de la aplicación
(unas pocas peticiones GET). No envía carga real ni cambia nada. Sirve para ver dónde está el cuello de botella."""
import json
import statistics
import threading
import time
from collections import defaultdict

from .bd import ahora, auditar, ej, j, q1

PLANTILLA = "FaceOps · Rendimiento del canal"
RONDAS, MUESTRAS = 3, 10


def _tabla():
    ej("CREATE TABLE IF NOT EXISTS rendimiento_canal (id INTEGER PRIMARY KEY, cliente_id INTEGER, segmento TEXT, cuando TEXT, por TEXT, "
       "estado TEXT, jobs TEXT DEFAULT '[]', tramos TEXT DEFAULT '{}', entrada TEXT DEFAULT '{}', resultado TEXT DEFAULT '{}', fin TEXT)")


def _stats(ms, perdidas=0):
    if not ms:
        return {"n": 0, "perdidas": perdidas}
    s = sorted(ms)
    return {"n": len(s), "min": round(s[0], 2), "prom": round(statistics.mean(s), 2), "p95": round(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))], 2),
            "max": round(s[-1], 2), "jitter": round(statistics.pstdev(s), 2), "perdidas": perdidas}


def _entrada(rid, url, rondas, muestras):
    """La entrada HTTP de la aplicación, medida desde FaceOps (pocas peticiones, con pausa)."""
    import requests
    out = []
    for _ in range(rondas):
        ms, err, cod = [], 0, {}
        for _ in range(muestras):
            try:
                r = requests.get(url, timeout=8, verify=False, headers={"User-Agent": "FaceOps-rendimiento/1.0"})
                ms.append(r.elapsed.total_seconds() * 1000)
                cod[r.status_code] = cod.get(r.status_code, 0) + 1
            except requests.RequestException:
                err += 1
            time.sleep(0.2)
        out.append(dict(_stats(ms, err), codigos=cod))
    todas = [x for x in out if x.get("n")]
    ej("UPDATE rendimiento_canal SET entrada=? WHERE id=?", json.dumps({"url": url, "rondas": out, "global": {
        "prom": round(statistics.mean(x["prom"] for x in todas), 2) if todas else None,
        "p95": max((x["p95"] for x in todas), default=None), "perdidas": sum(x.get("perdidas", 0) for x in out)}}), rid)


def lanzar(usuario, cliente, segmento, eqs):
    from .aap import AAP, ErrorAAP
    from .arquitectura import asignacion, flujos_expandidos, vigente
    from .operacion import acceso_equipo
    from .topologia import clave_flujo, flujos_verificables
    _tabla()
    arq = vigente(cliente["id"], segmento)
    if not arq:
        raise ValueError("El segmento no tiene arquitectura aprobada: no hay canal que medir")
    m = arq["m"]
    pares = {(f["desde"], f["hacia"]) for f in flujos_expandidos(m, asignacion(m, eqs)) if not f["desde"].startswith("x:")}
    flujos = [f for f in flujos_verificables(cliente, eqs) if any(f["clave"].startswith(clave_flujo(a, b, "")) for a, b in pares)]
    en_ = (m.get("entrada") or {}).get("url") or ""
    if not flujos and not en_:
        raise ValueError("No hay tramos medibles: los equipos de origen deben estar encendidos y con datos del recolector")
    aap = AAP.de_cliente(cliente)
    r = aap.get("job_templates/", name=PLANTILLA).get("results", [])
    if not r:
        raise ErrorAAP(f"Falta la plantilla «{PLANTILLA}» en AAP")
    por_nombre = {e["nombre"]: e for e in eqs}
    grupos = defaultdict(list)
    for f in flujos:
        try:
            inv, _, creds = acceso_equipo(aap, cliente, por_nombre[f["desde"]])
        except ErrorAAP:
            continue
        grupos[(inv, tuple(creds))].append(f)
    rid = ej("INSERT INTO rendimiento_canal(cliente_id, segmento, cuando, por, estado, tramos) VALUES (?,?,?,?,?,?)", cliente["id"], segmento, ahora(),
             usuario["usuario"], "en curso", json.dumps({f["clave"]: {"desde": f["desde"], "ip": f["ip"], "puerto": f["puerto"]} for f in flujos}))
    jobs = []
    for (inv, creds), fl in grupos.items():
        jobs.append(aap.lanzar("job", r[0]["id"], extra_vars={"faceops_flujos": fl, "fo_rondas": RONDAS, "fo_muestras": MUESTRAS},
                               limite=":".join(sorted({f["desde"] for f in fl})), inventario=inv, credenciales=list(creds)))
    ej("UPDATE rendimiento_canal SET jobs=?, entrada=? WHERE id=?", json.dumps(jobs), json.dumps({"url": en_, "pendiente": True} if en_ else {}), rid)
    if en_:
        threading.Thread(target=_entrada, args=(rid, en_, RONDAS, MUESTRAS), daemon=True).start()
    auditar(usuario["usuario"], cliente["id"], "prueba de rendimiento del canal", segmento, {"tramos": len(flujos), "trabajos": jobs})
    return {"id": rid, "tramos": len(flujos), "trabajos": jobs, "entrada": en_}


def estado(cliente, segmento):
    """Última prueba del segmento; si sigue en curso, lee de AAP los trabajos terminados y completa el resultado."""
    from .aap import AAP, ErrorAAP
    _tabla()
    r = q1("SELECT * FROM rendimiento_canal WHERE cliente_id=? AND segmento=? ORDER BY id DESC LIMIT 1", cliente["id"], segmento)
    if not r:
        return None
    if r["estado"] == "en curso":
        aap, fin, art = AAP.de_cliente(cliente), True, {}
        for jid in j(r["jobs"], []):
            try:
                x = aap.get(f"jobs/{jid}/")
            except ErrorAAP:
                fin = False
                continue
            if x.get("status") not in ("successful", "failed", "error", "canceled"):
                fin = False
                continue
            art.update(((x.get("artifacts") or {}).get("faceops_rendimiento")) or {})
        entrada = j(r["entrada"], {})
        if fin and entrada.get("pendiente") and time.time() - _ts(r["cuando"]) < 150:
            fin = False   # la entrada HTTP aún se está midiendo
        if fin:
            tramos = j(r["tramos"], {})
            for k, t in tramos.items():
                try:
                    d = json.loads(art.get(k) or "{}")
                except ValueError:
                    d = {}
                rondas = [_stats(x.get("ms") or [], x.get("perdidas", 0)) for x in d.get("rondas", [])]
                todas = [v for x in d.get("rondas", []) for v in (x.get("ms") or [])]
                t.update(rondas=rondas, **{"global": _stats(todas, sum(x.get("perdidas", 0) for x in d.get("rondas", [])))})
                if todas:   # la medición también alimenta la latencia normal del mapa
                    med = round(statistics.median(todas), 3)
                    ej("INSERT INTO flujos_latencia(cliente_id, clave, ms, en) VALUES (?,?,?,?)", cliente["id"], k, med, ahora())
                    ej("UPDATE flujos SET estado='abierto', verificado=?, latencia_ms=?, latencia_en=? WHERE cliente_id=? AND clave=?",
                       ahora(), med, ahora(), cliente["id"], k)
            ej("UPDATE rendimiento_canal SET estado='listo', tramos=?, fin=? WHERE id=?", json.dumps(tramos), ahora(), r["id"])
            r = q1("SELECT * FROM rendimiento_canal WHERE id=?", r["id"])
    return dict(r, tramos=j(r["tramos"], {}), entrada=j(r["entrada"], {}), jobs=j(r["jobs"], []))


def _ts(iso):
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0
