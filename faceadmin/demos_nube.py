"""FaceAdmin · Demo y PoC.

- Demo: un entorno de Invoke ya preparado en una nube (no en el cliente) para que el cliente pruebe la plataforma por unos días.
  Sin instalación ni licencias del cliente. Vence sola al llegar a su fecha de término.
- PoC (prueba de concepto): la instalación de FaceOps en las dependencias del cliente, por semanas (tabla «demos», ver oportunidades.py).

Las duraciones máximas de cada una se configuran en «Demos y PoC» (archivo duraciones.json junto a la base de datos)."""
import json
import os
from datetime import date, timedelta

from flask import abort, flash, g, redirect, render_template, request, url_for

from app import BD, ahora, app, bitacora, cliente_ok, ej, en_alcance, q, q1, requiere_login, solo_invoke

DUR_ARCH = os.path.join(os.path.dirname(BD), "duraciones.json")
DUR_DEF = {"demo_dias": 14, "poc_semanas": 4}
DIAS_SUGERIDOS = (3, 5, 7, 14, 21, 30)
ESTADOS = {"pedida": ("Pedida · falta preparar el entorno", "med"), "preparada": ("Entorno preparado", "med"), "activa": ("Activa", "ok"),
           "vencida": ("Vencida", "mal"), "cerrada": ("Cerrada", "")}


def duraciones():
    """Máximos configurables: días de la Demo y semanas de la PoC."""
    d = dict(DUR_DEF)
    try:
        x = json.load(open(DUR_ARCH, encoding="utf-8"))
        for k in d:
            if isinstance(x.get(k), int) and x[k] > 0:
                d[k] = x[k]
    except (OSError, ValueError):
        pass
    return d


def max_demo_dias():
    return duraciones()["demo_dias"]


def max_poc_semanas():
    return duraciones()["poc_semanas"]


app.jinja_env.globals["max_poc_semanas"] = max_poc_semanas
app.jinja_env.globals["max_demo_dias"] = max_demo_dias


def iniciar():
    with app.app_context():
        ej("CREATE TABLE IF NOT EXISTS demos_nube (id INTEGER PRIMARY KEY, numero TEXT, oportunidad_id INTEGER NOT NULL, cliente_id INTEGER NOT NULL, "
           "dias INTEGER, desde TEXT, hasta TEXT, entorno_nombre TEXT DEFAULT '', entorno_url TEXT DEFAULT '', entrega TEXT DEFAULT '', "
           "estado TEXT DEFAULT 'pedida', motivo TEXT DEFAULT '', creado TEXT, por TEXT, origen TEXT DEFAULT 'faceadmin', cerrada TEXT DEFAULT '')")


iniciar()


