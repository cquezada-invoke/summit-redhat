"""FaceAdmin · Oportunidades: el paso previo al contrato. Se crea el cliente y una oportunidad (más reducida que un contrato) y, desde
ella, se puede activar una demo de FaceOps por semanas (máximo 4). La demo habilita la instalación con las licencias demo del cliente
y vence sola; para seguir operando se firma el contrato."""
import json
from datetime import date, timedelta

from flask import abort, flash, g, redirect, render_template, request, url_for

from app import ahora, app, bitacora, cliente_ok, ej, en_alcance, es_invoke, nuevo_id, q, q1, requiere_login, solo_invoke

ETAPAS = [("prospecto", "Prospecto"), ("demo", "En demo"), ("propuesta", "Propuesta enviada"), ("ganada", "Ganada"), ("perdida", "Perdida")]
ETAPA_T = dict(ETAPAS)
# nivel de la oportunidad (probabilidad de cierre): 5 → 10 % … 1 → 99 %; 0 = ya es negocio (se puede crear el contrato)
NIVELES = [(5, 10), (4, 30), (3, 70), (2, 90), (1, 99), (0, 100)]
NIVEL_P = dict(NIVELES)
_ETAPA_NIVEL = {"prospecto": 5, "demo": 4, "propuesta": 3, "ganada": 0}


def quarter(fecha):
    """«2027-02-10» → «Q1 2027»."""
    try:
        y, m = int(fecha[:4]), int(fecha[5:7])
        return f"Q{(m - 1) // 3 + 1} {y}"
    except (TypeError, ValueError):
        return "—"


def _contactos_todos():
    import contactos as _ct   # noqa: F401  (crea la tabla de historial)
    cli = {c["id"]: c for c in q("SELECT * FROM clientes WHERE activo=1") if en_alcance(c)}
    cargo_cta = {(r["contacto_id"], r["cliente_id"]): r["cargo"] for r in q("SELECT contacto_id, cliente_id, cargo FROM contacto_cuentas WHERE COALESCE(cargo,'')<>'' ORDER BY id")}
    out = []
    for x in q("SELECT * FROM contactos ORDER BY nombre"):
        if x["cliente_id"] and x["cliente_id"] not in cli:
            continue
        x = dict(x)
        c = cli.get(x["cliente_id"]) or {}
        # el mismo contacto como principal de la cuenta (por correo o nombre): de ahí se completa lo que falte
        ppal = c and (((x.get("correo") or "").lower() and (x.get("correo") or "").lower() == (c.get("contacto_correo") or "").lower())
                      or (x.get("nombre") or "").strip().lower() == (c.get("contacto_nombre") or "").strip().lower())
        x["cargo"] = x.get("cargo") or cargo_cta.get((x["id"], x["cliente_id"])) or (c.get("contacto_cargo") if ppal else "") or ""
        x["telefono"] = x.get("telefono") or (c.get("contacto_telefono") if ppal else "") or ""
        x["correo"] = x.get("correo") or (c.get("contacto_correo") if ppal else "") or ""
        out.append(dict(x, cuenta=c.get("nombre", "")))
    return out


def _cts_prev(f):
    """Filas de contactos tal como se enviaron, para no perderlas si el formulario vuelve con un error."""
    ks = ("ct_id", "ct_nombre", "ct_cargo", "ct_correo", "ct_telefono")
    ls = {k: f.getlist(k) for k in ks}
    return [{k: (ls[k][i] if i < len(ls[k]) else "") for k in ks} for i in range(len(ls["ct_id"]))]


def _contactos_form(f):
    """Filas de contactos del formulario: existentes (ct_id) o nuevos (nombre y correo)."""
    out = []
    for i, cid in enumerate(f.getlist("ct_id")):
        def v(k):
            lst = f.getlist(k)
            return (lst[i] if i < len(lst) else "").strip()
        if cid.isdigit():
            x = q1("SELECT * FROM contactos WHERE id=?", int(cid))
            if x:
                out.append({"id": x["id"], "nombre": x["nombre"], "cargo": x["cargo"] or "", "correo": x["correo"] or "", "telefono": x["telefono"] or ""})
        elif v("ct_nombre") and v("ct_correo"):
            out.append({"id": None, "nombre": v("ct_nombre")[:120], "cargo": v("ct_cargo")[:120], "correo": v("ct_correo")[:160], "telefono": v("ct_telefono")[:40]})
    return out


