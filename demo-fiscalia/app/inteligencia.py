"""Aplicación 2 · Inteligencia y predicción sobre el data lake.
Lee todas las denuncias del lake con Trino (histórico + las que se registran en la aplicación de registro), entrena dos modelos
(probabilidad de continuidad de una carpeta: judicialización o acuerdo reparatorio, y días hasta su cierre), puntúa las carpetas abiertas, pronostica la carga por alcaldía
y responde preguntas en español generando SQL de Trino con Granite (local). Datos sintéticos."""
import json
import re
import threading
import time

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template_string, request
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, roc_auc_score
from sklearn.model_selection import train_test_split

import os

import requests

from comun import ESTILO, ahora, asegurar_lake, chat, enlace_complemento, faltantes, modelo_listo, s3_get_json, s3_json, solo_lectura, trino
from datos import ALCALDIAS, CANALES, DELITOS

app = Flask(__name__)
LOCK, LOCK_IA = threading.Lock(), threading.Lock()
NUM = ["evidencias", "carga_mp"]
BOOL = ["hay_detenido", "video", "testigos", "identifica_responsable"]
CAT = ["delito", "alcaldia", "canal", "prioridad"]
ETIQ = {"evidencias": "Evidencias", "carga_mp": "Carga del agente del MP", "hay_detenido": "Detenido", "video": "Video", "testigos": "Testigos",
        "identifica_responsable": "Víctima identifica al responsable", "delito": "Delito", "alcaldia": "Alcaldía", "canal": "Canal", "prioridad": "Prioridad"}
M = {"listo": False, "error": "", "t": 0}
CONTINUA = ["Judicializada", "Acuerdo reparatorio"]
UMBRAL_FOCO = .35   # probabilidad de continuidad desde la que conviene focalizar al fiscal
CACHE = {}


def _x(df, cats):
    x = df[NUM].astype(float).copy()
    for b in BOOL:
        x[b] = df[b].astype(bool).astype(int)
    for c in CAT:
        x[c] = pd.Categorical(df[c], categories=cats[c])
    return x


def entrenar():
    t0 = time.time()
    df = trino("""SELECT folio, fecha_denuncia, alcaldia, delito, categoria, canal, unidad, mp, prioridad, hay_detenido, evidencias, video, testigos,
                         identifica_responsable, carga_mp, estado, dias_resolucion, origen FROM lake.fiscalia.denuncias""")
    df["fecha_denuncia"] = pd.to_datetime(df["fecha_denuncia"])
    cats = {c: sorted(df[c].dropna().unique()) for c in CAT}
    cerr = df[df.estado != "En trámite"].copy()
    x, y = _x(cerr, cats), cerr.estado.isin(CONTINUA).astype(int)   # continuidad: la carpeta sigue (se judicializa o termina en acuerdo)
    xe, xp, ye, yp = train_test_split(x, y, test_size=.25, random_state=1, stratify=y)
    clf = HistGradientBoostingClassifier(max_iter=200, learning_rate=.07, categorical_features="from_dtype", random_state=1).fit(xe, ye)
    auc = roc_auc_score(yp, clf.predict_proba(xp)[:, 1])
    imp = permutation_importance(clf, xp.iloc[:3000], yp.iloc[:3000], n_repeats=3, random_state=1, scoring="roc_auc")
    dias = cerr.dias_resolucion.astype(float)
    reg = HistGradientBoostingRegressor(max_iter=200, learning_rate=.07, categorical_features="from_dtype", random_state=1)
    xe2, xp2, ye2, yp2 = train_test_split(x, dias, test_size=.25, random_state=1)
    reg.fit(xe2, ye2)
    mae = mean_absolute_error(yp2, reg.predict(xp2))
    ab = df[df.estado == "En trámite"].copy()
    xa = _x(ab, cats)
    ab["prob"] = clf.predict_proba(xa)[:, 1]
    ab["dias_est"] = reg.predict(xa).clip(1)
    ab["antig"] = (pd.Timestamp(ahora()) - ab.fecha_denuncia).dt.days
    # pronóstico de carga: denuncias por semana y alcaldía (promedio de las últimas 8 semanas con su tendencia)
    sem = df.set_index("fecha_denuncia").groupby("alcaldia").resample("W-MON").size().unstack(0).fillna(0).iloc[-13:-1]
    ult8 = sem.iloc[-8:]
    tend = (ult8.iloc[-4:].mean() - ult8.iloc[:4].mean()) / 4
    prox = (ult8.mean() + tend * 6).clip(0).round()
    tipo = cerr.groupby("delito").estado.apply(lambda e: round(100 * float(e.isin(CONTINUA).mean()), 1)).sort_values(ascending=False)
    abt = ab.groupby("delito").agg(abiertas=("folio", "size"), prob=("prob", "mean"))
    por_tipo = [{"delito": d, "continuidad": v, "abiertas": int(abt.abiertas.get(d, 0)), "prob": round(100 * float(abt.prob.get(d, 0)), 1),
                 "foco": "Focalizar fiscal" if v >= 100 * UMBRAL_FOCO else ("Evaluar caso a caso" if v >= 20 else "Mecanismos alternativos / archivo")} for d, v in tipo.items()]
    with LOCK:
        M.update(listo=True, por_tipo=por_tipo, df=df, ab=ab, clf=clf, reg=reg, cats=cats, auc=round(float(auc), 3), mae=round(float(mae)), n=len(df), n_cerr=len(cerr),
                 nuevas=int((df.origen == "aplicacion").sum()), importancia=sorted(((ETIQ[c], round(float(v), 3)) for c, v in zip(x.columns, imp.importances_mean)), key=lambda t: -t[1])[:8],
                 semanas=[str(s.date()) for s in sem.index], serie=sem.sum(axis=1).astype(int).tolist(), prox=prox.sort_values(ascending=False).head(8).astype(int).to_dict(),
                 actual=ult8.iloc[-4:].mean().round().astype(int).to_dict(), seg=round(time.time() - t0, 1), t=time.time(), error="")


