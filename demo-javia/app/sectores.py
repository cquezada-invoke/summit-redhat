"""Industrias Banca y Telco: 5 aplicaciones típicas de cada una que vuelcan al lake central (cientos de miles de registros
emulados, coherentes entre aplicaciones) y sus agentes con soluciones de IA para la operación comercial."""
import datetime as dt

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest
from sklearn.cluster import KMeans
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

import agentes as A
import lago
from agentes import num, pesos
from lago import APPS, tabla

FIN = dt.datetime(2026, 10, 31, 23, 59)
INI = FIN - dt.timedelta(days=365)
REGIONES = ["Metropolitana", "Valparaíso", "Biobío", "Araucanía", "Antofagasta", "Coquimbo", "O'Higgins", "Los Lagos", "Maule", "Ñuble"]

lago.INDUSTRIAS.update({
    "banca": {"nombre": "Banca", "desc": "Banco con core, créditos, tarjetas, banca digital y contact center",
              "llaves": {"cliente_id": ["b_core", "b_creditos", "b_tarjetas", "b_digital", "b_contacto"], "fecha": ["b_core", "b_creditos", "b_tarjetas", "b_digital", "b_contacto"],
                         "region": ["b_core"], "canal": ["b_core", "b_tarjetas", "b_digital", "b_contacto"]}},
    "telco": {"nombre": "Telecomunicaciones", "desc": "Operador móvil con BSS, uso de red, calidad de red, ventas y atención",
              "llaves": {"cliente_id": ["t_bss", "t_cdr", "t_ventas", "t_atencion"], "celda_id": ["t_bss", "t_oss"], "mes": ["t_bss", "t_cdr", "t_oss"],
                         "region": ["t_bss", "t_oss"], "fecha": ["t_ventas", "t_atencion"]}},
})
APPS.update({
    "b_core": {"industria": "banca", "esquema": "banca_core", "nombre": "Core bancario", "plataforma": "JBoss EAP · VM on-premise", "icono": "banco",
               "color": "#1D4ED8", "desc": "Clientes, cuentas y movimientos", "tablas": ["clientes", "movimientos"],
               "url": "https://banca.sno.34.176.42.56.sslip.io/core"},
    "b_creditos": {"industria": "banca", "esquema": "banca_creditos", "nombre": "Originación de créditos", "plataforma": "OpenShift", "icono": "credito",
                   "color": "#0E9F6E", "desc": "Solicitudes, evaluación, aprobación y mora", "tablas": ["solicitudes"],
                   "url": "https://banca.sno.34.176.42.56.sslip.io/creditos"},
    "b_tarjetas": {"industria": "banca", "esquema": "banca_tarjetas", "nombre": "Tarjetas", "plataforma": "OpenShift", "icono": "pagos",
                   "color": "#D97706", "desc": "Autorizaciones de compra por comercio y canal", "tablas": ["autorizaciones"],
                   "url": "https://banca.sno.34.176.42.56.sslip.io/tarjetas"},
    "b_digital": {"industria": "banca", "esquema": "banca_digital", "nombre": "Banca digital", "plataforma": "OpenShift", "icono": "movil",
                  "color": "#7C3AED", "desc": "Sesiones en app y web, productos vistos y simulaciones", "tablas": ["sesiones"],
                  "url": "https://banca.sno.34.176.42.56.sslip.io/digital"},
    "b_contacto": {"industria": "banca", "esquema": "banca_contacto", "nombre": "CRM y contact center", "plataforma": "SaaS (CRM)", "icono": "soporte",
                   "color": "#DB2777", "desc": "Campañas, contactos, ofertas y reclamos", "tablas": ["interacciones"],
                   "url": "https://banca.sno.34.176.42.56.sslip.io/contacto"},
    "t_bss": {"industria": "telco", "esquema": "telco_bss", "nombre": "BSS · facturación y planes", "plataforma": "JBoss EAP · VM on-premise", "icono": "crm",
              "color": "#1D4ED8", "desc": "Clientes, planes, facturas y mora", "tablas": ["clientes", "facturas"],
              "url": "https://telco.sno.34.176.42.56.sslip.io/bss"},
    "t_cdr": {"industria": "telco", "esquema": "telco_cdr", "nombre": "Uso de red (CDR)", "plataforma": "OpenShift", "icono": "antena",
              "color": "#0E9F6E", "desc": "Minutos, datos, llamadas caídas y roaming por mes", "tablas": ["uso_mensual"],
              "url": "https://telco.sno.34.176.42.56.sslip.io/cdr"},
    "t_oss": {"industria": "telco", "esquema": "telco_oss", "nombre": "OSS · calidad de red", "plataforma": "OpenShift", "icono": "red",
              "color": "#0284C7", "desc": "Disponibilidad, latencia y caídas por celda", "tablas": ["celdas"],
              "url": "https://telco.sno.34.176.42.56.sslip.io/oss"},
    "t_ventas": {"industria": "telco", "esquema": "telco_ventas", "nombre": "Ventas y recargas", "plataforma": "OpenShift", "icono": "web",
                 "color": "#D97706", "desc": "Recargas, upgrades y portabilidades por canal", "tablas": ["operaciones"],
                 "url": "https://telco.sno.34.176.42.56.sslip.io/ventas"},
    "t_atencion": {"industria": "telco", "esquema": "telco_atencion", "nombre": "Mi cuenta y atención", "plataforma": "SaaS (ITSM)", "icono": "soporte",
                   "color": "#DB2777", "desc": "Tickets, motivos y NPS", "tablas": ["tickets"],
                   "url": "https://telco.sno.34.176.42.56.sslip.io/atencion"},
})


def _fechas(rng, n):
    return pd.to_datetime(INI) + pd.to_timedelta(rng.integers(0, 365 * 24 * 3600, n), unit="s")


