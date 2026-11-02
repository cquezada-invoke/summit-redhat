"""Preguntas en español → SQL (DuckDB) con un modelo de lenguaje local servido por Ollama dentro del clúster."""
import json
import os
import re
import time

import requests

OLLAMA = os.environ.get("OLLAMA_URL", "http://ollama:11434")
MODELO = os.environ.get("MODELO", "granite3.3:2b")

ESQUEMA = """Tablas (DuckDB):
clientes(cliente_id INT, region TEXT, comuna TEXT, segmento TEXT -- 'Prepago' o 'Postpago', plan TEXT -- 'Prepago Full','Prepago Plus','Plan 20 GB','Plan 60 GB','Plan Libre','Plan Empresa',
  cargo_mensual DOUBLE -- CLP, antiguedad_meses INT, edad INT, datos_gb_mes DOUBLE, minutos_mes INT, reclamos_90d INT, caidas_red_30d INT,
  consultas_portabilidad INT, gama_equipo TEXT -- 'Alta','Media','Baja', equipo_en_cuotas BOOLEAN, canal_alta TEXT, fecha_alta DATE,
  fuga BOOLEAN -- true si el cliente se fue, fecha_baja DATE, riesgo_fuga DOUBLE -- probabilidad 0..1 del modelo predictivo (solo clientes activos, fuga=false))
ventas(venta_id INT, fecha DATE, region TEXT, canal TEXT -- 'Tienda','Web','App','Distribuidor','Televenta', tipo TEXT -- 'Alta postpago','Alta prepago','Portabilidad entrante','Renovación de equipo','Accesorio', monto INT -- CLP, cliente_id INT)
recargas(recarga_id INT, fecha DATE, cliente_id INT, region TEXT, canal TEXT -- 'App','Web','Supermercado','Kiosco','Banco', monto INT -- CLP)
Regiones: Metropolitana, Valparaíso, Biobío, Maule, La Araucanía, O'Higgins, Antofagasta, Coquimbo, Los Lagos, Ñuble, Tarapacá, Atacama, Los Ríos, Magallanes, Arica y Parinacota.
Los datos van de noviembre 2025 a octubre 2026."""

EJEMPLOS = [
    ("¿Cuál es la tasa de fuga por segmento?",
     "SELECT segmento, ROUND(100.0 * AVG(CASE WHEN fuga THEN 1 ELSE 0 END), 1) AS tasa_fuga_pct, COUNT(*) AS clientes FROM clientes GROUP BY segmento ORDER BY tasa_fuga_pct DESC"),
    ("Ventas totales por mes",
     "SELECT date_trunc('month', fecha) AS mes, SUM(monto) AS ventas_clp FROM ventas GROUP BY mes ORDER BY mes"),
    ("Top 10 clientes activos con mayor riesgo de fuga en Biobío",
     "SELECT cliente_id, comuna, plan, reclamos_90d, ROUND(100 * riesgo_fuga, 1) AS riesgo_pct FROM clientes WHERE NOT fuga AND region = 'Biobío' ORDER BY riesgo_fuga DESC LIMIT 10"),
    ("Monto de recargas por canal",
     "SELECT canal, SUM(monto) AS recargas_clp, COUNT(*) AS recargas FROM recargas GROUP BY canal ORDER BY recargas_clp DESC"),
]


def _chat(mensajes, max_tokens=300):
    r = requests.post(f"{OLLAMA}/api/chat", json={"model": MODELO, "messages": mensajes, "stream": False,
                                                   "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 4096}}, timeout=240)
    r.raise_for_status()
    return r.json()["message"]["content"]


def _sql_de(texto):
    m = re.search(r"```(?:sql)?\s*(.*?)```", texto, re.S | re.I)
    s = (m.group(1) if m else texto).strip()
    m = re.search(r"\b(WITH|SELECT)\b.*", s, re.S | re.I)
    s = (m.group(0) if m else s).strip().rstrip(";").split(";")[0]
    return s


def seguro(sql):
    return bool(re.match(r"^\s*(WITH|SELECT)\b", sql, re.I)) and not re.search(
        r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|COPY|PRAGMA|INSTALL|LOAD|EXPORT|IMPORT|CALL|SET)\b|read_csv|read_parquet|read_json|glob\(", sql, re.I)


def preguntar(con, pregunta):
    t0 = time.time()
    sistema = ("Eres un analista de datos de una empresa de telecomunicaciones en Chile. Conviertes preguntas en español a UNA consulta SQL "
               "de DuckDB, solo de lectura (SELECT), usando exclusivamente estas tablas y columnas. Usa alias de columnas en español, "
               "redondea porcentajes a 1 decimal, limita listados a 20 filas. Responde solo con el SQL, sin explicación.\n\n" + ESQUEMA)
    msgs = [{"role": "system", "content": sistema}]
    for p, s in EJEMPLOS:
        msgs += [{"role": "user", "content": p}, {"role": "assistant", "content": s}]
    msgs.append({"role": "user", "content": pregunta})
    intentos, error, sql, df = [], None, "", None
    for _ in range(3):
        sql = _sql_de(_chat(msgs))
        if not seguro(sql):
            error = "La consulta generada no es de solo lectura"
        else:
            try:
                df = con.execute(f"SELECT * FROM ({sql}) AS q LIMIT 200").fetchdf()
                error = None
                break
            except Exception as x:  # noqa: BLE001
                error = str(x).split("\n")[0][:300]
        intentos.append({"sql": sql, "error": error})
        msgs += [{"role": "assistant", "content": sql},
                 {"role": "user", "content": f"Esa consulta falla en DuckDB: {error}. Corrígela y responde solo con el SQL."}]
    t_sql = time.time() - t0
    if df is None:
        return {"ok": False, "sql": sql, "error": error, "intentos": intentos, "seg": round(t_sql, 1)}
    resumen = ""
    try:
        muestra = df.head(15).to_csv(index=False)
        resumen = _chat([{"role": "system", "content": "Eres un analista de datos. Respondes en español de Chile, en 2 a 3 frases, con las cifras clave "
                                                        "y una recomendación de negocio. No inventes datos que no estén en la tabla."},
                         {"role": "user", "content": f"Pregunta: {pregunta}\nResultado (CSV):\n{muestra}"}], max_tokens=160).strip()
    except Exception as x:  # noqa: BLE001
        resumen = f"(sin resumen: {x})"
    return {"ok": True, "sql": sql, "columnas": list(df.columns), "filas": json.loads(df.to_json(orient="values", date_format="iso")),
            "resumen": resumen, "intentos": intentos, "seg_sql": round(t_sql, 1), "seg": round(time.time() - t0, 1)}


def estado():
    try:
        tags = requests.get(f"{OLLAMA}/api/tags", timeout=5).json().get("models") or []
        return {"servidor": True, "modelo": MODELO, "listo": any(m["name"].split(":")[0] == MODELO.split(":")[0] and
                                                                  (m["name"] == MODELO or ":" not in MODELO) for m in tags)}
    except Exception:  # noqa: BLE001
        return {"servidor": False, "modelo": MODELO, "listo": False}


def descargar():
    """Descarga el modelo en Ollama si no está (la primera vez, ~1,5 GB)."""
    try:
        requests.post(f"{OLLAMA}/api/pull", json={"model": MODELO, "stream": False}, timeout=3600)
    except Exception:  # noqa: BLE001
        pass
