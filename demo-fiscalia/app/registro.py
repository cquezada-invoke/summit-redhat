"""Aplicación 1 · Registro y medición de denuncias.
La IA (Granite, local) lee el relato libre y propone delito, prioridad, alcaldía y un resumen; el agente confirma y la denuncia
queda registrada. Cada denuncia y cada cambio de estado se escribe al data lake (S3, JSON) en el momento, para la aplicación
de inteligencia. Datos sintéticos: ningún folio, agente ni hecho es real."""
import json
import os
import re
import threading
import time
from collections import Counter

import pandas as pd
import requests
from flask import Flask, jsonify, render_template_string, request

from comun import ESTILO, ahora, asegurar_lake, chat, modelo_listo, s3_json, trino
from datos import ALCALDIAS, CANALES, DELITOS, ESTADOS_CIERRE, HOY, unidad_de

app = Flask(__name__)
LOCK = threading.Lock()
INTELIGENCIA = os.environ.get("INTELIGENCIA_URL", "http://inteligencia:8080")
EST = {"df": None, "t": 0, "error": ""}
NUEVAS = []   # registradas en esta sesión (también están en el lake)

EJEMPLOS = [
    "Hoy a las 19:30 en la colonia Doctores, alcaldía Cuauhtémoc, dos sujetos en motocicleta me amenazaron con un arma y me quitaron el celular y la cartera. Hay una cámara del C5 en la esquina y un vecino vio todo.",
    "Mi expareja me golpeó anoche en nuestro domicilio en Iztapalapa, frente a mis hijos. No es la primera vez. Tengo fotos de las lesiones y mensajes de amenazas en el teléfono.",
    "Desde hace dos semanas me llaman diciendo que son de un grupo delictivo y que si no deposito 20 mil pesos le harán daño a mi negocio en Tlalpan. Tengo grabadas las llamadas y el número desde el que marcan.",
    "Al salir del trabajo en Benito Juárez encontré que se habían llevado mi automóvil, un sedán gris 2021. Tengo la factura y el estacionamiento tiene cámaras.",
]


def cargar():
    """Denuncias desde el lake (vista de Trino: histórico + las registradas en la aplicación)."""
    try:
        df = trino("""SELECT folio, fecha_denuncia, alcaldia, delito, unidad, mp, prioridad, canal, estado, dias_resolucion, origen
                      FROM lake.fiscalia.denuncias""")
        df["fecha_denuncia"] = pd.to_datetime(df["fecha_denuncia"])
        EST.update(df=df, t=time.time(), error="")
    except Exception as e:  # noqa: BLE001
        EST["error"] = str(e)[:300]


def _refrescar():
    while True:
        time.sleep(60)
        cargar()


def iniciar():
    for _ in range(60):
        try:
            asegurar_lake()
            break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    cargar()
    threading.Thread(target=_refrescar, daemon=True).start()


threading.Thread(target=iniciar, daemon=True).start()


def kpis():
    df = EST["df"]
    if df is None or df.empty:
        return None
    d30 = df[df.fecha_denuncia >= pd.Timestamp(ahora()) - pd.Timedelta(days=30)]
    cerr = df[df.estado != "En trámite"]
    abiertas = df[df.estado == "En trámite"]
    carga = abiertas.groupby("mp").size()
    return {"total": len(df), "d30": len(d30), "abiertas": len(abiertas),
            "dias": round(float(cerr.dias_resolucion.dropna().astype(float).mean() or 0)),
            "jud": round(100 * float((cerr.estado == "Judicializada").mean() or 0), 1),
            "carga": round(float(carga.mean() or 0)), "carga_max": int(carga.max() if len(carga) else 0),
            "rezago": int(((pd.Timestamp(ahora()) - abiertas.fecha_denuncia).dt.days > 90).sum()),
            "delitos": d30.delito.value_counts().head(8).to_dict(), "alcaldias": d30.alcaldia.value_counts().head(8).to_dict(),
            "nuevas": int((df.origen == "aplicacion").sum())}