# ─────────── Banca ───────────
def generar_banca(semilla=11):
    rng = np.random.default_rng(semilla)
    n = 20000
    seg = rng.choice(["Masivo", "Emergente", "Renta alta", "Pyme"], n, p=[.5, .25, .15, .1])
    renta = np.round(np.where(seg == "Renta alta", rng.lognormal(15.0, .35, n), np.where(seg == "Pyme", rng.lognormal(14.6, .5, n),
                     np.where(seg == "Emergente", rng.lognormal(13.9, .3, n), rng.lognormal(13.4, .35, n)))), -3)
    edad = rng.integers(20, 80, n)
    necesidad = rng.beta(2, 4, n) + .25 * (edad < 40)             # necesidad latente de crédito
    insat = rng.beta(2, 6, n)                                       # insatisfacción latente
    digital = rng.beta(2, 2, n) + .2 * (edad < 45)                  # afinidad digital
    region = rng.choice(REGIONES, n, p=[.45, .1, .1, .06, .06, .06, .05, .05, .04, .03])
    productos = 1 + rng.poisson(1.2 * (renta / renta.mean()))
    clientes = pd.DataFrame({"cliente_id": np.arange(1, n + 1), "segmento": seg, "edad": edad, "renta_mensual": renta, "region": region,
                             "antiguedad_meses": rng.integers(3, 240, n), "productos": productos,
                             "canal_alta": rng.choice(["Sucursal", "Web", "App", "Ejecutivo"], n, p=[.4, .25, .2, .15])})
    # core: movimientos
    k = rng.poisson(9 + 6 * digital)
    cid = np.repeat(clientes.cliente_id.values, k)
    m = len(cid)
    ix = cid - 1
    tipo = rng.choice(["Abono", "Cargo", "Transferencia", "Pago de cuenta"], m, p=[.25, .4, .2, .15])
    monto = np.round(rng.lognormal(10.5, 1.0, m) * (renta[ix] / renta.mean()) ** .5, -2)
    movimientos = pd.DataFrame({"mov_id": np.arange(1, m + 1), "cliente_id": cid, "fecha": _fechas(rng, m), "tipo": tipo,
                                "canal": np.where(rng.random(m) < digital[ix], rng.choice(["App", "Web"], m), rng.choice(["Caja", "Cajero", "Sucursal"], m)),
                                "monto": monto})
    # banca digital: sesiones con productos vistos
    ks = rng.poisson(1 + 10 * digital)
    cs = np.repeat(clientes.cliente_id.values, ks)
    s = len(cs)
    isx = cs - 1
    prod_vis = np.where(rng.random(s) < .35 + .4 * necesidad[isx], rng.choice(["Crédito de consumo", "Crédito hipotecario", "Crédito automotriz"], s, p=[.6, .25, .15]),
                        rng.choice(["Inversiones", "Seguros", "Tarjeta", "Cuenta"], s))
    simulo = (np.char.startswith(prod_vis.astype(str), "Crédito")) & (rng.random(s) < .2 + .5 * necesidad[isx])
    sesiones = pd.DataFrame({"sesion_id": np.arange(1, s + 1), "cliente_id": cs, "fecha": _fechas(rng, s),
                             "canal": rng.choice(["App", "Web"], s, p=[.7, .3]), "paginas": rng.poisson(5, s) + 1,
                             "producto_visto": prod_vis, "simulo": simulo})
    sim_cli = sesiones.groupby("cliente_id").simulo.sum().reindex(clientes.cliente_id, fill_value=0).values
    # contact center: campañas y reclamos
    kc = rng.poisson(1.5 + 3 * insat)
    cc = np.repeat(clientes.cliente_id.values, kc)
    c = len(cc)
    icx = cc - 1
    tipo_c = np.where(rng.random(c) < .35 + .4 * insat[icx], "Reclamo", rng.choice(["Campaña", "Consulta"], c, p=[.7, .3]))
    canal_c = rng.choice(["Teléfono", "Correo", "WhatsApp", "App"], c, p=[.35, .25, .25, .15])
    p_acepta = 1 / (1 + np.exp(-(-2.6 + 3.0 * necesidad[icx] + .9 * (canal_c == "WhatsApp") + .6 * (canal_c == "App") * digital[icx] - 1.5 * insat[icx])))
    resultado = np.where(tipo_c == "Campaña", np.where(rng.random(c) < p_acepta, "Aceptó", rng.choice(["Rechazó", "Sin respuesta"], c)), "")
    sentimiento = np.where(tipo_c == "Reclamo", np.where(rng.random(c) < .3 + .6 * insat[icx], "Negativo", "Neutro"), "Neutro")
    interacciones = pd.DataFrame({"interaccion_id": np.arange(1, c + 1), "cliente_id": cc, "fecha": _fechas(rng, c), "tipo": tipo_c, "canal": canal_c,
                                  "oferta": np.where(tipo_c == "Campaña", "Crédito de consumo preaprobado", ""), "resultado": resultado, "sentimiento": sentimiento})
    recl_cli = interacciones[interacciones.sentimiento == "Negativo"].groupby("cliente_id").size().reindex(clientes.cliente_id, fill_value=0).values
    # créditos: solicitudes y mora
    lam = .03 + .5 * necesidad + .35 * sim_cli
    kr = rng.poisson(lam)
    cr = np.repeat(clientes.cliente_id.values, kr)
    r = len(cr)
    irx = cr - 1
    score = np.clip(rng.normal(650 + 60 * np.log(renta[irx] / renta.mean() + .5) - 80 * insat[irx], 70, r), 300, 900).round()
    aprob = rng.random(r) < 1 / (1 + np.exp(-(score - 600) / 40))
    prod = rng.choice(["Consumo", "Hipotecario", "Automotriz"], r, p=[.7, .15, .15])
    monto_c = np.round(np.where(prod == "Hipotecario", rng.lognormal(17.8, .4, r), rng.lognormal(15.2, .6, r)), -4)
    mora = aprob & (rng.random(r) < 1 / (1 + np.exp(-(-3.2 + (620 - score) / 45 + 1.2 * (monto_c / renta[irx] > 12)))))
    solicitudes = pd.DataFrame({"solicitud_id": np.arange(1, r + 1), "cliente_id": cr, "fecha": _fechas(rng, r), "producto": prod, "monto": monto_c,
                                "plazo_meses": rng.choice([12, 24, 36, 48, 60, 240], r), "score_buro": score.astype(int),
                                "estado": np.where(aprob, "Aprobada", "Rechazada"), "mora_90": mora})
    # tarjetas: autorizaciones con fraude
    kt = rng.poisson(5 + 6 * digital)
    ct = np.repeat(clientes.cliente_id.values, kt)
    t = len(ct)
    itx = ct - 1
    fraude = rng.random(t) < .003
    base = rng.lognormal(10.2, .9, t)
    fecha_t = _fechas(rng, t)
    hora = np.where(fraude, rng.choice([1, 2, 3, 4], t), fecha_t.hour)
    autorizaciones = pd.DataFrame({"autorizacion_id": np.arange(1, t + 1), "cliente_id": ct,
                                   "fecha": fecha_t.normalize() + pd.to_timedelta(hora, unit="h") + pd.to_timedelta(rng.integers(0, 3600, t), unit="s"),
                                   "rubro": rng.choice(["Supermercado", "Combustible", "Viajes", "Tecnología", "Restaurantes", "Vestuario", "Servicios"], t),
                                   "canal": np.where(fraude & (rng.random(t) < .7), "E-commerce", rng.choice(["POS", "E-commerce", "Contactless"], t, p=[.5, .25, .25])),
                                   "pais": np.where(fraude & (rng.random(t) < .6), rng.choice(["EE.UU.", "Brasil", "Perú", "España"], t), "Chile"),
                                   "monto": np.round(np.where(fraude, base * rng.uniform(8, 25, t), base), -1), "fraude_confirmado": fraude})
    # fuga del banco: consecuencia de reclamos, poco uso y mora
    mora_cli = solicitudes[solicitudes.mora_90].groupby("cliente_id").size().reindex(clientes.cliente_id, fill_value=0).values
    mov_cli = movimientos.groupby("cliente_id").size().reindex(clientes.cliente_id, fill_value=0).values
    z = -5.2 + 7.5 * insat + .6 * recl_cli - .03 * mov_cli + .9 * mora_cli - .3 * productos
    fuga = rng.random(n) < 1 / (1 + np.exp(-z))
    clientes["estado"] = np.where(fuga, "Baja", "Activo")
    return {("b_core", "clientes"): clientes, ("b_core", "movimientos"): movimientos, ("b_creditos", "solicitudes"): solicitudes,
            ("b_tarjetas", "autorizaciones"): autorizaciones, ("b_digital", "sesiones"): sesiones, ("b_contacto", "interacciones"): interacciones}


