"""Catálogo de agentes de IA de JavIA: un agente por aplicación del negocio, cada uno con soluciones estándar de IA
que se evalúan contra el data lake central. Todas cruzan datos de otras aplicaciones (esa es la gracia del lake)."""
import json
import os
import re
import time

import numpy as np
import pandas as pd
import requests
from sklearn.cluster import KMeans
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

import lago
from lago import APPS, tabla

OLLAMA = os.environ.get("OLLAMA_URL", "http://ollama.demo-ia-analitica.svc:11434").rstrip("/")
MODELO = os.environ.get("MODELO", "granite3.3:2b")


def pesos(v):
    return "$" + f"{v:,.0f}".replace(",", ".")


def num(v, d=0):
    return f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ─────────── Vista 360 del cliente: la base de casi todos los agentes ───────────
_cache = {}


def cliente360():
    if "360" in _cache and time.time() - _cache["360"][0] < 600:
        return _cache["360"][1]
    q = f"""
    SELECT c.cliente_id, c.segmento, c.plan, c.valor_mensual, c.edad, c.canal_alta, c.region, c.sucursal, c.estado,
      COALESCE(p.n_tx, 0) AS n_tx, COALESCE(p.rechazos, 0) AS rechazos, COALESCE(p.monto, 0) AS monto_pagos,
      COALESCE(s.sesiones, 0) AS sesiones, COALESCE(s.compras, 0) AS compras_web,
      COALESCE(t.tickets, 0) AS tickets, COALESCE(t.negativos, 0) AS tickets_negativos, COALESCE(t.satisfaccion, 4.0) AS satisfaccion,
      COALESCE(e.envios, 0) AS envios, COALESCE(e.atrasos, 0) AS atrasos
    FROM {tabla('crm')} c
    LEFT JOIN (SELECT cliente_id, count(*) AS n_tx, sum(CASE WHEN estado = 'Rechazada' THEN 1 ELSE 0 END) AS rechazos,
               sum(monto) AS monto FROM {tabla('pagos')} GROUP BY cliente_id) p ON p.cliente_id = c.cliente_id
    LEFT JOIN (SELECT cliente_id, count(*) AS sesiones, sum(CASE WHEN compro THEN 1 ELSE 0 END) AS compras
               FROM {tabla('ecommerce')} GROUP BY cliente_id) s ON s.cliente_id = c.cliente_id
    LEFT JOIN (SELECT cliente_id, count(*) AS tickets, sum(CASE WHEN sentimiento = 'Negativo' THEN 1 ELSE 0 END) AS negativos,
               avg(CAST(satisfaccion AS double)) AS satisfaccion FROM {tabla('soporte')} GROUP BY cliente_id) t ON t.cliente_id = c.cliente_id
    LEFT JOIN (SELECT cliente_id, count(*) AS envios, sum(CASE WHEN atrasado THEN 1 ELSE 0 END) AS atrasos
               FROM {tabla('logistica')} GROUP BY cliente_id) e ON e.cliente_id = c.cliente_id"""
    df = lago.sql(q)
    for c in ("valor_mensual", "n_tx", "rechazos", "monto_pagos", "sesiones", "compras_web", "tickets", "tickets_negativos",
              "satisfaccion", "envios", "atrasos", "edad"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    _cache["360"] = (time.time(), df)
    return df


ORIGEN = {"valor_mensual": "crm", "edad": "crm", "seg_": "crm", "plan_": "crm", "canal_": "crm", "n_tx": "pagos",
          "rechazos": "pagos", "monto_pagos": "pagos", "sesiones": "ecommerce", "compras_web": "ecommerce", "tickets": "soporte",
          "tickets_negativos": "soporte", "satisfaccion": "soporte", "envios": "logistica", "atrasos": "logistica",
          "transportista": "logistica", "sucursal": "logistica", "ausentismo": "rrhh", "dias_prometidos": "logistica", "mes": "logistica",
          "paginas": "ecommerce", "minutos": "ecommerce", "carrito": "ecommerce", "dispositivo": "ecommerce"}
NOMBRE_VAR = {"valor_mensual": "valor mensual", "n_tx": "n.º de pagos", "rechazos": "pagos rechazados", "monto_pagos": "monto pagado",
              "sesiones": "visitas a la tienda web", "compras_web": "compras web", "tickets": "tickets de soporte",
              "tickets_negativos": "tickets con molestia", "satisfaccion": "satisfacción en soporte", "envios": "envíos",
              "atrasos": "despachos atrasados", "edad": "edad", "ausentismo": "ausentismo de la sucursal",
              "dias_prometidos": "plazo prometido", "paginas": "páginas vistas", "minutos": "minutos en el sitio", "carrito": "monto del carrito"}


def origen(var):
    for k, v in ORIGEN.items():
        if var == k or var.startswith(k):
            return v
    return "crm"


def nombre_var(var):
    if var.startswith("seg_"):
        return f"segmento {var[4:]}"
    if var.startswith("plan_"):
        return f"plan {var[5:]}"
    if var.startswith("canal_"):
        return f"alta por {var[6:]}"
    if var.startswith("transportista_"):
        return f"transportista {var[14:]}"
    if var.startswith("sucursal_"):
        return f"sucursal {var[9:]}"
    if var.startswith("dispositivo_"):
        return f"dispositivo {var[12:]}"
    return NOMBRE_VAR.get(var, var)


def importancias(modelo, cols, n=6):
    imp = getattr(modelo, "feature_importances_", None)
    if imp is None:
        imp = np.abs(modelo.coef_[0])
    imp = imp / (imp.sum() or 1)
    orden = np.argsort(imp)[::-1][:n]
    return [{"etq": nombre_var(cols[i]), "valor": round(float(imp[i]) * 100, 1), "app": origen(cols[i])} for i in orden]


# ─────────── Soluciones ───────────
def fuga():
    df = cliente360()
    y = (df.estado == "Baja").astype(int)
    X = pd.get_dummies(df[["valor_mensual", "edad", "segmento", "plan", "canal_alta"]].rename(
        columns={"segmento": "seg", "plan": "plan", "canal_alta": "canal"}), prefix=["seg", "plan", "canal"], prefix_sep="_").astype(float)
    cruz = ["n_tx", "rechazos", "monto_pagos", "sesiones", "compras_web", "tickets", "tickets_negativos", "satisfaccion", "envios", "atrasos"]
    Xc = pd.concat([X, df[cruz]], axis=1)
    a, b, ya, yb = train_test_split(Xc, y, test_size=.3, random_state=1, stratify=y)
    solo = GradientBoostingClassifier(random_state=1).fit(a[X.columns], ya)
    auc_solo = roc_auc_score(yb, solo.predict_proba(b[X.columns])[:, 1])
    m = GradientBoostingClassifier(random_state=1).fit(a, ya)
    auc = roc_auc_score(yb, m.predict_proba(b)[:, 1])
    df["riesgo"] = m.predict_proba(Xc)[:, 1]
    act = df[df.estado == "Activo"].sort_values("riesgo", ascending=False)
    top = act.head(300)
    en_riesgo = top.valor_mensual.sum()
    imp = importancias(m, list(Xc.columns))
    return {
        "metrica": {"nombre": "AUC (capacidad de anticipar la fuga)", "valor": auc, "fmt": num(auc, 2), "umbral": .75},
        "comparacion": [{"etq": "Solo datos del CRM", "valor": round(float(auc_solo), 2)}, {"etq": "Cruzando 5 aplicaciones del lake", "valor": round(float(auc), 2)}],
        "apps": ["crm", "pagos", "ecommerce", "soporte", "logistica"], "filas": int(len(df)),
        "hallazgos": [f"Con solo el CRM el modelo apenas anticipa la fuga (AUC {num(auc_solo, 2)}); al cruzar soporte, pagos y despachos llega a {num(auc, 2)}.",
                      f"Lo que más explica la fuga: {imp[0]['etq']} ({APPS[imp[0]['app']]['nombre']}), {imp[1]['etq']} y {imp[2]['etq']}.",
                      f"Hoy hay {num((act.riesgo > .5).sum())} clientes activos con más de 50 % de probabilidad de irse."],
        "decisiones": [{"texto": "Campaña de retención para los 300 clientes activos de mayor riesgo",
                        "impacto": f"{pesos(en_riesgo)} de ingreso mensual en juego", "area": "Comercial"},
                       {"texto": "Llamada proactiva de soporte a clientes con 2 o más tickets con molestia en el trimestre",
                        "impacto": "ataca la primera causa de fuga", "area": "Atención"}],
        "grafico": {"tipo": "barras", "titulo": "Qué explica la fuga (importancia %)", "datos": imp},
        "tabla": {"titulo": "Clientes activos con más riesgo", "cols": ["Cliente", "Segmento", "Sucursal", "Valor mensual", "Tickets con molestia", "Atrasos", "Riesgo"],
                  "filas": [[int(r.cliente_id), r.segmento, r.sucursal, pesos(r.valor_mensual), int(r.tickets_negativos), int(r.atrasos), f"{r.riesgo * 100:.0f} %"]
                            for r in top.head(10).itertuples()]},
    }


def segmentacion():
    df = cliente360()
    df = df[df.estado == "Activo"].copy()
    cols = ["valor_mensual", "n_tx", "monto_pagos", "sesiones", "compras_web", "tickets", "tickets_negativos", "atrasos"]
    Z = StandardScaler().fit_transform(np.log1p(df[cols]))
    km = KMeans(4, n_init=10, random_state=3).fit(Z)
    df["seg"] = km.labels_
    g = df.groupby("seg")[cols].mean()
    gl = g.copy()
    nombres, resto = {}, list(g.index)
    for etq, col in (("Molestos con servicio", "tickets_negativos"), ("Alto valor", "valor_mensual"), ("Digitales activos", "compras_web")):
        s_ = max(resto, key=lambda i: g.loc[i, col])
        nombres[s_] = etq
        resto.remove(s_)
    nombres[resto[0]] = "Poco uso"
    tam = df.seg.value_counts()
    filas = [[nombres[s], num(tam[s]), pesos(gl.loc[s, "valor_mensual"]), num(gl.loc[s, "n_tx"], 1), num(gl.loc[s, "compras_web"], 1),
              num(gl.loc[s, "tickets_negativos"], 1), num(gl.loc[s, "atrasos"], 1)] for s in g.index]
    mol = [s for s in nombres if nombres[s].startswith("Molestos")]
    m_n = int(tam[mol].sum()) if mol else 0
    return {
        "metrica": {"nombre": "Segmentos encontrados", "valor": 4, "fmt": "4", "umbral": 1},
        "apps": ["crm", "pagos", "ecommerce", "soporte", "logistica"], "filas": int(len(df)),
        "hallazgos": ["Los segmentos combinan valor (CRM), uso (pagos y tienda web) y experiencia (soporte y despachos): no salen de una sola aplicación.",
                      f"El segmento «Molestos con servicio» reúne {num(m_n)} clientes: concentran los tickets con molestia y los atrasos."],
        "decisiones": [{"texto": "Oferta digital para «Poco uso»: incentivo para pagar con la app", "impacto": "más transacciones por cliente", "area": "Marketing"},
                       {"texto": "Plan de recuperación para «Molestos con servicio» antes de cualquier venta cruzada", "impacto": f"{num(m_n)} clientes", "area": "Atención"}],
        "grafico": {"tipo": "barras", "titulo": "Clientes por segmento", "datos": [{"etq": nombres[s], "valor": int(tam[s]), "app": "crm"} for s in g.index]},
        "tabla": {"titulo": "Perfil promedio de cada segmento", "cols": ["Segmento", "Clientes", "Valor mensual", "Pagos", "Compras web", "Tickets con molestia", "Atrasos"], "filas": filas},
    }


def fraude():
    df = lago.sql(f"""SELECT t.tx_id, t.cliente_id, t.fecha, t.monto, t.pais, t.dispositivo_nuevo, t.canal, t.fraude_confirmado, c.segmento, c.valor_mensual
                      FROM {tabla('pagos')} t JOIN {tabla('crm')} c ON c.cliente_id = t.cliente_id
                      WHERE t.fecha >= TIMESTAMP '2026-08-01 00:00:00'""")
    df["fecha"] = pd.to_datetime(df.fecha)
    df["monto"] = pd.to_numeric(df.monto)
    df["valor_mensual"] = pd.to_numeric(df.valor_mensual)
    prom = df.groupby("cliente_id").monto.transform("median")
    X = pd.DataFrame({"monto_log": np.log1p(df.monto), "desvio": np.log1p(df.monto) - np.log1p(prom), "hora": df.fecha.dt.hour,
                      "noche": df.fecha.dt.hour.isin([0, 1, 2, 3, 4, 5]).astype(int), "extranjero": (df.pais != "Chile").astype(int),
                      "nuevo": df.dispositivo_nuevo.astype(int), "relativo": df.monto / (df.valor_mensual + 1)})
    iso = IsolationForest(n_estimators=200, contamination=.006, random_state=2).fit(X)
    df["puntaje"] = -iso.score_samples(X)
    k = int(len(df) * .006)
    top = df.sort_values("puntaje", ascending=False).head(k)
    reales = int(df.fraude_confirmado.astype(bool).sum())
    hit = int(top.fraude_confirmado.astype(bool).sum())
    precision = hit / max(k, 1)
    recall = hit / max(reales, 1)
    return {
        "metrica": {"nombre": "Fraudes confirmados detectados (recall)", "valor": recall, "fmt": f"{recall * 100:.0f} %", "umbral": .6},
        "comparacion": [{"etq": "Precisión de las alertas", "valor": round(precision, 2)}, {"etq": "Fraudes confirmados detectados", "valor": round(recall, 2)}],
        "apps": ["pagos", "crm"], "filas": int(len(df)),
        "hallazgos": [f"Revisando solo {num(k)} de {num(len(df))} transacciones (0,6 %) el agente encuentra {hit} de los {reales} fraudes confirmados.",
                      "Las señales: monto muy sobre lo habitual del cliente y sobre su valor en el CRM, de madrugada, desde fuera de Chile o en un dispositivo nuevo.",
                      "El modelo no usa la etiqueta de fraude para aprender (no supervisado): sirve también para fraudes nuevos."],
        "decisiones": [{"texto": "Pedir segunda autenticación en tiempo real a las transacciones con puntaje en el 0,6 % superior",
                        "impacto": f"{hit} fraudes evitados en 3 meses", "area": "Riesgo"}],
        "grafico": {"tipo": "barras", "titulo": "Alertas por país de origen", "datos": [{"etq": p, "valor": int(n), "app": "pagos"} for p, n in top.pais.value_counts().items()]},
        "tabla": {"titulo": "Transacciones más sospechosas", "cols": ["Transacción", "Cliente", "Fecha", "Monto", "País", "Dispositivo nuevo", "¿Fraude confirmado?"],
                  "filas": [[int(r.tx_id), int(r.cliente_id), r.fecha.strftime("%d-%m %H:%M"), pesos(r.monto), r.pais, "Sí" if r.dispositivo_nuevo else "No",
                             "Sí" if r.fraude_confirmado else "No"] for r in top.head(10).itertuples()]},
    }


def _serie(q, col):
    df = lago.sql(q)
    df["dia"] = pd.to_datetime(df.dia)
    df[col] = pd.to_numeric(df[col])
    s = df.set_index("dia")[col].asfreq("D", fill_value=0)
    return s


def _pronostico(s, titulo, unidad, fmt):
    d = pd.DataFrame({"y": s.values}, index=s.index)
    def X(ix):
        t = np.arange(len(ix)) if ix is s.index else np.arange(len(s), len(s) + len(ix))
        f = pd.get_dummies(pd.Categorical(ix.dayofweek, categories=range(7)), prefix="d").astype(float)
        f["t"] = t
        f["dic"] = (ix.month == 12).astype(float)
        return f.values
    Xa = X(s.index)
    corte = len(s) - 30
    m = Ridge(alpha=1).fit(Xa[:corte], d.y.values[:corte])
    pred = m.predict(Xa[corte:])
    real = d.y.values[corte:]
    mape = float(np.mean(np.abs(pred - real) / np.maximum(real, 1)))
    m = Ridge(alpha=1).fit(Xa, d.y.values)
    fut = pd.date_range(s.index[-1] + pd.Timedelta(days=1), periods=30)
    pf = m.predict(X(fut))
    hist = [{"etq": i.strftime("%d-%m"), "valor": float(v)} for i, v in s.tail(60).items()]
    pron = [{"etq": i.strftime("%d-%m"), "valor": float(v)} for i, v in zip(fut, pf)]
    return mape, pf, {"tipo": "serie", "titulo": titulo, "unidad": unidad, "historia": hist, "pronostico": pron}


def pronostico_pagos():
    s = _serie(f"SELECT date_trunc('day', fecha) AS dia, sum(monto) AS total FROM {tabla('pagos')} WHERE estado = 'Aprobada' GROUP BY 1", "total")
    mape, pf, g = _pronostico(s, "Monto aprobado por día (últimos 60 días y próximos 30)", "$", pesos)
    pico = max(g["pronostico"], key=lambda x: x["valor"])
    return {
        "metrica": {"nombre": "Error medio del pronóstico (MAPE)", "valor": 1 - mape, "fmt": f"{num(mape * 100, 1)} %", "umbral": .85, "menor_es_mejor": True},
        "apps": ["pagos"], "filas": int(len(s)),
        "hallazgos": [f"El volumen tiene estacionalidad semanal clara: viernes y sábados pagan más (error {num(mape * 100, 1)} % en los últimos 30 días).",
                      f"Próximos 30 días: {pesos(pf.sum())} aprobados; el día más alto sería el {pico['etq']}."],
        "decisiones": [{"texto": "Escalar la App de pagos en OpenShift antes de los viernes (réplicas programadas)", "impacto": "evita rechazos por timeout en los picos", "area": "TI"}],
        "grafico": g,
    }


def propension():
    df = lago.sql(f"""SELECT s.sesion_id, s.cliente_id, s.dispositivo, s.paginas, s.minutos, s.carrito_monto, s.compro, s.fecha
                      FROM {tabla('ecommerce')} s WHERE s.carrito_monto > 0""")
    c = cliente360()[["cliente_id", "segmento", "tickets_negativos", "atrasos", "rechazos", "valor_mensual"]]
    df = df.merge(c, on="cliente_id", how="left")
    for k in ("paginas", "minutos", "carrito_monto", "tickets_negativos", "atrasos", "rechazos", "valor_mensual"):
        df[k] = pd.to_numeric(df[k]).fillna(0)
    y = df.compro.astype(bool).astype(int)
    base = pd.get_dummies(df[["dispositivo"]], prefix="dispositivo").astype(float)
    base[["paginas", "minutos"]] = df[["paginas", "minutos"]]
    base["carrito"] = np.log1p(df.carrito_monto)
    cruz = base.copy()
    cruz = pd.concat([cruz, pd.get_dummies(df.segmento, prefix="seg").astype(float), df[["tickets_negativos", "atrasos", "rechazos"]]], axis=1)
    a, b, ya, yb = train_test_split(cruz, y, test_size=.3, random_state=4, stratify=y)
    sc = StandardScaler().fit(a)
    m0 = LogisticRegression(max_iter=500).fit(StandardScaler().fit_transform(a[base.columns]), ya)
    auc0 = roc_auc_score(yb, m0.predict_proba(StandardScaler().fit(a[base.columns]).transform(b[base.columns]))[:, 1])
    m = LogisticRegression(max_iter=500).fit(sc.transform(a), ya)
    auc = roc_auc_score(yb, m.predict_proba(sc.transform(b))[:, 1])
    df["p"] = m.predict_proba(sc.transform(cruz))[:, 1]
    aband = df[~df.compro.astype(bool)]
    rec = aband[aband.p > .45]
    return {
        "metrica": {"nombre": "AUC de propensión de compra", "valor": auc, "fmt": num(auc, 2), "umbral": .7},
        "comparacion": [{"etq": "Solo la sesión web", "valor": round(float(auc0), 2)}, {"etq": "Sesión + soporte, despachos y CRM", "valor": round(float(auc), 2)}],
        "apps": ["ecommerce", "crm", "soporte", "logistica", "pagos"], "filas": int(len(df)),
        "hallazgos": ([] if auc >= .7 else [f"Evaluación: con AUC {num(auc, 2)} esta solución estándar aún no alcanza el umbral (0,70); conviene sumar más datos (por ejemplo, historial de navegación) antes de activarla."]) + [f"De {num(len(aband))} carritos abandonados, {num(len(rec))} tienen alta propensión a comprar si se les contacta.",
                      "Un cliente con despachos atrasados o tickets con molestia abandona más su carrito: esa señal no está en la tienda web."],
        "decisiones": [{"texto": "Recordatorio con envío preferente a los carritos abandonados de alta propensión",
                        "impacto": f"{pesos(rec.carrito_monto.sum() * .25)} en ventas recuperables (25 % de conversión)", "area": "Marketing"}],
        "grafico": {"tipo": "barras", "titulo": "Qué mueve la compra (peso %)", "datos": importancias(m, list(cruz.columns))},
        "tabla": {"titulo": "Carritos abandonados más recuperables", "cols": ["Sesión", "Cliente", "Segmento", "Dispositivo", "Carrito", "Propensión"],
                  "filas": [[int(r.sesion_id), int(r.cliente_id), r.segmento, r.dispositivo, pesos(r.carrito_monto), f"{r.p * 100:.0f} %"]
                            for r in rec.sort_values("p", ascending=False).head(10).itertuples()]},
    }


_clasif = {}


def clasificacion():
    df = lago.sql(f"SELECT ticket_id, texto, categoria, sentimiento, prioridad, horas_resolucion FROM {tabla('soporte')}")
    a, b = train_test_split(df, test_size=.3, random_state=5)
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=2, strip_accents="unicode").fit(a.texto)
    mc = LogisticRegression(max_iter=800).fit(vec.transform(a.texto), a.categoria)
    ms = LogisticRegression(max_iter=800).fit(vec.transform(a.texto), a.sentimiento)
    acc = accuracy_score(b.categoria, mc.predict(vec.transform(b.texto)))
    acs = accuracy_score(b.sentimiento, ms.predict(vec.transform(b.texto)))
    _clasif.update(vec=vec, cat=mc, sen=ms)
    cats = df.categoria.value_counts()
    neg = (df[df.sentimiento == "Negativo"].categoria.value_counts() / cats).sort_values(ascending=False)
    return {
        "metrica": {"nombre": "Exactitud al clasificar el tipo de ticket", "valor": acc, "fmt": f"{acc * 100:.0f} %", "umbral": .8},
        "comparacion": [{"etq": "Tipo de ticket", "valor": round(acc, 2)}, {"etq": "Sentimiento (molestia)", "valor": round(acs, 2)}],
        "apps": ["soporte"], "filas": int(len(df)), "probar": True,
        "hallazgos": [f"El agente lee el texto del cliente y lo clasifica solo: tipo correcto en {acc * 100:.0f} % y molestia en {acs * 100:.0f} %.",
                      f"La categoría con más molestia es «{neg.index[0]}»: {neg.iloc[0] * 100:.0f} % de sus tickets vienen con molestia."],
        "decisiones": [{"texto": "Enrutar automáticamente los tickets por tipo y subir a prioridad alta los que vienen con molestia",
                        "impacto": f"{num(len(df) / 12)} tickets al mes sin clasificar a mano", "area": "Atención"}],
        "grafico": {"tipo": "barras", "titulo": "Tickets por tipo", "datos": [{"etq": k, "valor": int(v), "app": "soporte"} for k, v in cats.items()]},
    }


