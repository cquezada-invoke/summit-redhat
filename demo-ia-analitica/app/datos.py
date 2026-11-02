"""Datos sintéticos de una telco chilena: clientes (con fuga), ventas y recargas. Semilla fija: siempre los mismos datos."""
import datetime as dt

import duckdb
import numpy as np
import pandas as pd

REGIONES = [  # región, peso, comunas
    ("Metropolitana", 40, ["Santiago", "Maipú", "Puente Alto", "Las Condes", "La Florida", "Ñuñoa", "Providencia"]),
    ("Valparaíso", 10, ["Valparaíso", "Viña del Mar", "Quilpué"]),
    ("Biobío", 9, ["Concepción", "Talcahuano", "Los Ángeles"]),
    ("Maule", 6, ["Talca", "Curicó"]),
    ("La Araucanía", 5, ["Temuco", "Villarrica"]),
    ("O'Higgins", 5, ["Rancagua", "San Fernando"]),
    ("Antofagasta", 5, ["Antofagasta", "Calama"]),
    ("Coquimbo", 4, ["La Serena", "Coquimbo"]),
    ("Los Lagos", 5, ["Puerto Montt", "Osorno"]),
    ("Ñuble", 3, ["Chillán"]),
    ("Tarapacá", 2, ["Iquique"]),
    ("Atacama", 2, ["Copiapó"]),
    ("Los Ríos", 2, ["Valdivia"]),
    ("Magallanes", 1, ["Punta Arenas"]),
    ("Arica y Parinacota", 1, ["Arica"]),
]
PLANES = {  # plan: (segmento, cargo mensual CLP, GB)
    "Prepago Full": ("Prepago", 0, 0), "Prepago Plus": ("Prepago", 0, 0),
    "Plan 20 GB": ("Postpago", 9990, 20), "Plan 60 GB": ("Postpago", 14990, 60),
    "Plan Libre": ("Postpago", 19990, 300), "Plan Empresa": ("Postpago", 24990, 150),
}
CANALES_VENTA = ["Tienda", "Web", "App", "Distribuidor", "Televenta"]
CANALES_RECARGA = ["App", "Web", "Supermercado", "Kiosco", "Banco"]
HOY = dt.date(2026, 10, 31)


