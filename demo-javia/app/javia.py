"""JavIA · agentes de IA por aplicación sobre un data lake central."""
import datetime as dt
import functools
import os
import secrets
import threading
import time
import uuid

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import agentes as A
import lago
import sectores  # noqa: F401  (registra Banca y Telco)
from lago import APPS

app = Flask(__name__)
app.secret_key = os.environ.get("JAVIA_SECRETO") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=10),
                  SESSION_COOKIE_SECURE=os.environ.get("LAGO", "trino") != "duckdb")
_lock = threading.Lock()


# ─────────── Estado persistente (en el propio lake: zona javia/sistema) ───────────
def _leer(clave, defecto):
    try:
        return lago.lago.leer_json(clave) or defecto
    except Exception:  # noqa: BLE001
        return defecto


def _guardar(clave, obj):
    try:
        lago.lago.guardar_json(clave, obj)
    except Exception:  # noqa: BLE001
        pass


MEM = {"usuarios": None, "estado": None}


def usuarios():
    if MEM["usuarios"] is None:
        u = _leer("usuarios.json", {})
        adm = os.environ.get("JAVIA_ADMIN_USUARIO", "admin@javia.local").lower()
        if adm not in u and os.environ.get("JAVIA_ADMIN_CLAVE"):
            u[adm] = {"nombre": os.environ.get("JAVIA_ADMIN_NOMBRE", "Administrador"), "rol": "admin",
                      "hash": generate_password_hash(os.environ["JAVIA_ADMIN_CLAVE"]), "creado": dt.datetime.now().isoformat(timespec="minutes")}
            _guardar("usuarios.json", u)
        MEM["usuarios"] = u
    return MEM["usuarios"]


def estado():
    if MEM["estado"] is None:
        MEM["estado"] = _leer("estado.json", {}) or {}
        MEM["estado"].setdefault("agentes", {})
        MEM["estado"].setdefault("evaluaciones", {})
        MEM["estado"].setdefault("decisiones", [])
        MEM["estado"].setdefault("actividad", [])
    return MEM["estado"]


def guardar_estado():
    _guardar("estado.json", estado())


def registrar(texto):
    e = estado()
    e["actividad"].insert(0, {"en": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "por": g.usuario["nombre"] if g.get("usuario") else "sistema", "texto": texto})
    del e["actividad"][60:]


# ─────────── Sesión, CSRF y control de intentos ───────────
INTENTOS = {}


@app.before_request
def antes():
    g.usuario = None
    em = session.get("u")
    if em and em in usuarios():
        g.usuario = dict(usuarios()[em], email=em)
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST":
        tok = request.form.get("csrf") or request.headers.get("X-CSRF")
        if tok != session.get("csrf"):
            abort(400, "Sesión expirada: recarga la página.")


def requiere(rol=None):
    def deco(f):
        @functools.wraps(f)
        def env(*a, **k):
            if not g.usuario:
                if request.path.startswith("/api/"):
                    return jsonify(error="Sesión expirada"), 401
                return redirect(url_for("ingresar", siguiente=request.path))
            if rol and g.usuario["rol"] != rol:
                abort(403)
            return f(*a, **k)
        return env
    return deco


@app.context_processor
def ctx():
    e = estado()
    pend = sum(1 for d in e["decisiones"] if d["estado"] == "pendiente")
    ind = session.get("ind") if session.get("ind") in lago.INDUSTRIAS else "retail"
    apps_i = lago.apps_de(ind)
    return {"APPS": APPS, "APPS_IND": apps_i, "AGENTES_IND": {k: v for k, v in A.AGENTES.items() if k in apps_i}, "IND": ind,
            "INDUSTRIAS": lago.INDUSTRIAS, "AGENTES": A.AGENTES, "SOL": A.SOLUCIONES, "usuario": g.get("usuario"), "csrf": session.get("csrf"),
            "LAGO": lago.ESTADO, "MOTOR": lago.lago.nombre, "pendientes": pend, "activos": e["agentes"]}


@app.route("/ingresar", methods=["GET", "POST"])
def ingresar():
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]
        ahora = time.time()
        INTENTOS[ip] = [t for t in INTENTOS.get(ip, []) if ahora - t < 300]
        if len(INTENTOS[ip]) >= 6:
            flash("Demasiados intentos. Espera unos minutos.", "error")
            return render_template("ingresar.html"), 429
        em = (request.form.get("email") or "").strip().lower()
        u = usuarios().get(em)
        if u and check_password_hash(u["hash"], request.form.get("clave") or ""):
            session.clear()
            session.permanent = True
            session["u"] = em
            session["csrf"] = secrets.token_hex(16)
            sig = request.args.get("siguiente") or ""
            return redirect(sig if sig.startswith("/") and not sig.startswith("//") else url_for("inicio"))
        INTENTOS[ip].append(ahora)
        flash("Correo o clave incorrectos.", "error")
    return render_template("ingresar.html")


@app.route("/salir", methods=["POST"])
def salir():
    session.clear()
    return redirect(url_for("ingresar"))