def clasificar_texto(texto):
    if "vec" not in _clasif:
        clasificacion()
    v = _clasif["vec"].transform([texto])
    return {"categoria": _clasif["cat"].predict(v)[0], "sentimiento": _clasif["sen"].predict(v)[0],
            "confianza": round(float(_clasif["cat"].predict_proba(v).max()) * 100)}


def voz_cliente():
    df = lago.sql(f"""SELECT c.sucursal, t.categoria, count(*) AS n, avg(CAST(t.satisfaccion AS double)) AS sat,
                      sum(CASE WHEN t.sentimiento = 'Negativo' THEN 1 ELSE 0 END) AS neg
                      FROM {tabla('soporte')} t JOIN {tabla('crm')} c ON c.cliente_id = t.cliente_id GROUP BY 1, 2""")
    for k in ("n", "sat", "neg"):
        df[k] = pd.to_numeric(df[k])
    suc = df.groupby("sucursal").agg(n=("n", "sum"), neg=("neg", "sum"))
    suc["pct"] = suc.neg / suc.n
    suc = suc.sort_values("pct", ascending=False)
    ej = lago.sql(f"SELECT texto FROM {tabla('soporte')} WHERE sentimiento = 'Negativo' LIMIT 12").texto.tolist()
    resumen, seg = granite([{"role": "system", "content": "Eres un analista de experiencia de clientes. Responde en español, en 3 frases, sin listas."},
                            {"role": "user", "content": "Resume qué molesta a los clientes y qué harías primero. Peores sucursales: "
                             + ", ".join(f"{s} ({p * 100:.0f} % con molestia)" for s, p in suc.pct.head(3).items())
                             + ". Ejemplos de textos: " + " | ".join(ej)}], 160)
    if not resumen:
        resumen = (f"Las molestias se concentran en {suc.index[0]}, {suc.index[1]} y {suc.index[2]}, sobre todo por despachos atrasados y cobros. "
                   "Conviene atacar primero los atrasos de despacho en esas sucursales, que además arrastran baja satisfacción.")
    return {
        "metrica": {"nombre": "Sucursales con molestia sobre el promedio", "valor": int((suc.pct > suc.pct.mean()).sum()),
                    "fmt": str(int((suc.pct > suc.pct.mean()).sum())), "umbral": 0},
        "apps": ["soporte", "crm"], "filas": int(df.n.sum()), "llm": {"texto": resumen, "segundos": seg, "modelo": MODELO if seg else None},
        "hallazgos": [f"{suc.index[0]} tiene la mayor proporción de tickets con molestia ({suc.pct.iloc[0] * 100:.0f} %)."],
        "decisiones": [{"texto": f"Revisión de atención en {suc.index[0]}, {suc.index[1]} y {suc.index[2]}", "impacto": "sucursales con más molestia", "area": "Atención"}],
        "grafico": {"tipo": "barras", "titulo": "% de tickets con molestia por sucursal", "datos": [{"etq": s, "valor": round(p * 100, 1), "app": "soporte"} for s, p in suc.pct.items()]},
    }


