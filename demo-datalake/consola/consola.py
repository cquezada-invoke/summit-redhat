"""Consola SQL del data lake (solo lectura): escribe una consulta, Trino la ejecuta sobre S3 y se ve la tabla."""
import html
import re
import time

import requests
from flask import Flask, request

app = Flask(__name__)
TRINO = "http://trino:8080/v1/statement"
EJEMPLOS = [
    ("Lo que se ha preguntado en la aplicación", "SELECT date_add('hour', -3, CAST(fecha AS timestamp)) AS hora_chile, tipo, pregunta, segundos, ok, cache, filas\nFROM lake.telco.consultas ORDER BY fecha DESC LIMIT 50"),
    ("Preguntas más frecuentes", "SELECT pregunta, COUNT(*) AS veces, ROUND(AVG(segundos), 1) AS seg_promedio\nFROM lake.telco.consultas WHERE tipo = 'pregunta' GROUP BY pregunta ORDER BY veces DESC"),
    ("Tasa de fuga por región (Parquet)", "SELECT region, ROUND(100.0 * AVG(CASE WHEN fuga THEN 1 ELSE 0 END), 1) AS tasa_fuga_pct, COUNT(*) AS clientes\nFROM lake.telco.clientes GROUP BY region ORDER BY tasa_fuga_pct DESC"),
    ("Ventas por canal (CSV)", "SELECT canal, SUM(CAST(monto AS bigint)) AS ventas_clp FROM lake.telco.ventas GROUP BY canal ORDER BY ventas_clp DESC"),
    ("Recargas por región: JSON cruzado con Parquet", "SELECT c.region, COUNT(*) AS recargas, SUM(r.monto) AS monto_clp\nFROM lake.telco.recargas r JOIN lake.telco.clientes c ON c.cliente_id = r.cliente_id\nGROUP BY c.region ORDER BY monto_clp DESC"),
    ("Tablas del lake", "SHOW TABLES FROM lake.telco"),
    ("Fiscalía · denuncias del formulario", "SELECT folio, fecha_denuncia, delito, delito_ia, alcaldia, prioridad, n_adjuntos, adjuntos_coherentes, intervenidos\nFROM lake.fiscalia.denuncias_nuevas ORDER BY fecha_denuncia DESC"),
    ("Fiscalía · análisis de imágenes", "SELECT folio, archivo, relacion, observacion, texto_visible, ruta_s3\nFROM lake.fiscalia.adjuntos_analisis ORDER BY folio DESC"),
    ("Fiscalía · eventos (cómo transita cada denuncia)", "SELECT fecha, folio, evento, estado, detalle, usuario\nFROM lake.fiscalia.eventos ORDER BY fecha DESC LIMIT 100"),
    ("Fiscalía · histórico + nuevas por estado", "SELECT origen, estado, COUNT(*) AS carpetas\nFROM lake.fiscalia.denuncias GROUP BY origen, estado ORDER BY origen, carpetas DESC"),
    ("Fiscalía · continuidad por tipo de denuncia", "SELECT delito, ROUND(100.0 * AVG(CASE WHEN estado IN ('Judicializada', 'Acuerdo reparatorio') THEN 1 ELSE 0 END), 1) AS continuidad_pct,\n       COUNT(*) AS cerradas\nFROM lake.fiscalia.denuncias WHERE estado <> 'En trámite' GROUP BY delito ORDER BY continuidad_pct DESC"),
]


def ejecutar(q):
    h = {"X-Trino-User": "consola", "X-Trino-Catalog": "lake", "X-Trino-Schema": "telco"}
    r = requests.post(TRINO, data=q.encode(), headers=h, timeout=30).json()
    cols, filas = None, []
    while True:
        if r.get("columns") and cols is None:
            cols = [c["name"] for c in r["columns"]]
        filas += r.get("data") or []
        if r.get("error"):
            raise ValueError(r["error"]["message"])
        if "nextUri" not in r or len(filas) > 1000:
            break
        time.sleep(0.1)
        r = requests.get(r["nextUri"], headers=h, timeout=60).json()
    return cols or [], filas[:1000]


LAKE_WEB = "https://lake.sno.34.176.42.56.sslip.io/buckets/lake/"
CSS = """body{font-family:system-ui,sans-serif;margin:24px;color:#17243a;background:#f6f7f4}h1{margin:0 0 4px}h2{font-size:17px;margin:22px 0 8px}
.sub{color:#5b6675;font-size:14px}a{color:#1f5fa8}nav a{margin-right:16px;font-weight:600}.tab{overflow:auto;max-height:60vh;background:#fff;border:1px solid #dfe3e8;border-radius:8px}
table{border-collapse:collapse;font-size:13px;width:100%}th,td{padding:6px 10px;border-bottom:1px solid #eef0f2;text-align:left;white-space:nowrap}th{background:#eef1f5;position:sticky;top:0}
.err{color:#b1291f}.capas{display:flex;gap:10px;align-items:stretch;flex-wrap:wrap;margin:14px 0}.capa{background:#fff;border:1px solid #dfe3e8;border-radius:10px;padding:10px 14px;flex:1;min-width:200px}
.capa b{display:block}.flecha{align-self:center;font-size:22px;color:#8a95a5}.chip{display:inline-block;padding:2px 9px;border-radius:10px;font-size:12px;background:#eef0f4;margin-right:4px}
.PARQUET{background:#e3f1e8}.JSON{background:#fff1d6}.CSV{background:#e6eefb}.VISTA{background:#f1e6fb}"""
_cache = {}


