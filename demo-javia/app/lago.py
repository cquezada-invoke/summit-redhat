"""El data lake central de JavIA: las aplicaciones del negocio vuelcan aquí sus datos (una zona por aplicación).

Motor: Trino sobre S3 (el lake de la demo, catálogo «lake») o, para pruebas locales, DuckDB en memoria con el mismo
esquema. Los datos son emulados pero coherentes entre aplicaciones: comparten cliente_id, sucursal, región y fecha,
y tienen causas reales escondidas (la insatisfacción de un cliente se ve en soporte, en sus pagos rechazados y en sus
despachos atrasados, y termina en fuga), así los agentes encuentran señales cuando cruzan datos.
"""
import datetime as dt
import io
import json
import os
import threading
import time

import numpy as np
import pandas as pd
import requests

MOTOR = os.environ.get("LAGO", "trino")          # trino | duckdb
S3 = os.environ.get("S3_URL", "http://minio.demo-datalake.svc:9000").rstrip("/")
TRINO = os.environ.get("TRINO_URL", "http://trino.demo-datalake.svc:8080").rstrip("/")
BUCKET = "lake"
LOCAL = os.environ.get("JAVIA_DATOS", "/tmp/javia")

# ─────────── Las aplicaciones del negocio que vuelcan al lake ───────────
APPS = {
    "crm":       {"nombre": "CRM comercial", "plataforma": "JBoss EAP · VM on-premise", "icono": "crm", "color": "#4F46E5",
                  "desc": "Clientes, planes, valor mensual y bajas", "tablas": ["clientes"]},
    "pagos":     {"nombre": "App de pagos", "plataforma": "OpenShift", "icono": "pagos", "color": "#0E9F6E",
                  "desc": "Transacciones, medios de pago y rechazos", "tablas": ["transacciones"]},
    "ecommerce": {"nombre": "Tienda web", "plataforma": "OpenShift", "icono": "web", "color": "#D97706",
                  "desc": "Sesiones, carritos y compras online", "tablas": ["sesiones"]},
    "soporte":   {"nombre": "Mesa de atención", "plataforma": "SaaS (ITSM)", "icono": "soporte", "color": "#DB2777",
                  "desc": "Tickets, textos de los clientes y satisfacción", "tablas": ["tickets"]},
    "logistica": {"nombre": "Despachos", "plataforma": "RHEL · VM on-premise", "icono": "logistica", "color": "#0284C7",
                  "desc": "Envíos, transportistas y atrasos", "tablas": ["envios"]},
    "rrhh":      {"nombre": "Personas", "plataforma": "SaaS (RR.HH.)", "icono": "rrhh", "color": "#7C3AED",
                  "desc": "Dotación, ausentismo y rotación por sucursal", "tablas": ["dotacion"]},
}
LLAVES = {"cliente_id": ["crm", "pagos", "ecommerce", "soporte", "logistica"],
          "sucursal": ["crm", "logistica", "rrhh"], "region": ["crm", "pagos", "logistica", "rrhh"],
          "fecha": ["pagos", "ecommerce", "soporte", "logistica", "rrhh"]}

def esquema(app):
    return APPS.get(app, {}).get("esquema") or f"app_{app}"


INDUSTRIAS = {"retail": {"nombre": "Retail y servicios", "desc": "Cadena con tienda web, pagos, despachos y sucursales", "llaves": LLAVES}}
GENERADORES = {}   # industria → función que devuelve {(app, tabla): DataFrame}


def apps_de(ind):
    return {k: v for k, v in APPS.items() if v.get("industria", "retail") == ind}


def tabla(app, t=None):
    return f"lake.{esquema(app)}.{t or APPS[app]['tablas'][0]}"


# ─────────── Generación de los datos emulados ───────────
SUCURSALES = [("Santiago Centro", "Metropolitana"), ("Providencia", "Metropolitana"), ("Maipú", "Metropolitana"),
              ("Puente Alto", "Metropolitana"), ("Valparaíso", "Valparaíso"), ("Viña del Mar", "Valparaíso"),
              ("Concepción", "Biobío"), ("Temuco", "Araucanía"), ("Antofagasta", "Antofagasta"), ("La Serena", "Coquimbo"),
              ("Rancagua", "O'Higgins"), ("Puerto Montt", "Los Lagos")]