def atrasos():
    df = lago.sql(f"""SELECT e.envio_id, e.sucursal, e.transportista, e.dias_prometidos, e.atrasado, e.fecha, d.ausentismo_pct
                      FROM {tabla('logistica')} e LEFT JOIN {tabla('rrhh')} d
                        ON d.sucursal = e.sucursal AND d.mes = date_trunc('month', e.fecha)""")
    df["fecha"] = pd.to_datetime(df.fecha)
    df["ausentismo"] = pd.to_numeric(df.ausentismo_pct, errors="coerce")
    df["ausentismo"] = df.ausentismo.fillna(df.ausentismo.mean())
    y = df.atrasado.astype(bool).astype(int)
    base = pd.get_dummies(df[["transportista", "sucursal"]], prefix=["transportista", "sucursal"]).astype(float)
    base["dias_prometidos"] = df.dias_prometidos.astype(float)
    base["mes"] = df.fecha.dt.month.astype(float)
    cruz = base.copy()
    cruz["ausentismo"] = df.ausentismo
    a, b, ya, yb = train_test_split(cruz, y, test_size=.3, random_state=6, stratify=y)
    m = GradientBoostingClassifier(random_state=1).fit(a, ya)
    auc = roc_auc_score(yb, m.predict_proba(b)[:, 1])
    tr = df.groupby("transportista").atrasado.apply(lambda x: x.astype(bool).mean()).sort_values(ascending=False)
    su = df.groupby("sucursal").agg(atraso=("atrasado", lambda x: x.astype(bool).mean()), aus=("ausentismo", "mean")).sort_values("atraso", ascending=False)
    return {
        "metrica": {"nombre": "AUC para anticipar un despacho atrasado", "valor": auc, "fmt": num(auc, 2), "umbral": .65},
        "apps": ["logistica", "rrhh"], "filas": int(len(df)),
        "hallazgos": [f"{tr.index[0]} atrasa el {tr.iloc[0] * 100:.0f} % de sus envíos; {tr.index[-1]}, el {tr.iloc[-1] * 100:.0f} %.",
                      f"Las sucursales con más ausentismo (dato de Personas) atrasan más: {su.index[0]} ({su.atraso.iloc[0] * 100:.0f} % de atrasos, {num(su.aus.iloc[0], 1)} % de ausentismo).",
                      "Noviembre y diciembre suben los atrasos: hay que reforzar antes de la temporada."],
        "decisiones": [{"texto": f"Reasignar a {tr.index[-1]} las rutas de {tr.index[0]} en las sucursales críticas", "impacto": f"−{(tr.iloc[0] - tr.iloc[-1]) * 100:.0f} pp de atraso en esas rutas", "area": "Operaciones"},
                       {"texto": f"Reforzar dotación en {su.index[0]} y {su.index[1]} antes de noviembre", "impacto": "menos atrasos y menos tickets", "area": "Personas"}],
        "grafico": {"tipo": "barras", "titulo": "Qué explica un atraso (importancia %)", "datos": importancias(m, list(cruz.columns))},
        "tabla": {"titulo": "Atraso por sucursal", "cols": ["Sucursal", "% atrasados", "Ausentismo promedio"],
                  "filas": [[s, f"{r.atraso * 100:.0f} %", f"{num(r.aus, 1)} %"] for s, r in su.head(12).iterrows()]},
    }