# ─────────── Telco ───────────
def generar_telco(semilla=13):
    rng = np.random.default_rng(semilla)
    n = 30000
    nc = 600   # celdas
    celda_reg = rng.choice(REGIONES, nc, p=[.4, .1, .1, .07, .07, .07, .05, .05, .05, .04])
    calidad = rng.beta(5, 2, nc)                                   # calidad latente de cada celda
    malas = rng.choice(nc, 40, replace=False)
    calidad[malas] *= .45
    celda = rng.integers(0, nc, n)
    tipo = rng.choice(["Postpago", "Prepago"], n, p=[.55, .45])
    plan = np.where(tipo == "Postpago", rng.choice(["Plan 15 GB", "Plan 40 GB", "Plan Libre"], n, p=[.45, .4, .15]), "Prepago")
    cargo = np.where(plan == "Plan 15 GB", 12990, np.where(plan == "Plan 40 GB", 17990, np.where(plan == "Plan Libre", 24990, 0)))
    edad = rng.integers(16, 80, n)
    apego = rng.beta(3, 3, n)
    uso_datos = rng.gamma(2.5, 1.0, n) * (1.6 - edad / 100)        # intensidad de uso de datos
    clientes = pd.DataFrame({"cliente_id": np.arange(1, n + 1), "tipo": tipo, "plan": plan, "cargo_mensual": cargo, "edad": edad,
                             "region": celda_reg[celda], "celda_id": celda + 1, "antiguedad_meses": rng.integers(1, 120, n),
                             "gama_equipo": rng.choice(["Alta", "Media", "Baja"], n, p=[.25, .5, .25]), "equipo_en_cuotas": rng.random(n) < .35})
    # uso mensual (CDR agregado): 12 meses por cliente
    meses = pd.date_range(INI.replace(day=1, hour=0, minute=0) + pd.DateOffset(months=1), periods=12, freq="MS")
    cid = np.repeat(clientes.cliente_id.values, 12)
    ix = cid - 1
    mm = np.tile(meses.values, n)
    tope = np.where(plan[ix] == "Plan 15 GB", 15, np.where(plan[ix] == "Plan 40 GB", 40, np.where(plan[ix] == "Plan Libre", 120, 8)))
    datos_gb = np.round(rng.gamma(2, 1, len(cid)) * uso_datos[ix] * 4, 2)
    caidas = rng.poisson(.3 + 6 * (1 - calidad[celda[ix]]))
    uso = pd.DataFrame({"cliente_id": cid, "mes": mm, "minutos": rng.poisson(120 + 80 * apego[ix]), "datos_gb": datos_gb,
                        "sobre_consumo": datos_gb > tope, "llamadas_caidas": caidas, "roaming_mb": np.where(rng.random(len(cid)) < .03, rng.integers(50, 3000, len(cid)), 0)})
    # OSS: calidad mensual por celda
    oc = np.repeat(np.arange(1, nc + 1), 12)
    celdas = pd.DataFrame({"celda_id": oc, "region": celda_reg[oc - 1], "mes": np.tile(meses.values, nc),
                           "disponibilidad_pct": np.round(np.clip(99.95 - 4 * (1 - calidad[oc - 1]) ** 2 - rng.exponential(.1, len(oc)), 90, 100), 2),
                           "latencia_ms": np.round(25 + 90 * (1 - calidad[oc - 1]) + rng.normal(0, 5, len(oc)), 1),
                           "caidas": rng.poisson(1 + 20 * (1 - calidad[oc - 1]))})
    # facturas y mora (postpago)
    post = clientes[clientes.tipo == "Postpago"].cliente_id.values
    fcid = np.repeat(post, 12)
    fix = fcid - 1
    riesgo = rng.beta(2, 8, n) + .2 * (edad < 25) + .12 * (clientes.gama_equipo.values == 'Baja') + .1 * clientes.equipo_en_cuotas.values
    mora_d = np.where(rng.random(len(fcid)) < .01 + .12 * riesgo[fix], np.minimum(rng.exponential(30, len(fcid)).astype(int) + 1, 180), 0)
    facturas = pd.DataFrame({"factura_id": np.arange(1, len(fcid) + 1), "cliente_id": fcid, "mes": np.tile(meses.values, len(post)),
                             "monto": cargo[fix] + np.round(rng.exponential(1500, len(fcid)), -1), "dias_mora": mora_d, "pagada": mora_d < 60})
    # atención: tickets con NPS
    caidas_cli = uso.groupby("cliente_id").llamadas_caidas.sum().values
    kt = rng.poisson(.2 + .04 * caidas_cli + .3 * (1 - apego))
    tc = np.repeat(clientes.cliente_id.values, kt)
    t = len(tc)
    itc = tc - 1
    motivo = np.where(rng.random(t) < np.clip(.08 + .006 * caidas_cli[itc], 0, .6), "Cobertura y señal",
                      rng.choice(["Facturación", "Cambio de plan", "Equipo", "Portabilidad", "Consulta"], t, p=[.3, .2, .2, .1, .2]))
    nps = np.clip(np.round(8 - 4 * (motivo == "Cobertura y señal") - 3 * (1 - apego[itc]) + rng.normal(0, 1.5, t)), 0, 10).astype(int)
    tickets = pd.DataFrame({"ticket_id": np.arange(1, t + 1), "cliente_id": tc, "fecha": _fechas(rng, t), "motivo": motivo,
                            "canal": rng.choice(["App Mi cuenta", "Call center", "Tienda", "WhatsApp"], t, p=[.35, .3, .15, .2]),
                            "nps": nps, "resuelto_primer_contacto": rng.random(t) < .55 + .04 * nps})
    # fuga / portabilidad: consecuencia de la red, la atención y la mora
    nps_cli = tickets.groupby("cliente_id").nps.mean().reindex(clientes.cliente_id).fillna(8).values
    mora_cli = facturas.groupby("cliente_id").dias_mora.max().reindex(clientes.cliente_id, fill_value=0).values
    sobre = uso.groupby("cliente_id").sobre_consumo.sum().values
    z = -3.4 + .09 * caidas_cli / 12 * 6 - .35 * (nps_cli - 7) - 2.0 * apego + .01 * mora_cli - .8 * clientes.equipo_en_cuotas.values + .15 * sobre * (tipo == "Prepago")
    porta = rng.random(n) < 1 / (1 + np.exp(-z))
    clientes["estado"] = np.where(porta, "Portado", "Activo")
    # ventas y recargas
    kv = np.where(tipo == "Prepago", rng.poisson(10, n), rng.poisson(.4 + .15 * sobre))
    vc = np.repeat(clientes.cliente_id.values, kv)
    v = len(vc)
    ivx = vc - 1
    op = np.where(tipo[ivx] == "Prepago", "Recarga", np.where(rng.random(v) < .7, "Upgrade de plan", "Bolsa de datos"))
    operaciones = pd.DataFrame({"operacion_id": np.arange(1, v + 1), "cliente_id": vc, "fecha": _fechas(rng, v), "tipo": op,
                                "canal": rng.choice(["App", "Web", "Tienda", "Distribuidor", "Call center"], v, p=[.35, .2, .2, .15, .1]),
                                "monto": np.where(op == "Recarga", rng.choice([2000, 3000, 5000, 10000], v, p=[.3, .3, .3, .1]), np.where(op == "Upgrade de plan", 5000, 3990))})
    return {("t_bss", "clientes"): clientes, ("t_bss", "facturas"): facturas, ("t_cdr", "uso_mensual"): uso, ("t_oss", "celdas"): celdas,
            ("t_ventas", "operaciones"): operaciones, ("t_atencion", "tickets"): tickets}


lago.GENERADORES["banca"] = generar_banca
lago.GENERADORES["telco"] = generar_telco


# ─────────── Herramienta común: clasificador con comparación «solo esta aplicación» vs «cruzando el lake» ───────────
def _clasificar(df, y, base, cruz, origen, semilla=1):
    X = df[base + cruz].astype(float)
    a, b, ya, yb = train_test_split(X, y, test_size=.3, random_state=semilla, stratify=y)
    m0 = GradientBoostingClassifier(random_state=1).fit(a[base], ya)
    auc0 = roc_auc_score(yb, m0.predict_proba(b[base])[:, 1])
    m = GradientBoostingClassifier(random_state=1).fit(a, ya)
    auc = roc_auc_score(yb, m.predict_proba(b)[:, 1])
    imp = m.feature_importances_ / (m.feature_importances_.sum() or 1)
    orden = np.argsort(imp)[::-1][:6]
    cols = list(X.columns)
    barras = [{"etq": cols[i].replace("_", " "), "valor": round(float(imp[i]) * 100, 1), "app": origen.get(cols[i], "")} for i in orden]
    return m, X, float(auc0), float(auc), barras


def _dummies(df, cols):
    return pd.get_dummies(df[cols], prefix=cols).astype(float)