def _numero(cid):
    from contratos import pais_iso
    c = q1("SELECT pais FROM clientes WHERE id=?", cid) or {}
    ej("CREATE TABLE IF NOT EXISTS secuencias (clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
    ej("INSERT INTO secuencias (clave, valor) VALUES ('demo_nube', 1) ON CONFLICT(clave) DO UPDATE SET valor=valor+1")
    return f"DMO-{pais_iso(c.get('pais'))}-{date.today().year}-{q1('SELECT valor FROM secuencias WHERE clave=?', 'demo_nube')['valor']:04d}"


def estado(d):
    """Estado efectivo: la Demo vence sola al pasar su fecha de término."""
    hoy = date.today().isoformat()
    if d.get("estado") == "cerrada":
        return "cerrada"
    if (d.get("hasta") or "") < hoy:
        return "vencida"
    if d.get("estado") == "preparada":
        return "activa" if (d.get("desde") or "") <= hoy else "preparada"
    return "pedida"


def fila(d):
    d["c"] = q1("SELECT * FROM clientes WHERE id=?", d["cliente_id"]) or {}
    d["o"] = q1("SELECT * FROM oportunidades WHERE id=?", d["oportunidad_id"]) or {}
    d["est"] = estado(d)
    d["est_t"] = ESTADOS[d["est"]]
    d["vigente"] = d["est"] in ("pedida", "preparada", "activa")
    d["quedan"] = max(0, (date.fromisoformat(d["hasta"]) - date.today()).days) if d["vigente"] else 0
    d["clase"] = "demo"
    return d


def de_oportunidad(oid):
    return [fila(x) for x in q("SELECT * FROM demos_nube WHERE oportunidad_id=? ORDER BY id DESC", oid)]


def _validar_dias(f, usados=0):
    try:
        dias = int(f.get("dias") or 0)
    except ValueError:
        dias = 0
    mx = max_demo_dias()
    if dias < 1 or usados + dias > mx:
        raise ValueError(f"La Demo dura entre 1 y {mx} días en total" + (f" (ya usados: {usados})" if usados else ""))
    return dias


def pedir(o, c, f, origen="faceadmin", por=None):
    """Registra el pedido de una Demo en el entorno de Invoke en la nube. Devuelve el id."""
    dias = _validar_dias(f)
    if q1("SELECT id FROM demos_nube WHERE oportunidad_id=? AND estado<>'cerrada' AND hasta>=?", o["id"], date.today().isoformat()):
        raise ValueError("Esta oportunidad ya tiene una Demo vigente: extiéndela o ciérrala antes de pedir otra")
    try:
        ini = date.fromisoformat((f.get("desde") or "")[:10]) if f.get("desde") else date.today()
    except ValueError:
        ini = date.today()
    if ini < date.today():
        ini = date.today()
    fin = ini + timedelta(days=dias - 1)
    por = por or g.usuario["usuario"]
    did = ej("INSERT INTO demos_nube (numero, oportunidad_id, cliente_id, dias, desde, hasta, estado, motivo, creado, por, origen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
             _numero(c["id"]), o["id"], c["id"], dias, ini.isoformat(), fin.isoformat(), "pedida", (f.get("motivo") or "").strip()[:500], ahora(), por, origen)
    ej("UPDATE oportunidades SET etapa=CASE WHEN etapa='prospecto' THEN 'demo' ELSE etapa END WHERE id=?", o["id"])
    d = q1("SELECT * FROM demos_nube WHERE id=?", did)
    _nota(o["id"], por, f.get("motivo") or "", f"Demo pedida ({d['numero']}): {dias} día{'s' if dias > 1 else ''}, {d['desde']} → {d['hasta']}")
    bitacora("oportunidad · demo pedida", c["codigo"], {"oportunidad": o["numero"], "demo": d["numero"], "dias": dias, "hasta": d["hasta"], "origen": origen})
    return did


def _nota(oid, usuario, texto, cambios):
    from oportunidades import _notas_tabla
    _notas_tabla()
    ej("INSERT INTO oportunidad_notas (oportunidad_id, fecha, usuario, texto, cambios) VALUES (?,?,?,?,?)", oid, ahora(), usuario, (texto or "").strip()[:500], cambios)


@app.route("/demos-nube/<int:did>", methods=["GET", "POST"])
@requiere_login
def demo_nube(did):
    """La Demo: entorno asignado (URL y nombre), cómo se entregan las credenciales, período y estado."""
    solo_invoke()
    d = q1("SELECT * FROM demos_nube WHERE id=?", did) or abort(404)
    c = cliente_ok(d["cliente_id"])
    if request.method == "POST":
        f, acc = request.form, request.form.get("accion")
        u = g.usuario["usuario"]
        if acc == "entorno":
            url_ = (f.get("entorno_url") or "").strip()[:300]
            if url_ and not url_.startswith(("https://", "http://")):
                flash("La URL de acceso debe partir con https://", "error")
                return redirect(url_for("demo_nube", did=did))
            nom, ent = (f.get("entorno_nombre") or "").strip()[:120], (f.get("entrega") or "").strip()[:500]
            listo = bool(url_ and nom)
            ej("UPDATE demos_nube SET entorno_nombre=?, entorno_url=?, entrega=?, estado=CASE WHEN estado='cerrada' THEN estado WHEN ? THEN 'preparada' ELSE 'pedida' END "
               "WHERE id=?", nom, url_, ent, 1 if listo else 0, did)
            _nota(d["oportunidad_id"], u, "", f"Demo {d['numero']}: entorno {'asignado (' + nom + ')' if listo else 'actualizado'}")
            bitacora("demo · entorno asignado" if listo else "demo · entorno actualizado", c["codigo"], {"demo": d["numero"], "entorno": nom})
            flash("Entorno asignado: la Demo queda preparada y se activa en su fecha de inicio" if listo else "Datos guardados; falta la URL y el nombre del entorno", "ok")
        elif acc == "extender":
            try:
                dias = _validar_dias(f, d["dias"] or 0)
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("demo_nube", did=did))
            base = max(date.fromisoformat(d["hasta"]), date.today() - timedelta(days=1))
            fin = (base + timedelta(days=dias)).isoformat()
            ej("UPDATE demos_nube SET dias=?, hasta=? WHERE id=?", (d["dias"] or 0) + dias, fin, did)
            _nota(d["oportunidad_id"], u, f.get("motivo") or "", f"Demo {d['numero']} extendida {dias} día{'s' if dias > 1 else ''} hasta el {fin}")
            bitacora("demo · extendida", c["codigo"], {"demo": d["numero"], "dias": dias, "hasta": fin})
            flash(f"Demo extendida hasta el {fin}", "ok")
        elif acc == "cerrar" and d["estado"] != "cerrada":
            ej("UPDATE demos_nube SET estado='cerrada', cerrada=? WHERE id=?", ahora(), did)
            _nota(d["oportunidad_id"], u, f.get("motivo") or "", f"Demo {d['numero']} cerrada")
            bitacora("demo · cerrada", c["codigo"], {"demo": d["numero"]})
            flash("Demo cerrada: recuerda retirar el acceso del cliente al entorno", "ok")
        return redirect(url_for("demo_nube", did=did))
    d = fila(d)
    eventos = q("SELECT fecha, usuario, accion FROM bitacora WHERE objeto=? AND accion LIKE '%demo%' AND substr(fecha,1,10)>=? ORDER BY id DESC LIMIT 40",
                c["codigo"], d["creado"][:10])
    return render_template("demo_nube.html", d=d, c=c, o=d["o"], estados=ESTADOS, eventos=eventos, max_dias=max_demo_dias(), sugeridos=DIAS_SUGERIDOS)


@app.route("/demos/duraciones", methods=["POST"])
@requiere_login
def demos_duraciones():
    """Duración máxima de la Demo (días) y de la PoC (semanas)."""
    solo_invoke()
    try:
        dd, ps = int(request.form.get("demo_dias") or 0), int(request.form.get("poc_semanas") or 0)
    except ValueError:
        dd = ps = 0
    if not (1 <= dd <= 90 and 1 <= ps <= 26):
        flash("La Demo admite entre 1 y 90 días y la PoC entre 1 y 26 semanas", "error")
        return redirect(url_for("demos"))
    with open(DUR_ARCH, "w", encoding="utf-8") as fh:
        json.dump({"demo_dias": dd, "poc_semanas": ps}, fh)
    bitacora("duraciones máximas de Demo y PoC", "invoke", {"demo_dias": dd, "poc_semanas": ps})
    flash(f"Máximos guardados: Demo {dd} días · PoC {ps} semanas", "ok")
    return redirect(url_for("demos"))


def todas():
    out = [fila(x) for x in q("SELECT * FROM demos_nube ORDER BY id DESC")]
    return [d for d in out if d["c"] and en_alcance(d["c"])]