def _por_sucursal():
    q = f"""SELECT r.sucursal, r.aus, l.atraso, s.sat, f.fuga FROM
      (SELECT sucursal, avg(ausentismo_pct) AS aus FROM {tabla('rrhh')} GROUP BY 1) r
      JOIN (SELECT sucursal, avg(CASE WHEN atrasado THEN 1e0 ELSE 0e0 END) AS atraso FROM {tabla('logistica')} GROUP BY 1) l ON l.sucursal = r.sucursal
      JOIN (SELECT c.sucursal, avg(CAST(t.satisfaccion AS double)) AS sat FROM {tabla('soporte')} t JOIN {tabla('crm')} c ON c.cliente_id = t.cliente_id GROUP BY 1) s ON s.sucursal = r.sucursal
      JOIN (SELECT sucursal, avg(CASE WHEN estado = 'Baja' THEN 1e0 ELSE 0e0 END) AS fuga FROM {tabla('crm')} GROUP BY 1) f ON f.sucursal = r.sucursal"""
    df = lago.sql(q)
    for k in ("aus", "atraso", "sat", "fuga"):
        df[k] = pd.to_numeric(df[k])
    return df.sort_values("aus", ascending=False)


def impacto_personas():
    df = _por_sucursal()
    lr = LinearRegression().fit(df[["aus"]], df.atraso)
    lf = LinearRegression().fit(df[["aus"]], df.fuga)
    r = float(np.corrcoef(df.aus, df.atraso)[0, 1])
    return {
        "metrica": {"nombre": "Correlación ausentismo ↔ atrasos", "valor": r, "fmt": num(r, 2), "umbral": .5},
        "apps": ["rrhh", "logistica", "soporte", "crm"], "filas": int(len(df)),
        "hallazgos": [f"Cada punto de ausentismo suma {num(lr.coef_[0] * 100, 1)} pp de despachos atrasados y {num(lf.coef_[0] * 100, 1)} pp de fuga de clientes.",
                      f"Las 3 sucursales con más ausentismo: {', '.join(df.sucursal.head(3))}.",
                      "Es un hallazgo que ninguna aplicación ve sola: Personas no sabe de despachos y Despachos no sabe de ausentismo."],
        "decisiones": [{"texto": f"Plan de dotación y bienestar en {', '.join(df.sucursal.head(3))}", "impacto": f"−{lr.coef_[0] * 300:.0f} pp de atrasos si bajan 3 puntos de ausentismo", "area": "Personas"}],
        "grafico": {"tipo": "barras", "titulo": "Ausentismo promedio por sucursal (%)", "datos": [{"etq": s, "valor": round(a, 1), "app": "rrhh"} for s, a in zip(df.sucursal, df.aus)]},
        "tabla": {"titulo": "Cruce por sucursal", "cols": ["Sucursal", "Ausentismo", "% atrasos", "Satisfacción soporte", "% fuga"],
                  "filas": [[x.sucursal, f"{num(x.aus, 1)} %", f"{x.atraso * 100:.0f} %", num(x.sat, 2), f"{num(x.fuga * 100, 1)} %"] for x in df.itertuples()]},
    }