# ─────────── Banca: vista 360 ───────────
def banca360():
    q = f"""SELECT c.*, COALESCE(m.movs, 0) AS movimientos, COALESCE(m.monto_mov, 0) AS monto_movimientos, COALESCE(m.digitales, 0) AS movs_digitales,
      COALESCE(s.sesiones, 0) AS sesiones, COALESCE(s.simulaciones, 0) AS simulaciones, COALESCE(s.vio_credito, 0) AS vio_credito,
      COALESCE(i.reclamos, 0) AS reclamos, COALESCE(i.negativos, 0) AS reclamos_negativos, COALESCE(i.campanas, 0) AS campanas, COALESCE(i.acepto, 0) AS acepto_campana,
      COALESCE(r.solicitudes, 0) AS solicitudes, COALESCE(r.aprobadas, 0) AS aprobadas, COALESCE(r.moras, 0) AS moras,
      COALESCE(t.compras, 0) AS compras_tarjeta, COALESCE(t.monto_tarjeta, 0) AS monto_tarjeta
    FROM {tabla('b_core', 'clientes')} c
    LEFT JOIN (SELECT cliente_id, count(*) movs, sum(monto) monto_mov, sum(CASE WHEN canal IN ('App','Web') THEN 1 ELSE 0 END) digitales FROM {tabla('b_core', 'movimientos')} GROUP BY 1) m ON m.cliente_id = c.cliente_id
    LEFT JOIN (SELECT cliente_id, count(*) sesiones, sum(CASE WHEN simulo THEN 1 ELSE 0 END) simulaciones, sum(CASE WHEN producto_visto LIKE 'Crédito%' THEN 1 ELSE 0 END) vio_credito FROM {tabla('b_digital')} GROUP BY 1) s ON s.cliente_id = c.cliente_id
    LEFT JOIN (SELECT cliente_id, sum(CASE WHEN tipo='Reclamo' THEN 1 ELSE 0 END) reclamos, sum(CASE WHEN sentimiento='Negativo' THEN 1 ELSE 0 END) negativos,
               sum(CASE WHEN tipo='Campaña' THEN 1 ELSE 0 END) campanas, sum(CASE WHEN resultado='Aceptó' THEN 1 ELSE 0 END) acepto FROM {tabla('b_contacto')} GROUP BY 1) i ON i.cliente_id = c.cliente_id
    LEFT JOIN (SELECT cliente_id, count(*) solicitudes, sum(CASE WHEN estado='Aprobada' THEN 1 ELSE 0 END) aprobadas, sum(CASE WHEN mora_90 THEN 1 ELSE 0 END) moras FROM {tabla('b_creditos')} GROUP BY 1) r ON r.cliente_id = c.cliente_id
    LEFT JOIN (SELECT cliente_id, count(*) compras, sum(monto) monto_tarjeta FROM {tabla('b_tarjetas')} GROUP BY 1) t ON t.cliente_id = c.cliente_id"""
    df = lago.sql(q)
    for k in df.columns:
        if k not in ("segmento", "region", "canal_alta", "estado"):
            df[k] = pd.to_numeric(df[k], errors="coerce").fillna(0)
    return df


OB = {"renta_mensual": "b_core", "edad": "b_core", "antiguedad_meses": "b_core", "productos": "b_core", "movimientos": "b_core", "monto_movimientos": "b_core",
      "movs_digitales": "b_core", "sesiones": "b_digital", "simulaciones": "b_digital", "vio_credito": "b_digital", "reclamos": "b_contacto",
      "reclamos_negativos": "b_contacto", "campanas": "b_contacto", "acepto_campana": "b_contacto", "solicitudes": "b_creditos", "aprobadas": "b_creditos",
      "moras": "b_creditos", "compras_tarjeta": "b_tarjetas", "monto_tarjeta": "b_tarjetas"}


def b_fuga():
    df = banca360()
    y = (df.estado == "Baja").astype(int)
    base = ["renta_mensual", "edad", "antiguedad_meses", "productos", "movimientos", "monto_movimientos"]
    cruz = ["sesiones", "reclamos", "reclamos_negativos", "moras", "compras_tarjeta", "acepto_campana"]
    m, X, auc0, auc, imp = _clasificar(df, y, base, cruz, OB)
    df["p"] = m.predict_proba(X)[:, 1]
    act = df[df.estado == "Activo"].sort_values("p", ascending=False)
    top = act.head(500)
    return {"metrica": {"nombre": "AUC (anticipar la fuga)", "valor": auc, "fmt": num(auc, 2), "umbral": .75},
            "comparacion": [{"etq": "Solo el core bancario", "valor": round(auc0, 2)}, {"etq": "Cruzando las 5 aplicaciones", "valor": round(auc, 2)}],
            "apps": ["b_core", "b_contacto", "b_creditos", "b_tarjetas", "b_digital"], "filas": int(len(df)),
            "hallazgos": [f"El core solo anticipa la fuga con AUC {num(auc0, 2)}; sumando reclamos del contact center, mora y uso de tarjeta llega a {num(auc, 2)}.",
                          f"Lo que más empuja la fuga: {imp[0]['etq']} y {imp[1]['etq']}.", f"{num((act.p > .5).sum())} clientes activos con más de 50 % de probabilidad de irse."],
            "decisiones": [{"texto": "Plan de retención para los 500 clientes activos de mayor riesgo (ejecutivo + beneficio)", "impacto": f"{pesos(top.renta_mensual.sum() * .012)} de margen mensual estimado en juego", "area": "Comercial"},
                           {"texto": "Resolver en 24 h los reclamos de clientes con riesgo alto", "impacto": "ataca la primera causa", "area": "Contact center"}],
            "grafico": {"tipo": "barras", "titulo": "Qué explica la fuga (importancia %)", "datos": imp},
            "tabla": {"titulo": "Clientes activos con más riesgo", "cols": ["Cliente", "Segmento", "Región", "Renta", "Reclamos", "Riesgo"],
                      "filas": [[int(r.cliente_id), r.segmento, r.region, pesos(r.renta_mensual), int(r.reclamos), f"{r.p * 100:.0f} %"] for r in top.head(10).itertuples()]}}


def b_preaprobados():
    df = banca360()
    y = (df.solicitudes > 0).astype(int)
    base = ["renta_mensual", "edad", "antiguedad_meses", "productos"]
    cruz = ["sesiones", "simulaciones", "vio_credito", "acepto_campana", "movimientos", "movs_digitales"]
    m, X, auc0, auc, imp = _clasificar(df, y, base, cruz, OB, 2)
    df["p"] = m.predict_proba(X)[:, 1]
    cand = df[(df.solicitudes == 0) & (df.moras == 0) & (df.estado == "Activo")].sort_values("p", ascending=False)
    top = cand.head(1000)
    monto = (top.renta_mensual * 4).clip(upper=15_000_000)
    return {"metrica": {"nombre": "AUC (propensión a pedir crédito)", "valor": auc, "fmt": num(auc, 2), "umbral": .72},
            "comparacion": [{"etq": "Solo datos del cliente", "valor": round(auc0, 2)}, {"etq": "Con banca digital y campañas", "valor": round(auc, 2)}],
            "apps": ["b_core", "b_digital", "b_contacto", "b_creditos"], "filas": int(len(df)),
            "hallazgos": ["Quien simula un crédito en la app o la web y acepta campañas tiene mucha más probabilidad de pedirlo: la señal está en banca digital, no en el core.",
                          f"Hay {num(len(cand[cand.p > .5]))} clientes sin solicitud, sin mora y con propensión alta."],
            "decisiones": [{"texto": "Crédito de consumo preaprobado para los 1.000 clientes de mayor propensión (monto = 4 rentas, tope $15 MM)",
                            "impacto": f"{pesos(monto.sum() * .25)} en colocaciones si convierte el 25 %", "area": "Comercial"},
                           {"texto": "Mostrar la oferta en la app al momento de simular", "impacto": "canal con más conversión", "area": "Banca digital"}],
            "grafico": {"tipo": "barras", "titulo": "Qué anticipa una solicitud (importancia %)", "datos": imp},
            "tabla": {"titulo": "Candidatos a preaprobado", "cols": ["Cliente", "Segmento", "Renta", "Simulaciones", "Propensión", "Monto sugerido"],
                      "filas": [[int(r.cliente_id), r.segmento, pesos(r.renta_mensual), int(r.simulaciones), f"{r.p * 100:.0f} %", pesos(min(r.renta_mensual * 4, 15_000_000))] for r in top.head(10).itertuples()]}}