def _vincular_contactos(cts, c):
    """Los contactos nuevos se crean en la cuenta; los existentes sin cuenta pasan a esta. Devuelve sus ids (el primero es el principal)."""
    hoy, ids = ahora()[:10], []
    for i, x in enumerate(cts):
        kid = x["id"]
        if not kid:
            ya = q1("SELECT id FROM contactos WHERE cliente_id=? AND LOWER(correo)=LOWER(?)", c["id"], x["correo"])
            kid = ya["id"] if ya else ej("INSERT INTO contactos (cliente_id, nombre, cargo, correo, telefono, rol, principal, notas, creado, por) "
                                         "VALUES (?,?,?,?,?,?,?,?,?,?)", c["id"], x["nombre"], x["cargo"], x["correo"], x["telefono"], "", 0, "", ahora(), g.usuario["usuario"])
            if not ya:
                ej("INSERT INTO contacto_cuentas (contacto_id, cliente_id, cargo, desde, por) VALUES (?,?,?,?,?)", kid, c["id"], x["cargo"], hoy, g.usuario["usuario"])
        else:
            e = q1("SELECT * FROM contactos WHERE id=?", kid)
            if e and not e["cliente_id"]:
                ej("UPDATE contactos SET cliente_id=? WHERE id=?", c["id"], kid)
                ej("INSERT INTO contacto_cuentas (contacto_id, cliente_id, cargo, desde, por) VALUES (?,?,?,?,?)", kid, c["id"], e["cargo"] or "", hoy, g.usuario["usuario"])
        if i == 0:
            ej("UPDATE contactos SET principal=(id=?) WHERE cliente_id=?", kid, c["id"])
        ids.append(kid)
    return ids


def _notas_tabla():
    ej("CREATE TABLE IF NOT EXISTS oportunidad_notas (id INTEGER PRIMARY KEY, oportunidad_id INTEGER, fecha TEXT, usuario TEXT, texto TEXT, cambios TEXT DEFAULT '')")


def nivel_de(o):
    n = o.get("nivel")
    return n if n in NIVEL_P else _ETAPA_NIVEL.get(o.get("etapa"), 5)
TIPOS = {"multitenant": "Multi-tenant (un FaceOps centralizado para sus clientes)", "tenant": "Tenant (un FaceOps por cada cliente)", "directo": "Cliente directo"}
TIPO_CORTO = {"multitenant": "Multi-tenant", "tenant": "Tenant", "directo": "Directo"}
LICENCIA_TENANT = 2700   # licencia mensual de lista del tenant (editable en la oportunidad)


def estimar(f):
    """Precio de lista aproximado: la modalidad (consumo por créditos al año o costo fijo por alcance) + Red Hat CCSP y, para un tenant,
    su licencia y el costo de sus instancias de FaceOps (habilitación, versiones y parches). Lanza ValueError si falta algo."""
    import cobro
    r = cobro.calcular(f)
    tipo = f.get("tipo")
    lic = cobro._n(f.get("licencia") or "", LICENCIA_TENANT) if tipo != "directo" else 0
    n_cli = max(1, int(cobro._n(f.get("clientes_est") or "", 1))) if tipo != "directo" else 1
    D = cobro.DESPLIEGUES["centralizado" if tipo == "multitenant" else "por_cliente"] if tipo != "directo" else None
    inst = 1 if tipo == "multitenant" else (n_cli if tipo == "tenant" else 0)
    ope = D["operacion"] * inst if D else 0
    hab = D["habilitacion"] * inst if D else 0
    # en un tenant, el servicio y el CCSP estimados son por cliente: se multiplican por los clientes estimados
    serv, rh = r["servicio_mensual"] * n_cli, r["rh_mensual"] * n_cli
    mensual = serv + rh + lic + ope
    return {"modalidad": r["modalidad"], "plan": r.get("plan", ""), "nodos": r["nodos"], "items": r.get("items", []), "servicio": serv, "rh": rh, "licencia": lic,
            "operacion": ope, "habilitacion": hab, "instancias": inst, "clientes": n_cli, "mensual": round(mensual), "anual": round(mensual * 12),
            "valor_credito": r.get("valor_credito", 0), "valor_nodo": r["valor_nodo"]}
MAX_SEMANAS = 4


