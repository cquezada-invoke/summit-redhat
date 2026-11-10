"""Colector del data lake: recibe la copia (espejo de nginx) de cada request de un segmento de negocio y la guarda en el lake
(S3 + Trino) como JSON por línea, para que JavIA la tome después. También genera flujos funcionales hacia la aplicación del
segmento (para probar la derivación) y muestra lo recibido. No guarda cabeceras de autenticación ni cookies."""
import datetime
import json
import os
import re
import threading
import time

import requests
from flask import Flask, jsonify, render_template_string, request

S3 = os.environ.get("S3_URL", "http://minio:9000").rstrip("/")
TRINO = os.environ.get("TRINO_URL", "http://trino:8080").rstrip("/")
BUCKET = "lake"
LAKE_WEB = os.environ.get("LAKE_WEB", "https://lake.sno.34.176.42.56.sslip.io/buckets/lake/")
LOTE, CADA = 2000, 15          # se escribe al lake cada 100 registros o cada 15 segundos
app = Flask(__name__)
_buf, _cont, _ult, _gen, _tablas = {}, {}, {}, {}, set()
_lock = threading.Lock()
RE_PREF = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def esquema(pref):
    return "seg_" + pref.replace("-", "_")


def trino(q):
    h = {"X-Trino-User": "colector", "X-Trino-Catalog": "lake"}
    r = requests.post(f"{TRINO}/v1/statement", data=q.encode(), headers=h, timeout=30).json()
    filas, cols = [], None
    while True:
        if r.get("columns") and cols is None:
            cols = [c["name"] for c in r["columns"]]
        filas += r.get("data") or []
        if r.get("error"):
            raise ValueError(r["error"]["message"])
        if "nextUri" not in r:
            return cols or [], filas
        r = requests.get(r["nextUri"], headers=h, timeout=60).json()


def asegurar_tabla(pref):
    if pref in _tablas:
        return
    es = esquema(pref)
    trino(f"CREATE SCHEMA IF NOT EXISTS lake.{es} WITH (location = 's3://{BUCKET}/{pref}/')")
    trino(f"CREATE TABLE IF NOT EXISTS lake.{es}.requests (ts varchar, segmento varchar, metodo varchar, ruta varchar, consulta varchar, "
          f"ip varchar, agente varchar, tipo varchar, largo integer, cuerpo varchar) "
          f"WITH (format = 'JSON', external_location = 's3://{BUCKET}/{pref}/requests/')")
    _tablas.add(pref)


def archivos(pref):
    """Archivos que hay hoy en s3://lake/<pref>/requests/: (fecha, bytes, clave), más nuevos primero."""
    import urllib.parse
    import xml.etree.ElementTree as ET
    ns, out, token = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}, [], None
    while True:
        q = {"list-type": "2", "max-keys": "1000", "prefix": f"{pref}/requests/"}
        if token:
            q["continuation-token"] = token
        x = ET.fromstring(requests.get(f"{S3}/{BUCKET}?" + urllib.parse.urlencode(q), timeout=20).content)
        out += [(c.find("s:LastModified", ns).text, int(c.find("s:Size", ns).text), c.find("s:Key", ns).text) for c in x.findall("s:Contents", ns)]
        t = x.find("s:NextContinuationToken", ns)
        if t is None or not t.text:
            return sorted(out, reverse=True)
        token = t.text


def ocupado(pref):
    """Bytes y archivos que hay hoy en s3://lake/<pref>/requests/ (la rotación borra los más antiguos)."""
    a = archivos(pref)
    return sum(x[1] for x in a), len(a)


_uso_lock = threading.Lock()


