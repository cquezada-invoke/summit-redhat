"""JavIA · tendencias de comportamiento por uso: lee los requests reales de la App de pagos que FaceOps deriva al data lake
(lake.seg_app_de_pagos.requests) y, según las funcionalidades que ejecutan los clientes, identifica qué crece, qué cae, qué se
dispara fuera de lo normal, cómo cambia el canal y qué perfiles de uso hay. Granite (IBM, en nuestro OpenShift) lo explica."""
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

import agentes as A
import lago
from lago import APPS, INDUSTRIAS, GENERADORES

APPS["pagos_uso"] = {"nombre": "App de pagos · uso real", "plataforma": "JBoss EAP · VM (gestionada por FaceOps)", "icono": "pagos", "color": "#0E9F6E",
                     "desc": "Requests que FaceOps deriva al lake: qué funcionalidades usan los clientes y cómo cambia", "tablas": ["requests"],
                     "esquema": "seg_app_de_pagos", "industria": "faceops"}
INDUSTRIAS["faceops"] = {"nombre": "Aplicaciones gestionadas por FaceOps", "desc": "Datos reales derivados al lake desde el balanceador de cada aplicación",
                         "llaves": {}}
GENERADORES["faceops"] = lambda: {}   # no se emulan: los trae FaceOps (derivación al data lake)

T = "lake.seg_app_de_pagos.requests"
FUNC_SQL = """CASE
  WHEN metodo = 'POST' AND regexp_like(ruta, '/pagos/[^/]+/confirmar$') THEN 'Confirmar pago'
  WHEN metodo = 'POST' AND regexp_like(ruta, '/pagos/[^/]+/anular$') THEN 'Anular pago'
  WHEN metodo = 'POST' AND regexp_like(ruta, '/pagos$') THEN 'Pagar'
  WHEN metodo = 'GET' AND regexp_like(ruta, '/pagos/[^/]+$') THEN 'Consultar pago'
  WHEN regexp_like(ruta, '/qr') THEN 'Pago con QR'
  WHEN regexp_like(ruta, '/transferencias') THEN 'Transferir'
  WHEN regexp_like(ruta, '/saldo') THEN 'Consultar saldo'
  WHEN regexp_like(ruta, '/reembolsos') THEN 'Pedir reembolso'
  WHEN regexp_like(ruta, '/liquidacion') THEN 'Liquidación de comercio'
  WHEN regexp_like(ruta, '/login') THEN 'Ingresar'
  ELSE NULL END"""
BASE_SQL = f"""SELECT {FUNC_SQL} AS funcionalidad,
  COALESCE(TRY(CAST(from_iso8601_timestamp(json_extract_scalar(cuerpo, '$.fecha')) AS timestamp)), TRY(CAST(from_iso8601_timestamp(ts) AS timestamp))) AS fecha,
  COALESCE(json_extract_scalar(cuerpo, '$.canal'), 'sin dato') AS canal, json_extract_scalar(cuerpo, '$.cliente_id') AS cliente,
  TRY(CAST(json_extract_scalar(cuerpo, '$.monto') AS double)) AS monto, COALESCE(json_extract_scalar(cuerpo, '$.rubro'), 'sin dato') AS rubro
  FROM {T}"""


def _df(q):
    return lago.sql(q)