def iniciar():
    with app.app_context():
        ej("CREATE TABLE IF NOT EXISTS oportunidades (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, numero TEXT, tipo TEXT, etapa TEXT DEFAULT 'prospecto', "
           "despliegue TEXT DEFAULT '', plan TEXT DEFAULT '', nodos INTEGER DEFAULT 0, monto_estimado REAL DEFAULT 0, cierre_estimado TEXT DEFAULT '', "
           "notas TEXT DEFAULT '', creado TEXT, por TEXT, demo_semanas INTEGER DEFAULT 0, demo_inicio TEXT DEFAULT '', demo_fin TEXT DEFAULT '', "
           "contrato_demo_id INTEGER)")
        cols = {r["name"] for r in q("PRAGMA table_info(oportunidades)")}
        for col, t in (("modalidad", "TEXT DEFAULT ''"), ("estimacion", "TEXT DEFAULT '{}'"), ("monto_tentativo", "REAL DEFAULT 0"), ("nivel", "INTEGER"), ("contactos", "TEXT DEFAULT '[]'")):
            if col not in cols:
                ej(f"ALTER TABLE oportunidades ADD COLUMN {col} {t}")
        ej("UPDATE oportunidades SET tipo='directo' WHERE tipo NOT IN ('multitenant','tenant','directo')")
        ej("CREATE TABLE IF NOT EXISTS demos (id INTEGER PRIMARY KEY, oportunidad_id INTEGER NOT NULL, cliente_id INTEGER NOT NULL, tipo TEXT, semanas INTEGER, "
           "desde TEXT, hasta TEXT, contrato_id INTEGER, creado TEXT, por TEXT, motivo TEXT DEFAULT '')")
        if "numero" not in {r["name"] for r in q("PRAGMA table_info(demos)")}:
            ej("ALTER TABLE demos ADD COLUMN numero TEXT DEFAULT ''")
        for o in q("SELECT * FROM oportunidades WHERE COALESCE(demo_semanas,0)>0 AND id NOT IN (SELECT oportunidad_id FROM demos)"):
            ej("INSERT INTO demos (oportunidad_id, cliente_id, tipo, semanas, desde, hasta, contrato_id, creado, por) VALUES (?,?,?,?,?,?,?,?,?)", o["id"], o["cliente_id"],
               "activación", o["demo_semanas"], o["demo_inicio"], o["demo_fin"], o["contrato_demo_id"], o["creado"], o["por"])


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
        o["nivel_"] = nivel_de(o)
        o["prob"], o["quarter"] = NIVEL_P[o["nivel_"]], quarter(o.get("cierre_estimado") or "")
        out.append(o)
    return out


@app.route("/oportunidades")
@requiere_login
def oportunidades():
    solo_invoke()
    ops = _ops()
    abiertas = [o for o in ops if o["etapa"] not in ("ganada", "perdida")]
    return render_template("oportunidades.html", ops=ops, etapa_t=ETAPA_T, tipos=TIPO_CORTO,
                           kpi={"abiertas": len(abiertas), "demos": sum(1 for o in ops if o["demo"]["k"] == "activa"),
                                "ponderado": sum((o.get("monto_tentativo") or o["monto_estimado"] or 0) * o["prob"] / 100 for o in abiertas),
                                "pipeline": sum(o.get("monto_tentativo") or o["monto_estimado"] or 0 for o in abiertas), "ganadas": sum(1 for o in ops if o["etapa"] == "ganada")})