FIN = dt.datetime(2026, 10, 31, 23, 59)
INI = FIN - dt.timedelta(days=365)

TEXTOS = {
    "Cobro indebido": ["Me cobraron dos veces la misma compra", "Aparece un cargo que no reconozco en mi cuenta",
                       "El cobro mensual subió sin aviso"],
    "Despacho atrasado": ["Mi pedido no ha llegado y ya pasó la fecha prometida", "El envío lleva días detenido",
                          "Me dijeron que llegaba ayer y nada"],
    "Pago rechazado": ["Mi tarjeta fue rechazada sin motivo", "No puedo pagar en la app, da error", "Intenté pagar tres veces y falla"],
    "Cambio de plan": ["Quiero cambiarme a un plan más barato", "Necesito información para mejorar mi plan",
                       "Cómo cambio el plan que tengo"],
    "Consulta general": ["Necesito un certificado de mi cuenta", "Cuál es el horario de la sucursal", "Cómo actualizo mis datos"],
}
NEGATIVO = [" Estoy muy molesto, es la tercera vez.", " Pésimo servicio, estoy pensando en irme.", " Nadie me responde.",
            " Si no lo resuelven me cambio de empresa."]
POSITIVO = [" Gracias por la ayuda.", " Muy amables en la atención.", ""]


