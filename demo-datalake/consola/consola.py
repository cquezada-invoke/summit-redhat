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


@app.route("/", methods=["GET", "POST"])
def inicio():
    q = (request.form.get("q") or EJEMPLOS[0][1]).strip()
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
<body><h1>Data lake · consola SQL</h1><p class=sub>Trino sobre S3 · catálogo lake, esquema telco · clientes (Parquet), ventas (CSV), recargas (JSON) y consultas (lo que se pregunta en la aplicación de analítica, JSON)</p>
<div>{ej}</div><form id=f method=post><textarea id=q name=q>{html.escape(q)}</textarea><button class=run>Ejecutar</button></form>{res}<script>{js}</script></body></html>"""