@app.route("/oportunidades/nueva", methods=["GET", "POST"])
@requiere_login
def oportunidad_nueva():
    solo_invoke()
    existentes = [c for c in q("SELECT * FROM clientes WHERE activo=1 AND COALESCE(propio,0)=0 AND tenant_id IS NULL ORDER BY nombre") if en_alcance(c)]
    if request.method == "GET":
        from datetime import date, timedelta
        f0 = dict(request.args)
        f0.setdefault("cierre_estimado", (date.today() + timedelta(days=60)).isoformat())
        return render_template("oportunidad_nueva.html", existentes=existentes, f=f0, tipos=TIPOS, licencia_def=LICENCIA_TENANT, niveles=NIVELES, contactos=_contactos_todos())
    f = request.form.copy()
    import cobro
    from contratos import _empresa
    import alcance
    if not (f.get("empresa_id") or "").isdigit():   # el código de una empresa nueva siempre se genera del nombre
        f["codigo"] = alcance.codigo_empresa(f.get("nombre"))
    cts = _contactos_form(f)
    if cts:   # el primer contacto es el principal de la cuenta
        p0 = cts[0]
        f["contacto_nombre"], f["contacto_cargo"], f["contacto_correo"], f["contacto_telefono"] = p0["nombre"], p0["cargo"], p0["correo"], p0["telefono"]
    if f.get("modalidad") == "alcance" and not f.getlist("auto") and len((f.get("alcance_texto") or "").strip()) >= 20:
        import alcance   # sin analizar en pantalla: se analiza aquí y se marcan las automatizaciones propuestas
        d = alcance.analizar(f["alcance_texto"])
        f["alcance_ia"] = json.dumps(d, ensure_ascii=False)
        for a in d.get("automatizaciones") or []:
            f.add("auto", a["nombre"])
            f["vol::" + a["nombre"]] = str(a.get("volumen") or 5)
    try:
        tipo = f.get("tipo")
        if tipo not in TIPOS:
            raise ValueError("Elige el tipo: multi-tenant, tenant o cliente directo")
        est = estimar(f)
        tent = cobro._n(f.get("monto_tentativo") or "", est["anual"])
        if tent < 0:
            raise ValueError("El monto tentativo no puede ser negativo")
        if not (f.get("cierre_estimado") or "").strip():
            raise ValueError("Indica cuándo se espera cerrar")
        c = _empresa(f, "directo" if tipo == "directo" else "tenant", None)
    except ValueError as e:
        flash(str(e), "error")
        return render_template("oportunidad_nueva.html", existentes=existentes, f=f, tipos=TIPOS, licencia_def=LICENCIA_TENANT, niveles=NIVELES, contactos=_contactos_todos(), cts_prev=_cts_prev(f)), 400
    import alcance
    alcance.columnas()
    atexto = (f.get("alcance_texto") or "").strip()[:6000]
    if est["modalidad"] == "alcance" and len(atexto) < 20:
        flash("En costo fijo por alcance, describe el alcance (lo evalúa el especialista de negocio antes de fijar el precio)", "error")
        return render_template("oportunidad_nueva.html", existentes=existentes, f=f, tipos=TIPOS, licencia_def=LICENCIA_TENANT, niveles=NIVELES, contactos=_contactos_todos(), cts_prev=_cts_prev(f)), 400
    if not cts:
        flash("Agrega al menos un contacto (nombre y correo)", "error")
        return render_template("oportunidad_nueva.html", existentes=existentes, f=f, tipos=TIPOS, licencia_def=LICENCIA_TENANT, niveles=NIVELES,
                               contactos=_contactos_todos(), cts_prev=_cts_prev(f)), 400
    num = _numero(c)
    oid = ej("INSERT INTO oportunidades (cliente_id, numero, tipo, etapa, despliegue, plan, nodos, monto_estimado, cierre_estimado, notas, creado, por, modalidad, "
             "estimacion, monto_tentativo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", c["id"], num, tipo, "prospecto",
             {"multitenant": "centralizado", "tenant": "por_cliente"}.get(tipo, ""), est["plan"], est["nodos"], est["anual"], (f.get("cierre_estimado") or "")[:10],
             (f.get("notas") or "").strip()[:3000], ahora(), g.usuario["usuario"], est["modalidad"], json.dumps(est, ensure_ascii=False), tent)
    monto = tent
    ids = _vincular_contactos(cts, c)
    ej("UPDATE oportunidades SET contactos=? WHERE id=?", json.dumps(ids), oid)
    try:
        niv = int(f.get("nivel") or 5)
    except ValueError:
        niv = 5
    niv = niv if niv in NIVEL_P and niv > 0 else 5   # nivel 0 (negocio) se alcanza desde la oportunidad
    ej("UPDATE oportunidades SET nivel=? WHERE id=?", niv, oid)
    bitacora("oportunidad creada", c["codigo"], {"oportunidad": num, "monto": monto})
    aviso = ""
    if est["modalidad"] == "alcance":
        try:
            d = json.loads(f.get("alcance_ia") or "{}")
        except ValueError:
            d = {}
        if not d.get("automatizaciones") and not d.get("resumen"):
            d = alcance.analizar(atexto)
        ej("UPDATE oportunidades SET alcance_texto=?, alcance_ia=?, alcance_estado='por_confirmar' WHERE id=?", atexto, json.dumps(d, ensure_ascii=False), oid)
        estado = alcance.avisar_especialista(q1("SELECT * FROM oportunidades WHERE id=?", oid), c, d)
        aviso = f" · alcance enviado al especialista ({alcance.especialista()}): {estado}"
    flash(f"Oportunidad {num} creada para {c['nombre']}{aviso}", "ok")
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
        if acc == "etapa" and (f.get("etapa") in ETAPA_T or (f.get("nivel") or "").isdigit()):
            niv = int(f["nivel"]) if (f.get("nivel") or "").isdigit() and int(f["nivel"]) in NIVEL_P else nivel_de(o)
            etp = "perdida" if f.get("perdida") else ("ganada" if niv == 0 else (o["etapa"] if o["etapa"] in ("demo", "propuesta") else "prospecto"))
            f = f.copy()
            f["etapa"] = etp
            ej("UPDATE oportunidades SET nivel=? WHERE id=?", niv, oid)
            _notas_tabla()
            cambios = []
            if niv != nivel_de(o):
                cambios.append(f"nivel {nivel_de(o)} → {niv} ({NIVEL_P[niv]} %)")
            if etp == "perdida" and o["etapa"] != "perdida":
                cambios.append("marcada como perdida")
            if (f.get("cierre_estimado") or "")[:10] and (f.get("cierre_estimado") or "")[:10] != (o.get("cierre_estimado") or ""):
                cambios.append(f"cierre {quarter(o.get('cierre_estimado') or '')} → {quarter(f['cierre_estimado'])}")
            nota = (f.get("nota") or "").strip()[:3000]
            if nota or cambios:
                ej("INSERT INTO oportunidad_notas (oportunidad_id, fecha, usuario, texto, cambios) VALUES (?,?,?,?,?)", oid, ahora(), g.usuario["usuario"], nota,
                   " · ".join(cambios))
            f["notas"] = o["notas"] or ""
            import cobro
            tent = cobro._n(f.get("monto_tentativo") or "", o.get("monto_tentativo") or 0)
            ej("UPDATE oportunidades SET etapa=?, notas=?, cierre_estimado=?, monto_tentativo=? WHERE id=?", f["etapa"], (f.get("notas") or o["notas"] or "")[:3000],
               (f.get("cierre_estimado") or o["cierre_estimado"] or "")[:10], max(0, tent), oid)
            bitacora("oportunidad · etapa", c["codigo"], {"oportunidad": o["numero"], "etapa": f["etapa"]})
            flash("Oportunidad perdida" if etp == "perdida" else (f"Nivel {niv} · {NIVEL_P[niv]} %" + (": ya es negocio, crea el contrato" if niv == 0 else "")), "ok")
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
    try:
        o["est"] = json.loads(o.get("estimacion") or "{}")
    except ValueError:
        o["est"] = {}
    import alcance
    alcance.columnas()
    o = q1("SELECT * FROM oportunidades WHERE id=?", oid) | {"demo": o["demo"], "est": o["est"]}
    try:
        o["aia"] = json.loads(o.get("alcance_ia") or "{}")
    except ValueError:
        o["aia"] = {}
    o["especialista"] = alcance.especialista()
    dem = [_demo_fila(x) for x in q("SELECT * FROM demos WHERE oportunidad_id=? ORDER BY id DESC", oid)]
    o["nivel_"], o["quarter"] = nivel_de(o), quarter(o.get("cierre_estimado") or "")
    _notas_tabla()
    o["historial"] = q("SELECT * FROM oportunidad_notas WHERE oportunidad_id=? ORDER BY id DESC", oid)
    try:
        _ids = json.loads(o.get("contactos") or "[]")
    except ValueError:
        _ids = []
    o["contactos_"] = [x for x in (q1("SELECT * FROM contactos WHERE id=?", i) for i in _ids) if x]
    return render_template("oportunidad.html", niveles=NIVELES, inst_t=INST_T, demos=dem, o=o, c=c, k=k, etapas=ETAPAS, etapa_t=ETAPA_T, tipos=TIPOS, max_semanas=MAX_SEMANAS,
                           inst=(intentos(c["id"]) or [None])[0], lic=licencias(c["id"]))