def generar(semilla=7):
    """Devuelve {(app, tabla): DataFrame} con un año de datos coherentes entre aplicaciones."""
    rng = np.random.default_rng(semilla)
    n = 6000
    # sucursales: ausentismo propio (la causa escondida de los atrasos de despacho y la mala atención)
    aus_suc = rng.uniform(2.0, 11.0, len(SUCURSALES))
    aus_suc[[3, 7, 8]] += 4.0     # Puente Alto, Temuco y Antofagasta con problemas de dotación
    suc_i = rng.integers(0, len(SUCURSALES), n)
    inc = rng.beta(2, 5, n)                                   # insatisfacción latente del cliente (0..1)
    inc += (aus_suc[suc_i] - 6) / 30
    inc = np.clip(inc, 0, 1)
    segmento = rng.choice(["Personas", "Pyme", "Premium", "Empresas"], n, p=[.55, .2, .15, .1])
    valor = np.round(np.where(segmento == "Premium", rng.normal(85, 15, n), np.where(segmento == "Empresas", rng.normal(140, 30, n),
                     np.where(segmento == "Pyme", rng.normal(60, 12, n), rng.normal(32, 8, n)))).clip(12, 260) * 1000, -2)
    alta = [INI - dt.timedelta(days=int(d)) for d in rng.integers(30, 2400, n)]
    edad = rng.integers(19, 78, n)
    actividad = rng.gamma(2.2, 1.0, n) * np.where(segmento == "Personas", 1.0, 1.6)     # intensidad de uso
    clientes = pd.DataFrame({"cliente_id": np.arange(1, n + 1), "segmento": segmento,
                             "sucursal": [SUCURSALES[i][0] for i in suc_i], "region": [SUCURSALES[i][1] for i in suc_i],
                             "canal_alta": rng.choice(["Sucursal", "Web", "App", "Ejecutivo"], n, p=[.35, .3, .2, .15]),
                             "fecha_alta": alta, "edad": edad, "plan": rng.choice(["Básico", "Plus", "Full"], n, p=[.5, .35, .15]),
                             "valor_mensual": valor})

    # pagos: los insatisfechos tienen más rechazos; el fraude son montos raros, de noche, en dispositivo nuevo o desde fuera
    k = rng.poisson(actividad * 7.5) + 1
    cid = np.repeat(clientes.cliente_id.values, k)
    m = len(cid)
    idx = cid - 1
    seg_ = rng.integers(0, 365 * 24 * 3600, m)
    fechas = pd.to_datetime(INI) + pd.to_timedelta(seg_, unit="s")
    dow_boost = np.isin(fechas.dayofweek, [4, 5])
    base_monto = rng.lognormal(10.0, 0.8, m) * (1 + .25 * dow_boost)
    fraude = rng.random(m) < 0.004
    monto = np.where(fraude, base_monto * rng.uniform(4, 12, m), base_monto)
    hora = np.where(fraude, rng.choice([1, 2, 3, 4], m), fechas.hour)
    fechas = fechas.normalize() + pd.to_timedelta(hora, unit="h") + pd.to_timedelta(rng.integers(0, 3600, m), unit="s")
    rech_p = 0.03 + 0.18 * inc[idx]
    rechazada = rng.random(m) < rech_p
    pagos = pd.DataFrame({"tx_id": np.arange(1, m + 1), "cliente_id": cid, "fecha": fechas,
                          "monto": np.round(monto, -1), "medio": rng.choice(["Débito", "Crédito", "Transferencia", "Billetera"], m, p=[.4, .35, .15, .1]),
                          "rubro": rng.choice(["Supermercado", "Combustible", "Farmacia", "Tecnología", "Viajes", "Restaurantes", "Servicios"], m),
                          "canal": rng.choice(["App", "POS", "Web"], m, p=[.45, .4, .15]),
                          "pais": np.where(fraude & (rng.random(m) < .6), rng.choice(["Perú", "Argentina", "EE.UU.", "Brasil"], m), "Chile"),
                          "dispositivo_nuevo": np.where(fraude, rng.random(m) < .8, rng.random(m) < .05),
                          "estado": np.where(rechazada, "Rechazada", "Aprobada"),
                          "motivo_rechazo": np.where(rechazada, rng.choice(["Fondos insuficientes", "Error de sistema", "Tarjeta bloqueada", "Timeout"], m), ""),
                          "fraude_confirmado": fraude})
    pagos["region"] = clientes.region.values[idx]

    # tienda web: sesiones; compran más los activos y menos los insatisfechos
    ks = rng.poisson(actividad * 3.2)
    cs = np.repeat(clientes.cliente_id.values, ks)
    s = len(cs)
    ix = cs - 1
    fs = pd.to_datetime(INI) + pd.to_timedelta(rng.integers(0, 365 * 24 * 3600, s), unit="s")
    pag = rng.poisson(6, s) + 1
    minutos = np.round(pag * rng.uniform(.4, 1.6, s), 1)
    disp = rng.choice(["Móvil", "Escritorio", "Tablet"], s, p=[.62, .32, .06])
    carrito = np.where(rng.random(s) < .55, np.round(rng.lognormal(10.3, .7, s), -2), 0)
    logit = -1.6 + .18 * pag + .9 * (carrito > 0) - 4.0 * inc[ix] + .4 * (disp == "Escritorio") + .25 * (clientes.segmento.values[ix] == "Premium")
    compro = rng.random(s) < 1 / (1 + np.exp(-logit))
    sesiones = pd.DataFrame({"sesion_id": np.arange(1, s + 1), "cliente_id": cs, "fecha": fs, "dispositivo": disp,
                             "paginas": pag, "minutos": minutos, "carrito_monto": carrito, "compro": compro & (carrito > 0)})

    # logística: atrasos por transportista, ruta y ausentismo de la sucursal
    ke = rng.poisson(actividad * 1.4)
    ce = np.repeat(clientes.cliente_id.values, ke)
    e = len(ce)
    ie = ce - 1
    fe = pd.to_datetime(INI) + pd.to_timedelta(rng.integers(0, 365 * 24 * 3600, e), unit="s")
    trans = rng.choice(["RutaSur", "ExpressCL", "Andes Cargo", "Propio"], e, p=[.3, .3, .2, .2])
    sidx = suc_i[ie]
    prom = rng.choice([1, 2, 3, 5], e, p=[.25, .4, .25, .1])
    ruta = rng.normal(0, .18, len(SUCURSALES))
    lam = .12 + ruta[sidx] + .25 * (trans == "RutaSur") + .45 * (trans == "Andes Cargo") + (aus_suc[sidx] - 6) * .09 + 0.6 * np.isin(fe.month, [11, 12])
    retraso = rng.poisson(np.clip(lam, .05, None))
    envios = pd.DataFrame({"envio_id": np.arange(1, e + 1), "cliente_id": ce, "fecha": fe,
                           "sucursal": [SUCURSALES[i][0] for i in sidx], "region": [SUCURSALES[i][1] for i in sidx],
                           "transportista": trans, "dias_prometidos": prom, "dias_reales": prom + retraso,
                           "atrasado": retraso > 0, "costo": np.round(rng.uniform(2500, 9000, e), -1)})

    # soporte: más tickets y más negativos si el cliente está insatisfecho o le llegó tarde un despacho
    atrasos_cli = envios.groupby("cliente_id").atrasado.sum().reindex(clientes.cliente_id, fill_value=0).values
    rech_cli = pagos.groupby("cliente_id").estado.apply(lambda x: (x == "Rechazada").sum()).reindex(clientes.cliente_id, fill_value=0).values
    kt = rng.poisson(.25 + 2.2 * inc + .25 * atrasos_cli + .08 * rech_cli)
    ct = np.repeat(clientes.cliente_id.values, kt)
    t = len(ct)
    it = ct - 1
    ft = pd.to_datetime(INI) + pd.to_timedelta(rng.integers(0, 365 * 24 * 3600, t), unit="s")
    pc = np.stack([.12 + .1 * inc[it], .1 + .05 * np.minimum(atrasos_cli[it], 4), .08 + .02 * np.minimum(rech_cli[it], 6),
                   np.full(t, .15), np.full(t, .25)], 1)
    pc = pc / pc.sum(1, keepdims=True)
    cats = list(TEXTOS)
    cat = np.array([cats[rng.choice(5, p=p)] for p in pc])
    neg = rng.random(t) < (.05 + .55 * inc[it])
    # el texto no siempre calza con la categoría que registró el ejecutivo, y no toda molestia se escribe explícita
    txt_cat = np.where(rng.random(t) < .12, rng.choice(cats, t), cat)
    explicita = rng.random(t) < .85
    textos = [rng.choice(TEXTOS[c]) + (rng.choice(NEGATIVO) if ng and ex else rng.choice(POSITIVO)) for c, ng, ex in zip(txt_cat, neg, explicita)]
    aus_t = aus_suc[suc_i[it]]
    horas = np.round(rng.gamma(2, 6 + aus_t * .9, t), 1)
    sat = np.clip(np.round(4.6 - 2.2 * neg - .04 * horas + rng.normal(0, .5, t)), 1, 5).astype(int)
    tickets = pd.DataFrame({"ticket_id": np.arange(1, t + 1), "cliente_id": ct, "fecha": ft,
                            "canal": rng.choice(["Teléfono", "Chat", "Correo", "Sucursal"], t, p=[.35, .35, .2, .1]),
                            "categoria": cat, "texto": textos, "prioridad": np.where(neg, "Alta", rng.choice(["Media", "Baja"], t)),
                            "horas_resolucion": horas, "satisfaccion": sat, "sentimiento": np.where(neg, "Negativo", "Neutro/positivo")})

    # fuga (CRM): la consecuencia de todo lo anterior
    tick_cli = tickets.groupby("cliente_id").size().reindex(clientes.cliente_id, fill_value=0).values
    neg_cli = tickets[tickets.sentimiento == "Negativo"].groupby("cliente_id").size().reindex(clientes.cliente_id, fill_value=0).values
    zf = -5.0 + 5.5 * inc + .45 * neg_cli + .3 * atrasos_cli + .08 * rech_cli - .15 * actividad - .5 * (segmento == "Empresas") + .3 * (clientes.plan.values == "Básico")
    fuga = rng.random(n) < 1 / (1 + np.exp(-zf))
    clientes["estado"] = np.where(fuga, "Baja", "Activo")
    clientes["fecha_baja"] = [INI + dt.timedelta(days=int(d)) if f else pd.NaT for f, d in zip(fuga, rng.integers(60, 365, n))]

    # personas: dotación mensual por sucursal
    filas = []
    for i, (suc, reg) in enumerate(SUCURSALES):
        for mm in range(13):
            mes = (INI.replace(day=1, hour=0, minute=0) + pd.DateOffset(months=mm)).to_pydatetime()
            a = max(.5, aus_suc[i] + rng.normal(0, 1.2) + (1.5 if mes.month in (6, 7) else 0))
            filas.append({"sucursal": suc, "region": reg, "mes": mes, "dotacion": int(rng.integers(18, 60)),
                          "ausentismo_pct": round(a, 1), "rotacion_pct": round(max(0, a * .45 + rng.normal(1, .8)), 1),
                          "horas_capacitacion": int(rng.integers(0, 40))})
    dotacion = pd.DataFrame(filas)
    return {("crm", "clientes"): clientes, ("pagos", "transacciones"): pagos, ("ecommerce", "sesiones"): sesiones,
            ("soporte", "tickets"): tickets, ("logistica", "envios"): envios, ("rrhh", "dotacion"): dotacion}