# ─────────── Páginas ───────────
@app.route("/")
@requiere()
def inicio():
    e = estado()
    ind = session.get("ind") if session.get("ind") in lago.INDUSTRIAS else "retail"
    apps_i = lago.apps_de(ind)
    filas = sum((c.get("filas") or 0) for k, c in lago.ESTADO.get("cargas", {}).items() if k in apps_i)
    act = [a for a in A.AGENTES if a in apps_i and e["agentes"].get(a, {}).get("activo")]
    aprobadas = sum(1 for d in e["decisiones"] if d["estado"] == "aprobada")
    return render_template("inicio.html", filas=filas, act=act, aprobadas=aprobadas, e=e, gran=A.granite_listo())


@app.route("/industria/<ind>")
@requiere()
def industria(ind):
    if ind in lago.INDUSTRIAS:
        session["ind"] = ind
    sig = request.args.get("volver") or ""
    return redirect(sig if sig.startswith("/") and not sig.startswith("//") and not sig.startswith("/agentes/") else url_for("inicio"))


@app.route("/aplicaciones")
@requiere()
def aplicaciones():
    ind = session.get("ind") if session.get("ind") in lago.INDUSTRIAS else "retail"
    return render_template("aplicaciones.html", cargas=lago.ESTADO.get("cargas", {}), llaves=lago.INDUSTRIAS[ind].get("llaves", {}))


@app.route("/aplicaciones/recargar", methods=["POST"])
@requiere("admin")
def recargar():
    def tarea():
        try:
            lago.asegurar(forzar=True)
            A._cache.clear()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=tarea, daemon=True).start()
    registrar("Regeneró los datos emulados de todas las aplicaciones")
    guardar_estado()
    flash("Regenerando los datos emulados en el lake. Tarda menos de un minuto.", "ok")
    return redirect(url_for("aplicaciones"))


@app.route("/lake")
@requiere()
def lake():
    try:
        cat = lago.catalogo()
        err = None
    except Exception as ex:  # noqa: BLE001
        cat, err = [], str(ex)[:200]
    return render_template("lake.html", cat=cat, err=err)


@app.route("/lake/<esq>/<tab>")
@requiere()
def lake_tabla(esq, tab):
    if not (esq.replace("_", "").isalnum() and tab.replace("_", "").isalnum()):
        abort(404)
    cols = lago.columnas(esq, tab)
    if not cols:
        abort(404)
    df = lago.sql(f"SELECT * FROM lake.{esq}.{tab} LIMIT 25")
    return render_template("lake_tabla.html", esq=esq, tab=tab, cols=cols, cab=list(df.columns),
                           filas=df.astype(str).values.tolist(), app_=esq[4:] if esq[4:] in APPS else None)


@app.route("/agentes")
@requiere()
def catalogo():
    return render_template("agentes.html", ev=estado()["evaluaciones"])


@app.route("/agentes/<a>")
@requiere()
def agente(a):
    if a not in A.AGENTES and a != "lake":
        abort(404)
    e = estado()
    return render_template("agente.html", a=a, ag=A.AGENTES.get(a), ev=e["evaluaciones"].get(a, {}),
                           st=e["agentes"].get(a, {}), gran=A.granite_listo())


