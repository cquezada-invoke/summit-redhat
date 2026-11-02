"""Modelo de fuga de clientes (gradient boosting) entrenado al iniciar, con los datos históricos."""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

NUM = ["cargo_mensual", "antiguedad_meses", "edad", "datos_gb_mes", "minutos_mes", "reclamos_90d", "caidas_red_30d",
       "consultas_portabilidad"]
CAT = ["segmento", "plan", "gama_equipo", "region", "canal_alta"]
ETIQ = {"cargo_mensual": "Cargo mensual", "antiguedad_meses": "Antigüedad", "edad": "Edad", "datos_gb_mes": "Datos usados (GB/mes)",
        "minutos_mes": "Minutos al mes", "reclamos_90d": "Reclamos (90 días)", "caidas_red_30d": "Caídas de red (30 días)",
        "consultas_portabilidad": "Consultas de portabilidad", "segmento": "Segmento", "plan": "Plan", "gama_equipo": "Gama del equipo",
        "region": "Región", "canal_alta": "Canal de alta", "equipo_en_cuotas": "Equipo en cuotas"}


def _x(df, cats=None):
    x = df[NUM].astype(float).copy()
    x["equipo_en_cuotas"] = df["equipo_en_cuotas"].astype(int)
    for c in CAT:
        x[c] = pd.Categorical(df[c], categories=(cats or {}).get(c))
    return x


class Modelo:
    def __init__(self, clientes):
        self.cats = {c: sorted(clientes[c].unique()) for c in CAT}
        x, y = _x(clientes, self.cats), clientes["fuga"].astype(int)
        xe, xp, ye, yp = train_test_split(x, y, test_size=.25, random_state=1, stratify=y)
        self.m = HistGradientBoostingClassifier(max_iter=200, learning_rate=.06, categorical_features="from_dtype", random_state=1)
        self.m.fit(xe, ye)
        self.auc = round(float(roc_auc_score(yp, self.m.predict_proba(xp)[:, 1])), 3)
        imp = permutation_importance(self.m, xp.iloc[:2500], yp.iloc[:2500], n_repeats=3, random_state=1, scoring="roc_auc")
        self.importancia = sorted(((ETIQ[c], round(float(v), 4)) for c, v in zip(x.columns, imp.importances_mean)), key=lambda t: -t[1])[:8]
        self.tasa = round(float(y.mean()) * 100, 1)
        self.n = len(clientes)

    def probabilidad(self, df):
        return self.m.predict_proba(_x(df, self.cats))[:, 1]

    def explicar(self, fila):
        """Factores que suben el riesgo de un cliente (reglas legibles sobre las variables del modelo)."""
        f = []
        if fila["reclamos_90d"] >= 2: f.append(f"{int(fila['reclamos_90d'])} reclamos en 90 días")
        if fila["consultas_portabilidad"] >= 1: f.append("consultó por portabilidad")
        if fila["caidas_red_30d"] >= 3: f.append(f"{int(fila['caidas_red_30d'])} caídas de red en el mes")
        if fila["antiguedad_meses"] <= 6: f.append("cliente nuevo (≤ 6 meses)")
        if not fila["equipo_en_cuotas"] and fila["segmento"] == "Postpago": f.append("sin equipo en cuotas")
        if fila["gama_equipo"] == "Baja": f.append("equipo de gama baja")
        return f or ["sin factores destacados"]
