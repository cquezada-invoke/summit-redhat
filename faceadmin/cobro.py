"""FaceAdmin · cobro de los contratos.

Dos modalidades, y en ambas la suscripción de Red Hat (CCSP) por los nodos que gestiona la automatización:
  1. Plan anual de créditos: se vende por año, se carga al habilitar y cada automatización ejecutada descuenta sus créditos
     (el costo por unidad del catálogo de FaceOps). FaceOps informa el consumo a FaceAdmin.
  2. Cobro fijo por alcance: checklist de automatizaciones contratadas, cada una con su volumen mensual estimado; el valor
     mensual es la suma de créditos × volumen × valor del crédito.
Red Hat CCSP: nodos contratados × valor por nodo al mes. FaceOps informa los nodos que gestiona su AAP y FaceAdmin avisa si
superan lo contratado (se debe ampliar la suscripción)."""
import hashlib
import json
import os

from flask import request

from app import app, ej, q, q1, ahora, bitacora

CATALOGO = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogo.json"), encoding="utf-8"))["automatizaciones"]
POR_NOMBRE = {a["nombre"]: a for a in CATALOGO}
CATEGORIAS = list(dict.fromkeys(a["categoria"] for a in CATALOGO))

# Valores referenciales (CLP, netos): se ajustan en cada propuesta.
VALOR_CREDITO = 20000          # 1 crédito ≈ 1 hora de trabajo manual de un especialista, cobrada al 50 %
VALOR_NODO = 9000              # referencial; ajustar con la lista de precios Red Hat CCSP vigente
PLANES = {"c6k": {"nombre": "Plan 6.000 créditos al año", "creditos": 6000, "valor": 22000},
          "c12k": {"nombre": "Plan 12.000 créditos al año", "creditos": 12000, "valor": 20000},
          "c24k": {"nombre": "Plan 24.000 créditos al año", "creditos": 24000, "valor": 18000},
          "c50k": {"nombre": "Plan 50.000 créditos al año", "creditos": 50000, "valor": 16000}}
MODALIDADES = {"creditos": "Plan anual de créditos", "alcance": "Cobro fijo por alcance"}


def iniciar():
    with app.app_context():
        cols = {r["name"] for r in q("PRAGMA table_info(propuestas)")}
        for col, tipo in (("modalidad", "TEXT DEFAULT ''"), ("plan", "TEXT DEFAULT ''"), ("creditos_anio", "INTEGER DEFAULT 0"),
                          ("valor_credito", "REAL DEFAULT 0"), ("items", "TEXT DEFAULT '[]'"), ("nodos", "INTEGER DEFAULT 0"),
                          ("valor_nodo", "REAL DEFAULT 0"), ("servicio_mensual", "REAL DEFAULT 0"), ("rh_mensual", "REAL DEFAULT 0")):
            if col not in cols:
                ej(f"ALTER TABLE propuestas ADD COLUMN {col} {tipo}")
        if "ref" not in {r["name"] for r in q("PRAGMA table_info(creditos)")}:
            ej("ALTER TABLE creditos ADD COLUMN ref TEXT DEFAULT ''")
        ej("CREATE TABLE IF NOT EXISTS uso (cliente_id INTEGER PRIMARY KEY, fecha TEXT, nodos INTEGER DEFAULT 0, automatizados INTEGER DEFAULT 0, "
           "detalle TEXT DEFAULT '')")


iniciar()


def _n(v, defecto=0.0):
    try:
        return float(str(v).replace(".", "").replace(",", ".")) if str(v).strip() else defecto
    except ValueError:
        return -1.0


def calcular(f):
    """Del formulario de la propuesta: modalidad, detalle y valores. Lanza ValueError si falta algo."""
    mod = f.get("modalidad")
    if mod not in MODALIDADES:
        raise ValueError("Elige la modalidad de cobro")
    nodos = int(_n(f.get("nodos"), 0))
    vnodo = _n(f.get("valor_nodo"), VALOR_NODO)
    if nodos <= 0 or vnodo < 0:
        raise ValueError("Indica cuántos nodos gestionará la automatización (suscripción Red Hat CCSP)")
    r = {"modalidad": mod, "nodos": nodos, "valor_nodo": vnodo, "rh_mensual": round(nodos * vnodo), "plan": "", "creditos_anio": 0, "items": []}
    if mod == "creditos":
        pl = PLANES.get(f.get("plan"))
        if not pl:
            raise ValueError("Elige el plan anual de créditos")
        vc = _n(f.get("valor_credito"), pl["valor"])
        if vc <= 0:
            raise ValueError("El valor del crédito debe ser mayor que cero")
        r.update(plan=f.get("plan"), creditos_anio=pl["creditos"], valor_credito=vc, servicio_mensual=round(pl["creditos"] * vc / 12))
    else:
        vc = _n(f.get("valor_credito"), VALOR_CREDITO)
        if vc <= 0:
            raise ValueError("El valor del crédito debe ser mayor que cero")
        items = []
        for nombre in f.getlist("auto"):
            a = POR_NOMBRE.get(nombre)
            vol = _n(f.get("vol::" + nombre), 1)
            if a and vol > 0:
                items.append({"nombre": nombre, "creditos": a["creditos"], "unidad": a["unidad"], "volumen": vol,
                              "mensual": round(a["creditos"] * vol * vc)})
        if not items:
            raise ValueError("Marca al menos una automatización del alcance")
        r.update(valor_credito=vc, items=items, servicio_mensual=sum(i["mensual"] for i in items))
    r["mensual"] = r["servicio_mensual"] + r["rh_mensual"]
    return r