# ─────────── Motores ───────────
def _tipo(s):
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_integer_dtype(s):
        return "bigint"
    if pd.api.types.is_float_dtype(s):
        return "double"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "timestamp(3)"
    return "varchar"


def _normalizar(df):
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_integer_dtype(df[c]):
            df[c] = df[c].astype("int64")
        elif pd.api.types.is_datetime64_any_dtype(df[c]) or c.startswith("fecha") or c == "mes":
            df[c] = pd.to_datetime(df[c]).astype("datetime64[ms]")
    return df


class Trino:
    nombre = "Trino sobre S3"

    def sql(self, q, usuario="javia"):
        h = {"X-Trino-User": usuario, "X-Trino-Catalog": "lake"}
        r = requests.post(f"{TRINO}/v1/statement", data=q.encode(), headers=h, timeout=30).json()
        cols, filas = None, []
        while True:
            if r.get("columns") and cols is None:
                cols = [c["name"] for c in r["columns"]]
                tipos = [c["type"] for c in r["columns"]]
            filas += r.get("data") or []
            if r.get("error"):
                raise ValueError(r["error"]["message"])
            if "nextUri" not in r:
                break
            r = requests.get(r["nextUri"], headers=h, timeout=180).json()
        df = pd.DataFrame(filas, columns=cols or [])
        for c, ty in zip(cols or [], tipos if cols else []):
            if ty.startswith("timestamp") or ty == "date":
                df[c] = pd.to_datetime(df[c], errors="coerce")
        return df

    def cargar(self, app, t, df):
        import pyarrow as pa
        import pyarrow.parquet as pq
        df = _normalizar(df)
        b = io.BytesIO()
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), b, coerce_timestamps="ms", allow_truncated_timestamps=True)
        pref = f"apps/{esquema(app)}/{t}"
        requests.put(f"{S3}/{BUCKET}/{pref}/{t}.parquet", data=b.getvalue(), timeout=120).raise_for_status()
        self.sql(f"CREATE SCHEMA IF NOT EXISTS lake.{esquema(app)} WITH (location = 's3://{BUCKET}/apps/{esquema(app)}/')")
        self.sql(f"DROP TABLE IF EXISTS {tabla(app, t)}")
        cols = ", ".join(f"{c} {_tipo(df[c])}" for c in df.columns)
        self.sql(f"CREATE TABLE {tabla(app, t)} ({cols}) WITH (format = 'PARQUET', external_location = 's3://{BUCKET}/{pref}/')")

    def esquemas(self):
        return [s for s in self.sql("SHOW SCHEMAS FROM lake").iloc[:, 0].tolist() if s not in ("information_schema", "default")]

    def guardar_json(self, clave, obj):
        requests.put(f"{S3}/{BUCKET}/javia/sistema/{clave}", data=json.dumps(obj, ensure_ascii=False, default=str).encode(),
                     headers={"Content-Type": "application/json"}, timeout=20).raise_for_status()

    def leer_json(self, clave):
        r = requests.get(f"{S3}/{BUCKET}/javia/sistema/{clave}", timeout=20)
        return r.json() if r.status_code == 200 else None