INST_T = {"sin_arrancar": ("Sin arrancar", ""), "comando": ("Comando generado · esperando que se lance", "med"), "en_curso": ("Instalando", "med"),
          "validado": ("Validada · instalando", "med"), "commit": ("Instalada", "ok"), "rollback": ("Revertida", "mal"), "validacion": ("Validación fallida", "mal"),
          "sin_avisos": ("Sin avisos", "mal"), "interrumpido": ("Interrumpida", "mal")}


def estado_instalacion(d):
    """La instalación de la demo no arranca sola: primero las licencias y el comando; recién al lanzarlo en la máquina pasa a «instalando»."""
    from instalacion import intentos
    its = [x for x in intentos(d["cliente_id"]) if x["inicio"][:10] >= d["desde"]]
    if its:
        return its[0]["resultado"], its
    cmd = q1("SELECT creado FROM enlaces_paquete WHERE cliente_id=? AND creado>=? ORDER BY id DESC LIMIT 1", d["cliente_id"], d["desde"])
    return ("comando" if cmd else "sin_arrancar"), []


def _demo_fila(d):
    hoy = date.today().isoformat()
    d["c"] = q1("SELECT * FROM clientes WHERE id=?", d["cliente_id"]) or {}
    d["o"] = q1("SELECT * FROM oportunidades WHERE id=?", d["oportunidad_id"]) or {}
    d["vigente"] = d["desde"] <= hoy <= d["hasta"]
    d["est"] = "vigente" if d["vigente"] else ("programada" if d["desde"] > hoy else "terminada")
    d["inst"], d["its"] = estado_instalacion(d)
    d["dias"] = (date.fromisoformat(d["hasta"]) - date.today()).days if d["vigente"] else 0
    d["version"], d["version_instalada"] = version_demo(d)
    return d