def _sugerir_mp(alcaldia):
    """El agente del MP con menos carpetas abiertas en la alcaldía."""
    df = EST["df"]
    pref = f"MP-{alcaldia[:3].upper().replace('Á', 'A')}-"
    if df is None:
        return pref + "01", 0
    ab = df[(df.estado == "En trámite") & df.mp.str.startswith(pref)].groupby("mp").size()
    todos = pd.Series(0, index=[f"{pref}{i:02d}" for i in range(1, 9)]).add(ab, fill_value=0)
    m = todos.idxmin()
    return m, int(todos[m])


@app.get("/")
def inicio():
    return render_template_string(PAGINA, estilo=ESTILO, k=kpis(), error=EST["error"], alcaldias=[a[0] for a in ALCALDIAS], delitos=list(DELITOS),
                                  canales=[c[0] for c in CANALES], ejemplos=EJEMPLOS, nuevas=list(reversed(NUEVAS[-15:])), estados=ESTADOS_CIERRE,
                                  inteligencia=os.environ.get("INTELIGENCIA_PUBLICA", "#"), listo=modelo_listo(),
                                  actualizado=time.strftime("%H:%M:%S", time.localtime(EST["t"] - 3 * 3600)) if EST["t"] else "—")


SISTEMA = ("Eres un agente del Ministerio Público en Ciudad de México. Lees el relato de una denuncia y respondes SOLO un JSON con: "
           "delito (uno de: " + ", ".join(DELITOS) + "), prioridad (Alta, Media o Baja: Alta si hay violencia, armas, riesgo para la víctima o "
           "detenido), alcaldia (una de: " + ", ".join(a[0] for a in ALCALDIAS) + ", o vacío si no se dice), hay_detenido (true/false), "
           "evidencias (lista corta de evidencias mencionadas), resumen (una frase en tercera persona, sin datos personales).")


def _norm(v, opciones, defecto):
    v = (v or "").strip().lower()
    for o in opciones:
        if o.lower() == v:
            return o
    for o in opciones:
        if v and (v in o.lower() or o.lower() in v):
            return o
    return defecto


def _respaldo(texto):
    """Si el modelo no responde: clasificación por palabras clave (para que la demo nunca se quede sin respuesta)."""
    t = texto.lower()
    reglas = [("Violencia familiar", ["expareja", "pareja", "esposo", "golpe", "hijos"]), ("Extorsión", ["extorsi", "deposit", "cobro de piso"]),
              ("Robo de vehículo", ["automóvil", "auto", "vehículo", "camioneta"]), ("Fraude", ["fraude", "transferencia", "estafa"]),
              ("Robo a transeúnte", ["celular", "cartera", "asalt"]), ("Amenazas", ["amenaz"])]
    d = next((d for d, ps in reglas if any(p in t for p in ps)), "Robo a transeúnte")
    alc = next((a[0] for a in ALCALDIAS if a[0].lower() in t), "")
    return {"delito": d, "prioridad": "Alta" if any(p in t for p in ["arma", "golpe", "amenaz", "extorsi"]) else "Media", "alcaldia": alc,
            "hay_detenido": "detenid" in t, "evidencias": [], "resumen": texto[:160]}


@app.post("/api/analizar")
def analizar():
    texto = (request.json or {}).get("texto", "").strip()[:2000]
    if len(texto) < 20:
        return jsonify({"ok": False, "error": "Escribe el relato de la denuncia (al menos una frase)"}), 400
    t0 = time.time()
    fuente = "Granite (IA local)"
    try:
        with LOCK:
            r = json.loads(chat([{"role": "system", "content": SISTEMA}, {"role": "user", "content": texto}], max_tokens=220, formato="json"))
    except Exception:  # noqa: BLE001
        r, fuente = _respaldo(texto), "reglas (el modelo no respondió)"
    out = {"delito": _norm(r.get("delito"), list(DELITOS), _respaldo(texto)["delito"]),
           "prioridad": _norm(r.get("prioridad"), ["Alta", "Media", "Baja"], "Media"),
           "alcaldia": _norm(r.get("alcaldia"), [a[0] for a in ALCALDIAS], _respaldo(texto)["alcaldia"]),
           "hay_detenido": bool(r.get("hay_detenido")), "evidencias": [str(x)[:80] for x in (r.get("evidencias") or [])][:6],
           "resumen": str(r.get("resumen") or "")[:300]}
    out["unidad"] = unidad_de(out["delito"], out["alcaldia"] or "Cuauhtémoc")
    return jsonify({"ok": True, "ia": out, "fuente": fuente, "seg": round(time.time() - t0, 1)})


