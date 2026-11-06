"""Demo de analítica con IA en OpenShift: preguntas en español sobre datos de una telco y predicción de fuga de clientes."""
import os
import threading
import time

import pandas as pd
from flask import Flask, jsonify, render_template, request

from datos import PLANES, REGIONES, base
from ia import CONOCIDAS, MODELO, descargar, estado, precalentar, preguntar
from prediccion import Modelo

app = Flask(__name__)
t0 = time.time()
CON, CLIENTES = base()
MOD = Modelo(CLIENTES)
CLIENTES["riesgo_fuga"] = MOD.probabilidad(CLIENTES)
CLIENTES.loc[CLIENTES.fuga, "riesgo_fuga"] = None
CON.register("_r", CLIENTES[["cliente_id", "riesgo_fuga"]])
CON.execute("ALTER TABLE clientes ADD COLUMN riesgo_fuga DOUBLE")
CON.execute("UPDATE clientes SET riesgo_fuga = _r.riesgo_fuga FROM _r WHERE clientes.cliente_id = _r.cliente_id")
CON.unregister("_r")
ARRANQUE = round(time.time() - t0, 1)
LOCK = threading.Lock()
threading.Thread(target=descargar, daemon=True).start()

SUGERIDAS = list(CONOCIDAS)
threading.Thread(target=precalentar, args=(CON, LOCK), daemon=True).start()


def _conteos():
    return {t: CON.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("clientes", "ventas", "recargas")}


@app.get("/")
def inicio():
    return render_template("index.html", modelo=MODELO, sugeridas=SUGERIDAS, regiones=[r[0] for r in REGIONES], planes=list(PLANES),
                           pod=os.environ.get("HOSTNAME", "local"), arranque=ARRANQUE, conteos=_conteos())


@app.get("/api/estado")
def api_estado():
    return jsonify(estado())


@app.post("/api/preguntar")
def api_preguntar():
    p = (request.json or {}).get("pregunta", "").strip()[:400]
    if not p:
        return jsonify({"ok": False, "error": "Escribe una pregunta"}), 400
    with LOCK:   # un solo modelo en CPU: las preguntas se atienden de a una
        return jsonify(preguntar(CON, p))


@app.get("/api/prediccion")
def api_prediccion():
    reg, seg = request.args.get("region", ""), request.args.get("segmento", "")
    act = CLIENTES[~CLIENTES.fuga]
    if reg:
        act = act[act.region == reg]
    if seg:
        act = act[act.segmento == seg]
    top = act.sort_values("riesgo_fuga", ascending=False).head(25)
    alto = act[act.riesgo_fuga >= .3]
    por_region = (CLIENTES[~CLIENTES.fuga].groupby("region").riesgo_fuga.apply(lambda s: round(float((s >= .3).mean()) * 100, 1))
                  .sort_values(ascending=False).head(10))
    return jsonify({
        "auc": MOD.auc, "tasa": MOD.tasa, "n": MOD.n, "activos": int(len(act)), "alto": int(len(alto)),
        "ingreso_riesgo": int(alto.cargo_mensual.sum()), "importancia": MOD.importancia,
        "por_region": [[k, v] for k, v in por_region.items()],
        "top": [{"id": int(r["cliente_id"]), "region": r["region"], "comuna": r["comuna"], "plan": r["plan"], "antig": int(r["antiguedad_meses"]),
                 "riesgo": round(float(r["riesgo_fuga"]) * 100, 1), "factores": MOD.explicar(r)} for r in top.to_dict("records")]})


@app.post("/api/simular")
def api_simular():
    d = request.json or {}
    plan = d.get("plan", "Plan 20 GB")
    fila = {"cargo_mensual": PLANES[plan][1], "antiguedad_meses": int(d.get("antiguedad", 12)), "edad": int(d.get("edad", 35)),
            "datos_gb_mes": float(d.get("gb", 10)), "minutos_mes": int(d.get("minutos", 200)), "reclamos_90d": int(d.get("reclamos", 0)),
            "caidas_red_30d": int(d.get("caidas", 0)), "consultas_portabilidad": int(d.get("portabilidad", 0)),
            "equipo_en_cuotas": bool(d.get("cuotas")), "segmento": PLANES[plan][0], "plan": plan, "gama_equipo": d.get("gama", "Media"),
            "region": d.get("region", "Metropolitana"), "canal_alta": d.get("canal", "Tienda")}
    p = float(MOD.probabilidad(pd.DataFrame([fila]))[0])
    return jsonify({"riesgo": round(p * 100, 1), "factores": MOD.explicar(fila)})


@app.get("/salud")
def salud():
    return "ok"