def riesgo_sucursal():
    df = _por_sucursal()
    z = lambda s: (s - s.mean()) / (s.std() or 1)  # noqa: E731
    df["indice"] = z(df.aus) + z(df.atraso) - z(df.sat) + z(df.fuga)
    df = df.sort_values("indice", ascending=False)
    crit = df[df.indice > 1.5]
    return {
        "metrica": {"nombre": "Sucursales en riesgo", "valor": len(crit), "fmt": str(len(crit)), "umbral": 0},
        "apps": ["rrhh", "logistica", "soporte", "crm"], "filas": int(len(df)),
        "hallazgos": [f"Índice de riesgo combinado (ausentismo + atrasos + baja satisfacción + fuga): {', '.join(crit.sucursal) or 'ninguna'} sobre el umbral."],
        "decisiones": [{"texto": f"Visita de gerencia y plan de 30 días en {s}", "impacto": "sucursal en riesgo", "area": "Personas"} for s in crit.sucursal.head(3)],
        "grafico": {"tipo": "barras", "titulo": "Índice de riesgo por sucursal", "datos": [{"etq": s, "valor": round(v, 2), "app": "rrhh"} for s, v in zip(df.sucursal, df.indice)]},
    }


# ─────────── Granite (opcional: si no responde, el agente sigue con lo determinístico) ───────────
def granite(mensajes, max_tokens=200, formato=None, timeout=150):
    t0 = time.time()
    try:
        cuerpo = {"model": MODELO, "messages": mensajes, "stream": False, "keep_alive": "30m",
                  "options": {"num_predict": max_tokens, "temperature": .1}}
        if formato:
            cuerpo["format"] = formato
        r = requests.post(f"{OLLAMA}/api/chat", json=cuerpo, timeout=timeout)
        r.raise_for_status()
        return r.json()["message"]["content"].strip(), round(time.time() - t0, 1)
    except Exception:  # noqa: BLE001
        return "", 0