def _ciclo():
    for _ in range(60):
        try:
            asegurar_lake()
            break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    while True:
        try:
            entrenar()
        except Exception as e:  # noqa: BLE001
            M["error"] = str(e)[:300]
        EVENTO.wait(60)   # reentrena cada minuto, o apenas la aplicación de registro avisa que llegó una denuncia
        EVENTO.clear()


EVENTO = threading.Event()
VIVO = {"t": 0, "n": None, "nuevas": None}


def conteo_vivo():
    """Cuántas denuncias hay en el lake en este momento (consulta liviana, cada 5 s como máximo)."""
    if time.time() - VIVO["t"] > 5:
        try:
            d = trino("SELECT COUNT(*) AS n, COUNT_IF(origen = 'aplicacion') AS nuevas FROM lake.fiscalia.denuncias")
            VIVO.update(t=time.time(), n=int(d.n[0]), nuevas=int(d.nuevas[0]))
        except Exception:  # noqa: BLE001
            pass
    return VIVO


@app.post("/api/refrescar")
def refrescar():
    EVENTO.set()
    return jsonify({"ok": True})


threading.Thread(target=_ciclo, daemon=True).start()


def factores(f):
    out = []
    if f.get("hay_detenido"):
        out.append("hay detenido (sube mucho la probabilidad)")
    if int(f.get("evidencias") or 0) >= 3:
        out.append(f"{f['evidencias']} evidencias")
    if f.get("video"):
        out.append("hay video")
    if f.get("testigos"):
        out.append("hay testigos")
    if f.get("identifica_responsable"):
        out.append("la víctima identifica al responsable")
    if int(f.get("carga_mp") or 0) >= 100:
        out.append(f"agente con alta carga ({f['carga_mp']} carpetas): demora")
    if not out:
        out.append("sin detenido, testigos ni video: poca continuidad esperable")
    return out


def coherencia(n, adj):
    """Puntaje 0–100 de coherencia de una denuncia registrada en la aplicación, con sus motivos."""
    pts, mot = 0, []
    if n.get("delito_ia") and n["delito_ia"] == n["delito"]:
        pts += 25; mot.append("la IA confirma el tipo de denuncia")
    elif n.get("delito_ia"):
        pts += 5; mot.append(f"la IA lee «{n['delito_ia']}», no «{n['delito']}»")
    else:
        pts += 10; mot.append("sin lectura de la IA")
    co, nc, ni = sum(a == "coherente" for a in adj), sum(a == "no coherente" for a in adj), len(adj)
    if ni:
        pts += round(25 * co / ni) - (10 if nc else 0)
        mot.append(f"{co} de {ni} imágenes coherentes con el relato" + (f", {nc} no coherente" if nc else ""))
    else:
        pts += 8; mot.append("sin imágenes")
    comp = sum(bool(n.get(c)) for c in ("denunciante", "fecha_hechos", "colonia")) + (len(n.get("relato") or "") >= 120)
    pts += round(25 * comp / 4)
    if comp < 4:
        mot.append("formulario incompleto (" + ", ".join(c for c, v in (("denunciante", n.get("denunciante")), ("fecha de los hechos", n.get("fecha_hechos")),
                                                                  ("colonia", n.get("colonia")), ("relato detallado", len(n.get("relato") or "") >= 120)) if not v) + ")")
    ev = 10 * bool(n.get("hay_detenido")) + 5 * bool(n.get("testigos")) + 5 * bool(n.get("video")) + min(5, int(n.get("evidencias") or 0))
    pts += min(25, ev)
    pts = max(0, min(100, pts))
    return pts, ("Coherente: enviar al siguiente paso" if pts >= 70 else ("Revisar" if pts >= 45 else "Requiere complementar")), mot