@app.post("/api/registrar")
def registrar():
    d = request.json or {}
    if d.get("delito") not in DELITOS or d.get("alcaldia") not in [a[0] for a in ALCALDIAS]:
        return jsonify({"ok": False, "error": "Elige delito y alcaldía"}), 400
    f = ahora()
    mp, carga = _sugerir_mp(d["alcaldia"])
    folio = f"CI-{d['alcaldia'][:3].upper().replace('Á', 'A')}-{f:%y}-A{int(time.time() * 10) % 1000000:06d}"
    ev = d.get("evidencias") or []
    fila = {"folio": folio, "fecha_denuncia": f.strftime("%Y-%m-%d %H:%M:%S"), "alcaldia": d["alcaldia"], "delito": d["delito"],
            "categoria": DELITOS[d["delito"]][0], "canal": d.get("canal") or CANALES[0][0], "unidad": unidad_de(d["delito"], d["alcaldia"]),
            "mp": mp, "prioridad": d.get("prioridad") or "Media", "hay_detenido": bool(d.get("hay_detenido")), "evidencias": len(ev) if isinstance(ev, list) else int(ev or 0),
            "video": bool(d.get("video")), "testigos": bool(d.get("testigos")), "identifica_responsable": bool(d.get("identifica")),
            "carga_mp": carga, "relato": (d.get("texto") or "")[:2000], "resumen_ia": (d.get("resumen") or "")[:300],
            "delito_ia": d.get("delito_ia") or "", "prioridad_ia": d.get("prioridad_ia") or "", "segundos_ia": float(d.get("seg") or 0)}
    try:
        s3_json(f"denuncias_nuevas/{folio}.json", fila)
        s3_json(f"eventos/{f:%Y%m%d%H%M%S}-{folio}.json", {"fecha": fila["fecha_denuncia"], "folio": folio, "evento": "registro",
                                                           "estado": "En trámite", "detalle": f"asignada a {mp} ({fila['unidad']})", "usuario": "demo"})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"No se pudo escribir en el lake: {e}"}), 502
    pred = {}
    try:
        pred = requests.post(f"{INTELIGENCIA}/api/puntuar", json=fila, timeout=5).json()
    except Exception:  # noqa: BLE001
        pass
    NUEVAS.append(dict(fila, estado="En trámite", pred=pred))
    return jsonify({"ok": True, "folio": folio, "mp": mp, "unidad": fila["unidad"], "carga": carga, "prediccion": pred})


@app.post("/api/estado")
def estado():
    d = request.json or {}
    if d.get("estado") not in ESTADOS_CIERRE + ["En trámite"] or not re.match(r"^CI-[A-Z]{3}-\d{2}-A\d{6}$", d.get("folio") or ""):
        return jsonify({"ok": False}), 400
    f = ahora()
    s3_json(f"eventos/{f:%Y%m%d%H%M%S}-{d['folio']}.json", {"fecha": f.strftime("%Y-%m-%d %H:%M:%S"), "folio": d["folio"], "evento": "cambio de estado",
                                                           "estado": d["estado"], "detalle": d.get("detalle") or "", "usuario": "demo"})
    for n in NUEVAS:
        if n["folio"] == d["folio"]:
            n["estado"] = d["estado"]
    return jsonify({"ok": True})


@app.get("/salud")
def salud():
    return "ok"


