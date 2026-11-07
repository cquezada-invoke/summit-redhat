"""Aplicaciones típicas de Banca y Telco (demo): cada una atiende su operación y genera tráfico continuo. La copia de cada
operación va al data lake central (bifurcación): se acumula y se escribe en S3 cada minuto como JSON, y Trino la lee como
la tabla «<zona>.operaciones_vivo». Una imagen sirve las dos industrias (IND=banca | telco)."""
import datetime as dt
import json
import os
import random
import threading
import time
import uuid
from collections import deque

import requests
from flask import Flask, abort, jsonify, render_template_string, request

IND = os.environ.get("IND", "banca")
S3 = os.environ.get("S3_URL", "http://minio.demo-datalake.svc:9000").rstrip("/")
TRINO = os.environ.get("TRINO_URL", "http://trino.demo-datalake.svc:8080").rstrip("/")
TASA = float(os.environ.get("TASA", "0.5"))          # operaciones por segundo y aplicación
JAVIA = os.environ.get("JAVIA_URL", "https://javia.sno.34.176.42.56.sslip.io")
rnd = random.Random()

CAT = {
    "banca": {
        "nombre": "Banco Andino (demo)",
        "apps": {
            "core": {"nombre": "Core bancario", "esquema": "banca_core", "color": "#1D4ED8", "accion": "Registrar movimiento",
                     "tipos": ["Abono", "Cargo", "Transferencia", "Pago de cuenta"], "canales": ["App", "Web", "Caja", "Cajero"], "monto": (5000, 900000)},
            "creditos": {"nombre": "Originación de créditos", "esquema": "banca_creditos", "color": "#0E9F6E", "accion": "Ingresar solicitud",
                         "tipos": ["Solicitud consumo", "Solicitud hipotecario", "Solicitud automotriz", "Aprobación", "Rechazo"], "canales": ["App", "Sucursal", "Ejecutivo"], "monto": (500000, 60000000)},
            "tarjetas": {"nombre": "Tarjetas", "esquema": "banca_tarjetas", "color": "#D97706", "accion": "Autorizar compra",
                         "tipos": ["Compra aprobada", "Compra rechazada", "Avance"], "canales": ["POS", "E-commerce", "Contactless"], "monto": (1500, 450000)},
            "digital": {"nombre": "Banca digital", "esquema": "banca_digital", "color": "#7C3AED", "accion": "Simular producto",
                        "tipos": ["Inicio de sesión", "Simulación de crédito", "Vista de inversión", "Contratación de seguro"], "canales": ["App", "Web"], "monto": (0, 20000000)},
            "contacto": {"nombre": "CRM y contact center", "esquema": "banca_contacto", "color": "#DB2777", "accion": "Registrar contacto",
                         "tipos": ["Campaña aceptada", "Campaña rechazada", "Reclamo", "Consulta"], "canales": ["Teléfono", "WhatsApp", "Correo", "App"], "monto": (0, 0)},
        },
        "clientes": 20000,
    },
    "telco": {
        "nombre": "Telco Austral (demo)",
        "apps": {
            "bss": {"nombre": "BSS · facturación y planes", "esquema": "telco_bss", "color": "#1D4ED8", "accion": "Emitir factura",
                    "tipos": ["Factura emitida", "Pago recibido", "Cambio de plan", "Baja"], "canales": ["Web", "App", "Tienda"], "monto": (8990, 39990)},
            "cdr": {"nombre": "Uso de red (CDR)", "esquema": "telco_cdr", "color": "#0E9F6E", "accion": "Registrar sesión de datos",
                    "tipos": ["Llamada", "Sesión de datos", "SMS", "Llamada caída", "Roaming"], "canales": ["4G", "5G", "3G"], "monto": (0, 0)},
            "oss": {"nombre": "OSS · calidad de red", "esquema": "telco_oss", "color": "#0284C7", "accion": "Registrar alarma de celda",
                    "tipos": ["Medición de celda", "Caída de celda", "Latencia alta", "Celda recuperada"], "canales": ["Red de acceso", "Transporte"], "monto": (0, 0)},
            "ventas": {"nombre": "Ventas y recargas", "esquema": "telco_ventas", "color": "#D97706", "accion": "Registrar recarga",
                       "tipos": ["Recarga", "Upgrade de plan", "Bolsa de datos", "Portabilidad entrante"], "canales": ["App", "Web", "Tienda", "Distribuidor"], "monto": (2000, 10000)},
            "atencion": {"nombre": "Mi cuenta y atención", "esquema": "telco_atencion", "color": "#DB2777", "accion": "Abrir ticket",
                         "tipos": ["Ticket cobertura", "Ticket facturación", "Ticket equipo", "Encuesta NPS"], "canales": ["App Mi cuenta", "Call center", "WhatsApp"], "monto": (0, 0)},
        },
        "clientes": 30000,
    },
}[IND]
APPS = CAT["apps"]
ULTIMOS = {k: deque(maxlen=40) for k in APPS}
PEND = {k: [] for k in APPS}
CONT = {k: {"total": 0, "lake": 0, "archivos": 0, "error": ""} for k in APPS}
LOCK = threading.Lock()