def bandeja():
    n = trino("""SELECT n.*, COALESCE(e.etapa, 'Registrada') AS etapa FROM lake.fiscalia.denuncias_nuevas n
                 LEFT JOIN (SELECT folio, max_by(detalle, fecha) AS etapa FROM lake.fiscalia.eventos WHERE evento = 'etapa' GROUP BY folio) e ON e.folio = n.folio
                 ORDER BY n.fecha_denuncia DESC LIMIT 60""")
    a = trino("SELECT folio, archivo, relacion, descripcion FROM lake.fiscalia.adjuntos_analisis")
    out = []
    for r in n.to_dict("records"):
        adj = a[a.folio == r["folio"]] if len(a) else a
        p, sem, mot = coherencia(r, adj.relacion.tolist() if len(adj) else [])
        cont = {}
        try:
            with app.test_request_context(json=r):
                cont = puntuar().get_json() or {}
        except Exception:  # noqa: BLE001
            pass
        out.append(dict(r, puntaje=p, semaforo=sem, motivos=mot, adj=adj.to_dict("records") if len(adj) else [], cont=cont))
    return out


# ─────────── Aviso por correo para complementar: lo envía AAP (plantilla + notificador de correo) ───────────
AAP = os.environ.get("AAP_URL", "").rstrip("/") + "/api/controller/v2"
AAP_TOKEN = os.environ.get("AAP_TOKEN", "")
JT_AVISO = os.environ.get("AAP_PLANTILLA", "Fiscalía · Aviso de complemento")
NOTIF_AVISO = os.environ.get("AAP_NOTIFICADOR", "Correo · complemento Fiscalía")
AVISO_LOCK = threading.Lock()


def _aap(metodo, ruta, datos=None):
    r = requests.request(metodo, AAP + ruta, json=datos, headers={"Authorization": f"Bearer {AAP_TOKEN}"}, verify=False, timeout=30)
    r.raise_for_status()
    return r.json() if r.content else {}


def _evento(folio, evento, detalle):
    f = ahora()
    s3_json(f"eventos/{f:%Y%m%d%H%M%S%f}-{folio}-{evento}.json", {"fecha": f.strftime("%Y-%m-%d %H:%M:%S"), "folio": folio, "evento": evento,
                                                                 "estado": "", "detalle": detalle, "usuario": "evaluación"})


def correo_complemento(folio):
    """Arma el correo con lo que falta y el enlace de vuelta al formulario, y lo envía con AAP. El resultado queda como evento en el lake."""
    try:
        n = s3_get_json(f"denuncias_nuevas/{folio}.json")
        a = trino(f"SELECT relacion FROM lake.fiscalia.adjuntos_analisis WHERE folio = '{folio}'")
        falta = faltantes(n, a.relacion.tolist() if len(a) else [])
        if not falta:
            falta = ["confirmar los datos y agregar cualquier antecedente adicional que tenga"]
        enlace = enlace_complemento(folio)
        asunto = f"Fiscalía CDMX · Complementa tu denuncia {folio}"
        cuerpo = (f"Hola{(' ' + n['denunciante'].split()[0]) if n.get('denunciante') else ''},\n\n"
                  f"Recibimos tu denuncia {folio} ({n.get('delito')}, {n.get('alcaldia')}), registrada el {n.get('fecha_denuncia')}.\n"
                  "Para que el Ministerio Público pueda continuar, necesitamos que la complementes con:\n\n"
                  + "".join(f"  - {x}\n" for x in falta)
                  + f"\nComplétala aquí (el enlace es solo para esta denuncia):\n{enlace}\n\n"
                  "Fiscalía General de Justicia de la Ciudad de México · Atención a denunciantes\n"
                  "(Demostración de Invoke con datos sintéticos)\n")
        if not (AAP_TOKEN and os.environ.get("AAP_URL")):
            raise RuntimeError("AAP no configurado")
        with AVISO_LOCK:   # el notificador tiene un solo mensaje: un aviso a la vez
            nid = _aap("GET", "/notification_templates/?name=" + requests.utils.quote(NOTIF_AVISO))["results"][0]["id"]
            _aap("PATCH", f"/notification_templates/{nid}/", {"messages": {"started": None, "error": None, "workflow_approval": None,
                                                                              "success": {"message": asunto, "body": cuerpo}}})
            jt = _aap("GET", "/job_templates/?name=" + requests.utils.quote(JT_AVISO))["results"][0]["id"]
            job = _aap("POST", f"/job_templates/{jt}/launch/", {"extra_vars": {"folio": folio, "faltantes": falta, "enlace": enlace}})["id"]
            for _ in range(90):
                st = _aap("GET", f"/jobs/{job}/")["status"]
                if st in ("successful", "failed", "error", "canceled"):
                    break
                time.sleep(2)
            env = ""
            for _ in range(40):   # el notificador corre después del trabajo (Gmail puede tardar ~1 min)
                ns = _aap("GET", f"/jobs/{job}/notifications/").get("results") or []
                if ns and ns[0]["status"] != "pending":
                    env = ns[0]["status"]
                    break
                time.sleep(3)
        ok = st == "successful" and env in ("successful", "")
        _evento(folio, "etapa", ("Complemento solicitado por correo" if ok else "Error al enviar el correo de complemento")
                + f" (AAP job {job}) · falta: " + "; ".join(falta))
    except Exception as e:  # noqa: BLE001
        _evento(folio, "etapa", f"No se pudo enviar el correo de complemento: {str(e)[:160]}")