def version_a_instalar():
    """La versión de FaceOps que entrega hoy el instalador (la del repositorio central que construye FaceAdmin)."""
    import fabrica
    F = fabrica.datos().get("faceops") or {}
    return f"v{F.get('linea', '1.0')}.{F.get('build', '')}"


def version_demo(d):
    """Instalada: la que informa su FaceOps; si no, la que se instalará."""
    if d.get("inst") == "commit":
        u = q1("SELECT version FROM uso WHERE cliente_id=?", d["cliente_id"]) or {}
        try:
            v = json.loads(u.get("version") or "{}").get("texto")
        except ValueError:
            v = None
        if v:
            return v, True
    return version_a_instalar(), False


@app.route("/demos")
@requiere_login
def demos():
    solo_invoke()
    filas = [_demo_fila(d) for d in q("SELECT * FROM demos ORDER BY id DESC")]
    filas = [d for d in filas if d["c"] and en_alcance(d["c"])]
    return render_template("demos.html", filas=filas, inst_t=INST_T,
                           kpi={"vigentes": sum(1 for d in filas if d["vigente"]), "sin": sum(1 for d in filas if d["vigente"] and d["inst"] == "sin_arrancar"),
                                "inst": sum(1 for d in filas if d["inst"] == "commit"), "total": len(filas)})


@app.route("/demos/<int:did>")
@requiere_login
def demo_detalle(did):
    """La demo como objeto propio: período, licencias, comando e instalación (que no arranca hasta generar y lanzar el comando)."""
    solo_invoke()
    d = _demo_fila(q1("SELECT * FROM demos WHERE id=?", did) or abort(404))
    c = cliente_ok(d["cliente_id"])
    o = d["o"]
    k = q1("SELECT * FROM contratos WHERE id=?", d["contrato_id"]) if d.get("contrato_id") else None
    from instalacion import licencias
    lic = licencias(c["id"])
    eventos = q("SELECT fecha, usuario, accion FROM bitacora WHERE objeto=? AND substr(fecha,1,10) BETWEEN ? AND ? ORDER BY id DESC LIMIT 60", c["codigo"], d["desde"], d["hasta"])
    cmd = q1("SELECT creado FROM enlaces_paquete WHERE cliente_id=? AND creado>=? ORDER BY id DESC LIMIT 1", c["id"], d["desde"])
    pasos = [("Demo activada", True, f"{d['desde']} → {d['hasta']}"), ("Licencias demo del cliente", lic["ok"], "AAP y RHEL cargadas" if lic["ok"] else "faltan"),
             ("Comando de instalación generado", bool(cmd), cmd["creado"][:16] if cmd else "pendiente"),
             ("Instalación lanzada en la máquina", bool(d["its"]), d["its"][0]["inicio"][:16] if d["its"] else "sin arrancar"),
             ("FaceOps instalado y enlazado", d["inst"] == "commit", INST_T.get(d["inst"], (d["inst"],))[0])]
    return render_template("demo_detalle.html", o=o, d=d, c=c, k=k, lic=lic, eventos=eventos, pasos=pasos, inst_t=INST_T, vigente=d["vigente"])