def b_riesgo():
    df = lago.sql(f"""SELECT s.*, c.renta_mensual, c.antiguedad_meses, c.productos, c.segmento FROM {tabla('b_creditos')} s
                      JOIN {tabla('b_core', 'clientes')} c ON c.cliente_id = s.cliente_id WHERE s.estado = 'Aprobada'""")
    c360 = banca360()[["cliente_id", "reclamos_negativos", "movimientos", "movs_digitales", "compras_tarjeta"]]
    df = df.merge(c360, on="cliente_id", how="left")
    for k in ("monto", "plazo_meses", "score_buro", "renta_mensual", "antiguedad_meses", "productos", "reclamos_negativos", "movimientos", "movs_digitales", "compras_tarjeta"):
        df[k] = pd.to_numeric(df[k]).fillna(0)
    df["carga"] = df.monto / df.renta_mensual.clip(lower=1)
    y = df.mora_90.astype(bool).astype(int)
    org = dict(OB, score_buro="b_creditos", monto="b_creditos", plazo_meses="b_creditos", carga="b_creditos")
    m, X, auc0, auc, imp = _clasificar(df, y, ["score_buro", "monto", "plazo_meses"], ["carga", "renta_mensual", "antiguedad_meses", "movimientos", "reclamos_negativos"], org, 3)
    df["p"] = m.predict_proba(X)[:, 1]
    tasa = y.mean()
    return {"metrica": {"nombre": "AUC (anticipar mora a 90 días)", "valor": auc, "fmt": num(auc, 2), "umbral": .7},
            "comparacion": [{"etq": "Solo score de buró y la solicitud", "valor": round(auc0, 2)}, {"etq": "Con renta, movimientos y reclamos", "valor": round(auc, 2)}],
            "apps": ["b_creditos", "b_core", "b_contacto"], "filas": int(len(df)),
            "hallazgos": [f"Tasa de mora de la cartera aprobada: {num(tasa * 100, 1)} %.", "La carga (monto / renta del core) explica la mora más que el score de buró por sí solo."],
            "decisiones": [{"texto": "Límite de carga de 12 rentas para consumo y revisión manual sobre 30 % de riesgo", "impacto": f"−{(df[df.p > .3].mora_90.sum()) / max(y.sum(), 1) * 100:.0f} % de las moras pasarían por revisión", "area": "Riesgo"}],
            "grafico": {"tipo": "barras", "titulo": "Qué explica la mora (importancia %)", "datos": imp}}


def b_fraude():
    df = lago.sql(f"SELECT t.*, c.renta_mensual FROM {tabla('b_tarjetas')} t JOIN {tabla('b_core', 'clientes')} c ON c.cliente_id = t.cliente_id WHERE t.fecha >= TIMESTAMP '2026-08-01 00:00:00'")
    df["fecha"] = pd.to_datetime(df.fecha)
    df["monto"] = pd.to_numeric(df.monto)
    med = df.groupby("cliente_id").monto.transform("median")
    X = pd.DataFrame({"m": np.log1p(df.monto), "d": np.log1p(df.monto) - np.log1p(med), "noche": df.fecha.dt.hour.isin(range(0, 6)).astype(int),
                      "ext": (df.pais != "Chile").astype(int), "ecom": (df.canal == "E-commerce").astype(int), "rel": df.monto / (pd.to_numeric(df.renta_mensual) + 1)})
    iso = IsolationForest(n_estimators=200, contamination=.005, random_state=2).fit(X)
    df["s"] = -iso.score_samples(X)
    k = int(len(df) * .005)
    top = df.sort_values("s", ascending=False).head(k)
    reales = int(df.fraude_confirmado.astype(bool).sum())
    hit = int(top.fraude_confirmado.astype(bool).sum())
    rec = hit / max(reales, 1)
    return {"metrica": {"nombre": "Fraudes confirmados detectados", "valor": rec, "fmt": f"{rec * 100:.0f} %", "umbral": .6},
            "apps": ["b_tarjetas", "b_core"], "filas": int(len(df)),
            "hallazgos": [f"Revisando el 0,5 % de las autorizaciones ({num(k)}) se encuentran {hit} de {reales} fraudes confirmados.",
                          "Señales: monto muy sobre lo habitual del cliente y sobre su renta (core), de madrugada, e-commerce desde fuera de Chile."],
            "decisiones": [{"texto": "Autenticación reforzada en línea para el 0,5 % superior del puntaje", "impacto": f"{pesos(top[top.fraude_confirmado.astype(bool)].monto.sum())} en fraude evitado en 3 meses", "area": "Riesgo"}],
            "grafico": {"tipo": "barras", "titulo": "Alertas por canal", "datos": [{"etq": c, "valor": int(v), "app": "b_tarjetas"} for c, v in top.canal.value_counts().items()]}}


def b_mejor_oferta():
    df = lago.sql(f"""SELECT producto_visto, canal, count(*) AS vistas, sum(CASE WHEN simulo THEN 1 ELSE 0 END) AS simulaciones FROM {tabla('b_digital')} GROUP BY 1, 2""")
    camp = lago.sql(f"""SELECT canal, count(*) AS campanas, sum(CASE WHEN resultado='Aceptó' THEN 1 ELSE 0 END) AS aceptadas FROM {tabla('b_contacto')} WHERE tipo='Campaña' GROUP BY 1""")
    for k in ("vistas", "simulaciones"):
        df[k] = pd.to_numeric(df[k])
    camp["tasa"] = pd.to_numeric(camp.aceptadas) / pd.to_numeric(camp.campanas)
    camp = camp.sort_values("tasa", ascending=False)
    pv = df.groupby("producto_visto").vistas.sum().sort_values(ascending=False)
    return {"metrica": {"nombre": "Mejor canal de oferta (tasa de aceptación)", "valor": float(camp.tasa.iloc[0]), "fmt": f"{camp.tasa.iloc[0] * 100:.0f} %", "umbral": 0},
            "apps": ["b_digital", "b_contacto"], "filas": int(df.vistas.sum()),
            "hallazgos": [f"El producto más mirado en banca digital es «{pv.index[0]}».", f"Las campañas por {camp.canal.iloc[0]} aceptan {camp.tasa.iloc[0] * 100:.0f} %; por {camp.canal.iloc[-1]}, {camp.tasa.iloc[-1] * 100:.0f} %."],
            "decisiones": [{"texto": f"Mover presupuesto de campañas de {camp.canal.iloc[-1]} a {camp.canal.iloc[0]}", "impacto": f"×{camp.tasa.iloc[0] / max(camp.tasa.iloc[-1], .001):.1f} en aceptación", "area": "Marketing"}],
            "grafico": {"tipo": "barras", "titulo": "Aceptación de campañas por canal (%)", "datos": [{"etq": c, "valor": round(t * 100, 1), "app": "b_contacto"} for c, t in zip(camp.canal, camp.tasa)]}}