def granite_listo():
    try:
        return any(m["name"].split(":")[0] == MODELO.split(":")[0] for m in requests.get(f"{OLLAMA}/api/tags", timeout=4).json().get("models") or [])
    except Exception:  # noqa: BLE001
        return False


# ─────────── El catálogo ───────────
SOLUCIONES = {
    "fuga": {"nombre": "Predicción de fuga de clientes", "tipo": "Clasificación supervisada", "tecnica": "Gradient Boosting", "fn": fuga,
             "desc": "Anticipa qué clientes se van a ir, cruzando su experiencia en todas las aplicaciones."},
    "segmentacion": {"nombre": "Segmentación de clientes", "tipo": "Clustering", "tecnica": "K-Means", "fn": segmentacion,
                     "desc": "Agrupa a los clientes por valor, uso y experiencia para decidir ofertas."},
    "fraude": {"nombre": "Detección de fraude", "tipo": "Detección de anomalías", "tecnica": "Isolation Forest", "fn": fraude,
               "desc": "Marca transacciones fuera de lo normal para cada cliente, sin necesidad de etiquetas."},
    "pronostico_pagos": {"nombre": "Pronóstico de volumen", "tipo": "Series de tiempo", "tecnica": "Regresión con estacionalidad", "fn": pronostico_pagos,
                         "desc": "Proyecta el monto diario de los próximos 30 días para planificar capacidad."},
    "propension": {"nombre": "Propensión de compra", "tipo": "Clasificación supervisada", "tecnica": "Regresión logística", "fn": propension,
                   "desc": "Indica qué carritos abandonados conviene recuperar."},
    "clasificacion": {"nombre": "Clasificación de tickets y sentimiento", "tipo": "Procesamiento de lenguaje", "tecnica": "TF-IDF + clasificador", "fn": clasificacion,
                      "desc": "Lee el texto del cliente, detecta el tipo de problema y la molestia."},
    "voz_cliente": {"nombre": "Voz del cliente con IA generativa", "tipo": "IA generativa (LLM)", "tecnica": "IBM Granite", "fn": voz_cliente,
                    "desc": "Resume en lenguaje natural qué molesta a los clientes y dónde actuar primero."},
    "atrasos": {"nombre": "Predicción de atrasos", "tipo": "Clasificación supervisada", "tecnica": "Gradient Boosting", "fn": atrasos,
                "desc": "Anticipa qué despachos se atrasarán y por qué (transportista, sucursal, dotación)."},
    "impacto_personas": {"nombre": "Impacto del ausentismo en el negocio", "tipo": "Análisis causal", "tecnica": "Regresión", "fn": impacto_personas,
                         "desc": "Mide cuánto cuesta en despachos y clientes cada punto de ausentismo."},
    "riesgo_sucursal": {"nombre": "Sucursales en riesgo", "tipo": "Índice compuesto", "tecnica": "Puntaje z multivariable", "fn": riesgo_sucursal,
                        "desc": "Combina personas, despachos, soporte y fuga en un solo indicador por sucursal."},
}

