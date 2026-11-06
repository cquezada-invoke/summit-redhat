"""Aplicación 1 · Registro y medición de denuncias.
La IA (Granite, local) lee el relato libre y propone delito, prioridad, alcaldía y un resumen; el agente confirma y la denuncia
queda registrada. Cada denuncia y cada cambio de estado se escribe al data lake (S3, JSON) en el momento, para la aplicación
de inteligencia. Datos sintéticos: ningún folio, agente ni hecho es real."""
import base64
import json
import os
import re
import threading
import time

import pandas as pd
import requests
from flask import Flask, jsonify, render_template_string, request

from comun import ESTILO, ahora, asegurar_lake, chat, modelo_listo, s3_json, s3_put, trino
from imagenes import analizar as analizar_img
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

# Escenarios listos para la demostración: llenan todo el formulario (y adjuntan imágenes de ejemplo) para mostrar resultados distintos.
ESCENARIOS = [
    {"titulo": "Robo de vehículo con cámara", "espera": "Imagen coherente (placa y fecha) · coherencia alta · continuidad baja",
     "delito": "Robo de vehículo", "canal": "Denuncia digital", "denunciante": "Jorge Pérez Salinas", "calidad": "Víctima directa",
     "fecha_hechos": "2026-11-05T19:40", "alcaldia": "Benito Juárez", "colonia": "Del Valle, calle Pilares", "prioridad": "Media",
     "detenido": False, "testigos": False, "video": True, "identifica": False, "adjuntos": ["camara7_placa.jpg"],
     "relato": "El 5 de noviembre, al salir del trabajo en la colonia Del Valle, Benito Juárez, encontré que se habían llevado mi automóvil, un sedán gris 2021 con placas NAZ-512-C. El estacionamiento tiene cámaras y adjunto la captura de la cámara 7 de las 19:42, donde se ve el vehículo saliendo."},
    {"titulo": "Violencia familiar con certificado", "espera": "Certificado coherente · coherencia alta · continuidad alta: focalizar fiscal",
     "delito": "Violencia familiar", "canal": "Presencial en agencia del MP", "denunciante": "Ana Ruiz Martínez", "calidad": "Víctima directa",
     "fecha_hechos": "2026-11-05T22:30", "alcaldia": "Iztapalapa", "colonia": "Santa Cruz Meyehualco", "prioridad": "Alta",
     "detenido": False, "testigos": True, "video": False, "identifica": True, "adjuntos": ["certificado_lesiones.jpg"],
     "relato": "La noche del 5 de noviembre mi expareja llegó a mi domicilio en Santa Cruz Meyehualco, Iztapalapa, me golpeó en el brazo y en la cara frente a mis hijos y me amenazó con volver. No es la primera vez. Mi vecina escuchó y llamó a la patrulla. Adjunto el certificado médico de lesiones a nombre de Ana Ruiz."},
    {"titulo": "Narcomenudeo con detenido", "espera": "Puesta a disposición · continuidad muy alta: focalizar fiscal",
     "delito": "Narcomenudeo", "canal": "Puesta a disposición con detenido", "denunciante": "Policía de investigación (informe)", "calidad": "Representante legal",
     "fecha_hechos": "2026-11-06T01:15", "alcaldia": "Gustavo A. Madero", "colonia": "Martín Carrera", "prioridad": "Alta",
     "detenido": True, "testigos": True, "video": True, "identifica": True, "adjuntos": [],
     "relato": "Durante un recorrido de vigilancia en la colonia Martín Carrera, Gustavo A. Madero, se detuvo en flagrancia a una persona que entregaba envoltorios con sustancia similar a la cocaína a transeúntes. Se aseguraron 32 envoltorios y dinero en efectivo. La detención quedó registrada en la cámara corporal del agente y hay dos testigos."},
    {"titulo": "Extorsión telefónica con capturas", "espera": "Captura coherente (número y fecha) · prioridad alta",
     "delito": "Extorsión", "canal": "MP Virtual (teléfono)", "denunciante": "Luis Gómez Castro", "calidad": "Víctima directa",
     "fecha_hechos": "2026-11-04T21:10", "alcaldia": "Tlalpan", "colonia": "Coapa", "prioridad": "Alta",
     "detenido": False, "testigos": False, "video": False, "identifica": False, "adjuntos": ["captura_extorsion.jpg"],
     "relato": "Desde el 4 de noviembre me escriben del número +52 55 4812 7730 diciendo que son de un grupo que controla la zona de Coapa, Tlalpan, y que si no deposito 20,000 pesos en 48 horas le pasará algo a mi negocio. Adjunto la captura de los mensajes con la cuenta a la que piden el depósito."},
    {"titulo": "Robo a negocio con corte de caja", "espera": "Documento coherente · con video y testigos",
     "delito": "Robo a negocio", "canal": "Presencial en agencia del MP", "denunciante": "Rosa Méndez Ortiz", "calidad": "Víctima directa",
     "fecha_hechos": "2026-11-03T21:05", "alcaldia": "Coyoacán", "colonia": "Pedregal de Santo Domingo", "prioridad": "Media",
     "detenido": False, "testigos": True, "video": True, "identifica": False, "adjuntos": ["corte_caja_negocio.jpg"],
     "relato": "El 3 de noviembre a las 21:05 dos personas entraron a mi tienda Abarrotes La Esquina en Pedregal de Santo Domingo, Coyoacán, amenazaron al cajero y se llevaron el efectivo. El corte de caja muestra un faltante de 18,450 pesos. Las cámaras 1 y 2 grabaron todo y una clienta presenció el robo."},
    {"titulo": "Denuncia incoherente", "espera": "Tipo distinto al relato e imagen de otra fecha y otra placa · requiere revisar o complementar",
     "delito": "Fraude", "canal": "Denuncia digital", "denunciante": "Carlos Vega Ramos", "calidad": "Víctima directa",
     "fecha_hechos": "2026-11-02T18:00", "alcaldia": "Miguel Hidalgo", "colonia": "Anzures", "prioridad": "Baja",
     "detenido": False, "testigos": False, "video": True, "identifica": False, "adjuntos": ["camara2_otra_fecha.jpg"],
     "relato": "El 2 de noviembre en la colonia Anzures, Miguel Hidalgo, me robaron mi automóvil blanco con placas MXC-221-B mientras estaba estacionado en la calle. Adjunto una imagen de la cámara."},
    {"titulo": "Denuncia incompleta (anónima)", "espera": "Faltan datos y evidencia · requiere complementar · no prioritaria",
     "delito": "Amenazas", "canal": "Denuncia digital", "denunciante": "", "calidad": "Anónima",
     "fecha_hechos": "", "alcaldia": "Cuauhtémoc", "colonia": "", "prioridad": "Baja",
     "detenido": False, "testigos": False, "video": False, "identifica": False, "adjuntos": ["calle_sin_texto.jpg"],
     "relato": "Un vecino me amenazó en la calle hace unos días."},
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
                                  canales=[c[0] for c in CANALES], ejemplos=EJEMPLOS, escenarios=ESCENARIOS, nuevas=list(reversed(NUEVAS[-15:])), estados=ESTADOS_CIERRE,
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


@app.post("/api/analizar_imagen")
def analizar_imagen():
    d = request.json or {}
    b64 = (d.get("b64") or "").split(",")[-1]
    if not b64 or len(b64) > 4_000_000:
        return jsonify({"ok": False, "error": "Imagen vacía o demasiado grande"}), 400
    t0 = time.time()
    try:
        an = analizar_img(b64, d.get("relato") or "", d.get("tipo") or "", d.get("fecha_hechos") or "")
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"No se pudo leer la imagen ({str(e)[:120]})"}), 400
    return jsonify({"ok": True, "analisis": an, "seg": round(time.time() - t0, 1)})