def b_segmentos():
    df = banca360()
    df = df[df.estado == "Activo"].copy()
    cols = ["renta_mensual", "productos", "movimientos", "sesiones", "compras_tarjeta", "reclamos"]
    km = KMeans(4, n_init=10, random_state=3).fit(StandardScaler().fit_transform(np.log1p(df[cols])))
    df["g"] = km.labels_
    df["valor"] = df.renta_mensual * .012 * df.productos * 12
    g = df.groupby("g").agg(clientes=("cliente_id", "count"), renta=("renta_mensual", "mean"), productos=("productos", "mean"),
                            digital=("sesiones", "mean"), reclamos=("reclamos", "mean"), valor=("valor", "mean")).sort_values("valor", ascending=False)
    nombres = ["Alto valor", "Digitales en crecimiento", "Tradicionales", "Riesgo de servicio"]
    g["nombre"] = [nombres[i] for i in range(len(g))]
    return {"metrica": {"nombre": "Segmentos de valor", "valor": 4, "fmt": "4", "umbral": 1}, "apps": ["b_core", "b_digital", "b_tarjetas", "b_contacto"], "filas": int(len(df)),
            "hallazgos": [f"«{g.nombre.iloc[0]}» concentra {num(g.clientes.iloc[0])} clientes con valor anual medio de {pesos(g.valor.iloc[0])}."],
            "decisiones": [{"texto": "Ejecutivo dedicado al segmento de alto valor y oferta digital al segmento en crecimiento", "impacto": "foco comercial por valor de vida", "area": "Comercial"}],
            "grafico": {"tipo": "barras", "titulo": "Valor anual medio por segmento", "datos": [{"etq": r.nombre, "valor": round(r.valor), "app": "b_core"} for r in g.itertuples()]},
            "tabla": {"titulo": "Perfil de cada segmento", "cols": ["Segmento", "Clientes", "Renta", "Productos", "Sesiones digitales", "Reclamos"],
                      "filas": [[r.nombre, num(r.clientes), pesos(r.renta), num(r.productos, 1), num(r.digital, 1), num(r.reclamos, 1)] for r in g.itertuples()]}}


# ─────────── Telco: vista 360 ───────────
def telco360():
    q = f"""SELECT c.*, u.minutos, u.datos_gb, u.meses_sobre, u.caidas, u.roaming, COALESCE(f.mora_max, 0) AS mora_max, COALESCE(f.impagas, 0) AS impagas,
      COALESCE(t.tickets, 0) AS tickets, COALESCE(t.nps, 8) AS nps, COALESCE(t.cobertura, 0) AS tickets_cobertura,
      COALESCE(v.recargas, 0) AS recargas, COALESCE(v.monto_recargas, 0) AS monto_recargas, COALESCE(v.upgrades, 0) AS upgrades,
      k.disponibilidad, k.latencia, k.caidas_celda
    FROM {tabla('t_bss', 'clientes')} c
    JOIN (SELECT cliente_id, avg(minutos) minutos, avg(datos_gb) datos_gb, sum(CASE WHEN sobre_consumo THEN 1 ELSE 0 END) meses_sobre, sum(llamadas_caidas) caidas, sum(roaming_mb) roaming FROM {tabla('t_cdr')} GROUP BY 1) u ON u.cliente_id = c.cliente_id
    LEFT JOIN (SELECT cliente_id, max(dias_mora) mora_max, sum(CASE WHEN pagada THEN 0 ELSE 1 END) impagas FROM {tabla('t_bss', 'facturas')} GROUP BY 1) f ON f.cliente_id = c.cliente_id
    LEFT JOIN (SELECT cliente_id, count(*) tickets, avg(CAST(nps AS double)) nps, sum(CASE WHEN motivo='Cobertura y señal' THEN 1 ELSE 0 END) cobertura FROM {tabla('t_atencion')} GROUP BY 1) t ON t.cliente_id = c.cliente_id
    LEFT JOIN (SELECT cliente_id, sum(CASE WHEN tipo='Recarga' THEN 1 ELSE 0 END) recargas, sum(CASE WHEN tipo='Recarga' THEN monto ELSE 0 END) monto_recargas, sum(CASE WHEN tipo<>'Recarga' THEN 1 ELSE 0 END) upgrades FROM {tabla('t_ventas')} GROUP BY 1) v ON v.cliente_id = c.cliente_id
    LEFT JOIN (SELECT celda_id, avg(disponibilidad_pct) disponibilidad, avg(latencia_ms) latencia, sum(caidas) caidas_celda FROM {tabla('t_oss')} GROUP BY 1) k ON k.celda_id = c.celda_id"""
    df = lago.sql(q)
    for k in df.columns:
        if k not in ("tipo", "plan", "region", "gama_equipo", "estado"):
            df[k] = pd.to_numeric(df[k], errors="coerce").fillna(0)
    return df


OT = {"cargo_mensual": "t_bss", "edad": "t_bss", "antiguedad_meses": "t_bss", "equipo_en_cuotas": "t_bss", "mora_max": "t_bss", "impagas": "t_bss",
      "minutos": "t_cdr", "datos_gb": "t_cdr", "meses_sobre": "t_cdr", "caidas": "t_cdr", "roaming": "t_cdr", "tickets": "t_atencion", "nps": "t_atencion",
      "tickets_cobertura": "t_atencion", "recargas": "t_ventas", "monto_recargas": "t_ventas", "upgrades": "t_ventas", "disponibilidad": "t_oss",
      "latencia": "t_oss", "caidas_celda": "t_oss"}


def t_portabilidad():
    df = telco360()
    y = (df.estado == "Portado").astype(int)
    base = ["cargo_mensual", "edad", "antiguedad_meses", "equipo_en_cuotas", "mora_max"]
    cruz = ["caidas", "datos_gb", "meses_sobre", "nps", "tickets_cobertura", "latencia", "disponibilidad", "recargas"]
    m, X, auc0, auc, imp = _clasificar(df, y, base, cruz, OT, 4)
    df["p"] = m.predict_proba(X)[:, 1]
    act = df[df.estado == "Activo"].sort_values("p", ascending=False)
    top = act.head(1500)
    return {"metrica": {"nombre": "AUC (anticipar la portabilidad)", "valor": auc, "fmt": num(auc, 2), "umbral": .75},
            "comparacion": [{"etq": "Solo BSS (plan, cargo, mora)", "valor": round(auc0, 2)}, {"etq": "Con red, uso y atención", "valor": round(auc, 2)}],
            "apps": ["t_bss", "t_cdr", "t_oss", "t_atencion", "t_ventas"], "filas": int(len(df)),
            "hallazgos": [f"Con solo el BSS el modelo llega a AUC {num(auc0, 2)}; sumando llamadas caídas, NPS y calidad de la celda llega a {num(auc, 2)}.",
                          f"Lo que más empuja la portabilidad: {imp[0]['etq']} ({APPS.get(imp[0]['app'], {}).get('nombre', '')}) y {imp[1]['etq']}.",
                          f"{num((act.p > .5).sum())} clientes activos con más de 50 % de probabilidad de portarse."],
            "decisiones": [{"texto": "Contención proactiva para los 1.500 clientes de mayor riesgo (bono de datos + llamada)", "impacto": f"{pesos(top.cargo_mensual.sum() + top.monto_recargas.sum() / 12)} de ingreso mensual en juego", "area": "Comercial"},
                           {"texto": "Priorizar inversión de red en las celdas con más clientes en riesgo", "impacto": "ataca la causa", "area": "Red"}],
            "grafico": {"tipo": "barras", "titulo": "Qué explica la portabilidad (importancia %)", "datos": imp},
            "tabla": {"titulo": "Clientes activos con más riesgo", "cols": ["Cliente", "Plan", "Región", "Caídas (año)", "NPS", "Riesgo"],
                      "filas": [[int(r.cliente_id), r.plan, r.region, int(r.caidas), num(r.nps, 1), f"{r.p * 100:.0f} %"] for r in top.head(10).itertuples()]}}


