"""Preguntas en español → SQL (DuckDB) con un modelo de lenguaje local servido por Ollama dentro del clúster."""
import difflib
import json
import os
import re
import threading
import time
import unicodedata

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


# Preguntas sugeridas en pantalla: SQL ya validado (no depende del modelo) y respuesta precalculada al iniciar.
CONOCIDAS = {
    "¿Cuál es la tasa de fuga por región?":
        "SELECT region, ROUND(100.0 * AVG(CASE WHEN fuga THEN 1 ELSE 0 END), 1) AS tasa_fuga_pct, COUNT(*) AS clientes FROM clientes GROUP BY region ORDER BY tasa_fuga_pct DESC",
    "Clientes activos de la Metropolitana con mayor riesgo de fuga":
        "SELECT cliente_id, comuna, plan, reclamos_90d, consultas_portabilidad, ROUND(100 * riesgo_fuga, 1) AS riesgo_pct FROM clientes WHERE NOT fuga AND region = 'Metropolitana' ORDER BY riesgo_fuga DESC LIMIT 20",
    "¿Qué plan tiene más fuga entre clientes con más de 2 reclamos?":
        "SELECT plan, ROUND(100.0 * AVG(CASE WHEN fuga THEN 1 ELSE 0 END), 1) AS tasa_fuga_pct, COUNT(*) AS clientes FROM clientes WHERE reclamos_90d > 2 GROUP BY plan ORDER BY tasa_fuga_pct DESC",
    "Ventas por canal en diciembre 2025":
        "SELECT canal, SUM(monto) AS ventas_clp, COUNT(*) AS ventas FROM ventas WHERE fecha >= DATE '2025-12-01' AND fecha < DATE '2026-01-01' GROUP BY canal ORDER BY ventas_clp DESC",
    "¿Cuántas portabilidades entrantes hubo por mes?":
        "SELECT date_trunc('month', fecha) AS mes, COUNT(*) AS portabilidades FROM ventas WHERE tipo = 'Portabilidad entrante' GROUP BY mes ORDER BY mes",
    "Ticket promedio de renovación de equipo por región":
        "SELECT region, ROUND(AVG(monto)) AS ticket_promedio_clp, COUNT(*) AS renovaciones FROM ventas WHERE tipo = 'Renovación de equipo' GROUP BY region ORDER BY ticket_promedio_clp DESC",
    "Evolución mensual de recargas por canal":
        "SELECT date_trunc('month', fecha) AS mes, canal, SUM(monto) AS recargas_clp FROM recargas GROUP BY mes, canal ORDER BY mes, canal",
}
CACHE = {}


def _norm(t):
    t = unicodedata.normalize("NFKD", t.lower())
    return re.sub(r"[^a-z0-9 ]", "", "".join(c for c in t if not unicodedata.combining(c))).strip()


_SQL_CONOCIDO = {_norm(k): v for k, v in CONOCIDAS.items()}


def _columnas(con):
    return [r[0] for r in con.execute("SELECT DISTINCT column_name FROM information_schema.columns").fetchall()]


def _corregir_columna(con, sql, error):
    """El modelo pequeño a veces inventa un nombre de columna (reclamaciones_90d): se cambia por la columna real más parecida."""
    m = re.search(r'Referenced column "([^"]+)" not found', error or "")
    if not m:
        return None
    c = difflib.get_close_matches(m.group(1), _columnas(con), 1, 0.5)
    return re.sub(r"\b" + re.escape(m.group(1)) + r"\b", c[0], sql) if c else None


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
    k = _norm(pregunta)
    if k in CACHE:
        return dict(CACHE[k], seg=round(time.time() - t0, 1), cache=True)
    intentos, error, sql, df = [], None, "", None
    for i in range(3):
        sql = _SQL_CONOCIDO[k] if (i == 0 and k in _SQL_CONOCIDO) else _sql_de(_chat(msgs))
        if not seguro(sql):
            error = "La consulta generada no es de solo lectura"
        else:
            for _c in range(3):   # nombres de columna inventados: se corrigen sin volver al modelo
                try:
                    df = con.execute(f"SELECT * FROM ({sql}) AS q LIMIT 200").fetchdf()
                    error = None
                    break
                except Exception as x:  # noqa: BLE001
                    error = str(x).split("\n")[0][:300]
                    sql2 = _corregir_columna(con, sql, error)
                    if not sql2 or sql2 == sql:
                        break
                    intentos.append({"sql": sql, "error": error, "corregido": True})
                    sql = sql2
            if df is not None:
                break
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
                         {"role": "user", "content": f"Pregunta: {pregunta}\nResultado (CSV):\n{muestra}"}], max_tokens=130).strip()
    except Exception as x:  # noqa: BLE001
        resumen = f"(sin resumen: {x})"
    r = {"ok": True, "sql": sql, "columnas": list(df.columns), "filas": json.loads(df.to_json(orient="values", date_format="iso")),
         "resumen": resumen, "intentos": intentos, "seg_sql": round(t_sql, 1), "seg": round(time.time() - t0, 1)}
    if not resumen.startswith("(sin resumen"):
        CACHE[k] = r
    return r


def precalentar(con, lock):
    """Deja respondidas de antemano las preguntas sugeridas (de a una, soltando el turno entre una y otra)."""
    for _ in range(120):
        if estado().get("listo"):
            break
        time.sleep(5)
    for p in CONOCIDAS:
        if _norm(p) in CACHE:
            continue
        with lock:
            try:
                preguntar(con, p)
            except Exception:  # noqa: BLE001
                pass
        time.sleep(1)


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