def _cuenta(esq, tab):
    k = (esq, tab)
    if k not in _cache or time.time() - _cache[k][0] > 30:
        try:
            _cache[k] = (time.time(), ejecutar(f'SELECT COUNT(*) FROM lake."{esq}"."{tab}"')[1][0][0])
        except Exception:  # noqa: BLE001
            _cache[k] = (time.time(), None)
    return _cache[k][1]


def _ficha(esq, tab):
    try:
        ddl = ejecutar(f'SHOW CREATE TABLE lake."{esq}"."{tab}"')[1][0][0]
    except Exception:  # noqa: BLE001
        return "VISTA", None
    fmt = re.search(r"format = '(\w+)'", ddl)
    loc = re.search(r"external_location = '(s3://[^']+)'", ddl)
    return (fmt.group(1) if fmt else "?"), (loc.group(1) if loc else None)


def _pagina(titulo, cuerpo):
    return f"""<!DOCTYPE html><html lang=es><head><meta charset=utf-8><title>{html.escape(titulo)}</title><meta name=viewport content="width=device-width,initial-scale=1">
<style>{CSS}</style></head><body><nav><a href=/lake>Explorador del lake</a><a href=/>Consola SQL</a></nav>{cuerpo}</body></html>"""


@app.route("/salud")
def salud():
    """Para la sonda de OpenShift: no consulta Trino (una consulta lenta no debe sacar la consola de servicio)."""
    return "ok"


@app.route("/lake")
def explorador():
    try:
        _, filas = ejecutar("SELECT table_schema, table_name, table_type FROM lake.information_schema.tables "
                            "WHERE table_schema NOT IN ('information_schema') ORDER BY 1, 2")
    except Exception as e:  # noqa: BLE001
        return _pagina("Data lake", f"<p class=err>{html.escape(str(e))[:400]}</p>")
    cuerpo = """<h1>Data lake · explorador</h1><p class=sub>Lo que Trino ve en el lake: cada tabla es una carpeta de archivos en S3. Trino no copia los datos; los lee donde están, en su formato original.</p>
<div class=capas><div class=capa><b>1 · Almacenamiento S3</b><span class=sub>bucket <code>lake</code>: archivos Parquet, CSV y JSON tal como llegan</span></div><div class=flecha>→</div>
<div class=capa><b>2 · Catálogo (metastore)</b><span class=sub>dice qué carpeta es qué tabla, su formato y sus columnas</span></div><div class=flecha>→</div>
<div class=capa><b>3 · Motor SQL Trino</b><span class=sub>consulta y cruza todo con SQL estándar (REST o JDBC)</span></div><div class=flecha>→</div>
<div class=capa><b>4 · Aplicaciones</b><span class=sub>analítica, IA, tableros y esta consola</span></div></div>"""
    esqs = {}
    for esq, tab, tipo in filas:
        esqs.setdefault(esq, []).append((tab, tipo))
    for esq, tabs in esqs.items():
        cuerpo += f"<h2>Esquema lake.{html.escape(esq)} <a class=sub href='{LAKE_WEB}{esq}/' target=_blank>ver carpeta en S3 ↗</a></h2><div class=tab><table><tr><th>Tabla</th><th>Formato</th><th>Filas</th><th>Ubicación en S3</th><th></th></tr>"
        for tab, tipo in tabs:
            fmt, loc = ("VISTA", None) if tipo == "VIEW" else _ficha(esq, tab)
            n = _cuenta(esq, tab)
            ubic = (f"<a href='{LAKE_WEB}{html.escape(loc[len('s3://lake/'):])}' target=_blank>{html.escape(loc)}</a>" if loc
                    else "<span class=sub>vista: une tablas del lake</span>")
            cuerpo += (f"<tr><td><b>{html.escape(tab)}</b></td><td><span class='chip {fmt}'>{fmt}</span></td><td>{'' if n is None else f'{n:,}'.replace(',', '.')}</td>"
                       f"<td>{ubic}</td><td><a href='/lake/{esq}/{tab}'>ver columnas y datos →</a></td></tr>")
        cuerpo += "</table></div>"
    return _pagina("Data lake · explorador", cuerpo)


