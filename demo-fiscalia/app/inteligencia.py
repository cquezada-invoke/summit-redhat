"""Aplicación 2 · Inteligencia y predicción sobre el data lake.
Lee todas las denuncias del lake con Trino (histórico + las que se registran en la aplicación de registro), entrena dos modelos
(probabilidad de judicializar una carpeta y días hasta su cierre), puntúa las carpetas abiertas, pronostica la carga por alcaldía
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

from comun import ESTILO, ahora, asegurar_lake, chat, modelo_listo, solo_lectura, trino
from datos import ALCALDIAS, CANALES, DELITOS

app = Flask(__name__)
LOCK, LOCK_IA = threading.Lock(), threading.Lock()
NUM = ["evidencias", "carga_mp"]
BOOL = ["hay_detenido", "video", "testigos", "identifica_responsable"]
CAT = ["delito", "alcaldia", "canal", "prioridad"]
ETIQ = {"evidencias": "Evidencias", "carga_mp": "Carga del agente del MP", "hay_detenido": "Detenido", "video": "Video", "testigos": "Testigos",
        "identifica_responsable": "Víctima identifica al responsable", "delito": "Delito", "alcaldia": "Alcaldía", "canal": "Canal", "prioridad": "Prioridad"}
M = {"listo": False, "error": "", "t": 0}
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
    x, y = _x(cerr, cats), (cerr.estado == "Judicializada").astype(int)
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
    with LOCK:
        M.update(listo=True, df=df, ab=ab, clf=clf, reg=reg, cats=cats, auc=round(float(auc), 3), mae=round(float(mae)), n=len(df), n_cerr=len(cerr),
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
        time.sleep(120)   # reentrena con lo que haya llegado al lake


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
        out.append("sin detenido, testigos ni video: difícil de judicializar")
    return out


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
           "usando solo estas tablas y columnas. Alias en español, porcentajes con 1 decimal, máximo 20 filas. Responde solo el SQL.\n\n" + ESQUEMA)
    msgs = [{"role": "system", "content": sis}]
    for q, s in list(CONOCIDAS.items())[:4]:
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": s}]
    msgs.append({"role": "user", "content": p})
    conocido = {re.sub(r"[^a-z0-9áéíóúñ ]", "", q.lower()).strip(): s for q, s in CONOCIDAS.items()}
    sql, df, error = "", None, None
    with LOCK_IA:
        for i in range(3):
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
    return render_template_string(PAGINA, **ctx)


@app.get("/salud")
def salud():
    return "ok"


PAGINA = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Denuncias · inteligencia y predicción</title>{{ estilo|safe }}</head><body>
<header><b>Denuncias · inteligencia y predicción</b><span class="sub">Sobre el data lake (Trino + S3) · demo de Invoke en Red Hat OpenShift · datos sintéticos</span></header><main>
<div class="kpis">
<div class="kpi"><b>{{ '{:,}'.format(M.n).replace(',', '.') }}</b><span>denuncias en el lake ({{ M.nuevas }} de la aplicación de registro)</span></div>
<div class="kpi"><b>{{ '{:,}'.format(n_ab).replace(',', '.') }}</b><span>carpetas en trámite puntuadas</span></div>
<div class="kpi"><b>{{ n_prior }}</b><span>judicializables a empujar (prob. ≥ 45 %, más de 30 días)</span></div>
<div class="kpi"><b>{{ n_arch }}</b><span>candidatas a acuerdo o archivo (prob. &lt; 8 %, más de 60 días)</span></div>
<div class="kpi"><b>{{ n_rezago }}</b><span>con rezago (más de 1,5 veces su tiempo estimado)</span></div>
<div class="kpi"><b>{{ M.auc }} · ±{{ M.mae }} d</b><span>AUC del modelo de judicialización · error medio en días</span></div>
</div>
<div class="grid">
<div class="card"><h2>Pregunta en español sobre el lake</h2>
<p>{% for q in conocidas %}<button class="sec" style="padding:4px 10px;font-size:12px;margin:2px" onclick="preg(this.textContent)">{{ q }}</button>{% endfor %}</p>
<p style="display:flex;gap:8px"><input id="q" placeholder="Ej.: ¿qué alcaldía tiene más robos de vehículo con detenido?"><button onclick="preg(document.getElementById('q').value)">Preguntar</button></p>
<div id="resp" class="nota">Granite (IA local) escribe el SQL, Trino lo ejecuta sobre S3 · modelo {{ 'listo' if listo else 'cargando…' }}</div></div>
<div class="card"><h2>Simulador: ¿se judicializará esta carpeta?</h2>
<div class="fila"><label>Delito<select id="s_delito">{% for d in delitos %}<option>{{ d }}</option>{% endfor %}</select></label>
<label>Alcaldía<select id="s_alcaldia">{% for a in alcaldias %}<option>{{ a }}</option>{% endfor %}</select></label>
<label>Canal<select id="s_canal">{% for c in canales %}<option>{{ c }}</option>{% endfor %}</select></label></div>
<div class="fila" style="margin-top:8px"><label>Evidencias<input id="s_evid" type="number" value="1" min="0" max="6"></label>
<label>Carga del MP<input id="s_carga" type="number" value="75" min="10" max="200"></label><label>Prioridad<select id="s_prio"><option>Alta</option><option selected>Media</option><option>Baja</option></select></label></div>
<p class="nota"><input type="checkbox" id="s_det" style="width:auto"> Detenido · <input type="checkbox" id="s_video" style="width:auto"> Video · <input type="checkbox" id="s_test" style="width:auto"> Testigos · <input type="checkbox" id="s_ident" style="width:auto"> Identifica al responsable</p>
<button onclick="simular()">Calcular</button> <span id="sim" style="font-size:18px;font-weight:600;margin-left:8px"></span><p id="simf" class="nota"></p></div>
<div class="card"><h2>Carpetas a priorizar: alta probabilidad de judicializar</h2><div class="tab"><table><tr><th>Folio</th><th>Delito · alcaldía</th><th>Prob.</th><th>Días abierta / estimados</th><th>MP</th></tr>
{% for a in prioridad %}<tr><td>{{ a.folio }}</td><td>{{ a.delito }}<br><span class="nota">{{ a.alcaldia }}</span></td><td><b>{{ (100 * a.prob)|round(1) }} %</b></td><td>{{ a.antig }} / {{ a.dias_est|round|int }}</td><td>{{ a.mp }}</td></tr>{% endfor %}</table></div>
<p class="nota">Recomendación: empujar diligencias en estas carpetas antes de que pierdan evidencia.</p></div>
<div class="card"><h2>Candidatas a acuerdo reparatorio o archivo</h2><div class="tab"><table><tr><th>Folio</th><th>Delito · alcaldía</th><th>Prob.</th><th>Días abierta</th><th>MP</th></tr>
{% for a in archivo %}<tr><td>{{ a.folio }}</td><td>{{ a.delito }}<br><span class="nota">{{ a.alcaldia }}</span></td><td>{{ (100 * a.prob)|round(1) }} %</td><td>{{ a.antig }}</td><td>{{ a.mp }}</td></tr>{% endfor %}</table></div>
<p class="nota">Recomendación: revisarlas para liberar carga de los agentes (mecanismos alternativos o archivo, según criterio del MP).</p></div>
<div class="card"><h2>Pronóstico de denuncias por semana (próximas 4 semanas)</h2>{% set m = (M.prox.values()|list|max) or 1 %}
{% for a, n in M.prox.items() %}<div class="barra"><span>{{ a }}</span><i style="width:{{ (100 * n / m)|round }}%"></i><span>{{ n }}/sem <span class="nota">({{ M.actual[a] }})</span></span></div>{% endfor %}
<p class="nota">Promedio de las últimas 8 semanas con su tendencia; entre paréntesis, el promedio de las últimas 4. Sirve para planificar turnos y agentes por alcaldía.</p></div>
<div class="card"><h2>Qué explica la judicialización (importancia en el modelo)</h2>{% set m = (M.importancia|map(attribute=1)|max) or 1 %}
{% for n, v in M.importancia %}<div class="barra"><span>{{ n }}</span><i style="width:{{ (100 * v / m)|round }}%;background:#1d2a44"></i><span>{{ v }}</span></div>{% endfor %}
<p class="nota">Agentes con más carpetas abiertas: {% for k, v in carga.items() %}{{ k }} ({{ v }}){{ ', ' if not loop.last }}{% endfor %} · promedio {{ carga_media }}</p></div>
</div><p class="nota">Modelos entrenados con {{ '{:,}'.format(M.n_cerr).replace(',', '.') }} carpetas cerradas leídas del lake en {{ M.seg }} s · última lectura {{ actualizado }} (hora de Chile) · se reentrenan cada 2 minutos con lo que llega al lake</p>
</main><script>
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function preg(q){ if(!q) return; const d=document.getElementById('resp'); d.innerHTML='Pensando… (en CPU, 20–90 s; las sugeridas responden al instante)';
 const r=await (await fetch('api/preguntar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pregunta:q})})).json();
 if(!r.ok){d.innerHTML='<p>No pude responder: '+esc(r.error)+'</p><pre>'+esc(r.sql)+'</pre>';return;}
 d.innerHTML='<p><b>'+esc(r.resumen)+'</b></p><div class="tab"><table><tr>'+r.columnas.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr>'+
  r.filas.slice(0,50).map(f=>'<tr>'+f.map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('')+'</table></div><pre>'+esc(r.sql)+'</pre><p class="nota">'+r.seg+' s'+(r.cache?' · respuesta guardada':'')+'</p>'; }
async function simular(){ const g=id=>document.getElementById(id);
 const b={delito:g('s_delito').value,alcaldia:g('s_alcaldia').value,canal:g('s_canal').value,prioridad:g('s_prio').value,evidencias:+g('s_evid').value,carga_mp:+g('s_carga').value,
  hay_detenido:g('s_det').checked,video:g('s_video').checked,testigos:g('s_test').checked,identifica_responsable:g('s_ident').checked};
 const r=await (await fetch('api/puntuar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})).json();
 g('sim').textContent=r.probabilidad+' % de judicializar · ~'+r.dias+' días'; g('simf').textContent=(r.factores||[]).join(' · '); }
</script></body></html>"""