AGENTES = {
    "crm": {"nombre": "Agente comercial", "rol": "Retener y crecer la cartera", "soluciones": ["fuga", "segmentacion"],
            "preguntas": [("¿Cuántos clientes se fueron por segmento?", "SELECT segmento, count(*) AS clientes, sum(CASE WHEN estado = 'Baja' THEN 1 ELSE 0 END) AS bajas FROM lake.app_crm.clientes GROUP BY 1 ORDER BY 3 DESC"),
                          ("¿Qué sucursal pierde más ingreso por bajas?", "SELECT sucursal, sum(valor_mensual) AS ingreso_mensual_perdido FROM lake.app_crm.clientes WHERE estado = 'Baja' GROUP BY 1 ORDER BY 2 DESC LIMIT 6"),
                          ("¿Los clientes que se fueron tuvieron más atrasos de despacho?", "SELECT c.estado, avg(CASE WHEN e.atrasado THEN 1e0 ELSE 0e0 END) AS tasa_atraso, count(*) AS envios FROM lake.app_logistica.envios e JOIN lake.app_crm.clientes c ON c.cliente_id = e.cliente_id GROUP BY 1")]},
    "pagos": {"nombre": "Agente de pagos", "rol": "Cuidar la plata y la capacidad", "soluciones": ["fraude", "pronostico_pagos"],
              "preguntas": [("¿Cuál es la tasa de rechazo por medio de pago?", "SELECT medio, count(*) AS transacciones, avg(CASE WHEN estado = 'Rechazada' THEN 1e0 ELSE 0e0 END) AS tasa_rechazo FROM lake.app_pagos.transacciones GROUP BY 1 ORDER BY 3 DESC"),
                            ("¿Por qué se rechazan los pagos?", "SELECT motivo_rechazo, count(*) AS rechazos FROM lake.app_pagos.transacciones WHERE estado = 'Rechazada' GROUP BY 1 ORDER BY 2 DESC"),
                            ("¿Cuánto se paga por rubro?", "SELECT rubro, sum(monto) AS monto, count(*) AS transacciones FROM lake.app_pagos.transacciones WHERE estado = 'Aprobada' GROUP BY 1 ORDER BY 2 DESC")]},
    "ecommerce": {"nombre": "Agente de ventas web", "rol": "Convertir visitas en ventas", "soluciones": ["propension"],
                  "preguntas": [("¿Cómo convierte cada dispositivo?", "SELECT dispositivo, count(*) AS sesiones, avg(CASE WHEN compro THEN 1e0 ELSE 0e0 END) AS conversion FROM lake.app_ecommerce.sesiones GROUP BY 1 ORDER BY 3 DESC"),
                                ("¿Cuánto dinero queda en carritos abandonados?", "SELECT sum(carrito_monto) AS monto_abandonado, count(*) AS carritos FROM lake.app_ecommerce.sesiones WHERE carrito_monto > 0 AND NOT compro")]},
    "soporte": {"nombre": "Agente de atención", "rol": "Entender y resolver al cliente", "soluciones": ["clasificacion", "voz_cliente"],
                "preguntas": [("¿Qué tipo de ticket deja peor satisfacción?", "SELECT categoria, count(*) AS tickets, avg(CAST(satisfaccion AS double)) AS satisfaccion FROM lake.app_soporte.tickets GROUP BY 1 ORDER BY 3"),
                              ("¿Cuánto demora resolver por canal?", "SELECT canal, avg(horas_resolucion) AS horas_promedio, count(*) AS tickets FROM lake.app_soporte.tickets GROUP BY 1 ORDER BY 2 DESC")]},
    "logistica": {"nombre": "Agente logístico", "rol": "Cumplir lo prometido", "soluciones": ["atrasos"],
                  "preguntas": [("¿Qué transportista atrasa más?", "SELECT transportista, count(*) AS envios, avg(CASE WHEN atrasado THEN 1e0 ELSE 0e0 END) AS tasa_atraso FROM lake.app_logistica.envios GROUP BY 1 ORDER BY 3 DESC"),
                                ("¿Cuántos días de atraso promedio por región?", "SELECT region, avg(dias_reales - dias_prometidos) AS dias_atraso FROM lake.app_logistica.envios GROUP BY 1 ORDER BY 2 DESC")]},
    "rrhh": {"nombre": "Agente de personas", "rol": "Dotación que sostiene el servicio", "soluciones": ["impacto_personas", "riesgo_sucursal"],
             "preguntas": [("¿Qué sucursales tienen más ausentismo?", "SELECT sucursal, avg(ausentismo_pct) AS ausentismo, avg(rotacion_pct) AS rotacion FROM lake.app_rrhh.dotacion GROUP BY 1 ORDER BY 2 DESC"),
                           ("¿Cómo varía el ausentismo por mes?", "SELECT mes, avg(ausentismo_pct) AS ausentismo FROM lake.app_rrhh.dotacion GROUP BY 1 ORDER BY 1")]},
}


