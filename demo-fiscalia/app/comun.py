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


def s3_get_json(clave):
    r = requests.get(f"{S3}/{BUCKET}/{PREF}/{clave}", timeout=30)
    r.raise_for_status()
    return r.json()


def s3_json(clave, obj):
    s3_put(clave, json.dumps(obj, ensure_ascii=False, default=str).encode())


def ahora():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=-6))).replace(tzinfo=None)   # hora de Ciudad de México


# ─────────── Enlace firmado para complementar una denuncia ───────────
import hashlib  # noqa: E402
import hmac  # noqa: E402

ENLACE_CLAVE = os.environ.get("ENLACE_CLAVE", "")
FORMULARIO_URL = os.environ.get("FORMULARIO_URL", "https://denuncias.sno.34.176.42.56.sslip.io").rstrip("/")


def firma(folio):
    return hmac.new(ENLACE_CLAVE.encode(), folio.encode(), hashlib.sha256).hexdigest()[:24]


def firma_ok(folio, t):
    return bool(ENLACE_CLAVE) and hmac.compare_digest(firma(folio), t or "")


def enlace_complemento(folio):
    return f"{FORMULARIO_URL}/complementar/{folio}?t={firma(folio)}"


def faltantes(n, relaciones=()):
    """Lo que le falta a una denuncia para estar completa (lo mismo que descuenta el puntaje de coherencia)."""
    f = []
    if not n.get("denunciante"):
        f.append("nombre de la persona denunciante")
    if not n.get("fecha_hechos"):
        f.append("fecha y hora de los hechos")
    if not n.get("colonia"):
        f.append("colonia o lugar exacto")
    if len(n.get("relato") or "") < 120:
        f.append("un relato más detallado de los hechos")
    rel = list(relaciones)
    if not rel:
        f.append("al menos una evidencia (foto, captura o documento)")
    elif "no coherente" in rel:
        f.append("una evidencia que corresponda a los hechos (alguna imagen no coincide con el relato)")
    return f


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
       h.video, h.testigos, h.identifica_responsable, h.carga_mp, h.estado, h.fecha_cierre, h.dias_resolucion, 'historico' AS origen
FROM lake.{PREF}.denuncias_historico h
UNION ALL
SELECT n.folio, CAST(n.fecha_denuncia AS timestamp(3)), n.alcaldia, n.delito, n.categoria, n.canal, n.unidad, n.mp, n.prioridad, n.hay_detenido,
       n.evidencias, n.video, n.testigos, n.identifica_responsable, n.carga_mp, COALESCE(u.estado, 'En trámite'),
       CASE WHEN COALESCE(u.estado, 'En trámite') <> 'En trámite' THEN CAST(u.fecha AS timestamp(3)) END,
       CASE WHEN COALESCE(u.estado, 'En trámite') <> 'En trámite' THEN date_diff('day', CAST(n.fecha_denuncia AS timestamp(3)), CAST(u.fecha AS timestamp(3))) END,
       'aplicacion' AS origen