@app.post("/api/etapa")
def etapa():
    d = request.json or {}
    if d.get("etapa") not in ("Enviada al siguiente paso (fiscal asignado)", "Solicitar complemento a la persona denunciante", "Revisión de coherencia"):
        return jsonify({"ok": False}), 400
    if not re.match(r"^CI-[A-Z]{3}-\d{2}-A\d{6}$", d.get("folio") or ""):
        return jsonify({"ok": False}), 400
    from comun import s3_json
    f = ahora()
    s3_json(f"eventos/{f:%Y%m%d%H%M%S}-{d['folio']}-etapa.json", {"fecha": f.strftime("%Y-%m-%d %H:%M:%S"), "folio": d["folio"], "evento": "etapa",
                                                                "estado": "", "detalle": d["etapa"], "usuario": "evaluación"})
    if d["etapa"].startswith("Solicitar complemento"):
        threading.Thread(target=correo_complemento, args=(d["folio"],), daemon=True).start()
        return jsonify({"ok": True, "aviso": "Preparando el correo con lo que falta; lo envía AAP en unos segundos."})
    return jsonify({"ok": True})


@app.post("/api/puntuar")
def puntuar():
    if not M["listo"]:
        return jsonify({}), 503
    f = request.json or {}
    fila = pd.DataFrame([{**{c: f.get(c) for c in CAT}, **{n: int(f.get(n) or 0) for n in NUM}, **{b: bool(f.get(b)) for b in BOOL}}])
    with LOCK:
        x = _x(fila, M["cats"])
        p, d = float(M["clf"].predict_proba(x)[0, 1]), float(M["reg"].predict(x)[0])
    return jsonify({"probabilidad": round(p * 100, 1), "dias": int(round(max(d, 1))), "factores": factores(f)})


ESQUEMA = """Vista de Trino lake.fiscalia.denuncias (una fila por carpeta de investigación):
folio varchar, fecha_denuncia timestamp, alcaldia varchar, delito varchar, categoria varchar ('Patrimonio','Familia','Personas','Seguridad pública'),
canal varchar ('Presencial en agencia del MP','Denuncia digital','MP Virtual (teléfono)','Puesta a disposición con detenido'), unidad varchar,
mp varchar (agente del Ministerio Público), prioridad varchar ('Alta','Media','Baja'), hay_detenido boolean, evidencias integer, video boolean,
testigos boolean, identifica_responsable boolean, carga_mp integer (carpetas abiertas del agente al recibirla),
estado varchar ('En trámite','Judicializada','Acuerdo reparatorio','Archivo temporal','No ejercicio de la acción penal'), fecha_cierre timestamp,
dias_resolucion integer, origen varchar ('historico' o 'aplicacion': registrada en la aplicación de registro).
Tabla lake.fiscalia.eventos(fecha varchar, folio varchar, evento varchar, estado varchar, detalle varchar).
Delitos: """ + ", ".join(DELITOS) + ". Alcaldías: " + ", ".join(a[0] for a in ALCALDIAS) + ". Datos de noviembre 2025 a noviembre 2026."

CONOCIDAS = {
    "¿Qué delitos tardan más en resolverse?":
        "SELECT delito, ROUND(AVG(dias_resolucion)) AS dias_promedio, COUNT(*) AS carpetas_cerradas FROM lake.fiscalia.denuncias WHERE estado <> 'En trámite' GROUP BY delito ORDER BY dias_promedio DESC",
    "Tasa de judicialización por alcaldía":
        "SELECT alcaldia, ROUND(100.0 * AVG(CASE WHEN estado = 'Judicializada' THEN 1 ELSE 0 END), 1) AS judicializacion_pct, COUNT(*) AS cerradas FROM lake.fiscalia.denuncias WHERE estado <> 'En trámite' GROUP BY alcaldia ORDER BY judicializacion_pct DESC",
    "¿Cuántas carpetas en trámite tiene cada unidad?":
        "SELECT unidad, COUNT(*) AS en_tramite FROM lake.fiscalia.denuncias WHERE estado = 'En trámite' GROUP BY unidad ORDER BY en_tramite DESC LIMIT 20",
    "Denuncias por mes de violencia familiar":
        "SELECT date_trunc('month', fecha_denuncia) AS mes, COUNT(*) AS denuncias FROM lake.fiscalia.denuncias WHERE delito = 'Violencia familiar' GROUP BY 1 ORDER BY 1",
    "¿Qué efecto tiene tener video en la judicialización?":
        "SELECT video, ROUND(100.0 * AVG(CASE WHEN estado = 'Judicializada' THEN 1 ELSE 0 END), 1) AS judicializacion_pct, COUNT(*) AS cerradas FROM lake.fiscalia.denuncias WHERE estado <> 'En trámite' GROUP BY video",
    "Agentes del MP con más carpetas abiertas":
        "SELECT mp, unidad, COUNT(*) AS abiertas FROM lake.fiscalia.denuncias WHERE estado = 'En trámite' GROUP BY mp, unidad ORDER BY abiertas DESC LIMIT 15",
    "Denuncias registradas hoy en la aplicación":
        "SELECT folio, fecha_denuncia, delito, alcaldia, prioridad, estado FROM lake.fiscalia.denuncias WHERE origen = 'aplicacion' ORDER BY fecha_denuncia DESC LIMIT 20",
}


