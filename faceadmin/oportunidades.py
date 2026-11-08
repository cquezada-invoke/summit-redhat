"""FaceAdmin · Oportunidades: el paso previo al contrato. Se crea el cliente y una oportunidad (más reducida que un contrato) y, desde
ella, se puede activar una demo de FaceOps por semanas (máximo 4). La demo habilita la instalación con las licencias demo del cliente
y vence sola; para seguir operando se firma el contrato."""
import json
from datetime import date, timedelta

from flask import abort, flash, g, redirect, render_template, request, url_for

from app import ahora, app, bitacora, cliente_ok, ej, en_alcance, es_invoke, nuevo_id, q, q1, requiere_login, solo_invoke

ETAPAS = [("prospecto", "Prospecto"), ("demo", "En demo"), ("propuesta", "Propuesta enviada"), ("ganada", "Ganada"), ("perdida", "Perdida")]
ETAPA_T = dict(ETAPAS)
TIPOS = {"directo": "Cliente directo", "tenant": "Tenant"}
MAX_SEMANAS = 4


def iniciar():
    with app.app_context():
        ej("CREATE TABLE IF NOT EXISTS oportunidades (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, numero TEXT, tipo TEXT, etapa TEXT DEFAULT 'prospecto', "
           "despliegue TEXT DEFAULT '', plan TEXT DEFAULT '', nodos INTEGER DEFAULT 0, monto_estimado REAL DEFAULT 0, cierre_estimado TEXT DEFAULT '', "
           "notas TEXT DEFAULT '', creado TEXT, por TEXT, demo_semanas INTEGER DEFAULT 0, demo_inicio TEXT DEFAULT '', demo_fin TEXT DEFAULT '', "
           "contrato_demo_id INTEGER)")


iniciar()