class Local:
    nombre = "DuckDB local (pruebas)"

    def __init__(self):
        import duckdb
        self.con = duckdb.connect()
        self.con.execute("ATTACH ':memory:' AS lake")
        self.lock = threading.Lock()
        os.makedirs(LOCAL, exist_ok=True)

    def sql(self, q, usuario="javia"):
        with self.lock:
            return self.con.execute(q).df()

    def cargar(self, app, t, df):
        df = _normalizar(df)
        with self.lock:
            self.con.execute(f"CREATE SCHEMA IF NOT EXISTS lake.{esquema(app)}")
            self.con.register("_df", df)
            self.con.execute(f"CREATE OR REPLACE TABLE {tabla(app, t)} AS SELECT * FROM _df")
            self.con.unregister("_df")

    def esquemas(self):
        return sorted(self.sql("SELECT schema_name FROM information_schema.schemata WHERE catalog_name = 'lake'").schema_name
                      .loc[lambda s: ~s.isin(["main", "information_schema", "pg_catalog"])].tolist())

    def guardar_json(self, clave, obj):
        with open(os.path.join(LOCAL, clave), "w") as f:
            json.dump(obj, f, ensure_ascii=False, default=str)

    def leer_json(self, clave):
        try:
            with open(os.path.join(LOCAL, clave)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None


lago = Local() if MOTOR == "duckdb" else Trino()
ESTADO = {"listo": False, "mensaje": "Preparando el data lake…", "cargas": {}}


def sql(q):
    return lago.sql(q)


def asegurar(forzar=False):
    """Crea las zonas de cada aplicación si faltan (el lake de la demo es efímero: se rehace solo)."""
    try:
        GENERADORES.setdefault("retail", generar)
        cargas = lago.leer_json("cargas.json") or {}
        faltan = []
        for ind in INDUSTRIAS:
            try:
                ok = all(int(sql(f"SELECT count(*) AS n FROM {tabla(a, t)}").iloc[0, 0]) > 0 for a in apps_de(ind) for t in APPS[a]["tablas"])
            except Exception:  # noqa: BLE001
                ok = False
            if forzar or not ok:
                faltan.append(ind)
        if not faltan:
            ESTADO.update(listo=True, mensaje="", cargas=cargas)
            return
        for ind in faltan:
            ESTADO.update(listo=False, mensaje=f"Generando datos emulados de {INDUSTRIAS[ind]['nombre']} y volcándolos al lake…")
            vistos = set()
            for (a, t), df in GENERADORES[ind]().items():
                t0 = time.time()
                lago.cargar(a, t, df)
                prev = cargas.get(a) if a in vistos else None
                vistos.add(a)
                cargas[a] = {"tabla": t if not prev else prev["tabla"] + ", " + t, "filas": len(df) + (prev or {}).get("filas", 0),
                             "en": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "segundos": round(time.time() - t0, 1), "formato": "Parquet"}
            ESTADO["cargas"] = cargas
        try:
            lago.guardar_json("cargas.json", cargas)
        except Exception:  # noqa: BLE001
            pass
        ESTADO.update(listo=True, mensaje="")
    except Exception as e:  # noqa: BLE001
        ESTADO.update(listo=False, mensaje=f"El lake no responde todavía ({str(e)[:120]}). Reintentando…")
        raise


def asegurar_en_fondo():
    def ciclo():
        for i in range(120):
            try:
                asegurar()
                return
            except Exception:  # noqa: BLE001
                time.sleep(10)
    threading.Thread(target=ciclo, daemon=True).start()


def catalogo():
    """Todas las zonas del lake (las de JavIA y las que ya existían: telco, fiscalía…) con sus tablas y filas."""
    out = []
    for s in lago.esquemas():
        try:
            if MOTOR == "duckdb":
                ts = sql(f"SELECT table_name FROM information_schema.tables WHERE table_catalog = 'lake' AND table_schema = '{s}'").table_name.tolist()
            else:
                ts = sql(f"SHOW TABLES FROM lake.{s}").iloc[:, 0].tolist()
        except Exception:  # noqa: BLE001
            ts = []
        app = next((k for k in APPS if esquema(k) == s), None)
        filas = []
        for t in ts:
            try:
                nf = int(sql(f"SELECT count(*) FROM lake.{s}.{t}").iloc[0, 0])
            except Exception:  # noqa: BLE001
                nf = None
            filas.append({"tabla": t, "filas": nf})
        out.append({"esquema": s, "app": app, "tablas": filas})
    out.sort(key=lambda z: (z["app"] is None, z["esquema"]))
    return out


def columnas(esq, t):
    if MOTOR == "duckdb":
        df = sql(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_catalog = 'lake' AND table_schema = '{esq}' AND table_name = '{t}' ORDER BY ordinal_position")
    else:
        df = sql(f"SELECT column_name, data_type FROM lake.information_schema.columns WHERE table_schema = '{esq}' AND table_name = '{t}' ORDER BY ordinal_position")
    return list(zip(df.column_name, df.data_type))


def solo_lectura(q):
    import re
    s = q.strip().rstrip(";").strip()
    if ";" in s or not re.match(r"(?is)^(select|with|show|describe)\b", s) or re.search(r"(?i)\b(insert|update|delete|drop|create|alter|grant|call|merge|truncate)\b", s):
        raise ValueError("Solo se permiten consultas de lectura (SELECT).")
    return s