def _arreglar_group_by(sql):
    """El modelo pequeño a veces pone un agregado en el GROUP BY: se agrupa por posición de las columnas que no son agregados."""
    m = re.search(r"^\s*SELECT\s+(.*?)\s+FROM\s", sql, re.S | re.I)
    if not m:
        return None
    partes, prof, act = [], 0, ""
    for ch in m.group(1):
        prof += (ch == "(") - (ch == ")")
        if ch == "," and prof == 0:
            partes.append(act); act = ""
        else:
            act += ch
    partes.append(act)
    pos = [str(i + 1) for i, p in enumerate(partes) if not re.search(r"\b(COUNT|SUM|AVG|MIN|MAX|ROUND\s*\(\s*(AVG|SUM|100))\s*\(", p, re.I)]
    if not pos:
        return re.sub(r"\bGROUP\s+BY\b.*?(?=\bORDER\s+BY\b|\bLIMIT\b|\bHAVING\b|$)", "", sql, flags=re.S | re.I)
    return re.sub(r"\bGROUP\s+BY\b.*?(?=\bORDER\s+BY\b|\bLIMIT\b|\bHAVING\b|$)", "GROUP BY " + ", ".join(pos) + " ", sql, flags=re.S | re.I)


def _sql_de(t):
    m = re.search(r"```(?:sql)?\s*(.*?)```", t, re.S | re.I)
    s = (m.group(1) if m else t).strip()
    m = re.search(r"\b(WITH|SELECT)\b.*", s, re.S | re.I)
    return (m.group(0) if m else s).strip().rstrip(";").split(";")[0]


@app.post("/api/preguntar")
def preguntar():
    p = (request.json or {}).get("pregunta", "").strip()[:400]
    if not p:
        return jsonify({"ok": False, "error": "Escribe una pregunta"}), 400
    k = re.sub(r"[^a-z0-9áéíóúñ ]", "", p.lower()).strip()
    if k in CACHE and "aplicación" not in p:
        return jsonify(dict(CACHE[k], cache=True))
    t0 = time.time()
    sis = ("Eres analista de datos de una fiscalía. Conviertes preguntas en español en UNA consulta SQL de Trino, solo lectura (SELECT), "
           "usando solo estas tablas y columnas. Alias en español, porcentajes con 1 decimal, máximo 20 filas. Responde solo el SQL.\n"
           "Reglas de Trino: en GROUP BY van solo columnas (nunca COUNT, SUM, AVG, MIN ni MAX); para ordenar por un agregado usa ORDER BY con su alias; "
           "días entre fechas con date_diff('day', a, b); mes con date_trunc('month', fecha_denuncia); texto entre comillas simples; "
           "estado cerrado es estado <> 'En trámite'; continuidad es estado IN ('Judicializada', 'Acuerdo reparatorio').\n\n" + ESQUEMA)
    msgs = [{"role": "system", "content": sis}]
    for q, s in CONOCIDAS.items():
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": s}]
    msgs.append({"role": "user", "content": p})
    conocido = {re.sub(r"[^a-z0-9áéíóúñ ]", "", q.lower()).strip(): s for q, s in CONOCIDAS.items()}
    sql, df, error = "", None, None
    with LOCK_IA:
        for i in range(4):
            sql = conocido[k] if (i == 0 and k in conocido) else _sql_de(chat(msgs))
            if not solo_lectura(sql):
                error = "La consulta generada no es de solo lectura"
            else:
                try:
                    # Trino no conserva el ORDER BY de una subconsulta: el límite va al final de la consulta misma
                    df = trino(sql if re.search(r"\blimit\s+\d+\s*$", sql, re.I) else f"{sql}\nLIMIT 200")
                    error = None
                    break
                except Exception as e:  # noqa: BLE001
                    error = str(e)[:300]
                    if "GROUP BY clause cannot contain" in error and (sql2 := _arreglar_group_by(sql)):
                        try:
                            df = trino(sql2 if re.search(r"\blimit\s+\d+\s*$", sql2, re.I) else f"{sql2}\nLIMIT 200")
                            sql, error = sql2, None
                            break
                        except Exception as e2:  # noqa: BLE001
                            error = str(e2)[:300]
            msgs += [{"role": "assistant", "content": sql}, {"role": "user", "content": f"Trino respondió: {error}. Corrige y responde solo el SQL."}]
        if df is None:
            return jsonify({"ok": False, "sql": sql, "error": error, "seg": round(time.time() - t0, 1)})
        try:
            resumen = chat([{"role": "system", "content": "Eres analista de una fiscalía. En español, 2 frases: las cifras clave y una recomendación operativa. No inventes datos."},
                            {"role": "user", "content": f"Pregunta: {p}\nResultado:\n{df.head(15).to_csv(index=False)}"}], max_tokens=130).strip()
        except Exception as e:  # noqa: BLE001
            resumen = f"(sin resumen: {e})"
    r = {"ok": True, "sql": sql, "columnas": list(df.columns), "filas": json.loads(df.to_json(orient="values", date_format="iso")),
         "resumen": resumen, "seg": round(time.time() - t0, 1)}
    if not resumen.startswith("(sin"):
        CACHE[k] = r
    return jsonify(r)