def t_upsell():
    df = telco360()
    post = df[(df.tipo == "Postpago") & (df.estado == "Activo")].copy()
    y = (post.upgrades > 0).astype(int)
    m, X, auc0, auc, imp = _clasificar(post, y, ["cargo_mensual", "edad", "antiguedad_meses"], ["datos_gb", "meses_sobre", "minutos", "nps", "roaming"], OT, 5)
    post["p"] = m.predict_proba(X)[:, 1]
    cand = post[(post.upgrades == 0)].sort_values("p", ascending=False)
    top = cand.head(2000)
    return {"metrica": {"nombre": "AUC (propensión a subir de plan)", "valor": auc, "fmt": num(auc, 2), "umbral": .7},
            "comparacion": [{"etq": "Solo plan y cargo", "valor": round(auc0, 2)}, {"etq": "Con el uso real de la red (CDR)", "valor": round(auc, 2)}],
            "apps": ["t_bss", "t_cdr", "t_ventas", "t_atencion"], "filas": int(len(post)),
            "hallazgos": ["Los meses con sobreconsumo de datos (CDR) son la mejor señal de que el cliente subirá de plan.",
                          f"{num(len(cand[cand.p > .4]))} clientes de postpago sin upgrade con propensión sobre 40 %."],
            "decisiones": [{"texto": "Oferta de upgrade al plan siguiente para los 2.000 de mayor propensión, en la app Mi cuenta", "impacto": f"{pesos(2000 * 5000 * .3)} mensuales si acepta el 30 %", "area": "Comercial"}],
            "grafico": {"tipo": "barras", "titulo": "Qué anticipa un upgrade (importancia %)", "datos": imp}}


def t_red_vs_fuga():
    df = telco360()
    df["q"] = pd.qcut(df.latencia.rank(method="first"), 5, labels=["Q1 mejor", "Q2", "Q3", "Q4", "Q5 peor"])
    g = df.groupby("q", observed=True).agg(clientes=("cliente_id", "count"), porta=("estado", lambda s: (s == "Portado").mean()), lat=("latencia", "mean"))
    peores = df.groupby("celda_id").agg(clientes=("cliente_id", "count"), porta=("estado", lambda s: (s == "Portado").mean()), lat=("latencia", "mean"),
                                         region=("region", "first")).query("clientes >= 20").sort_values(["porta", "lat"], ascending=False).head(10)
    r = float(np.corrcoef(df.latencia, (df.estado == "Portado").astype(int))[0, 1])
    return {"metrica": {"nombre": "Portabilidad en el peor quintil de red vs el mejor", "valor": float(g.porta.iloc[-1] / max(g.porta.iloc[0], .001)),
                        "fmt": f"×{g.porta.iloc[-1] / max(g.porta.iloc[0], .001):.1f}", "umbral": 1.2},
            "apps": ["t_oss", "t_bss"], "filas": int(len(df)),
            "hallazgos": [f"En las celdas del peor quintil de latencia se porta el {num(g.porta.iloc[-1] * 100, 1)} % de los clientes; en el mejor, el {num(g.porta.iloc[0] * 100, 1)} %.",
                          "La calidad de red (OSS) es una palanca comercial: cada mejora en las peores celdas retiene clientes."],
            "decisiones": [{"texto": f"Plan de mejora de red en las 10 celdas con más fuga (principalmente {peores.region.mode().iloc[0]})", "impacto": f"{num(peores.clientes.sum())} clientes afectados", "area": "Red"}],
            "grafico": {"tipo": "barras", "titulo": "% de portabilidad por quintil de latencia", "datos": [{"etq": str(k), "valor": round(v * 100, 1), "app": "t_oss"} for k, v in g.porta.items()]},
            "tabla": {"titulo": "Celdas con más fuga", "cols": ["Celda", "Región", "Clientes", "Latencia ms", "% portados"],
                      "filas": [[int(i), x.region, int(x.clientes), num(x.lat, 0), f"{x.porta * 100:.0f} %"] for i, x in peores.iterrows()]}}


def t_mora():
    df = telco360()
    post = df[df.tipo == "Postpago"].copy()
    y = (post.mora_max >= 60).astype(int)
    m, X, auc0, auc, imp = _clasificar(post, y, ["cargo_mensual", "edad", "antiguedad_meses", "equipo_en_cuotas"], ["nps", "tickets", "datos_gb", "minutos", "recargas"], OT, 6)
    return {"metrica": {"nombre": "AUC (anticipar mora de 60 días)", "valor": auc, "fmt": num(auc, 2), "umbral": .7},
            "comparacion": [{"etq": "Solo datos del contrato", "valor": round(auc0, 2)}, {"etq": "Con uso y atención", "valor": round(auc, 2)}],
            "apps": ["t_bss", "t_cdr", "t_atencion"], "filas": int(len(post)),
            "hallazgos": [f"{num(y.mean() * 100, 1)} % de los clientes de postpago llegó a 60 días de mora en el año.",
                          "En estos datos la mora depende poco del uso y la atención: el modelo no mejora mucho al cruzar (resultado honesto de la evaluación)."],
            "decisiones": [{"texto": "Cobranza preventiva por WhatsApp al primer día de atraso", "impacto": "menos clientes llegan a 60 días", "area": "Cobranza"}],
            "grafico": {"tipo": "barras", "titulo": "Qué explica la mora (importancia %)", "datos": imp}}


def t_recargas():
    df = lago.sql(f"SELECT date_trunc('day', fecha) AS dia, sum(monto) AS total FROM {tabla('t_ventas')} WHERE tipo = 'Recarga' GROUP BY 1")
    df["dia"] = pd.to_datetime(df.dia)
    s = df.set_index("dia").total.astype(float).asfreq("D", fill_value=0)
    mape, pf, g = A._pronostico(s, "Monto de recargas por día (últimos 60 y próximos 30)", "$", pesos)
    canal = lago.sql(f"SELECT canal, sum(monto) AS monto FROM {tabla('t_ventas')} WHERE tipo = 'Recarga' GROUP BY 1 ORDER BY 2 DESC")
    return {"metrica": {"nombre": "Error del pronóstico de recargas (MAPE)", "valor": 1 - mape, "fmt": f"{num(mape * 100, 1)} %", "umbral": .85},
            "apps": ["t_ventas"], "filas": int(len(s)),
            "hallazgos": [f"Próximos 30 días: {pesos(pf.sum())} en recargas.", f"El canal que más recarga es {canal.canal.iloc[0]}."],
            "decisiones": [{"texto": "Bono de recarga los días de menor venta pronosticada, por el canal App", "impacto": "aplana la demanda y sube recarga promedio", "area": "Comercial"}],
            "grafico": g}


def t_nps():
    df = lago.sql(f"""SELECT motivo, canal, count(*) AS tickets, avg(CAST(nps AS double)) AS nps,
                      avg(CASE WHEN resuelto_primer_contacto THEN 1e0 ELSE 0e0 END) AS fcr FROM {tabla('t_atencion')} GROUP BY 1, 2""")
    for k in ("tickets", "nps", "fcr"):
        df[k] = pd.to_numeric(df[k])
    mot = df.groupby("motivo").apply(lambda x: pd.Series({"tickets": x.tickets.sum(), "nps": np.average(x.nps, weights=x.tickets)}), include_groups=False).sort_values("nps")
    resumen, seg = A.granite([{"role": "system", "content": "Eres analista de experiencia de clientes de un operador móvil. Responde en español en 2 frases."},
                              {"role": "user", "content": "NPS por motivo de contacto: " + ", ".join(f"{k} {v:.1f}" for k, v in mot.nps.items()) + ". ¿Qué priorizarías?"}], 120)
    return {"metrica": {"nombre": "NPS del peor motivo", "valor": float(mot.nps.iloc[0]), "fmt": num(mot.nps.iloc[0], 1), "umbral": 0},
            "apps": ["t_atencion"], "filas": int(df.tickets.sum()),
            "llm": {"texto": resumen or f"El peor NPS está en «{mot.index[0]}»: hay que priorizar esos casos y derivarlos a resolución técnica.", "segundos": seg, "modelo": A.MODELO if seg else None},
            "hallazgos": [f"«{mot.index[0]}» tiene el peor NPS ({num(mot.nps.iloc[0], 1)}) con {num(mot.tickets.iloc[0])} tickets."],
            "decisiones": [{"texto": f"Cola prioritaria y técnico de red para tickets de «{mot.index[0]}»", "impacto": "sube NPS donde más cae", "area": "Atención"}],
            "grafico": {"tipo": "barras", "titulo": "NPS por motivo", "datos": [{"etq": k, "valor": round(v, 1), "app": "t_atencion"} for k, v in mot.nps.items()]}}