def nueva(app, manual=None):
    x = APPS[app]
    m0, m1 = x["monto"]
    ev = {"operacion_id": uuid.uuid4().hex[:12], "fecha": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "aplicacion": x["nombre"],
          "tipo": rnd.choice(x["tipos"]), "canal": rnd.choice(x["canales"]), "cliente_id": rnd.randint(1, CAT["clientes"]),
          "monto": round(rnd.uniform(m0, m1), -1) if m1 else 0.0, "origen": "simulador"}
    if app == "cdr":
        ev["detalle"] = json.dumps({"mb": round(rnd.expovariate(1 / 120), 1), "segundos": rnd.randint(5, 900)})
    elif app == "oss":
        ev["detalle"] = json.dumps({"celda_id": rnd.randint(1, 600), "latencia_ms": round(rnd.gauss(45, 20), 1), "disponibilidad": round(rnd.uniform(97, 100), 2)})
    elif app == "atencion" and ev["tipo"] == "Encuesta NPS":
        ev["detalle"] = json.dumps({"nps": rnd.randint(0, 10)})
    else:
        ev["detalle"] = ""
    if manual:
        ev.update({k: v for k, v in manual.items() if k in ("tipo", "canal", "cliente_id", "monto", "detalle")}, origen="manual")
    with LOCK:
        ULTIMOS[app].appendleft(ev)
        PEND[app].append(ev)
        CONT[app]["total"] += 1
    return ev


def trino(q):
    h = {"X-Trino-User": "aplicaciones"}
    r = requests.post(f"{TRINO}/v1/statement", data=q.encode(), headers=h, timeout=30).json()
    while "nextUri" in r:
        r = requests.get(r["nextUri"], headers=h, timeout=60).json()
    if r.get("error"):
        raise ValueError(r["error"]["message"])


def asegurar_tablas():
    for k, x in APPS.items():
        pref = f"apps/{x['esquema']}/operaciones_vivo"
        requests.put(f"{S3}/lake/{pref}/.keep", data=b"", timeout=20)
        trino(f"CREATE SCHEMA IF NOT EXISTS lake.{x['esquema']} WITH (location = 's3://lake/apps/{x['esquema']}/')")
        trino(f"""CREATE TABLE IF NOT EXISTS lake.{x['esquema']}.operaciones_vivo (operacion_id varchar, fecha varchar, aplicacion varchar, tipo varchar,
                  canal varchar, cliente_id bigint, monto double, origen varchar, detalle varchar)
                  WITH (format = 'JSON', external_location = 's3://lake/{pref}/')""")


def ciclo_generador():
    while True:
        for k in APPS:
            n = sum(1 for _ in range(int(TASA)) ) + (1 if rnd.random() < TASA - int(TASA) else 0)
            for _ in range(n):
                nueva(k)
        time.sleep(1)