def _precalentar():
    for _ in range(120):
        if modelo_listo() and M["listo"]:
            break
        time.sleep(5)
    with app.test_client() as c:
        for q in CONOCIDAS:
            if "aplicación" not in q:
                c.post("/api/preguntar", json={"pregunta": q})


threading.Thread(target=_precalentar, daemon=True).start()


@app.get("/")
def inicio():
    if not M["listo"]:
        return render_template_string("<!DOCTYPE html><meta http-equiv=refresh content=5>{{ e|safe }}<main><h2>Entrenando con los datos del lake…</h2><p>{{ err }}</p></main>",
                                      e=ESTILO, err=M["error"])
    with LOCK:
        ab = M["ab"]
        prioridad = ab[(ab.prob >= .45) & (ab.antig > 30)].sort_values("prob", ascending=False).head(12)
        archivo = ab[(ab.prob < .08) & (ab.antig > 60)].sort_values("antig", ascending=False).head(12)
        rezago = ab[ab.antig > ab.dias_est * 1.5]
        carga = ab.groupby("mp").size().sort_values(ascending=False)
        ctx = dict(estilo=ESTILO, M=M, prioridad=prioridad.to_dict("records"), archivo=archivo.to_dict("records"), n_ab=len(ab), n_rezago=len(rezago),
                   n_prior=int(((ab.prob >= .45) & (ab.antig > 30)).sum()), n_arch=int(((ab.prob < .08) & (ab.antig > 60)).sum()),
                   carga=carga.head(8).to_dict(), carga_media=round(float(carga.mean())), conocidas=list(CONOCIDAS),
                   delitos=list(DELITOS), alcaldias=[a[0] for a in ALCALDIAS], canales=[c[0] for c in CANALES],
                   actualizado=time.strftime("%H:%M:%S", time.localtime(M["t"] - 3 * 3600)), listo=modelo_listo())
    ctx["vivo"] = conteo_vivo()
    try:
        ctx["bandeja"] = bandeja()
    except Exception as e:  # noqa: BLE001
        ctx["bandeja"], ctx["err_bandeja"] = [], str(e)[:200]
    return render_template_string(PAGINA, **ctx)


@app.get("/salud")
def salud():
    return "ok"