@app.route("/demos/<int:did>/requisitos.pdf")
@requiere_login
def demo_requisitos(did):
    """Documento para el cliente con los requisitos técnicos de la demo (máquina, red, licencias y pasos)."""
    from flask import Response
    solo_invoke()
    d = _demo_fila(q1("SELECT * FROM demos WHERE id=?", did) or abort(404))
    c = cliente_ok(d["cliente_id"])
    import fabrica
    from requisitos_pdf import pdf_requisitos
    from urllib.parse import urlsplit
    pdf = pdf_requisitos(c, d, d["o"], d["version"], fabrica.productos(fabrica.datos()), urlsplit(request.host_url).hostname or "")
    bitacora("requisitos técnicos de la demo descargados", c["codigo"], {"demo": d["numero"]})
    nombre = f"Requisitos tecnicos DEMO FaceOps {d['numero']} {c['codigo']}.pdf"
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="{nombre}"'})


@app.route("/oportunidades/<int:oid>/demos/<int:did>")
@requiere_login
def demo_detalle_viejo(oid, did):
    return redirect(url_for("demo_detalle", did=did))


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
    desde_ped = o["demo_fin"] if (o["demo_fin"] and o["demo_fin"] >= hoy.isoformat()) else hoy.isoformat()
    ej("INSERT INTO demos (numero, oportunidad_id, cliente_id, tipo, semanas, desde, hasta, contrato_id, creado, por, motivo) VALUES (?,?,?,?,?,?,?,?,?,?,?)", _numero_demo(c["id"]), o["id"], c["id"],
       "extensión" if (o["demo_fin"] and o["demo_fin"] >= hoy.isoformat()) else "activación", sem, desde_ped, fin, kid, ahora(), g.usuario["usuario"], (request.form.get("motivo") or "")[:500])
    _notas_tabla()
    ej("INSERT INTO oportunidad_notas (oportunidad_id, fecha, usuario, texto, cambios) VALUES (?,?,?,?,?)", o["id"], ahora(), g.usuario["usuario"],
       (request.form.get("motivo") or "").strip()[:500], f"demo {'extendida' if o['demo_semanas'] else 'activada'}: {sem} semana{'s' if sem > 1 else ''} hasta el {fin}")
    bitacora("oportunidad · demo " + ("extendida" if o["demo_semanas"] else "activada"), c["codigo"],
             {"oportunidad": o["numero"], "semanas": sem, "hasta": fin, "por": g.usuario["usuario"]})
    flash(f"Demo {'extendida' if o['demo_semanas'] else 'activada'} hasta el {fin}. Ya puedes cargar las licencias demo del cliente y generar el comando de instalación.", "ok")


# ---------------------------------------------------------------- oportunidades de demostración (solo con el modo demo)
DEMO_OPS = [   # código, cliente, país, tipo, modalidad, plan/acciones, nodos, clientes, etapa, días al cierre, monto tentativo, semanas de demo (días desde el inicio)
    ("demo-op-minsal", "Red de Salud Pública (demo)", "Chile", "directo", "creditos", "c12k", 60, 1, "demo", 45, 380000, (2, 5)),
    ("demo-op-bancoc", "Banco Cordillera (demo)", "Chile", "directo", "alcance", 6, 120, 1, "propuesta", 20, 520000, None),
    ("demo-op-telco", "Telecom Austral (demo)", "Perú", "multitenant", "creditos", "c24k", 80, 6, "demo", 60, 1900000, (4, 20)),
    ("demo-op-integra", "Integra Partners (demo)", "México", "tenant", "alcance", 4, 30, 4, "prospecto", 90, 640000, None),
    ("demo-op-retail", "Supermercados del Valle (demo)", "Colombia", "directo", "creditos", "c6k", 25, 1, "ganada", -10, 210000, (3, 30)),
    ("demo-op-gob", "Ministerio de Obras (demo)", "Honduras", "directo", "alcance", 8, 90, 1, "perdida", -25, 300000, None),
    ("demo-op-energia", "Generadora Pacífico (demo)", "Guatemala", "directo", "creditos", "c12k", 40, 1, "demo", 30, 260000, (1, 12)),
]