def _numero(c):
    from contratos import pais_iso
    ej("CREATE TABLE IF NOT EXISTS secuencias (clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
    ej("INSERT INTO secuencias (clave, valor) VALUES ('oportunidad', 1) ON CONFLICT(clave) DO UPDATE SET valor=valor+1")
    n = q1("SELECT valor FROM secuencias WHERE clave='oportunidad'")["valor"]
    return f"OPO-{pais_iso(c.get('pais'))}-{date.today().year}-{n:04d}"


def demo_estado(o):
    """Estado de la demo de la oportunidad: sin demo, activa (días restantes) o vencida."""
    if not o.get("demo_fin"):
        return {"k": "sin", "t": "sin demo"}
    hoy = date.today()
    fin = date.fromisoformat(o["demo_fin"])
    if fin >= hoy:
        return {"k": "activa", "t": f"activa hasta el {o['demo_fin']}", "dias": (fin - hoy).days, "puede_extender": o["demo_semanas"] < MAX_SEMANAS}
    return {"k": "vencida", "t": f"venció el {o['demo_fin']}", "dias": 0, "puede_extender": o["demo_semanas"] < MAX_SEMANAS}


def _ops():
    out = []
    for o in q("SELECT * FROM oportunidades ORDER BY id DESC"):
        c = q1("SELECT * FROM clientes WHERE id=?", o["cliente_id"])
        if not c or not en_alcance(c):
            continue
        o["c"], o["demo"] = c, demo_estado(o)
        out.append(o)
    return out


@app.route("/oportunidades")
@requiere_login
def oportunidades():
    solo_invoke()
    ops = _ops()
    abiertas = [o for o in ops if o["etapa"] not in ("ganada", "perdida")]
    return render_template("oportunidades.html", ops=ops, etapa_t=ETAPA_T, tipos=TIPOS,
                           kpi={"abiertas": len(abiertas), "demos": sum(1 for o in ops if o["demo"]["k"] == "activa"),
                                "pipeline": sum(o["monto_estimado"] or 0 for o in abiertas), "ganadas": sum(1 for o in ops if o["etapa"] == "ganada")})


@app.route("/oportunidades/nueva", methods=["GET", "POST"])
@requiere_login
def oportunidad_nueva():
    solo_invoke()
    existentes = [c for c in q("SELECT * FROM clientes WHERE activo=1 AND COALESCE(propio,0)=0 AND tenant_id IS NULL ORDER BY nombre") if en_alcance(c)]
    if request.method == "GET":
        return render_template("oportunidad_nueva.html", existentes=existentes, f=request.args, tipos=TIPOS)
    f = request.form
    import cobro
    from contratos import _empresa
    try:
        tipo = f.get("tipo")
        if tipo not in TIPOS:
            raise ValueError("Elige si es un cliente directo o un tenant")
        if tipo == "tenant" and f.get("despliegue") not in cobro.DESPLIEGUES:
            raise ValueError("Para un tenant, elige si su FaceOps será centralizado o uno por cliente")
        c = _empresa(f, tipo, None)
        monto = cobro._n(f.get("monto_estimado") or "", 0)
        nodos = int(cobro._n(f.get("nodos") or "", 0))
        if monto < 0 or nodos < 0:
            raise ValueError("Monto y nodos no pueden ser negativos")
    except ValueError as e:
        flash(str(e), "error")
        return render_template("oportunidad_nueva.html", existentes=existentes, f=f, tipos=TIPOS), 400
    num = _numero(c)
    oid = ej("INSERT INTO oportunidades (cliente_id, numero, tipo, etapa, despliegue, plan, nodos, monto_estimado, cierre_estimado, notas, creado, por) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], num, tipo, "prospecto", f.get("despliegue") if tipo == "tenant" else "",
             f.get("plan") if f.get("plan") in cobro.PLANES else "", nodos, monto, (f.get("cierre_estimado") or "")[:10], (f.get("notas") or "").strip()[:3000],
             ahora(), g.usuario["usuario"])
    bitacora("oportunidad creada", c["codigo"], {"oportunidad": num, "monto": monto})
    flash(f"Oportunidad {num} creada para {c['nombre']}", "ok")
    return redirect(url_for("oportunidad", oid=oid))


@app.route("/oportunidades/<int:oid>", methods=["GET", "POST"])
@requiere_login
def oportunidad(oid):
    solo_invoke()
    o = q1("SELECT * FROM oportunidades WHERE id=?", oid) or abort(404)
    c = cliente_ok(o["cliente_id"])
    if request.method == "POST":
        f = request.form
        acc = f.get("accion")
        if acc == "etapa" and f.get("etapa") in ETAPA_T:
            ej("UPDATE oportunidades SET etapa=?, notas=? WHERE id=?", f["etapa"], (f.get("notas") or o["notas"] or "")[:3000], oid)
            bitacora("oportunidad · etapa", c["codigo"], {"oportunidad": o["numero"], "etapa": f["etapa"]})
            flash(f"Etapa: {ETAPA_T[f['etapa']]}", "ok")
        elif acc in ("demo", "extender"):
            try:
                sem = int(f.get("semanas") or 0)
            except ValueError:
                sem = 0
            usadas = o["demo_semanas"] or 0
            if sem < 1 or usadas + sem > MAX_SEMANAS:
                flash(f"La demo dura entre 1 y {MAX_SEMANAS} semanas en total (ya usadas: {usadas})", "error")
                return redirect(url_for("oportunidad", oid=oid))
            _activar_demo(o, c, sem)
        return redirect(url_for("oportunidad", oid=oid))
    o["demo"] = demo_estado(o)
    k = q1("SELECT * FROM contratos WHERE id=?", o["contrato_demo_id"]) if o.get("contrato_demo_id") else None
    from instalacion import intentos, licencias
    return render_template("oportunidad.html", o=o, c=c, k=k, etapas=ETAPAS, etapa_t=ETAPA_T, tipos=TIPOS, max_semanas=MAX_SEMANAS,
                           inst=(intentos(c["id"]) or [None])[0], lic=licencias(c["id"]))


def _activar_demo(o, c, sem):
    """Activa o extiende la demo: un contrato «demo» sin cobro que habilita la instalación hasta el fin de la demo."""
    hoy = date.today()
    if o["demo_fin"] and date.fromisoformat(o["demo_fin"]) >= hoy:   # extender desde el fin actual
        fin = date.fromisoformat(o["demo_fin"]) + timedelta(weeks=sem)
        ini = o["demo_inicio"]
    else:
        ini, fin = hoy.isoformat(), hoy + timedelta(weeks=sem)
    fin = fin.isoformat()
    kid = o.get("contrato_demo_id")
    if kid and q1("SELECT id FROM contratos WHERE id=?", kid):
        ej("UPDATE contratos SET fin=?, activacion_limite=? WHERE id=?", fin, fin, kid)
    else:
        pid = ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, "
                 "enviada, respuesta, emisor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], o["numero"], 1, "estandar", 1, 0, 0, "USD", 0,
                 f"Demo de FaceOps de la oportunidad {o['numero']}: sin cobro, con licencias demo del cliente.", "aceptada", ahora(), g.usuario["usuario"],
                 ahora(), ahora(), "invoke")
        kid = ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por, inicio, fin, activacion_limite) VALUES (?,?,?,?,?,?,?,?,?)",
                 c["id"], pid, o["numero"] + "-DEMO", "demo", ahora(), g.usuario["usuario"], ini, fin, fin)
    if not c.get("habilitado") or not c.get("instancia_id"):
        ej("UPDATE clientes SET habilitado=1, instancia_id=COALESCE(NULLIF(instancia_id,''), ?), activacion_limite=? WHERE id=?", nuevo_id(c["codigo"]), fin, c["id"])
    ej("UPDATE oportunidades SET demo_semanas=?, demo_inicio=?, demo_fin=?, contrato_demo_id=?, etapa=CASE WHEN etapa='prospecto' THEN 'demo' ELSE etapa END "
       "WHERE id=?", (o["demo_semanas"] or 0) + sem, ini, fin, kid, o["id"])
    bitacora("oportunidad · demo " + ("extendida" if o["demo_semanas"] else "activada"), c["codigo"],
             {"oportunidad": o["numero"], "semanas": sem, "hasta": fin, "por": g.usuario["usuario"]})
    flash(f"Demo {'extendida' if o['demo_semanas'] else 'activada'} hasta el {fin}. Ya puedes cargar las licencias demo del cliente y generar el comando de instalación.", "ok")
