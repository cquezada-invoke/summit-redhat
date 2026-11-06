"""Piezas comunes de las dos aplicaciones: el data lake (S3 + Trino), el modelo de lenguaje (Granite en Ollama) y el estilo."""
import datetime as dt
import io
import json
import os
import re
import time

import pandas as pd
import requests

S3 = os.environ.get("S3_URL", "http://minio.demo-datalake.svc:9000").rstrip("/")
TRINO = os.environ.get("TRINO_URL", "http://trino.demo-datalake.svc:8080").rstrip("/")
OLLAMA = os.environ.get("OLLAMA_URL", "http://ollama.demo-ia-analitica.svc:11434").rstrip("/")
MODELO = os.environ.get("MODELO", "granite3.3:2b")
BUCKET, PREF = "lake", "fiscalia"


# ─────────── S3 (el almacenamiento del lake) ───────────
def s3_put(clave, datos, tipo="application/json"):
    r = requests.put(f"{S3}/{BUCKET}/{PREF}/{clave}", data=datos, headers={"Content-Type": tipo}, timeout=30)
    r.raise_for_status()


def s3_json(clave, obj):
    s3_put(clave, json.dumps(obj, ensure_ascii=False, default=str).encode())


def ahora():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=-6))).replace(tzinfo=None)   # hora de Ciudad de México


# ─────────── Trino (SQL sobre el lake) ───────────
def trino(q, usuario="fiscalia"):
    h = {"X-Trino-User": usuario, "X-Trino-Catalog": "lake", "X-Trino-Schema": PREF}
    r = requests.post(f"{TRINO}/v1/statement", data=q.encode(), headers=h, timeout=30).json()
    cols, filas = None, []
    while True:
        if r.get("columns") and cols is None:
            cols = [c["name"] for c in r["columns"]]
        filas += r.get("data") or []
        if r.get("error"):
            raise ValueError(r["error"]["message"])
        if "nextUri" not in r:
            break
        r = requests.get(r["nextUri"], headers=h, timeout=120).json()
    return pd.DataFrame(filas, columns=cols or [])


VISTA = f"""CREATE OR REPLACE VIEW lake.{PREF}.denuncias AS
WITH ult AS (SELECT folio, max_by(estado, fecha) AS estado, max(fecha) AS fecha
             FROM lake.{PREF}.eventos WHERE estado <> '' GROUP BY folio)
SELECT h.folio, h.fecha_denuncia, h.alcaldia, h.delito, h.categoria, h.canal, h.unidad, h.mp, h.prioridad, h.hay_detenido, h.evidencias,
       h.video, h.testigos, h.identifica_responsable, h.carga_mp, h.estado, h.fecha_cierre, h.dias_resolucion, 'histórico' AS origen
FROM lake.{PREF}.denuncias_historico h
UNION ALL
SELECT n.folio, CAST(n.fecha_denuncia AS timestamp(3)), n.alcaldia, n.delito, n.categoria, n.canal, n.unidad, n.mp, n.prioridad, n.hay_detenido,
       n.evidencias, n.video, n.testigos, n.identifica_responsable, n.carga_mp, COALESCE(u.estado, 'En trámite'),
       CASE WHEN COALESCE(u.estado, 'En trámite') <> 'En trámite' THEN CAST(u.fecha AS timestamp(3)) END,
       CASE WHEN COALESCE(u.estado, 'En trámite') <> 'En trámite' THEN date_diff('day', CAST(n.fecha_denuncia AS timestamp(3)), CAST(u.fecha AS timestamp(3))) END,
       'aplicación' AS origen
FROM lake.{PREF}.denuncias_nuevas n LEFT JOIN ult u ON u.folio = n.folio"""