@app.post("/api/registrar")
def registrar():
    d = request.json or {}
    if d.get("delito") not in DELITOS or d.get("alcaldia") not in [a[0] for a in ALCALDIAS]:
        return jsonify({"ok": False, "error": "Elige el tipo de denuncia y la alcaldía"}), 400
    if len((d.get("texto") or "").strip()) < 20:
        return jsonify({"ok": False, "error": "Falta el relato de los hechos"}), 400
    f = ahora()
    mp, carga = _sugerir_mp(d["alcaldia"])
    folio = f"CI-{d['alcaldia'][:3].upper().replace('Á', 'A')}-{f:%y}-A{int(time.time() * 10) % 1000000:06d}"
    adj = d.get("adjuntos") or []
    ia = d.get("ia") or {}
    intervenidos = [c for c in ("delito", "prioridad", "alcaldia") if ia.get(c) and ia.get(c) != d.get(c)]
    fila = {"folio": folio, "fecha_denuncia": f.strftime("%Y-%m-%d %H:%M:%S"), "alcaldia": d["alcaldia"], "delito": d["delito"],
            "categoria": DELITOS[d["delito"]][0], "canal": d.get("canal") or CANALES[0][0], "unidad": unidad_de(d["delito"], d["alcaldia"]),
            "mp": mp, "prioridad": d.get("prioridad") or "Media", "hay_detenido": bool(d.get("hay_detenido")),
            "evidencias": len(adj) + len(ia.get("evidencias") or []), "video": bool(d.get("video")), "testigos": bool(d.get("testigos")),
            "identifica_responsable": bool(d.get("identifica")), "carga_mp": carga, "relato": (d.get("texto") or "")[:3000],
            "resumen_ia": (ia.get("resumen") or "")[:300], "delito_ia": ia.get("delito") or "", "prioridad_ia": ia.get("prioridad") or "",
            "segundos_ia": float(d.get("seg") or 0), "denunciante": (d.get("denunciante") or "")[:120], "calidad_denunciante": d.get("calidad") or "",
            "fecha_hechos": (d.get("fecha_hechos") or "")[:20], "colonia": (d.get("colonia") or "")[:120], "n_adjuntos": len(adj),
            "adjuntos_coherentes": sum(1 for a in adj if (a.get("analisis") or {}).get("relacion") == "coherente"),
            "adjuntos_no_coherentes": sum(1 for a in adj if (a.get("analisis") or {}).get("relacion") == "no coherente"),
            "intervenidos": ", ".join(intervenidos)}
    try:
        for i, a in enumerate(adj[:6], 1):   # los archivos al lake (S3) y el análisis de cada uno como dato consultable
            nombre = re.sub(r"[^A-Za-z0-9._-]", "_", a.get("nombre") or f"adjunto{i}")[:80]
            ruta = f"adjuntos/{folio}/{i}-{nombre}"
            s3_put(ruta, base64.b64decode((a.get("b64") or "").split(",")[-1] or b""), a.get("tipo") or "application/octet-stream")
            an = a.get("analisis") or {}
            s3_json(f"adjuntos_analisis/{folio}-{i}.json", {"folio": folio, "archivo": nombre, "ruta_s3": f"s3://lake/fiscalia/{ruta}", "tipo": a.get("tipo") or "",
                                                           "descripcion": an.get("descripcion") or "", "elementos": ", ".join(an.get("elementos") or []),
                                                           "texto_visible": an.get("texto_visible") or "", "relacion": an.get("relacion") or "sin analizar",
                                                           "observacion": an.get("observacion") or "", "segundos": float(a.get("seg") or 0)})
        s3_json(f"denuncias_nuevas/{folio}.json", fila)
        s3_json(f"eventos/{f:%Y%m%d%H%M%S}-{folio}.json", {"fecha": fila["fecha_denuncia"], "folio": folio, "evento": "registro", "estado": "En trámite",
                                                           "detalle": f"asignada a {mp} ({fila['unidad']})" + (f"; la agente corrigió a la IA en: {fila['intervenidos']}" if intervenidos else ""),
                                                           "usuario": "demo"})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"No se pudo escribir en el lake: {e}"}), 502
    pred = {}
    try:
        pred = requests.post(f"{INTELIGENCIA}/api/puntuar", json=fila, timeout=5).json()
        requests.post(f"{INTELIGENCIA}/api/refrescar", timeout=3)   # que la aplicación de inteligencia la considere ya
    except Exception:  # noqa: BLE001
        pass
    threading.Thread(target=cargar, daemon=True).start()
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
<title>Denuncias · registro</title>{{ estilo|safe }}<style>.sec2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:#8a1538;margin:14px 0 6px;font-weight:700}
.adj{display:grid;grid-template-columns:110px 1fr;gap:10px;border:1px solid #e3e5e9;border-radius:10px;padding:8px;margin:6px 0}.adj img{width:110px;height:90px;object-fit:cover;border-radius:6px}
.ok{color:#2e7d4f}.no{color:#b3261e}.ind{color:#8a5a00}.dif{background:#fff1d6;border-radius:6px;padding:1px 6px;font-size:12px}label{font-size:13px;color:#3b4658}</style></head><body>
<header><b>Denuncias · formulario y registro</b><span class="sub">Demo de Invoke sobre Red Hat OpenShift · datos sintéticos</span>
<a href="{{ inteligencia }}">Ir a evaluación e inteligencia →</a></header><main>
{% if error %}<p class="nota">Lake: {{ error }}</p>{% endif %}
{% if k %}<div class="kpis">
<div class="kpi"><b>{{ '{:,}'.format(k.d30).replace(',', '.') }}</b><span>denuncias últimos 30 días</span></div>
<div class="kpi"><b>{{ '{:,}'.format(k.abiertas).replace(',', '.') }}</b><span>carpetas en trámite</span></div>
<div class="kpi"><b>{{ k.rezago }}</b><span>en trámite hace más de 90 días</span></div>
<div class="kpi"><b>{{ k.dias }} días</b><span>tiempo medio de resolución</span></div>
<div class="kpi"><b>{{ k.carga }}</b><span>carpetas abiertas por agente del MP (máx. {{ k.carga_max }})</span></div>
</div>{% endif %}
<div class="grid">
<div class="card" style="grid-column:1/-1;display:grid;grid-template-columns:300px 1fr;gap:20px">
<div style="border-right:1px solid #e3e5e9;padding-right:16px"><h2>Escenarios de demostración</h2>
<p class="nota">Llenan el formulario completo y adjuntan las imágenes de ejemplo. Después: «Analizar» y «Registrar».</p>
{% for e in escenarios %}<div onclick="escenario({{ loop.index0 }})" style="cursor:pointer;border:1px solid #e3e5e9;border-radius:10px;padding:9px 11px;margin:7px 0;background:#fafafa">
<b style="font-size:13.5px">{{ loop.index }} · {{ e.titulo }}</b><br><span class="nota">{{ e.espera }}</span></div>{% endfor %}
<button class="sec" style="margin-top:6px;font-size:12.5px" onclick="limpiar()">Limpiar formulario</button></div>
<div><h2>Nueva denuncia</h2>
<p class="nota">Relatos de ejemplo: {% for e in ejemplos %}<button class="sec" style="padding:3px 9px;font-size:12px" onclick="ejemplo({{ loop.index0 }})">{{ loop.index }}</button> {% endfor %}
· IA de texto {{ 'lista' if listo else 'cargando…' }} · imágenes: texto visible (OCR), metadatos y coherencia con el relato</p>
<div class="grid" style="grid-template-columns:1fr 1fr">
<div>
<div class="sec2">1 · Tipo de denuncia</div>
<div class="fila" style="grid-template-columns:1fr 1fr"><label>Tipo de denuncia<select id="delito">{% for d in delitos %}<option>{{ d }}</option>{% endfor %}</select></label>
<label>Canal<select id="canal">{% for c in canales %}<option>{{ c }}</option>{% endfor %}</select></label></div>
<div class="sec2">2 · Denunciante</div>
<div class="fila" style="grid-template-columns:1fr 1fr"><label>Nombre (ficticio en la demo)<input id="denunciante" placeholder="Nombre y apellidos"></label>
<label>Calidad<select id="calidad"><option>Víctima directa</option><option>Familiar de la víctima</option><option>Testigo</option><option>Representante legal</option><option>Anónima</option></select></label></div>
<div class="sec2">3 · Hechos</div>
<div class="fila"><label>Fecha y hora<input id="fecha_hechos" type="datetime-local"></label><label>Alcaldía<select id="alcaldia"><option value="">—</option>{% for a in alcaldias %}<option>{{ a }}</option>{% endfor %}</select></label>
<label>Colonia / calle<input id="colonia"></label></div>
<p class="nota" style="margin:8px 0"><label><input type="checkbox" id="detenido" style="width:auto"> Hay detenido</label> · <label><input type="checkbox" id="testigos" style="width:auto"> Hay testigos</label> ·
<label><input type="checkbox" id="video" style="width:auto"> Hay video</label> · <label><input type="checkbox" id="identifica" style="width:auto"> Identifica al responsable</label></p>
<label>Relato de los hechos<textarea id="texto" rows="6" placeholder="Qué pasó, cuándo, dónde, quiénes participaron…"></textarea></label>
<div class="sec2">4 · Evidencias adjuntas</div>
<input type="file" id="archivos" accept="image/*,application/pdf" multiple onchange="adjuntar(this.files)">
<p class="nota">Fotos (se leen texto, placas, fechas y metadatos, y se contrastan con el relato) o documentos PDF (se guardan en el lake). Hasta 6 archivos.</p>
</div>
<div>
<div class="sec2">Análisis con IA (la agente revisa y corrige)</div>
<button onclick="analizarTodo()">Analizar relato e imágenes</button> <span id="espera" class="nota"></span>
<div id="propuesta" class="nota" style="margin-top:10px"></div><div id="lista_adj"></div>
<div class="sec2">5 · Enviar</div>
<p><label>Prioridad<select id="prioridad" style="max-width:220px"><option>Alta</option><option selected>Media</option><option>Baja</option></select></label></p>
<button onclick="registrar()">Registrar denuncia y enviar al data lake</button><p id="res" class="nota"></p>
</div></div></div></div>
<div class="card"><h2>Registradas en esta sesión</h2><div class="tab"><table><tr><th>Folio</th><th>Tipo · alcaldía</th><th>Adjuntos</th><th>Continuidad</th><th>Estado</th></tr>
{% for n in nuevas %}<tr><td>{{ n.folio }}<br><span class="nota">{{ n.mp }}</span></td><td>{{ n.delito }}<br><span class="nota">{{ n.alcaldia }}{{ ' · corregido: ' ~ n.intervenidos if n.intervenidos }}</span></td>
<td>{{ n.n_adjuntos }}{% if n.n_adjuntos %}<br><span class="nota">{{ n.adjuntos_coherentes }} coherentes</span>{% endif %}</td>
<td>{{ (n.pred.probabilidad ~ ' %') if n.pred and n.pred.probabilidad is defined else '—' }}</td>
<td><select onchange="cambiar('{{ n.folio }}', this.value)"><option>{{ n.estado }}</option>{% for e in ['En trámite'] + estados %}{% if e != n.estado %}<option>{{ e }}</option>{% endif %}{% endfor %}</select></td></tr>
{% else %}<tr><td colspan="5" class="nota">Aún no hay denuncias registradas en esta sesión.</td></tr>{% endfor %}</table></div>
<p class="nota">Cada denuncia, sus archivos, el análisis de cada imagen y cada cambio de estado se escriben en el lake (S3) en el momento.</p></div>
{% if k %}<div class="card"><h2>Tipos de denuncia más frecuentes (30 días)</h2>{% set m = (k.delitos.values()|list|max) %}{% for d, n in k.delitos.items() %}
<div class="barra"><span>{{ d }}</span><i style="width:{{ (100 * n / m)|round }}%"></i><span>{{ n }}</span></div>{% endfor %}</div>{% endif %}
</div><p class="nota">{{ '{:,}'.format(k.total).replace(',', '.') if k else 0 }} denuncias en el lake ({{ k.nuevas if k else 0 }} registradas en la aplicación) · leído de Trino a las {{ actualizado }} (hora de Chile)</p>
</main><script>
const EJ = {{ ejemplos|tojson }}; let IA = null, SEG = 0, ADJ = [];
const g = id => document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
const ESC = {{ escenarios|tojson }};
function limpiar(){ for (const id of ['texto','denunciante','fecha_hechos','colonia']) g(id).value = ''; g('alcaldia').value = '';
  for (const id of ['detenido','testigos','video','identifica']) g(id).checked = false; ADJ = []; IA = null; pintarAdj(); g('propuesta').innerHTML = ''; g('res').textContent = ''; g('espera').textContent = ''; }
async function escenario(i){ const e = ESC[i]; limpiar();
  g('delito').value = e.delito; g('canal').value = e.canal; g('denunciante').value = e.denunciante; g('calidad').value = e.calidad; g('fecha_hechos').value = e.fecha_hechos;
  g('alcaldia').value = e.alcaldia; g('colonia').value = e.colonia; g('prioridad').value = e.prioridad; g('texto').value = e.relato;
  g('detenido').checked = e.detenido; g('testigos').checked = e.testigos; g('video').checked = e.video; g('identifica').checked = e.identifica;
  for (const a of e.adjuntos) { const b = await (await fetch('static/' + a)).blob(); ADJ.push({nombre: a, tipo: 'image/jpeg', b64: await reducir(new File([b], a, {type: 'image/jpeg'})), analisis: null, seg: 0}); }
  pintarAdj(); g('espera').textContent = 'Escenario «' + e.titulo + '» cargado: ahora «Analizar relato e imágenes».'; window.scrollTo({top: g('texto').getBoundingClientRect().top + scrollY - 300, behavior: 'smooth'}); }
function ejemplo(i){ g('texto').value = EJ[i]; g('denunciante').value = ['María López Hernández','Ana Ruiz Martínez','Jorge Pérez Salinas','Luis Gómez Castro'][i]; }
function reducir(f){ return new Promise(ok => { const r = new FileReader(); r.onload = () => { if (!f.type.startsWith('image/')) return ok(r.result);
  const im = new Image(); im.onload = () => { const k = Math.min(1, 1280 / Math.max(im.width, im.height)); const c = document.createElement('canvas');
  c.width = im.width * k; c.height = im.height * k; c.getContext('2d').drawImage(im, 0, 0, c.width, c.height); ok(c.toDataURL('image/jpeg', .85)); }; im.src = r.result; };
  r.readAsDataURL(f); }); }
async function adjuntar(fs){ for (const f of [...fs].slice(0, 6 - ADJ.length)) ADJ.push({nombre: f.name, tipo: f.type.startsWith('image/') ? 'image/jpeg' : f.type, b64: await reducir(f), analisis: null, seg: 0}); pintarAdj(); }
function pintarAdj(){ g('lista_adj').innerHTML = ADJ.map((a, i) => '<div class="adj">' + (a.tipo.startsWith('image/') ? '<img src="' + a.b64 + '">' : '<div class="nota">PDF</div>') +
  '<div><b>' + esc(a.nombre) + '</b> <a href="#" onclick="ADJ.splice(' + i + ',1);pintarAdj();return false" class="nota">quitar</a><br>' +
  (a.analisis ? '<span class="' + ({'coherente': 'ok', 'no coherente': 'no'}[a.analisis.relacion] || 'ind') + '"><b>' + esc(a.analisis.relacion) + '</b></span> · ' + esc(a.analisis.descripcion) +
   (a.analisis.texto_visible ? '<br>Texto visible: ' + esc(a.analisis.texto_visible) : '') + '<br><span class="nota">' + esc(a.analisis.observacion) + ' · ' + a.seg + ' s</span>'
   : (a.tipo.startsWith('image/') ? '<span class="nota">sin analizar</span>' : '<span class="nota">documento: se guarda en el lake</span>')) + '</div></div>').join(''); }
async function analizarTodo(){
  const t = g('texto').value; if (t.trim().length < 20) { g('espera').textContent = 'Escribe primero el relato.'; return; }
  g('espera').textContent = 'La IA está leyendo el relato (20–60 s en CPU)…';
  const r = await (await fetch('api/analizar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({texto: t})})).json();
  if (r.ok) { IA = r.ia; SEG = r.seg; const tipo = g('delito').value;
    g('propuesta').innerHTML = '<b>Propuesta de ' + esc(r.fuente) + ' (' + r.seg + ' s):</b><br>Tipo: <b>' + esc(IA.delito) + '</b>' + (IA.delito !== tipo ? ' <span class="dif">distinto del declarado (' + esc(tipo) + ')</span>' : ' ✓ coincide') +
      ' · Prioridad: <b>' + esc(IA.prioridad) + '</b> · Alcaldía: <b>' + esc(IA.alcaldia || '—') + '</b><br>Resumen: ' + esc(IA.resumen) + '<br>Evidencias mencionadas: ' + esc((IA.evidencias || []).join(', ') || '—') +
      '<br>Unidad sugerida: ' + esc(IA.unidad) + ' <button class="sec" style="padding:2px 8px;font-size:12px" onclick="aplicar()">Usar la propuesta en el formulario</button>'; }
  for (const a of ADJ) { if (!a.tipo.startsWith('image/') || a.analisis) continue;
    g('espera').textContent = 'Analizando la imagen «' + a.nombre + '»…';
    const v = await (await fetch('api/analizar_imagen', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({b64: a.b64, relato: t, tipo: g('delito').value, fecha_hechos: g('fecha_hechos').value})})).json();
    if (v.ok) { a.analisis = v.analisis; a.seg = v.seg; } else { a.analisis = {relacion: 'indeterminado', descripcion: v.error, observacion: ''}; }
    pintarAdj(); }
  g('espera').textContent = 'Listo: revisa, corrige lo que haga falta y registra.'; }
function aplicar(){ if (!IA) return; g('delito').value = IA.delito; g('prioridad').value = IA.prioridad; if (IA.alcaldia) g('alcaldia').value = IA.alcaldia; g('detenido').checked = IA.hay_detenido; }
async function registrar(){
  const b = {texto: g('texto').value, delito: g('delito').value, alcaldia: g('alcaldia').value, prioridad: g('prioridad').value, canal: g('canal').value,
    hay_detenido: g('detenido').checked, video: g('video').checked, testigos: g('testigos').checked, identifica: g('identifica').checked,
    denunciante: g('denunciante').value, calidad: g('calidad').value, fecha_hechos: g('fecha_hechos').value, colonia: g('colonia').value, ia: IA || {}, seg: SEG, adjuntos: ADJ};
  g('res').textContent = 'Enviando al data lake…';
  const r = await (await fetch('api/registrar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)})).json();
  g('res').textContent = r.ok ? ('Registrada ' + r.folio + ' · ' + r.unidad + ' · ' + r.mp + (r.prediccion && r.prediccion.probabilidad !== undefined ? ' · probabilidad de continuidad ' + r.prediccion.probabilidad + ' %' : '')) : r.error;
  if (r.ok) setTimeout(() => location.reload(), 2500); }
async function cambiar(f, e){ await fetch('api/estado', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({folio: f, estado: e})}); }
</script></body></html>"""