def ciclo_lake():
    listo = False
    while True:
        time.sleep(60)
        if not listo:
            try:
                asegurar_tablas()
                listo = True
            except Exception as e:  # noqa: BLE001
                for k in APPS:
                    CONT[k]["error"] = f"lake: {str(e)[:120]}"
                continue
        for k, x in APPS.items():
            with LOCK:
                lote, PEND[k] = PEND[k], []
            if not lote:
                continue
            clave = f"apps/{x['esquema']}/operaciones_vivo/{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.json"
            try:
                requests.put(f"{S3}/lake/{clave}", data="\n".join(json.dumps(e, ensure_ascii=False) for e in lote).encode(), timeout=30).raise_for_status()
                CONT[k]["lake"] += len(lote)
                CONT[k]["archivos"] += 1
                CONT[k]["error"] = ""
            except Exception as e:  # noqa: BLE001
                with LOCK:
                    PEND[k] = lote + PEND[k]
                CONT[k]["error"] = f"lake: {str(e)[:120]}"


app = Flask(__name__)
threading.Thread(target=ciclo_generador, daemon=True).start()
threading.Thread(target=ciclo_lake, daemon=True).start()

PAG = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ titulo }}</title><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap">
<style>:root{--c:{{ color }}}*{box-sizing:border-box}body{margin:0;font-family:'Plus Jakarta Sans',system-ui,sans-serif;background:#F4F6FA;color:#14182B}
header{background:#0F172A;color:#fff;padding:14px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}header b{font-size:18px}header nav{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
header nav a{color:#CBD5E1;text-decoration:none;padding:6px 11px;border-radius:8px;font-size:13px;font-weight:600}header nav a.on{background:var(--c);color:#fff}
main{max-width:1180px;margin:0 auto;padding:22px 16px}h1{margin:0 0 4px;font-size:26px}.sub{color:#5B6478;margin:0 0 18px}
.k{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}.k div{background:#fff;border:1px solid #E3E7EF;border-radius:12px;padding:14px}
.k small{color:#6B7489;font-weight:600}.k b{display:block;font-size:26px}.card{background:#fff;border:1px solid #E3E7EF;border-radius:12px;padding:16px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 9px;border-bottom:1px solid #EEF1F6;text-align:left;white-space:nowrap}th{color:#5B6478;background:#F8FAFC}
form{display:flex;gap:8px;flex-wrap:wrap;align-items:end}label{display:flex;flex-direction:column;font-size:12px;font-weight:600;gap:4px}
select,input{border:1px solid #D7DCE6;border-radius:8px;padding:8px 10px;font:inherit}button{background:var(--c);color:#fff;border:0;border-radius:8px;padding:9px 16px;font:600 14px inherit;font-family:inherit;cursor:pointer}
.bif{display:flex;align-items:center;gap:10px;background:#EEF2FF;border:1px dashed #7F77DD;color:#3C3489;border-radius:10px;padding:10px 14px;margin-bottom:16px;font-size:13px}
.ok{color:#047857}.err{color:#B91C1C}.tw{overflow-x:auto}@media(max-width:700px){.k{grid-template-columns:1fr 1fr}}</style></head><body>
<header><b>{{ marca }}</b><nav>{% for k, x in apps.items() %}<a href="/{{ k }}" class="{{ 'on' if k == a }}">{{ x.nombre }}</a>{% endfor %}</nav></header>
<main>{% if a %}<h1>{{ apps[a].nombre }}</h1><p class="sub">Aplicación de negocio (demo) · cada operación se guarda en su sistema y se copia al data lake central.</p>
<div class="k"><div><small>Operaciones desde el arranque</small><b id="t">{{ cont.total }}</b></div><div><small>Copiadas al data lake</small><b id="l">{{ cont.lake }}</b></div>
<div><small>Archivos en S3</small><b id="f">{{ cont.archivos }}</b></div><div><small>Ritmo</small><b>{{ tasa }}/s</b></div></div>
<div class="bif">⤳ Bifurcación activa: <code>s3://lake/apps/{{ apps[a].esquema }}/operaciones_vivo/</code> · tabla <code>lake.{{ apps[a].esquema }}.operaciones_vivo</code>
<span id="e" class="{{ 'err' if cont.error else 'ok' }}">{{ cont.error or 'al día' }}</span> · <a href="{{ javia }}" target="_blank">ver en JavIA ↗</a></div>
<div class="card"><h3 style="margin:0 0 10px">{{ apps[a].accion }}</h3><form id="fm"><label>Tipo<select name="tipo">{% for t in apps[a].tipos %}<option>{{ t }}</option>{% endfor %}</select></label>
<label>Canal<select name="canal">{% for t in apps[a].canales %}<option>{{ t }}</option>{% endfor %}</select></label><label>Cliente<input name="cliente_id" type="number" min="1" value="1024"></label>
{% if apps[a].monto[1] %}<label>Monto<input name="monto" type="number" min="0" value="{{ apps[a].monto[0] * 10 }}"></label>{% endif %}<button>Registrar</button> <span id="r"></span></form></div>
<div class="card"><h3 style="margin:0 0 10px">Últimas operaciones</h3><div class="tw"><table><thead><tr><th>Hora</th><th>Tipo</th><th>Canal</th><th>Cliente</th><th>Monto</th><th>Origen</th></tr></thead><tbody id="tb"></tbody></table></div></div>
<script>const A={{ a|tojson }};function f(){fetch('/api/'+A).then(r=>r.json()).then(j=>{t.textContent=j.cont.total;l.textContent=j.cont.lake;document.getElementById('f').textContent=j.cont.archivos;
e.textContent=j.cont.error||'al día';e.className=j.cont.error?'err':'ok';tb.innerHTML=j.ultimos.map(x=>`<tr><td>${x.fecha.slice(11)}</td><td>${x.tipo}</td><td>${x.canal}</td><td>${x.cliente_id}</td><td>${x.monto?Math.round(x.monto).toLocaleString('es-CL'):'—'}</td><td>${x.origen}</td></tr>`).join('');});}
f();setInterval(f,3000);fm.onsubmit=ev=>{ev.preventDefault();const d=Object.fromEntries(new FormData(fm));d.cliente_id=+d.cliente_id;if(d.monto)d.monto=+d.monto;
fetch('/api/'+A,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}).then(r=>r.json()).then(j=>{r.textContent='Registrada '+j.operacion_id;f();});};</script>
{% else %}<h1>{{ marca }}</h1><p class="sub">Cinco aplicaciones típicas de la industria, cada una con su copia al data lake central.</p>
<div class="k">{% for k, x in apps.items() %}<div><small>{{ x.nombre }}</small><b>{{ conts[k].total }}</b><a href="/{{ k }}">abrir →</a></div>{% endfor %}</div>{% endif %}
</main></body></html>"""


@app.get("/")
def portada():
    return render_template_string(PAG, a=None, apps=APPS, marca=CAT["nombre"], conts=CONT, color="#1D4ED8", titulo=CAT["nombre"], tasa=TASA, javia=JAVIA)


@app.get("/<a>")
def pagina(a):
    if a not in APPS:
        abort(404)
    return render_template_string(PAG, a=a, apps=APPS, marca=CAT["nombre"], cont=CONT[a], color=APPS[a]["color"], titulo=APPS[a]["nombre"], tasa=TASA, javia=JAVIA)


@app.get("/api/<a>")
def api_ver(a):
    if a not in APPS:
        abort(404)
    with LOCK:
        return jsonify(cont=CONT[a], ultimos=list(ULTIMOS[a])[:20])


@app.post("/api/<a>")
def api_nueva(a):
    if a not in APPS:
        abort(404)
    d = request.get_json(silent=True) or {}
    if d.get("tipo") and d["tipo"] not in APPS[a]["tipos"]:
        return jsonify(error="tipo no válido"), 400
    return jsonify(nueva(a, d))


@app.get("/salud")
def salud():
    return jsonify(ok=True, industria=IND, aplicaciones={k: CONT[k] for k in APPS})