PAGINA = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Denuncias · inteligencia y predicción</title>{{ estilo|safe }}</head><body>
<header><b>Denuncias · inteligencia y predicción</b><span class="sub">Sobre el data lake (Trino + S3) · demo de Invoke en Red Hat OpenShift · datos sintéticos</span></header><main>
<div class="kpis">
<div class="kpi"><b>{{ '{:,}'.format(vivo.n or M.n).replace(',', '.') }}</b><span>denuncias en el lake ahora ({{ vivo.nuevas if vivo.nuevas is not none else M.nuevas }} del formulario de registro)</span></div>
<div class="kpi"><b>{{ '{:,}'.format(n_ab).replace(',', '.') }}</b><span>carpetas en trámite puntuadas</span></div>
<div class="kpi"><b>{{ n_prior }}</b><span>con continuidad a empujar (prob. ≥ 45 %, más de 30 días)</span></div>
<div class="kpi"><b>{{ n_arch }}</b><span>candidatas a acuerdo o archivo (prob. &lt; 8 %, más de 60 días)</span></div>
<div class="kpi"><b>{{ n_rezago }}</b><span>con rezago (más de 1,5 veces su tiempo estimado)</span></div>
<div class="kpi"><b>{{ M.auc }} · ±{{ M.mae }} d</b><span>AUC del modelo de continuidad · error medio en días</span></div>
</div>
<div class="card" style="margin-bottom:16px"><h2>Bandeja de evaluación · denuncias registradas en el formulario</h2>{{ err_bandeja or '' }}
<div class="tab" style="max-height:560px"><table><tr><th>Folio · tipo</th><th>Coherencia</th><th>Imágenes</th><th>Continuidad</th><th>Etapa</th><th>Acción</th></tr>
{% for b in bandeja %}<tr><td><b>{{ b.folio }}</b><br>{{ b.delito }} · {{ b.alcaldia }}<br><span class="nota">{{ b.fecha_denuncia }} · {{ b.mp }}{{ ' · la agente corrigió: ' ~ b.intervenidos if b.intervenidos }}</span></td>
<td><b style="font-size:20px">{{ b.puntaje }}</b> <span class="chip {{ 'Baja' if b.puntaje >= 70 else ('Media' if b.puntaje >= 45 else 'Alta') }}">{{ b.semaforo }}</span><br><span class="nota">{{ b.motivos|join(' · ') }}</span></td>
<td>{% for a in b.adj %}<span class="nota"><b>{{ a.relacion }}</b>: {{ a.descripcion[:90] }}</span><br>{% else %}<span class="nota">—</span>{% endfor %}</td>
<td>{% if b.cont %}<b>{{ b.cont.probabilidad }} %</b> · ~{{ b.cont.dias }} días<br><span class="chip {{ 'Baja' if b.cont.probabilidad >= 35 else 'Media' }}">{{ 'Focalizar fiscal' if b.cont.probabilidad >= 35 else 'No prioritaria para el fiscal' }}</span>{% endif %}</td>
<td>{{ b.etapa }}</td>
<td><button style="padding:5px 9px;font-size:12px" onclick="etapa('{{ b.folio }}','Enviada al siguiente paso (fiscal asignado)')">Enviar al siguiente paso</button><br>
<button class="sec" style="padding:5px 9px;font-size:12px;margin-top:4px" onclick="etapa('{{ b.folio }}','Solicitar complemento a la persona denunciante')">Pedir complemento por correo</button><br><span class="nota" id="msg-{{ b.folio }}"></span></td></tr>
{% else %}<tr><td colspan="6" class="nota">Aún no hay denuncias registradas desde el formulario: regístralas en la aplicación de registro y aparecen aquí al recargar.</td></tr>{% endfor %}</table></div>
<p class="nota">Puntaje de coherencia: tipo confirmado por la IA (25) + imágenes coherentes con el relato (25) + formulario completo (25) + evidencia: detenido, testigos, video, archivos (25). Las acciones quedan como eventos en el lake.</p></div>
<div class="card" style="margin-bottom:16px"><h2>Continuidad por tipo de denuncia · dónde focalizar a los fiscales</h2><div class="tab"><table>
<tr><th>Tipo de denuncia</th><th>Continuidad histórica</th><th>Carpetas abiertas</th><th>Probabilidad de continuidad (abiertas, según el modelo)</th><th>Recomendación</th></tr>
{% for t in M.por_tipo %}<tr><td>{{ t.delito }}</td><td><b>{{ t.continuidad }} %</b></td><td>{{ t.abiertas }}</td><td>{{ t.prob }} %</td>
<td><span class="chip {{ 'Baja' if t.foco == 'Focalizar fiscal' else ('Media' if t.foco.startswith('Evaluar') else 'Alta') }}">{{ t.foco }}</span></td></tr>{% endfor %}</table></div>
<p class="nota">Continuidad = la carpeta se judicializa o termina en acuerdo reparatorio (no en archivo ni en no ejercicio de la acción penal).</p></div>
<div class="grid">
<div class="card"><h2>Pregunta en español sobre el lake</h2>
<p>{% for q in conocidas %}<button class="sec" style="padding:4px 10px;font-size:12px;margin:2px" onclick="preg(this.textContent)">{{ q }}</button>{% endfor %}</p>
<p style="display:flex;gap:8px"><input id="q" placeholder="Ej.: ¿qué alcaldía tiene más robos de vehículo con detenido?"><button onclick="preg(document.getElementById('q').value)">Preguntar</button></p>
<div id="resp" class="nota">Granite (IA local) escribe el SQL, Trino lo ejecuta sobre S3 · modelo {{ 'listo' if listo else 'cargando…' }}</div></div>
<div class="card"><h2>Simulador: ¿tendrá continuidad esta carpeta?</h2>
<div class="fila"><label>Delito<select id="s_delito">{% for d in delitos %}<option>{{ d }}</option>{% endfor %}</select></label>
<label>Alcaldía<select id="s_alcaldia">{% for a in alcaldias %}<option>{{ a }}</option>{% endfor %}</select></label>
<label>Canal<select id="s_canal">{% for c in canales %}<option>{{ c }}</option>{% endfor %}</select></label></div>
<div class="fila" style="margin-top:8px"><label>Evidencias<input id="s_evid" type="number" value="1" min="0" max="6"></label>
<label>Carga del MP<input id="s_carga" type="number" value="75" min="10" max="200"></label><label>Prioridad<select id="s_prio"><option>Alta</option><option selected>Media</option><option>Baja</option></select></label></div>
<p class="nota"><input type="checkbox" id="s_det" style="width:auto"> Detenido · <input type="checkbox" id="s_video" style="width:auto"> Video · <input type="checkbox" id="s_test" style="width:auto"> Testigos · <input type="checkbox" id="s_ident" style="width:auto"> Identifica al responsable</p>
<button onclick="simular()">Calcular</button> <span id="sim" style="font-size:18px;font-weight:600;margin-left:8px"></span><p id="simf" class="nota"></p></div>
<div class="card"><h2>Carpetas a priorizar: alta probabilidad de continuidad</h2><div class="tab"><table><tr><th>Folio</th><th>Delito · alcaldía</th><th>Prob.</th><th>Días abierta / estimados</th><th>MP</th></tr>
{% for a in prioridad %}<tr><td>{{ a.folio }}</td><td>{{ a.delito }}<br><span class="nota">{{ a.alcaldia }}</span></td><td><b>{{ (100 * a.prob)|round(1) }} %</b></td><td>{{ a.antig }} / {{ a.dias_est|round|int }}</td><td>{{ a.mp }}</td></tr>{% endfor %}</table></div>
<p class="nota">Recomendación: empujar diligencias en estas carpetas antes de que pierdan evidencia.</p></div>
<div class="card"><h2>Candidatas a acuerdo reparatorio o archivo</h2><div class="tab"><table><tr><th>Folio</th><th>Delito · alcaldía</th><th>Prob.</th><th>Días abierta</th><th>MP</th></tr>
{% for a in archivo %}<tr><td>{{ a.folio }}</td><td>{{ a.delito }}<br><span class="nota">{{ a.alcaldia }}</span></td><td>{{ (100 * a.prob)|round(1) }} %</td><td>{{ a.antig }}</td><td>{{ a.mp }}</td></tr>{% endfor %}</table></div>
<p class="nota">Recomendación: revisarlas para liberar carga de los agentes (mecanismos alternativos o archivo, según criterio del MP).</p></div>
<div class="card"><h2>Pronóstico de denuncias por semana (próximas 4 semanas)</h2>{% set m = (M.prox.values()|list|max) or 1 %}
{% for a, n in M.prox.items() %}<div class="barra"><span>{{ a }}</span><i style="width:{{ (100 * n / m)|round }}%"></i><span>{{ n }}/sem <span class="nota">({{ M.actual[a] }})</span></span></div>{% endfor %}
<p class="nota">Promedio de las últimas 8 semanas con su tendencia; entre paréntesis, el promedio de las últimas 4. Sirve para planificar turnos y agentes por alcaldía.</p></div>
<div class="card"><h2>Qué explica la continuidad (importancia en el modelo)</h2>{% set m = (M.importancia|map(attribute=1)|max) or 1 %}
{% for n, v in M.importancia %}<div class="barra"><span>{{ n }}</span><i style="width:{{ (100 * v / m)|round }}%;background:#1d2a44"></i><span>{{ v }}</span></div>{% endfor %}
<p class="nota">Agentes con más carpetas abiertas: {% for k, v in carga.items() %}{{ k }} ({{ v }}){{ ', ' if not loop.last }}{% endfor %} · promedio {{ carga_media }}</p></div>
</div><p class="nota">Modelos entrenados con {{ '{:,}'.format(M.n_cerr).replace(',', '.') }} carpetas cerradas leídas del lake en {{ M.seg }} s · último entrenamiento {{ actualizado }} (hora de Chile) · se reentrenan cada minuto y cada vez que llega una denuncia del formulario</p>
</main><script>
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function preg(q){ if(!q) return; const d=document.getElementById('resp'); d.innerHTML='Pensando… (en CPU, 20–90 s; las sugeridas responden al instante)';
 const r=await (await fetch('api/preguntar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pregunta:q})})).json();
 if(!r.ok){d.innerHTML='<p>No pude responder: '+esc(r.error)+'</p><pre>'+esc(r.sql)+'</pre>';return;}
 d.innerHTML='<p><b>'+esc(r.resumen)+'</b></p><div class="tab"><table><tr>'+r.columnas.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr>'+
  r.filas.slice(0,50).map(f=>'<tr>'+f.map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('')+'</table></div><pre>'+esc(r.sql)+'</pre><p class="nota">'+r.seg+' s'+(r.cache?' · respuesta guardada':'')+'</p>'; }
async function etapa(f,e){ const r=await (await fetch('api/etapa',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folio:f,etapa:e})})).json();
 const m=document.getElementById('msg-'+f); if(m&&r.aviso){ m.textContent=r.aviso; } setTimeout(()=>location.reload(), r.aviso?12000:800); }
async function simular(){ const g=id=>document.getElementById(id);
 const b={delito:g('s_delito').value,alcaldia:g('s_alcaldia').value,canal:g('s_canal').value,prioridad:g('s_prio').value,evidencias:+g('s_evid').value,carga_mp:+g('s_carga').value,
  hay_detenido:g('s_det').checked,video:g('s_video').checked,testigos:g('s_test').checked,identifica_responsable:g('s_ident').checked};
 const r=await (await fetch('api/puntuar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})).json();
 g('sim').textContent=r.probabilidad+' % de continuidad · ~'+r.dias+' días · '+(r.probabilidad>=35?'focalizar fiscal':'no prioritaria'); g('simf').textContent=(r.factores||[]).join(' · '); }
</script></body></html>"""