FROM lake.{PREF}.denuncias_nuevas n LEFT JOIN ult u ON u.folio = n.folio"""


COLS_NUEVAS = """folio varchar, fecha_denuncia varchar, alcaldia varchar, delito varchar, categoria varchar, canal varchar, unidad varchar,
      mp varchar, prioridad varchar, hay_detenido boolean, evidencias integer, video boolean, testigos boolean, identifica_responsable boolean,
      carga_mp integer, relato varchar, resumen_ia varchar, delito_ia varchar, prioridad_ia varchar, segundos_ia double, denunciante varchar,
      calidad_denunciante varchar, fecha_hechos varchar, colonia varchar, n_adjuntos integer, adjuntos_coherentes integer,
      adjuntos_no_coherentes integer, intervenidos varchar"""


def asegurar_lake():
    """Tablas y vista de la fiscalía en Trino (idempotente; las definiciones viven en Trino y se recrean si se pierden o cambian)."""
    for k in ("denuncias_nuevas/.keep", "eventos/.keep", "adjuntos_analisis/.keep"):
        try:
            s3_put(k, b"")
        except requests.RequestException:
            pass
    trino(f"CREATE SCHEMA IF NOT EXISTS lake.{PREF} WITH (location = 's3://{BUCKET}/{PREF}/')")
    trino(f"""CREATE TABLE IF NOT EXISTS lake.{PREF}.denuncias_historico (folio varchar, fecha_denuncia timestamp(3), alcaldia varchar,
      delito varchar, categoria varchar, canal varchar, unidad varchar, mp varchar, prioridad varchar, hay_detenido boolean, evidencias integer,
      video boolean, testigos boolean, identifica_responsable boolean, carga_mp integer, estado varchar, fecha_cierre timestamp(3),
      dias_resolucion integer) WITH (format = 'PARQUET', external_location = 's3://{BUCKET}/{PREF}/denuncias_historico/')""")
    cols = set(trino(f"SELECT column_name FROM lake.information_schema.columns WHERE table_schema = '{PREF}' AND table_name = 'denuncias_nuevas'")
               .get("column_name", pd.Series(dtype=str)).tolist())
    if cols and "intervenidos" not in cols:   # cambió el formulario: se redefine (tabla externa: los archivos quedan)
        trino(f"DROP TABLE lake.{PREF}.denuncias_nuevas")
    trino(f"CREATE TABLE IF NOT EXISTS lake.{PREF}.denuncias_nuevas ({COLS_NUEVAS}) WITH (format = 'JSON', external_location = 's3://{BUCKET}/{PREF}/denuncias_nuevas/')")
    trino(f"""CREATE TABLE IF NOT EXISTS lake.{PREF}.eventos (fecha varchar, folio varchar, evento varchar, estado varchar, detalle varchar,
      usuario varchar) WITH (format = 'JSON', external_location = 's3://{BUCKET}/{PREF}/eventos/')""")
    trino(f"""CREATE TABLE IF NOT EXISTS lake.{PREF}.adjuntos_analisis (folio varchar, archivo varchar, ruta_s3 varchar, tipo varchar, descripcion varchar,
      elementos varchar, texto_visible varchar, relacion varchar, observacion varchar, segundos double)
      WITH (format = 'JSON', external_location = 's3://{BUCKET}/{PREF}/adjuntos_analisis/')""")
    trino(VISTA)


# ─────────── Granite (modelo de lenguaje local) ───────────
def chat(mensajes, max_tokens=300, formato=None):
    cuerpo = {"model": MODELO, "messages": mensajes, "stream": False, "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 4096}}
    if formato:
        cuerpo["format"] = formato
    r = requests.post(f"{OLLAMA}/api/chat", json=cuerpo, timeout=240)
    r.raise_for_status()
    return r.json()["message"]["content"]


VISION = os.environ.get("MODELO_VISION", "granite3.2-vision:2b")


def ver_imagen(b64, pregunta, max_tokens=260):
    """Granite Vision (local) describe una imagen. Se descarga de memoria al poco rato para no competir con el modelo de texto."""
    r = requests.post(f"{OLLAMA}/api/chat", json={"model": VISION, "stream": False, "keep_alive": "60s", "format": "json",
                                                  "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 4096},
                                                  "messages": [{"role": "user", "content": pregunta, "images": [b64]}]}, timeout=300)
    r.raise_for_status()
    return r.json()["message"]["content"]


def vision_lista():
    try:
        return any(m["name"].startswith(VISION.split(":")[0]) for m in requests.get(f"{OLLAMA}/api/tags", timeout=5).json().get("models") or [])
    except requests.RequestException:
        return False


def descargar_vision():
    if not vision_lista():
        try:
            requests.post(f"{OLLAMA}/api/pull", json={"model": VISION, "stream": False}, timeout=3600)
        except requests.RequestException:
            pass


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