def detalle(p):
    """Para mostrar una propuesta (o el contrato que nace de ella)."""
    p = dict(p or {})
    try:
        p["items_"] = json.loads(p.get("items") or "[]")
    except ValueError:
        p["items_"] = []
    p["modalidad_"] = MODALIDADES.get(p.get("modalidad") or "", "")
    p["plan_"] = PLANES.get(p.get("plan") or "", {}).get("nombre", "")
    return p


def resumen(p):
    p = detalle(p)
    if p.get("modalidad") == "creditos":
        return f"{p['plan_'] or 'Plan de créditos'}"
    if p.get("modalidad") == "alcance":
        return f"Alcance fijo · {len(p['items_'])} automatizaciones"
    return "Valor mensual (sin modalidad)"


def nodos(cid):
    """Nodos de la suscripción Red Hat: contratados (contrato firmado vigente) y gestionados (informados por FaceOps)."""
    k = q1("SELECT p.nodos FROM contratos k JOIN propuestas p ON p.id=k.propuesta_id WHERE k.cliente_id=? AND k.estado='firmado' "
           "ORDER BY k.id DESC LIMIT 1", cid) or {}
    u = q1("SELECT * FROM uso WHERE cliente_id=?", cid) or {}
    con, usa = int(k.get("nodos") or 0), int(u.get("nodos") or 0)
    return {"contratados": con, "usados": usa, "fecha": u.get("fecha", ""), "exceso": max(0, usa - con) if con else 0,
            "pct": round(100 * usa / con) if con else 0}


def al_habilitar(c):
    """Plan de créditos: se cargan los créditos del año al habilitar (una sola vez por contrato)."""
    k = q1("SELECT k.numero, p.* FROM contratos k JOIN propuestas p ON p.id=k.propuesta_id WHERE k.cliente_id=? AND k.estado='firmado' "
           "ORDER BY k.id DESC LIMIT 1", c["id"])
    if not k or k.get("modalidad") != "creditos" or not k.get("creditos_anio"):
        return
    ref = f"plan:{k['numero']}:1"
    if q1("SELECT id FROM creditos WHERE cliente_id=? AND ref=?", c["id"], ref):
        return
    ej("INSERT INTO creditos (cliente_id, fecha, tipo, monto, detalle, usuario, ref) VALUES (?,?,?,?,?,?,?)", c["id"], ahora(), "carga",
       k["creditos_anio"], f"{PLANES.get(k['plan'], {}).get('nombre', 'Plan anual')} · contrato {k['numero']} · año 1", "sistema", ref)


@app.route("/api/instancia/uso", methods=["POST"])
def api_uso():
    """FaceOps informa (con su llave) los nodos que gestiona su AAP y las automatizaciones ejecutadas con sus créditos.
    Cada ejecución viene con su id: se registra una sola vez."""
    tok = request.headers.get("X-FaceAdmin-Token", "")
    b = q1("SELECT * FROM bloqueos WHERE token=?", hashlib.sha256(tok.encode()).hexdigest()) if tok else None
    if not b:
        return {"error": "no autorizado"}, 401
    d = request.get_json(silent=True) or {}
    cid = b["cliente_id"]
    if "nodos" in d:
        ej("INSERT INTO uso (cliente_id, fecha, nodos, automatizados, detalle) VALUES (?,?,?,?,?) ON CONFLICT(cliente_id) DO UPDATE SET "
           "fecha=excluded.fecha, nodos=excluded.nodos, automatizados=excluded.automatizados, detalle=excluded.detalle", cid, ahora(),
           int(d.get("nodos") or 0), int(d.get("automatizados") or 0), str(d.get("fuente", ""))[:80])
    nuevos = 0
    for e in (d.get("consumo") or [])[:500]:
        ref = f"ej:{str(e.get('id', ''))[:40]}"
        cr = float(e.get("creditos") or 0)
        if not e.get("id") or cr <= 0 or q1("SELECT id FROM creditos WHERE cliente_id=? AND ref=?", cid, ref):
            continue
        ej("INSERT INTO creditos (cliente_id, fecha, tipo, monto, detalle, usuario, ref) VALUES (?,?,?,?,?,?,?)", cid, str(e.get("fecha") or ahora())[:19],
           "consumo", cr, str(e.get("detalle", "ejecución"))[:200], "faceops", ref)
        nuevos += 1
    if nuevos:
        bitacora("consumo informado por FaceOps", (q1("SELECT codigo FROM clientes WHERE id=?", cid) or {}).get("codigo", ""), {"ejecuciones": nuevos})
    return {"ok": True, "registradas": nuevos, "nodos": nodos(cid)}


@app.context_processor
def _ctx_cobro():
    return {"cobro_": {"catalogo": CATALOGO, "categorias": CATEGORIAS, "planes": PLANES, "modalidades": MODALIDADES, "valor_credito": VALOR_CREDITO,
                       "valor_nodo": VALOR_NODO, "resumen": resumen, "detalle": detalle}}