def asegurar_lake():
    """Tablas y vista de la fiscalía en Trino (idempotente; las definiciones viven en Trino y se recrean si se pierden)."""
    for k in ("denuncias_nuevas/.keep", "eventos/.keep"):
        try:
            s3_put(k, b"")
        except requests.RequestException:
            pass
    trino(f"CREATE SCHEMA IF NOT EXISTS lake.{PREF} WITH (location = 's3://{BUCKET}/{PREF}/')")
    trino(f"""CREATE TABLE IF NOT EXISTS lake.{PREF}.denuncias_historico (folio varchar, fecha_denuncia timestamp(3), alcaldia varchar,
      delito varchar, categoria varchar, canal varchar, unidad varchar, mp varchar, prioridad varchar, hay_detenido boolean, evidencias integer,
      video boolean, testigos boolean, identifica_responsable boolean, carga_mp integer, estado varchar, fecha_cierre timestamp(3),
      dias_resolucion integer) WITH (format = 'PARQUET', external_location = 's3://{BUCKET}/{PREF}/denuncias_historico/')""")
    trino(f"""CREATE TABLE IF NOT EXISTS lake.{PREF}.denuncias_nuevas (folio varchar, fecha_denuncia varchar, alcaldia varchar, delito varchar,
      categoria varchar, canal varchar, unidad varchar, mp varchar, prioridad varchar, hay_detenido boolean, evidencias integer, video boolean,
      testigos boolean, identifica_responsable boolean, carga_mp integer, relato varchar, resumen_ia varchar, delito_ia varchar,
      prioridad_ia varchar, segundos_ia double) WITH (format = 'JSON', external_location = 's3://{BUCKET}/{PREF}/denuncias_nuevas/')""")
    trino(f"""CREATE TABLE IF NOT EXISTS lake.{PREF}.eventos (fecha varchar, folio varchar, evento varchar, estado varchar, detalle varchar,
      usuario varchar) WITH (format = 'JSON', external_location = 's3://{BUCKET}/{PREF}/eventos/')""")
    trino(VISTA)


# ─────────── Granite (modelo de lenguaje local) ───────────
def chat(mensajes, max_tokens=300, formato=None):
    cuerpo = {"model": MODELO, "messages": mensajes, "stream": False, "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 4096}}
    if formato:
        cuerpo["format"] = formato
    r = requests.post(f"{OLLAMA}/api/chat", json=cuerpo, timeout=240)
    r.raise_for_status()
    return r.json()["message"]["content"]


def modelo_listo():
    try:
        return any(m["name"].split(":")[0] == MODELO.split(":")[0] for m in requests.get(f"{OLLAMA}/api/tags", timeout=5).json().get("models") or [])
    except requests.RequestException:
        return False


def solo_lectura(sql):
    return bool(re.match(r"^\s*(WITH|SELECT)\b", sql, re.I)) and not re.search(
        r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|GRANT|REVOKE|CALL|SET|RESET|PREPARE|EXECUTE|COMMENT|TRUNCATE|MERGE)\b", sql, re.I)


ESTILO = """<style>
:root{--osc:#1d2a44;--acc:#8a1538;--suave:#5b6675;--fondo:#f5f4f1;--linea:#dfe1e5;--bien:#2e7d4f;--mal:#b3261e}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:var(--fondo);color:var(--osc)}
header{background:var(--osc);color:#fff;padding:14px 28px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}header b{font-size:19px}
header .sub{color:#c9d1de;font-size:13px}header a{color:#f4c7d2;font-size:14px;margin-left:auto}main{padding:22px 28px;max-width:1500px;margin:auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:18px}
.kpi{background:#fff;border:1px solid var(--linea);border-radius:12px;padding:14px 16px}.kpi b{display:block;font-size:28px}.kpi span{color:var(--suave);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px}.card{background:#fff;border:1px solid var(--linea);border-radius:12px;padding:16px 18px}
.card h2{font-size:16px;margin:0 0 10px}.barra{display:grid;grid-template-columns:190px 1fr 70px;gap:8px;align-items:center;font-size:13px;margin:5px 0}
.barra i{display:block;height:12px;background:var(--acc);border-radius:6px}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #eef0f2;vertical-align:top}th{background:#f1f2f5;position:sticky;top:0}
.tab{max-height:420px;overflow:auto}textarea,input,select{font:inherit;border:1px solid #c9ced6;border-radius:8px;padding:8px;width:100%}
button{font:inherit;background:var(--acc);color:#fff;border:0;border-radius:8px;padding:9px 16px;cursor:pointer}button.sec{background:#fff;color:var(--osc);border:1px solid #c9ced6}
.chip{display:inline-block;padding:2px 9px;border-radius:10px;font-size:12px;background:#eef0f4}.Alta{background:#fbe3e6;color:var(--mal)}.Media{background:#fff1d6;color:#8a5a00}
.Baja{background:#e3f1e8;color:var(--bien)}.nota{color:var(--suave);font-size:12.5px}.fila{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
pre{white-space:pre-wrap;background:#f6f7f9;border-radius:8px;padding:10px;font-size:12.5px}
</style>"""