A.SOLUCIONES.update({
    "b_fuga": {"nombre": "Fuga de clientes del banco", "tipo": "Clasificación supervisada", "tecnica": "Gradient Boosting", "fn": b_fuga, "desc": "Anticipa qué clientes cerrarán sus productos, cruzando reclamos, mora y uso."},
    "b_segmentos": {"nombre": "Segmentos por valor de vida", "tipo": "Clustering", "tecnica": "K-Means", "fn": b_segmentos, "desc": "Agrupa a los clientes por valor, digitalización y servicio."},
    "b_preaprobados": {"nombre": "Créditos preaprobados", "tipo": "Propensión", "tecnica": "Gradient Boosting", "fn": b_preaprobados, "desc": "Elige a quién ofrecer crédito antes de que lo pida, con monto sugerido."},
    "b_riesgo": {"nombre": "Riesgo de mora", "tipo": "Scoring", "tecnica": "Gradient Boosting", "fn": b_riesgo, "desc": "Anticipa la mora a 90 días combinando buró, renta y comportamiento."},
    "b_fraude": {"nombre": "Fraude en tarjetas", "tipo": "Detección de anomalías", "tecnica": "Isolation Forest", "fn": b_fraude, "desc": "Marca autorizaciones fuera de lo normal para cada cliente."},
    "b_mejor_oferta": {"nombre": "Mejor oferta y canal", "tipo": "Analítica de conversión", "tecnica": "Tasas por canal y producto", "fn": b_mejor_oferta, "desc": "Qué ofrecer y por dónde, según lo que miran y aceptan."},
    "t_portabilidad": {"nombre": "Portabilidad y fuga", "tipo": "Clasificación supervisada", "tecnica": "Gradient Boosting", "fn": t_portabilidad, "desc": "Anticipa quién se porta cruzando red, uso, atención y facturación."},
    "t_mora": {"nombre": "Riesgo de mora", "tipo": "Scoring", "tecnica": "Gradient Boosting", "fn": t_mora, "desc": "Anticipa la mora de 60 días en postpago."},
    "t_upsell": {"nombre": "Upgrade de plan", "tipo": "Propensión", "tecnica": "Gradient Boosting", "fn": t_upsell, "desc": "A quién ofrecer el plan siguiente según su uso real."},
    "t_red_vs_fuga": {"nombre": "Impacto de la red en la fuga", "tipo": "Análisis causal", "tecnica": "Quintiles y correlación", "fn": t_red_vs_fuga, "desc": "Cuánta fuga explica la calidad de la celda."},
    "t_recargas": {"nombre": "Pronóstico de recargas", "tipo": "Series de tiempo", "tecnica": "Regresión con estacionalidad", "fn": t_recargas, "desc": "Proyecta las recargas de prepago de los próximos 30 días."},
    "t_nps": {"nombre": "Priorización por NPS con IA generativa", "tipo": "IA generativa (LLM)", "tecnica": "IBM Granite", "fn": t_nps, "desc": "Dónde cae el NPS y qué priorizar, en lenguaje natural."},
})

A.AGENTES.update({
    "b_core": {"nombre": "Agente de clientes", "rol": "Retener y crecer la cartera", "soluciones": ["b_fuga", "b_segmentos"],
               "preguntas": [("¿Cuántos clientes se fueron por segmento?", "SELECT segmento, count(*) AS clientes, sum(CASE WHEN estado='Baja' THEN 1 ELSE 0 END) AS bajas FROM lake.banca_core.clientes GROUP BY 1 ORDER BY 3 DESC"),
                             ("¿Cuánto se mueve por canal?", "SELECT canal, count(*) AS movimientos, sum(monto) AS monto FROM lake.banca_core.movimientos GROUP BY 1 ORDER BY 3 DESC")]},
    "b_creditos": {"nombre": "Agente de créditos", "rol": "Colocar más con menos riesgo", "soluciones": ["b_preaprobados", "b_riesgo"],
                   "preguntas": [("¿Cuál es la tasa de aprobación por producto?", "SELECT producto, count(*) AS solicitudes, avg(CASE WHEN estado='Aprobada' THEN 1e0 ELSE 0e0 END) AS aprobacion FROM lake.banca_creditos.solicitudes GROUP BY 1"),
                                 ("¿Cuánta mora hay por producto?", "SELECT producto, avg(CASE WHEN mora_90 THEN 1e0 ELSE 0e0 END) AS mora FROM lake.banca_creditos.solicitudes WHERE estado='Aprobada' GROUP BY 1")]},
    "b_tarjetas": {"nombre": "Agente de tarjetas", "rol": "Cuidar las compras de los clientes", "soluciones": ["b_fraude"],
                   "preguntas": [("¿Cuánto se compra por rubro?", "SELECT rubro, count(*) AS compras, sum(monto) AS monto FROM lake.banca_tarjetas.autorizaciones GROUP BY 1 ORDER BY 3 DESC")]},
    "b_digital": {"nombre": "Agente digital", "rol": "Convertir visitas en productos", "soluciones": ["b_mejor_oferta"],
                  "preguntas": [("¿Qué productos se miran más en la app?", "SELECT producto_visto, count(*) AS vistas, sum(CASE WHEN simulo THEN 1 ELSE 0 END) AS simulaciones FROM lake.banca_digital.sesiones GROUP BY 1 ORDER BY 2 DESC")]},
    "b_contacto": {"nombre": "Agente de campañas", "rol": "Ofertas que se aceptan", "soluciones": ["b_mejor_oferta"],
                   "preguntas": [("¿Qué canal acepta más campañas?", "SELECT canal, count(*) AS campanas, avg(CASE WHEN resultado='Aceptó' THEN 1e0 ELSE 0e0 END) AS aceptacion FROM lake.banca_contacto.interacciones WHERE tipo='Campaña' GROUP BY 1 ORDER BY 3 DESC")]},
    "t_bss": {"nombre": "Agente de clientes", "rol": "Retener la base y cobrar", "soluciones": ["t_portabilidad", "t_mora"],
              "preguntas": [("¿Cuántos clientes se portaron por plan?", "SELECT plan, count(*) AS clientes, sum(CASE WHEN estado='Portado' THEN 1 ELSE 0 END) AS portados FROM lake.telco_bss.clientes GROUP BY 1 ORDER BY 3 DESC")]},
    "t_cdr": {"nombre": "Agente de uso", "rol": "Vender según el uso real", "soluciones": ["t_upsell"],
              "preguntas": [("¿Cuántos datos se usan por mes?", "SELECT mes, avg(datos_gb) AS gb_promedio, sum(CASE WHEN sobre_consumo THEN 1 ELSE 0 END) AS clientes_sobre_plan FROM lake.telco_cdr.uso_mensual GROUP BY 1 ORDER BY 1")]},
    "t_oss": {"nombre": "Agente de red", "rol": "La red como palanca comercial", "soluciones": ["t_red_vs_fuga"],
              "preguntas": [("¿Qué regiones tienen peor latencia?", "SELECT region, avg(latencia_ms) AS latencia, avg(disponibilidad_pct) AS disponibilidad FROM lake.telco_oss.celdas GROUP BY 1 ORDER BY 2 DESC")]},
    "t_ventas": {"nombre": "Agente de ventas", "rol": "Más recargas y upgrades", "soluciones": ["t_recargas"],
                 "preguntas": [("¿Cuánto se recarga por canal?", "SELECT canal, count(*) AS recargas, sum(monto) AS monto FROM lake.telco_ventas.operaciones WHERE tipo='Recarga' GROUP BY 1 ORDER BY 3 DESC")]},
    "t_atencion": {"nombre": "Agente de atención", "rol": "Subir el NPS donde más cae", "soluciones": ["t_nps"],
                   "preguntas": [("¿Qué motivo tiene peor NPS?", "SELECT motivo, count(*) AS tickets, avg(CAST(nps AS double)) AS nps FROM lake.telco_atencion.tickets GROUP BY 1 ORDER BY 3")]},
})