def generar(n=20000, semilla=7):
    rng = np.random.default_rng(semilla)
    pesos = np.array([r[1] for r in REGIONES], float); pesos /= pesos.sum()
    ir = rng.choice(len(REGIONES), n, p=pesos)
    region = [REGIONES[i][0] for i in ir]
    comuna = [REGIONES[i][2][rng.integers(len(REGIONES[i][2]))] for i in ir]
    planes = list(PLANES)
    plan = rng.choice(planes, n, p=[.22, .16, .2, .2, .14, .08])
    seg = np.array([PLANES[p][0] for p in plan])
    cargo = np.array([PLANES[p][1] for p in plan]).astype(float)
    antig = np.clip(rng.gamma(2.2, 14, n), 1, 180).round().astype(int)
    edad = np.clip(rng.normal(38, 13, n), 18, 85).round().astype(int)
    gb = np.where(seg == "Prepago", rng.gamma(2, 2.5, n), rng.gamma(3, 9, n)).round(1)
    minutos = rng.gamma(2, 110, n).round().astype(int)
    reclamos = rng.poisson(np.where(seg == "Prepago", .4, .7))
    caidas = rng.poisson(1.2 + 1.5 * np.isin(region, ["La Araucanía", "Los Lagos", "Ñuble"]), n)
    gama = rng.choice(["Alta", "Media", "Baja"], n, p=[.25, .45, .3])
    portab = rng.poisson(.15, n)
    tiene_equipo_cuotas = (seg == "Postpago") & (rng.random(n) < .35)
    canal_alta = rng.choice(CANALES_VENTA, n, p=[.35, .2, .15, .2, .1])
    # probabilidad de fuga: reclamos, caídas, poca antigüedad, consultas de portabilidad, sin equipo en cuotas
    z = (-3.2 + .9 * reclamos + .32 * caidas - .022 * antig + 1.8 * portab - 1.4 * tiene_equipo_cuotas
         + .5 * (seg == "Prepago") + .45 * (gama == "Baja") - .006 * gb + .45 * (plan == "Plan 20 GB"))
    p = 1 / (1 + np.exp(-z))
    fuga = rng.random(n) < p
    alta = [HOY - dt.timedelta(days=int(a * 30.4)) for a in antig]
    baja = [HOY - dt.timedelta(days=int(rng.integers(1, 360))) if f else None for f in fuga]
    clientes = pd.DataFrame({
        "cliente_id": np.arange(1, n + 1), "region": region, "comuna": comuna, "segmento": seg, "plan": plan,
        "cargo_mensual": cargo, "antiguedad_meses": antig, "edad": edad, "datos_gb_mes": gb, "minutos_mes": minutos,
        "reclamos_90d": reclamos, "caidas_red_30d": caidas, "consultas_portabilidad": portab, "gama_equipo": gama,
        "equipo_en_cuotas": tiene_equipo_cuotas, "canal_alta": canal_alta, "fecha_alta": alta, "fuga": fuga, "fecha_baja": baja})

    # ventas: 12 meses, estacionalidad (Cyber en noviembre, Navidad en diciembre)
    meses = pd.date_range("2025-11-01", "2026-10-01", freq="MS")
    estac = {11: 1.35, 12: 1.5, 1: .85, 2: .8, 3: 1.0, 4: .95, 5: 1.0, 6: 1.05, 7: 1.0, 8: .95, 9: 1.1, 10: 1.0}
    tipos = {"Alta postpago": 14990, "Alta prepago": 3990, "Portabilidad entrante": 12990, "Renovación de equipo": 289990, "Accesorio": 19990}
    filas = []
    for m in meses:
        k = int(5200 * estac[m.month] * (1 + .01 * (m.month % 5)))
        dias = rng.integers(0, 28, k)
        rr = rng.choice(len(REGIONES), k, p=pesos)
        can = rng.choice(CANALES_VENTA, k, p=[.34, .22, .17, .19, .08])
        tip = rng.choice(list(tipos), k, p=[.28, .3, .14, .13, .15])
        monto = np.array([tipos[t] for t in tip]) * rng.uniform(.8, 1.35, k) * np.where(can == "Web", .95, 1)
        for i in range(k):
            filas.append((m.date() + dt.timedelta(days=int(dias[i])), REGIONES[rr[i]][0], can[i], tip[i], int(monto[i]),
                          int(rng.integers(1, n + 1))))
    ventas = pd.DataFrame(filas, columns=["fecha", "region", "canal", "tipo", "monto", "cliente_id"])
    ventas.insert(0, "venta_id", np.arange(1, len(ventas) + 1))

    # recargas de prepago
    pre = clientes[clientes.segmento == "Prepago"]
    filas = []
    for _, c in pre.iterrows():
        k = rng.poisson(9 if not c.fuga else 4)
        for _ in range(k):
            f = HOY - dt.timedelta(days=int(rng.integers(0, 365)))
            if c.fecha_baja and f > c.fecha_baja:
                continue
            filas.append((f, int(c.cliente_id), c.region, rng.choice(CANALES_RECARGA, p=[.38, .12, .25, .17, .08]),
                          int(rng.choice([1000, 2000, 3000, 5000, 10000], p=[.15, .3, .25, .2, .1]))))
    recargas = pd.DataFrame(filas, columns=["fecha", "cliente_id", "region", "canal", "monto"])
    recargas.insert(0, "recarga_id", np.arange(1, len(recargas) + 1))
    return clientes, ventas, recargas


def base():
    clientes, ventas, recargas = generar()
    con = duckdb.connect(":memory:")
    for nom, df in (("clientes", clientes), ("ventas", ventas), ("recargas", recargas)):
        for c in df.columns:   # numpy str_ → str y fechas a datetime64 (DuckDB no reconoce los tipos de numpy en columnas object)
            if c.startswith("fecha"):
                df[c] = pd.to_datetime(df[c])
            elif df[c].dtype == object:
                df[c] = [None if x is None else f"{x}" for x in df[c]]
        con.register("_df", df)
        con.execute(f"CREATE TABLE {nom} AS SELECT * FROM _df")
        con.unregister("_df")
    return con, clientes