@app.route("/lake/<esq>/<tab>")
def tabla(esq, tab):
    if not re.fullmatch(r"\w+", esq) or not re.fullmatch(r"\w+", tab):
        return _pagina("Data lake", "<p class=err>Nombre inválido</p>")
    try:
        _, cols = ejecutar(f'DESCRIBE lake."{esq}"."{tab}"')
        cn, filas = ejecutar(f'SELECT * FROM lake."{esq}"."{tab}" LIMIT 50')
    except Exception as e:  # noqa: BLE001
        return _pagina("Data lake", f"<p class=err>{html.escape(str(e))[:400]}</p>")
    fmt, loc = _ficha(esq, tab)
    n = _cuenta(esq, tab)
    def celda(v):
        t = "" if v is None else str(v)
        return html.escape(t if len(t) < 120 else t[:117] + "…")
    cuerpo = (f"<h1>lake.{esq}.{tab}</h1><p class=sub><span class='chip {fmt}'>{fmt}</span> {'' if n is None else f'{n:,}'.replace(',', '.') + ' filas'}"
              + (f" · archivos en <a href='{LAKE_WEB}{html.escape(loc[len('s3://lake/'):])}' target=_blank>{html.escape(loc)} ↗</a>" if loc else "")
              + f" · <a href='/?q=SELECT+*+FROM+lake.{esq}.{tab}+LIMIT+100'>abrir en la consola SQL</a></p>"
              + "<h2>Columnas</h2><div class=tab style='max-height:30vh'><table><tr><th>Columna</th><th>Tipo</th></tr>"
              + "".join(f"<tr><td>{html.escape(c[0])}</td><td>{html.escape(c[1])}</td></tr>" for c in cols) + "</table></div>"
              + "<h2>Primeras 50 filas</h2><div class=tab><table><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in cn) + "</tr>"
              + "".join("<tr>" + "".join(f"<td>{celda(v)}</td>" for v in f) + "</tr>" for f in filas) + "</table></div>")
    return _pagina(f"lake.{esq}.{tab}", cuerpo)


@app.route("/", methods=["GET", "POST"])
def inicio():
    q = (request.form.get("q") or request.args.get("q") or EJEMPLOS[0][1]).strip()
    res = ""
    if not re.match(r"^\s*(SELECT|WITH|SHOW|DESCRIBE)\b", q, re.I) or re.search(r";\s*\S", q):
        res = "<p class=err>Solo consultas de lectura (SELECT, WITH, SHOW, DESCRIBE), una a la vez.</p>"
    else:
        t0 = time.time()
        try:
            cols, filas = ejecutar(q.rstrip(";"))
            res = (f"<p class=sub>{len(filas)} filas · {time.time() - t0:.1f} s</p><div class=tab><table><tr>"
                   + "".join(f"<th>{html.escape(str(c))}</th>" for c in cols) + "</tr>"
                   + "".join("<tr>" + "".join(f"<td>{html.escape('' if v is None else str(v))}</td>" for v in f) + "</tr>" for f in filas)
                   + "</table></div>")
        except Exception as e:  # noqa: BLE001
            res = f"<p class=err>{html.escape(str(e))[:500]}</p>"
    ej = "".join(f"<button type=button onclick='usar({i})'>{html.escape(n)}</button>" for i, (n, _) in enumerate(EJEMPLOS))
    js = "const E=" + repr([s for _, s in EJEMPLOS]).replace("</", "<\\/") + ";function usar(i){document.getElementById('q').value=E[i];document.getElementById('f').submit();}"
    return f"""<!DOCTYPE html><html lang=es><head><meta charset=utf-8><title>Data lake · consola SQL</title><meta name=viewport content="width=device-width,initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#17243a;background:#f6f7f4}}h1{{margin:0 0 4px}}.sub{{color:#5b6675;font-size:14px}}
textarea{{width:100%;height:120px;font-family:monospace;font-size:14px;padding:10px;border:1px solid #c9d0d8;border-radius:8px;box-sizing:border-box}}
button{{margin:4px 6px 4px 0;padding:7px 12px;border:1px solid #c9d0d8;border-radius:16px;background:#fff;cursor:pointer;font-size:13px}}
.run{{background:#17243a;color:#fff;border-color:#17243a}}.tab{{overflow:auto;max-height:65vh;background:#fff;border:1px solid #dfe3e8;border-radius:8px}}
table{{border-collapse:collapse;font-size:13px;width:100%}}th,td{{padding:6px 10px;border-bottom:1px solid #eef0f2;text-align:left;white-space:nowrap}}th{{background:#eef1f5;position:sticky;top:0}}.err{{color:#b1291f}}</style></head>
<body><nav style="margin-bottom:10px"><a href=/lake style="margin-right:16px;font-weight:600;color:#1f5fa8">Explorador del lake</a><b>Consola SQL</b></nav><h1>Data lake · consola SQL</h1><p class=sub>Trino sobre S3 · lake.telco: clientes (Parquet), ventas (CSV), recargas (JSON), consultas de la app de analítica (JSON) · lake.fiscalia: histórico (Parquet), denuncias del formulario, análisis de imágenes y eventos (JSON)</p>
<div>{ej}</div><form id=f method=post><textarea id=q name=q>{html.escape(q)}</textarea><button class=run>Ejecutar</button></form>{res}<script>{js}</script></body></html>"""