def leer_uso(pref):
    """Total enviado al lake por el segmento (acumulado; no baja cuando la rotación borra). Vive en s3://lake/<pref>/_uso.json."""
    r = requests.get(f"{S3}/{BUCKET}/{pref}/_uso.json", timeout=10)
    if r.status_code == 200:
        return r.json()
    b, n = ocupado(pref)   # primera vez: parte desde lo que ya hay guardado
    return {"bytes": b, "archivos": n, "registros": 0, "desde": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z", "base": True}


def sumar_uso(pref, b, regs):
    with _uso_lock:
        u = leer_uso(pref)
        u["bytes"] += b
        u["archivos"] += 1
        u["registros"] = u.get("registros", 0) + regs
        requests.put(f"{S3}/{BUCKET}/{pref}/_uso.json", data=json.dumps(u).encode(), headers={"Content-Type": "application/json"},
                     timeout=10).raise_for_status()


def volcar(pref):
    with _lock:
        lote, _buf[pref] = _buf.get(pref, []), []
    if not lote:
        return 0
    clave = f"{pref}/requests/{datetime.datetime.utcnow():%Y%m%d-%H%M%S}-{len(lote)}-{int(time.time() * 1000) % 1000:03d}.json"
    datos = "\n".join(json.dumps(x, ensure_ascii=False) for x in lote).encode()
    requests.put(f"{S3}/{BUCKET}/{clave}", data=datos, headers={"Content-Type": "application/json"}, timeout=30).raise_for_status()
    try:
        sumar_uso(pref, len(datos), len(lote))
    except Exception as e:  # noqa: BLE001 - el contador es informativo
        app.logger.warning("uso %s: %s", pref, e)
    try:
        asegurar_tabla(pref)
    except Exception as e:  # noqa: BLE001 - el archivo ya quedó en S3; la tabla se reintenta en el próximo lote
        app.logger.warning("tabla %s: %s", pref, e)
    return len(lote)


def _volcador():
    while True:
        time.sleep(CADA)
        for p in list(_buf):
            try:
                volcar(p)
            except Exception as e:  # noqa: BLE001
                app.logger.warning("volcar %s: %s", p, e)


threading.Thread(target=_volcador, daemon=True).start()


@app.route("/salud")
def salud():
    return {"ok": True}


@app.route("/ingesta/<pref>/", defaults={"resto": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@app.route("/ingesta/<pref>/<path:resto>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def ingesta(pref, resto):
    if not RE_PREF.match(pref):
        return "", 400
    ruta = "/" + resto
    if ruta.startswith("/fo_lake_"):
        return "", 204
    tipo = request.headers.get("Content-Type", "")
    cuerpo = request.get_data(cache=False)[:8192]
    txt = ""
    if cuerpo and ("json" in tipo or "form" in tipo or "text" in tipo):
        txt = cuerpo.decode("utf-8", "replace")
        txt = re.sub(r'(?i)("?(password|clave|contrase[nñ]a|token|secret|tarjeta|cvv)"?\s*[:=]\s*)("[^"]*"|[^&,}\s]*)', r"\1\"***\"", txt)
    r = {"ts": datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z", "segmento": pref,
         "metodo": request.headers.get("X-FO-Metodo") or request.method, "ruta": ruta, "consulta": request.query_string.decode()[:1000],
         "ip": request.headers.get("X-FO-IP", ""), "agente": (request.headers.get("User-Agent") or "")[:200], "tipo": tipo[:80],
         "largo": len(cuerpo), "cuerpo": txt[:4000]}
    with _lock:
        _buf.setdefault(pref, []).append(r)
        _cont[pref] = _cont.get(pref, 0) + 1
        _ult[pref] = r["ts"]
        lleno = len(_buf[pref]) >= LOTE
    if lleno:
        threading.Thread(target=volcar, args=(pref,), daemon=True).start()
    return "", 204


@app.route("/estado/<pref>")
def estado(pref):
    g = _gen.get(pref) or {}
    uso = {}
    if RE_PREF.match(pref):
        try:
            u = leer_uso(pref)
            b, n = ocupado(pref)
            uso = {"enviado_bytes": u["bytes"], "enviado_desde": u.get("desde", ""), "en_s3_bytes": b, "en_s3_archivos": n}
        except Exception as e:  # noqa: BLE001
            app.logger.warning("uso %s: %s", pref, e)
    return jsonify({"segmento": pref, **uso, "recibidos": _cont.get(pref, 0), "ultimo": _ult.get(pref, ""), "en_cola": len(_buf.get(pref, [])),
                    "generador": {k: g.get(k) for k in ("activo", "enviados", "errores", "hasta", "base")}})


# ─────────── flujos funcionales: tráfico de negocio sintético hacia la aplicación (pasa por el balanceador y se espeja) ───────────
def _generar(pref, base, escenario, hasta, rps):
    g = _gen[pref]
    s = requests.Session(); s.verify = False
    i = 0
    while g.get("activo") and time.time() < hasta:
        m, ruta, cuerpo = escenario[i % len(escenario)]
        i += 1
        try:
            datos = json.loads(json.dumps(cuerpo).replace("{n}", str(i))) if cuerpo else None
            s.request(m, base.rstrip("/") + ruta.replace("{n}", str(i)), json=datos, timeout=10,
                      headers={"User-Agent": "FaceOps-flujos-funcionales/1.0"})
            g["enviados"] += 1
        except requests.RequestException:
            g["errores"] += 1
        time.sleep(1.0 / max(rps, 0.1))
    g["activo"] = False


@app.route("/generador/<pref>", methods=["POST", "DELETE"])
def generador(pref):
    if not RE_PREF.match(pref):
        return {"error": "segmento no válido"}, 400
    if request.method == "DELETE":
        (_gen.get(pref) or {})["activo"] = False
        return {"ok": True, "activo": False}
    d = request.get_json(silent=True) or {}
    base = str(d.get("base", ""))
    if not re.match(r"^https?://192\.168\.\d+\.\d+(:\d+)?(/|$)", base):   # solo hacia la red interna del laboratorio
        return {"error": "base no válida"}, 400
    esc = [(str(x[0]).upper(), str(x[1]), x[2] if len(x) > 2 else None) for x in (d.get("escenario") or [["GET", "/"]])][:30]
    minutos, rps = min(int(d.get("minutos", 30)), 240), min(float(d.get("rps", 2)), 20)
    (_gen.get(pref) or {})["activo"] = False
    _gen[pref] = {"activo": True, "enviados": 0, "errores": 0, "hasta": time.time() + 60 * minutos, "base": base}
    threading.Thread(target=_generar, args=(pref, base, esc, _gen[pref]["hasta"], rps), daemon=True).start()
    return {"ok": True, "activo": True, "minutos": minutos, "rps": rps}


# ─────────── ver los datos ───────────
VISTA = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Data lake · {{ pref }}</title><style>body{font-family:system-ui,sans-serif;margin:24px;color:#151515;background:#fafafa}h1{font-size:20px;margin:0 0 4px}
.k{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}.k div{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px;min-width:150px}
.k b{display:block;font-size:22px}table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}td,th{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f3f3f3}.s{color:#6a6e73;font-size:12.5px}code{font-size:12px;word-break:break-all}</style></head><body>
<h1>Data lake · segmento «{{ pref }}»</h1><div class="s">Copia de cada request que entra por el balanceador del segmento · <code>s3://lake/{{ pref }}/requests/</code> · tabla <code>lake.{{ esquema }}.requests</code> en Trino (JavIA la consulta desde ahí)</div>
<div class="k"><div>Recibidos (desde el arranque)<b>{{ rec }}</b></div><div>En el lake<b>{{ total }}</b></div><div>Último<b style="font-size:14px">{{ ult or '—' }}</b></div>
<div>Flujos funcionales<b style="font-size:14px">{{ 'activos · ' ~ gen.enviados ~ ' enviados' if gen.activo else 'detenidos' }}</b></div></div>
{% if por_ruta %}<h3 style="font-size:15px">Por ruta</h3><table><tr><th>Método</th><th>Ruta</th><th>Requests</th></tr>{% for f in por_ruta %}<tr><td>{{ f[0] }}</td><td>{{ f[1] }}</td><td>{{ f[2] }}</td></tr>{% endfor %}</table>{% endif %}
<h3 style="font-size:15px">Últimos requests</h3><table><tr><th>Fecha (UTC)</th><th>Método</th><th>Ruta</th><th>Consulta</th><th>Cuerpo</th></tr>
{% for f in ultimos %}<tr><td>{{ f[0] }}</td><td>{{ f[1] }}</td><td>{{ f[2] }}</td><td><code>{{ f[3] }}</code></td><td><code>{{ (f[4] or '')[:200] }}</code></td></tr>{% else %}<tr><td colspan="5" class="s">Aún no llegan datos al lake (se escriben cada {{ cada }} s).</td></tr>{% endfor %}</table>
<h3 style="font-size:15px">Archivos en S3 · más nuevos arriba <span class="s">({{ narch }} archivos · {{ '%.2f' % (mb) }} MB)</span></h3>
<table><tr><th>Fecha (UTC)</th><th>Archivo</th><th>Tamaño</th></tr>{% for f in arch %}<tr><td>{{ f[0][:19].replace('T', ' ') }}</td>
<td><a href="{{ web }}{{ f[2] }}" target="_blank"><code>{{ f[2].split('/')[-1] }}</code></a></td><td>{{ '%.1f' % (f[1] / 1024) }} KB</td></tr>{% else %}<tr><td colspan="3" class="s">Sin archivos.</td></tr>{% endfor %}</table>
<p class="s">{{ error }}</p><script>setTimeout(()=>location.reload(),20000)</script></body></html>"""


@app.route("/datos/<pref>")
def datos(pref):
    if not RE_PREF.match(pref):
        return "", 404
    es, total, por_ruta, ultimos, error = esquema(pref), 0, [], [], ""
    try:
        asegurar_tabla(pref)
        total = trino(f"SELECT count(*) FROM lake.{es}.requests")[1][0][0]
        por_ruta = trino(f"SELECT metodo, ruta, count(*) n FROM lake.{es}.requests GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15")[1]
        ultimos = trino(f"SELECT ts, metodo, ruta, consulta, cuerpo FROM lake.{es}.requests ORDER BY ts DESC LIMIT 30")[1]
    except Exception as e:  # noqa: BLE001
        error = f"Trino: {str(e)[:200]}"
    try:
        arch = archivos(pref)
    except Exception:  # noqa: BLE001
        arch = []
    if request.args.get("formato") == "json":
        return jsonify({"segmento": pref, "en_lake": total, "recibidos": _cont.get(pref, 0), "por_ruta": por_ruta, "error": error})
    return render_template_string(VISTA, pref=pref, esquema=es, rec=_cont.get(pref, 0), total=total, ult=_ult.get(pref, ""), por_ruta=por_ruta,
                                  ultimos=ultimos, gen=_gen.get(pref) or {}, cada=CADA, error=error,
                                  arch=arch[:50], narch=len(arch), mb=sum(x[1] for x in arch) / 1048576, web=LAKE_WEB)