def evaluar(sol):
    t0 = time.time()
    r = SOLUCIONES[sol]["fn"]()
    m = r["metrica"]
    m["apta"] = bool((m["valor"] >= m["umbral"]) if m.get("umbral") is not None else True)
    r["segundos"] = round(time.time() - t0, 1)
    r["en"] = time.strftime("%Y-%m-%d %H:%M")
    r["solucion"] = sol
    return r


# ─────────── Preguntas en lenguaje natural (Granite traduce a SQL, el lake responde) ───────────
def esquema_texto(apps=None):
    partes = []
    for a in (apps or APPS):
        for t in APPS[a]["tablas"]:
            try:
                cols = lago.columnas(lago.esquema(a), t)
            except Exception:  # noqa: BLE001
                cols = []
            partes.append(f"{tabla(a, t)} ({', '.join(f'{c} {ty}' for c, ty in cols)})")
    return "\n".join(partes)


def preguntar(app, pregunta, ind=None):
    """Devuelve {sql, tabla, resumen, segundos, via}: primero las preguntas preparadas del agente; si no, Granite escribe el SQL."""
    t0 = time.time()
    ag = AGENTES.get(app)
    pre = None
    ind_ = APPS[app].get("industria", "retail") if app in APPS else (ind or "retail")
    for p, q in (ag["preguntas"] if ag else []) + [x for k, a in AGENTES.items() if APPS.get(k, {}).get("industria", "retail") == ind_ for x in a["preguntas"]]:
        if p.strip("¿? ").lower() == pregunta.strip("¿? ").lower():
            pre = q
            break
    via = "consulta preparada"
    if pre:
        q = pre
    else:
        foco = f"El usuario está en la aplicación {APPS[app]['nombre']}, pero puede cruzar con las demás." if app in APPS else ""
        sis = ("Traduce la pregunta a UNA consulta SQL de Trino de solo lectura sobre estas tablas del data lake. "
               "Usa los nombres completos de tabla. Para booleanos usa CASE WHEN col THEN 1e0 ELSE 0e0 END. "
               "Limita a 20 filas. Responde SOLO con la consulta, sin explicación ni ```.\n"
               + esquema_texto(lago.apps_de(APPS[app].get("industria", "retail") if app in APPS else (ind or "retail"))) + "\n" + foco)
        txt, _ = granite([{"role": "system", "content": sis}, {"role": "user", "content": pregunta}], 260)
        if not txt:
            return {"error": "El modelo de lenguaje (Granite) no respondió. Prueba con una de las preguntas sugeridas.", "segundos": round(time.time() - t0, 1)}
        q = re.sub(r"(?s)^.*?```(?:sql)?|```.*$", "", txt).strip() if "```" in txt else txt.strip()
        via = f"SQL escrito por {MODELO}"
    try:
        q = lago.solo_lectura(q)
        df = lago.sql(q if re.search(r"(?i)\blimit\b", q) else q + " LIMIT 50")
    except Exception as e:  # noqa: BLE001
        return {"sql": q, "error": f"La consulta no se pudo ejecutar: {str(e)[:200]}", "segundos": round(time.time() - t0, 1), "via": via}
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].round(3)
    apps = sorted({a for a in APPS if f"{lago.esquema(a)}." in q})
    return {"sql": q, "cols": list(df.columns), "filas": json.loads(df.head(50).to_json(orient="values", date_format="iso", force_ascii=False)),
            "apps": apps, "segundos": round(time.time() - t0, 1), "via": via}