PAGINA = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Denuncias · registro y medición</title>{{ estilo|safe }}</head><body>
<header><b>Denuncias · registro y medición</b><span class="sub">Demo de Invoke sobre Red Hat OpenShift · datos sintéticos</span>
<a href="{{ inteligencia }}">Ir a inteligencia y predicción →</a></header><main>
{% if error %}<p class="nota">Lake: {{ error }}</p>{% endif %}
{% if k %}<div class="kpis">
<div class="kpi"><b>{{ '{:,}'.format(k.d30).replace(',', '.') }}</b><span>denuncias últimos 30 días</span></div>
<div class="kpi"><b>{{ '{:,}'.format(k.abiertas).replace(',', '.') }}</b><span>carpetas en trámite</span></div>
<div class="kpi"><b>{{ k.rezago }}</b><span>en trámite hace más de 90 días</span></div>
<div class="kpi"><b>{{ k.dias }} días</b><span>tiempo medio de resolución</span></div>
<div class="kpi"><b>{{ k.jud }} %</b><span>de las cerradas, judicializadas</span></div>
<div class="kpi"><b>{{ k.carga }}</b><span>carpetas abiertas por agente del MP (máx. {{ k.carga_max }})</span></div>
</div>{% endif %}
<div class="grid">
<div class="card"><h2>Nueva denuncia · la IA lee el relato</h2>
<p class="nota">Ejemplos: {% for e in ejemplos %}<button class="sec" style="padding:3px 9px;font-size:12px" onclick="document.getElementById('texto').value=EJ[{{ loop.index0 }}]">{{ loop.index }}</button> {% endfor %}
· modelo {{ 'listo' if listo else 'cargando…' }}</p>
<textarea id="texto" rows="5" placeholder="Relato de la persona denunciante…"></textarea>
<p><button onclick="analizar()">Analizar con IA</button> <span id="espera" class="nota"></span></p>
<div id="propuesta" style="display:none">
<div class="fila"><label>Delito<select id="delito">{% for d in delitos %}<option>{{ d }}</option>{% endfor %}</select></label>
<label>Alcaldía<select id="alcaldia"><option value="">—</option>{% for a in alcaldias %}<option>{{ a }}</option>{% endfor %}</select></label>
<label>Prioridad<select id="prioridad"><option>Alta</option><option>Media</option><option>Baja</option></select></label></div>
<div class="fila" style="margin-top:8px"><label>Canal<select id="canal">{% for c in canales %}<option>{{ c }}</option>{% endfor %}</select></label>
<label><input type="checkbox" id="video" style="width:auto"> Video</label><label><input type="checkbox" id="testigos" style="width:auto"> Testigos</label></div>
<p class="nota"><input type="checkbox" id="detenido" style="width:auto"> Con detenido · <input type="checkbox" id="identifica" style="width:auto"> La víctima identifica al responsable</p>
<p><b>Resumen IA:</b> <span id="resumen"></span><br><b>Evidencias:</b> <span id="evid"></span><br><b>Unidad sugerida:</b> <span id="unidad"></span></p>
<button onclick="registrar()">Confirmar y registrar</button> <span id="res" class="nota"></span></div></div>
<div class="card"><h2>Registradas en esta sesión (van al data lake)</h2><div class="tab"><table><tr><th>Folio</th><th>Delito · alcaldía</th><th>Prioridad</th><th>MP</th><th>Prob. judicializar</th><th>Estado</th></tr>
{% for n in nuevas %}<tr><td>{{ n.folio }}</td><td>{{ n.delito }}<br><span class="nota">{{ n.alcaldia }}</span></td><td><span class="chip {{ n.prioridad }}">{{ n.prioridad }}</span></td>
<td>{{ n.mp }}<br><span class="nota">{{ n.carga_mp }} abiertas</span></td><td>{{ (n.pred.probabilidad ~ ' %') if n.pred and n.pred.probabilidad is defined else '—' }}{% if n.pred and n.pred.dias %}<br><span class="nota">~{{ n.pred.dias }} días</span>{% endif %}</td>
<td><select onchange="cambiar('{{ n.folio }}', this.value)"><option>{{ n.estado }}</option>{% for e in ['En trámite'] + estados %}{% if e != n.estado %}<option>{{ e }}</option>{% endif %}{% endfor %}</select></td></tr>
{% else %}<tr><td colspan="6" class="nota">Aún no hay denuncias registradas en esta sesión.</td></tr>{% endfor %}</table></div>
<p class="nota">Cada registro y cada cambio de estado se escribe en S3 (JSON) en el momento: lake.fiscalia.denuncias_nuevas y lake.fiscalia.eventos.</p></div>
{% if k %}<div class="card"><h2>Delitos más denunciados (30 días)</h2>{% set m = (k.delitos.values()|list|max) %}{% for d, n in k.delitos.items() %}
<div class="barra"><span>{{ d }}</span><i style="width:{{ (100 * n / m)|round }}%"></i><span>{{ n }}</span></div>{% endfor %}</div>
<div class="card"><h2>Alcaldías con más denuncias (30 días)</h2>{% set m = (k.alcaldias.values()|list|max) %}{% for d, n in k.alcaldias.items() %}
<div class="barra"><span>{{ d }}</span><i style="width:{{ (100 * n / m)|round }}%;background:#1d2a44"></i><span>{{ n }}</span></div>{% endfor %}</div>{% endif %}
</div><p class="nota">{{ '{:,}'.format(k.total).replace(',', '.') if k else 0 }} denuncias en el lake ({{ k.nuevas if k else 0 }} registradas en la aplicación) · leído de Trino a las {{ actualizado }} (hora de Chile; se refresca cada minuto)</p>
</main><script>
const EJ = {{ ejemplos|tojson }}; let IA = null, SEG = 0;
async function analizar(){ const t=document.getElementById('texto').value; document.getElementById('espera').textContent='La IA está leyendo el relato (en CPU tarda 20–60 s)…';
 const r=await (await fetch('api/analizar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({texto:t})})).json();
 if(!r.ok){document.getElementById('espera').textContent=r.error;return;} IA=r.ia; SEG=r.seg;
 document.getElementById('espera').textContent='Propuesta de '+r.fuente+' en '+r.seg+' s: revísala y confirma.';
 for(const c of ['delito','alcaldia','prioridad']) document.getElementById(c).value=r.ia[c]||'';
 document.getElementById('detenido').checked=r.ia.hay_detenido; document.getElementById('resumen').textContent=r.ia.resumen;
 document.getElementById('evid').textContent=(r.ia.evidencias||[]).join(', ')||'—'; document.getElementById('unidad').textContent=r.ia.unidad;
 document.getElementById('video').checked=/c[aá]mara|video|c5/i.test(t); document.getElementById('testigos').checked=/vecino|testigo|vio /i.test(t);
 document.getElementById('propuesta').style.display='block'; }
async function registrar(){ const g=id=>document.getElementById(id);
 const b={texto:g('texto').value,delito:g('delito').value,alcaldia:g('alcaldia').value,prioridad:g('prioridad').value,canal:g('canal').value,
  hay_detenido:g('detenido').checked,video:g('video').checked,testigos:g('testigos').checked,identifica:g('identifica').checked,
  evidencias:IA?IA.evidencias:[],resumen:IA?IA.resumen:'',delito_ia:IA?IA.delito:'',prioridad_ia:IA?IA.prioridad:'',seg:SEG};
 const r=await (await fetch('api/registrar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})).json();
 g('res').textContent=r.ok?('Registrada '+r.folio+' · '+r.mp+(r.prediccion&&r.prediccion.probabilidad!==undefined?(' · prob. de judicializar '+r.prediccion.probabilidad+' %'):'')):r.error;
 if(r.ok) setTimeout(()=>location.reload(),1500); }
async function cambiar(f,e){ await fetch('api/estado',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folio:f,estado:e})}); }
</script></body></html>"""