def tendencias_uso():
    dia = _df(f"""SELECT funcionalidad, date_trunc('day', fecha) AS dia, count(*) AS n FROM ({BASE_SQL}) WHERE funcionalidad IS NOT NULL AND fecha IS NOT NULL
                  GROUP BY 1, 2""")
    if dia.empty:
        return {"metrica": {"nombre": "Requests analizados", "valor": 0, "fmt": "0", "umbral": 1}, "apps": ["pagos_uso"], "filas": 0,
                "hallazgos": ["Aún no hay requests en el lake: activa el data lake del segmento App de pagos en FaceOps (flotante del segmento)."],
                "decisiones": []}
    dia["dia"] = pd.to_datetime(dia.dia)
    dia["n"] = pd.to_numeric(dia.n)
    total = int(dia.n.sum())
    piv = dia.pivot_table(index="dia", columns="funcionalidad", values="n", aggfunc="sum").fillna(0).asfreq("D", fill_value=0)
    sem = piv.resample("W-MON").sum()
    sem = sem.iloc[1:-1] if len(sem) > 4 else sem   # semanas completas
    filas, crecen, caen = [], [], []
    for f in piv.columns:
        y = sem[f].values.astype(float)
        if len(y) < 3 or y.sum() == 0:
            continue
        x = np.arange(len(y))
        pend = np.polyfit(x, y, 1)[0]
        mitad = max(1, len(y) // 2)
        antes, ahora = y[:mitad].mean(), y[mitad:].mean()
        var = (ahora - antes) / antes if antes else 0
        est = "crece" if var > .15 else ("cae" if var < -.15 else "estable")
        filas.append({"f": f, "total": int(piv[f].sum()), "var": var, "pend": pend, "estado": est})
        (crecen if est == "crece" else caen if est == "cae" else []).append((f, var))
    filas.sort(key=lambda r: -r["var"])
    # anomalías: días fuera de lo normal por funcionalidad (z-score robusto en los últimos 21 días)
    anom = []
    for f in piv.columns:
        s = piv[f]
        base = s.iloc[:-21] if len(s) > 35 else s
        med, mad = base.median(), (base - base.median()).abs().median() or 1
        rec = s.iloc[-21:]
        z = (rec - med) / (1.4826 * mad)
        for d, v in z[z > 4].items():
            anom.append((f, d, int(s[d]), float(v)))
    anom.sort(key=lambda a: -a[3])
    # mezcla de canal: últimas 2 semanas contra el resto
    can = _df(f"""SELECT canal, fecha >= (SELECT max(fecha) FROM ({BASE_SQL}) WHERE funcionalidad IS NOT NULL) - INTERVAL '14' DAY AS reciente, count(*) AS n
                  FROM ({BASE_SQL}) WHERE funcionalidad IS NOT NULL AND fecha IS NOT NULL GROUP BY 1, 2""")
    can["n"] = pd.to_numeric(can.n)
    mez = can.pivot_table(index="canal", columns="reciente", values="n", aggfunc="sum").fillna(0)
    mez = (mez / mez.sum()).rename(columns={True: "reciente", False: "antes"})
    if "reciente" in mez and "antes" in mez:
        mez["cambio"] = mez.reciente - mez.antes
        mez = mez.sort_values("cambio", ascending=False)
    # embudo Pagar → Confirmar por canal
    emb = _df(f"""SELECT canal, sum(CASE WHEN funcionalidad = 'Pagar' THEN 1 ELSE 0 END) AS pagar, sum(CASE WHEN funcionalidad = 'Confirmar pago' THEN 1 ELSE 0 END) AS confirmar
                  FROM ({BASE_SQL}) WHERE fecha >= (SELECT max(fecha) FROM ({BASE_SQL}) WHERE funcionalidad IS NOT NULL) - INTERVAL '14' DAY GROUP BY 1""")
    for c in ("pagar", "confirmar"):
        emb[c] = pd.to_numeric(emb[c])
    emb = emb[emb.pagar > 0]
    emb["conv"] = emb.confirmar / emb.pagar
    # perfiles de uso: clientes por la mezcla de funcionalidades que usan
    cli = _df(f"""SELECT cliente, funcionalidad, count(*) AS n FROM ({BASE_SQL}) WHERE cliente IS NOT NULL AND funcionalidad IS NOT NULL GROUP BY 1, 2""")
    perfiles = []
    if not cli.empty:
        cli["n"] = pd.to_numeric(cli.n)
        m = cli.pivot_table(index="cliente", columns="funcionalidad", values="n", aggfunc="sum").fillna(0)
        m = m[m.sum(axis=1) >= 3]
        if len(m) >= 50:
            share = m.div(m.sum(axis=1), axis=0)
            km = KMeans(n_clusters=4, n_init=10, random_state=3).fit(StandardScaler().fit_transform(share))
            share["perfil"] = km.labels_
            for k, g in share.groupby("perfil"):
                prom = g.drop(columns="perfil").mean().sort_values(ascending=False)
                perfiles.append({"clientes": len(g), "rasgos": ", ".join(f"{f} {v * 100:.0f} %" for f, v in prom.head(3).items())})
            perfiles.sort(key=lambda p: -p["clientes"])
    hall = [f"{A.num(total)} usos de funcionalidades analizados desde el lake ({len(piv.columns)} funcionalidades, {len(piv)} días)."]
    if crecen:
        f, v = max(crecen, key=lambda x: x[1])
        hall.append(f"Lo que más crece: «{f}» (+{v * 100:.0f} % entre la primera y la segunda mitad del período).")
    if caen:
        f, v = min(caen, key=lambda x: x[1])
        hall.append(f"Lo que más cae: «{f}» ({v * 100:.0f} %).")
    if anom:
        f, d, n, z = anom[0]
        hall.append(f"Pico fuera de lo normal: «{f}» el {d.strftime('%d-%m')} con {A.num(n)} usos ({z:.0f} veces la variación habitual).")
    if "cambio" in mez:
        c0 = mez.index[0]
        hall.append(f"El canal que más gana participación es «{c0}» (+{mez.cambio.iloc[0] * 100:.1f} puntos en las últimas 2 semanas).")
    if len(emb):
        peor = emb.sort_values("conv").iloc[0]
        hall.append(f"Embudo Pagar → Confirmar: el canal «{peor.canal}» confirma solo el {peor.conv * 100:.0f} % de los pagos iniciados (últimas 2 semanas).")
    if perfiles:
        hall.append("Perfiles de uso: " + "; ".join(f"{p['clientes']} clientes ({p['rasgos']})" for p in perfiles[:3]) + ".")
    resumen, seg = A.granite([{"role": "system", "content": "Eres un analista de negocio de una empresa de pagos. Responde en español de Chile, en 3 frases concretas y con una prioridad clara."},
                              {"role": "user", "content": "Tendencias de uso de la app de pagos:\n" + "\n".join(hall) + "\n¿Qué está cambiando en el comportamiento de los clientes y qué harías primero?"}], 220)
    dec = []
    if crecen:
        dec.append({"texto": f"Dar capacidad y foco comercial a «{max(crecen, key=lambda x: x[1])[0]}», que es la funcionalidad que más crece", "impacto": "acompañar la demanda antes de que se sature", "area": "Producto"})
    if caen:
        dec.append({"texto": f"Investigar por qué cae «{min(caen, key=lambda x: x[1])[0]}» (experiencia, precio o reemplazo por otra funcionalidad)", "impacto": "frenar la pérdida de uso", "area": "Producto"})
    if anom:
        dec.append({"texto": f"Revisar el pico de «{anom[0][0]}» del {anom[0][1].strftime('%d-%m')}: posible incidente, campaña o fraude", "impacto": "detectar el problema antes del reclamo", "area": "Operaciones / Riesgo"})
    if len(emb):
        dec.append({"texto": f"Mejorar la confirmación en «{emb.sort_values('conv').iloc[0].canal}»", "impacto": "más pagos completados con el mismo tráfico", "area": "Canales digitales"})
    return {
        "metrica": {"nombre": "Funcionalidades con tendencia clara", "valor": len(crecen) + len(caen), "fmt": f"{len(crecen)} crecen · {len(caen)} caen", "umbral": 1},
        "apps": ["pagos_uso"], "filas": total, "hallazgos": hall, "decisiones": dec,
        "llm": {"texto": resumen or (hall[1] if len(hall) > 1 else hall[0]), "segundos": seg, "modelo": A.MODELO if seg else None},
        "grafico": {"tipo": "barras", "titulo": "Variación de uso por funcionalidad (%)", "datos": [{"etq": r["f"], "valor": round(r["var"] * 100, 1), "app": "pagos_uso"} for r in filas]},
        "tabla": {"titulo": "Funcionalidades: uso y tendencia", "cols": ["Funcionalidad", "Usos", "Variación", "Tendencia"],
                  "filas": [[r["f"], A.num(r["total"]), f"{r['var'] * 100:+.0f} %", r["estado"]] for r in filas]},
    }


A.SOLUCIONES["tendencias_uso"] = {"nombre": "Tendencias de comportamiento por uso", "tipo": "Análisis de comportamiento + IA generativa",
                                  "tecnica": "Series de tiempo, detección de picos, embudo, K-Means e IBM Granite", "fn": tendencias_uso,
                                  "desc": "Qué funcionalidades crecen, caen o se disparan, cómo cambia el canal y qué perfiles de uso hay, desde los requests reales de la app."}
A.AGENTES["pagos_uso"] = {"nombre": "Agente de uso de la app de pagos", "rol": "Entender cómo usan la app los clientes", "soluciones": ["tendencias_uso"],
                          "preguntas": [("¿Qué funcionalidades se usan más?", f"SELECT funcionalidad, count(*) AS usos FROM ({BASE_SQL}) WHERE funcionalidad IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"),
                                        ("¿Cómo se reparte el uso por canal?", f"SELECT canal, count(*) AS usos FROM ({BASE_SQL}) WHERE funcionalidad IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"),
                                        ("¿A qué hora se usa más la app?", f"SELECT hour(fecha) AS hora, count(*) AS usos FROM ({BASE_SQL}) WHERE funcionalidad IS NOT NULL GROUP BY 1 ORDER BY 1")]}