def sembrar_demo_ops():
    from werkzeug.datastructures import MultiDict
    import cobro
    with app.app_context():
        if q1("SELECT id FROM clientes WHERE codigo='demo-op-minsal'"):
            return
        hoy = date.today()
        for cod, nom, pais, tipo, mod, pl, nodos, ncli, etapa, dias, tent, demo in DEMO_OPS:
            datos = [("tipo", tipo), ("modalidad", mod), ("nodos", str(nodos)), ("valor_nodo", str(cobro.VALOR_NODO)), ("clientes_est", str(ncli)),
                     ("licencia", str(LICENCIA_TENANT))]
            if mod == "creditos":
                datos += [("plan", pl), ("valor_credito", str(cobro.PLANES[pl]["valor"]))]
            else:
                datos += [("valor_credito", str(cobro.VALOR_CREDITO))]
                for a in cobro.CATALOGO[:pl]:
                    datos += [("auto", a["nombre"]), ("vol::" + a["nombre"], "10")]
            est = estimar(MultiDict(datos))
            cid = ej("INSERT INTO clientes (codigo, nombre, url, rol, creado, instancia_id, razon_social, pais, contacto_nombre, contacto_correo, tipo, habilitado, alcance) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", cod, nom, "", "tenant" if tipo != "directo" else "cliente", ahora(), "", nom, pais, "contacto (demo)",
                     f"contacto@{cod}.example", "tenant" if tipo != "directo" else "cliente", 0, "estandar")
            c = q1("SELECT * FROM clientes WHERE id=?", cid)
            oid = ej("INSERT INTO oportunidades (cliente_id, numero, tipo, etapa, despliegue, plan, nodos, monto_estimado, cierre_estimado, notas, creado, por, modalidad, "
                     "estimacion, monto_tentativo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cid, f"DEMO-OPO-{c['pais'][:2].upper()}-{cid:04d}", tipo, etapa,
                     {"multitenant": "centralizado", "tenant": "por_cliente"}.get(tipo, ""), est["plan"], nodos, est["anual"], str(hoy + timedelta(days=dias)),
                     "Oportunidad de demostración", ahora(), "demo", mod, json.dumps(est, ensure_ascii=False), tent)
            if demo:   # demo de FaceOps: semanas activadas y días desde que partió
                sem, desde = demo
                ini = hoy - timedelta(days=desde)
                fin = ini + timedelta(weeks=sem)
                pid = ej("INSERT INTO propuestas (cliente_id, numero, version, alcance, anios, mensual, implementacion, moneda, creditos, condiciones, estado, creado, por, "
                         "enviada, respuesta, emisor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cid, f"DEMO-OPO-{cid}", 1, "estandar", 1, 0, 0, "USD", 0,
                         "Demo de FaceOps (demo)", "aceptada", ahora(), "demo", ahora(), ahora(), "invoke")
                kid = ej("INSERT INTO contratos (cliente_id, propuesta_id, numero, estado, creado, por, inicio, fin, activacion_limite) VALUES (?,?,?,?,?,?,?,?,?)",
                         cid, pid, f"DEMO-OPO-{cid:04d}-DEMO", "demo", ahora(), "demo", str(ini), str(fin), str(fin))
                ej("UPDATE oportunidades SET demo_semanas=?, demo_inicio=?, demo_fin=?, contrato_demo_id=? WHERE id=?", sem, str(ini), str(fin), kid, oid)


sembrar_demo_ops()



def _numero_demo(cid):
    from contratos import pais_iso
    c = q1("SELECT pais FROM clientes WHERE id=?", cid) or {}
    ej("CREATE TABLE IF NOT EXISTS secuencias (clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
    ej("INSERT INTO secuencias (clave, valor) VALUES ('demo', 1) ON CONFLICT(clave) DO UPDATE SET valor=valor+1")
    return f"DEM-{pais_iso(c.get('pais'))}-{date.today().year}-{q1('SELECT valor FROM secuencias WHERE clave=?', 'demo')['valor']:04d}"


def _demos_previas():
    with app.app_context():
        for o in q("SELECT * FROM oportunidades WHERE COALESCE(demo_semanas,0)>0 AND id NOT IN (SELECT oportunidad_id FROM demos)"):
            ej("INSERT INTO demos (oportunidad_id, cliente_id, tipo, semanas, desde, hasta, contrato_id, creado, por) VALUES (?,?,?,?,?,?,?,?,?)", o["id"], o["cliente_id"],
               "activación", o["demo_semanas"], o["demo_inicio"], o["demo_fin"], o["contrato_demo_id"], o["creado"], o["por"])
        for d_ in q("SELECT id, cliente_id FROM demos WHERE COALESCE(numero,'')=''"):
            ej("UPDATE demos SET numero=? WHERE id=?", _numero_demo(d_["cliente_id"]), d_["id"])


_demos_previas()

app.jinja_env.globals["ahora_fecha"] = lambda: date.today().isoformat()