@app.route("/agentes/<a>/activar", methods=["POST"])
@requiere()
def activar(a):
    if a not in A.AGENTES:
        abort(404)
    e = estado()
    on = request.form.get("on") == "1"
    sol = [s for s in request.form.getlist("sol") if s in A.AGENTES[a]["soluciones"]]
    with _lock:
        if on:
            if not sol:
                flash("Elige al menos una solución evaluada para activar el agente.", "error")
                return redirect(url_for("agente", a=a))
            e["agentes"][a] = {"activo": True, "soluciones": sol, "por": g.usuario["nombre"], "en": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
            for s in sol:
                ev = e["evaluaciones"].get(a, {}).get(s)
                for d in (ev or {}).get("decisiones", []):
                    if not any(x["texto"] == d["texto"] and x["estado"] == "pendiente" for x in e["decisiones"]):
                        e["decisiones"].insert(0, {"id": uuid.uuid4().hex[:8], "app": a, "sol": s, "texto": d["texto"], "impacto": d.get("impacto", ""),
                                                   "area": d.get("area", ""), "estado": "pendiente", "en": dt.datetime.now().strftime("%Y-%m-%d %H:%M")})
            registrar(f"Activó el {A.AGENTES[a]['nombre']} ({APPS[a]['nombre']}) con {', '.join(A.SOLUCIONES[s]['nombre'] for s in sol)}")
            flash(f"{A.AGENTES[a]['nombre']} activo. Sus decisiones sugeridas quedaron en Decisiones, pendientes de aprobación.", "ok")
        else:
            e["agentes"][a] = {"activo": False, "por": g.usuario["nombre"], "en": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
            registrar(f"Desactivó el {A.AGENTES[a]['nombre']}")
            flash("Agente desactivado.", "ok")
        guardar_estado()
    return redirect(url_for("agente", a=a))


@app.route("/decisiones")
@requiere()
def decisiones():
    return render_template("decisiones.html", ds=estado()["decisiones"])


@app.route("/decisiones/<i>", methods=["POST"])
@requiere()
def decidir(i):
    acc = request.form.get("accion")
    with _lock:
        for d in estado()["decisiones"]:
            if d["id"] == i and d["estado"] == "pendiente" and acc in ("aprobada", "descartada"):
                d.update(estado=acc, por=g.usuario["nombre"], resuelta=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                         comentario=(request.form.get("comentario") or "")[:300])
                registrar(f"{'Aprobó' if acc == 'aprobada' else 'Descartó'} la decisión «{d['texto']}»")
        guardar_estado()
    return redirect(url_for("decisiones"))


@app.route("/usuarios", methods=["GET", "POST"])
@requiere("admin")
def gestion_usuarios():
    us = usuarios()
    if request.method == "POST":
        em = (request.form.get("email") or "").strip().lower()
        if request.form.get("accion") == "eliminar":
            if em != g.usuario["email"] and em in us:
                del us[em]
                registrar(f"Eliminó al usuario {em}")
        else:
            clave = request.form.get("clave") or ""
            if "@" not in em or len(clave) < 10:
                flash("Correo válido y clave de al menos 10 caracteres.", "error")
                return redirect(url_for("gestion_usuarios"))
            us[em] = {"nombre": (request.form.get("nombre") or em).strip()[:60], "rol": "admin" if request.form.get("rol") == "admin" else "analista",
                      "hash": generate_password_hash(clave), "creado": dt.datetime.now().isoformat(timespec="minutes")}
            registrar(f"Creó al usuario {em}")
            flash("Usuario creado.", "ok")
        _guardar("usuarios.json", us)
        guardar_estado()
        return redirect(url_for("gestion_usuarios"))
    return render_template("usuarios.html", us=us)


@app.route("/perfil", methods=["GET", "POST"])
@requiere()
def perfil():
    if request.method == "POST":
        u = usuarios()[g.usuario["email"]]
        if not check_password_hash(u["hash"], request.form.get("actual") or ""):
            flash("La clave actual no es correcta.", "error")
        elif len(request.form.get("nueva") or "") < 10 or request.form.get("nueva") != request.form.get("repite"):
            flash("La nueva clave debe tener al menos 10 caracteres y coincidir.", "error")
        else:
            u["hash"] = generate_password_hash(request.form["nueva"])
            _guardar("usuarios.json", usuarios())
            flash("Clave actualizada.", "ok")
        return redirect(url_for("perfil"))
    return render_template("perfil.html")


# ─────────── API ───────────
@app.post("/api/evaluar/<a>/<sol>")
@requiere()
def api_evaluar(a, sol):
    if a not in A.AGENTES or sol not in A.AGENTES[a]["soluciones"]:
        abort(404)
    if not lago.ESTADO["listo"]:
        return jsonify(error=lago.ESTADO["mensaje"] or "El lake aún no está listo"), 503
    try:
        r = A.evaluar(sol)
    except Exception as ex:  # noqa: BLE001
        return jsonify(error=f"No se pudo evaluar: {str(ex)[:200]}"), 500
    with _lock:
        estado()["evaluaciones"].setdefault(a, {})[sol] = r
        registrar(f"Evaluó «{A.SOLUCIONES[sol]['nombre']}» para {APPS[a]['nombre']}: {r['metrica']['fmt']}")
        guardar_estado()
    return jsonify(r)


@app.post("/api/preguntar/<a>")
@requiere()
def api_preguntar(a):
    p = (request.json or {}).get("pregunta", "").strip()[:400]
    if not p:
        return jsonify(error="Escribe una pregunta"), 400
    r = A.preguntar(a, p, session.get("ind"))
    with _lock:
        registrar(f"Preguntó al {'lake' if a == 'lake' else A.AGENTES[a]['nombre']}: «{p[:80]}»")
    return jsonify(r)


@app.post("/api/clasificar")
@requiere()
def api_clasificar():
    t = (request.json or {}).get("texto", "").strip()[:500]
    return jsonify(A.clasificar_texto(t)) if t else (jsonify(error="Escribe un texto"), 400)


@app.post("/api/sql")
@requiere()
def api_sql():
    q = (request.json or {}).get("sql", "")
    try:
        q = lago.solo_lectura(q)
        import re
        df = lago.sql(q if re.search(r"(?i)\blimit\b", q) else q + " LIMIT 200")
    except Exception as ex:  # noqa: BLE001
        return jsonify(error=str(ex)[:300]), 400
    for c in df.columns:
        if str(df[c].dtype).startswith("float"):
            df[c] = df[c].round(4)
    return jsonify(cols=list(df.columns), filas=df.astype(str).values.tolist())


@app.get("/api/estado")
def api_estado():
    return jsonify(listo=lago.ESTADO["listo"], mensaje=lago.ESTADO["mensaje"])


@app.get("/salud")
def salud():
    return jsonify(ok=True, lago=lago.ESTADO["listo"], motor=lago.lago.nombre)


lago.asegurar_en_fondo()
