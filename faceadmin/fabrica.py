"""FaceAdmin · Fábrica: FaceOps como un ensamblado de módulos, igual que un avión. Cada módulo tiene su repositorio, su versión
y su ciclo de vida; FaceOps es la suma de todos. Los datos de cada módulo (piezas, actividad en git) salen de fabrica.json, que
version.sh regenera en cada construcción de FaceAdmin; la etapa del ciclo de vida la fija Invoke aquí."""
import json
import os

from flask import abort, flash, g, redirect, render_template, request, url_for

from app import REPO, ahora, app, bitacora, ej, estados, q, q1, requiere_login, solo_invoke

ARCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fabrica.json")
ETAPAS = [("diseno", "Diseño"), ("construccion", "Construcción"), ("pruebas", "Pruebas"), ("produccion", "Producción"), ("retiro", "Retiro")]
ETAPA_T = dict(ETAPAS)
# dónde va cada módulo en el avión
PARTES = {"nucleo": "fuselaje", "integraciones": "cabina", "infraestructura": "ala-i1", "servidores": "ala-i2", "sistemas": "ala-i3", "hipervisores": "ala-i4",
          "nubes": "ala-i5", "redes": "ala-d1", "storage": "ala-d2", "bases_datos": "ala-d3", "plataformas": "ala-d4", "contenedores": "ala-d5",
          "aplicaciones": "motor-i", "monitoreo": "motor-d", "gobierno": "deriva", "seguridad": "cola-i", "estaciones": "cola-d"}


def iniciar():
    with app.app_context():
        ej("CREATE TABLE IF NOT EXISTS fabrica_ciclo (clave TEXT PRIMARY KEY, etapa TEXT, repo_url TEXT DEFAULT '', notas TEXT DEFAULT '', "
           "actualizado TEXT, por TEXT)")


iniciar()


def datos():
    try:
        d = json.load(open(ARCH, encoding="utf-8"))
    except (OSError, ValueError):
        d = {"modulos": [], "faceops": {}, "sin_modulo": {"n": 0}}
    ciclo = {r["clave"]: r for r in q("SELECT * FROM fabrica_ciclo")}
    for m in d["modulos"]:
        a = m["actividad"]
        est = ("produccion" if a["commits"] >= 30 and m["lineas"] >= 1000 else "pruebas" if m["lineas"] >= 500 else
               "construccion" if m["lineas"] else "diseno")
        c = ciclo.get(m["clave"]) or {}
        m["etapa"] = c.get("etapa") or est
        m["etapa_estimada"] = not c.get("etapa")
        m["repo_url"] = c.get("repo_url") or ""
        m["notas"] = c.get("notas") or ""
        m["parte"] = PARTES.get(m["clave"], "")
        m["n_piezas"] = sum(len(v) for v in m["piezas"].values())
        m["ultimo"] = a["ultimos"][0] if a["ultimos"] else {}
    d["por_etapa"] = {k: sum(1 for m in d["modulos"] if m["etapa"] == k) for k, _ in ETAPAS}
    d["total_lineas"] = sum(m["lineas"] for m in d["modulos"])
    d["total_piezas"] = sum(m["n_piezas"] for m in d["modulos"])
    return d


@app.route("/fabrica")
@requiere_login
def fabrica():
    solo_invoke()
    d = datos()
    flota = estados(q("SELECT * FROM clientes WHERE activo=1 AND habilitado=1 AND COALESCE(tipo,'cliente')='cliente' ORDER BY rol='laboratorio' DESC, nombre"))
    from instalacion import archivos
    return render_template("fabrica.html", d=d, etapas=ETAPAS, etapa_t=ETAPA_T, flota=flota, repo=REPO, ar=archivos())


@app.route("/fabrica/<clave>", methods=["GET", "POST"])
@requiere_login
def fabrica_modulo(clave):
    solo_invoke()
    d = datos()
    m = next((x for x in d["modulos"] if x["clave"] == clave), None) or abort(404)
    if request.method == "POST":
        f = request.form
        if f.get("etapa") not in ETAPA_T:
            flash("Elige la etapa del ciclo de vida", "error")
        else:
            ej("INSERT INTO fabrica_ciclo (clave, etapa, repo_url, notas, actualizado, por) VALUES (?,?,?,?,?,?) ON CONFLICT(clave) DO UPDATE SET "
               "etapa=excluded.etapa, repo_url=excluded.repo_url, notas=excluded.notas, actualizado=excluded.actualizado, por=excluded.por",
               clave, f["etapa"], (f.get("repo_url") or "").strip()[:200], (f.get("notas") or "").strip()[:2000], ahora(), g.usuario["usuario"])
            bitacora("fábrica · ciclo de vida", clave, {"etapa": f["etapa"]})
            flash(f"{m['nombre']}: etapa «{ETAPA_T[f['etapa']]}»", "ok")
        return redirect(url_for("fabrica_modulo", clave=clave))
    return render_template("fabrica_modulo.html", d=d, m=m, etapas=ETAPAS, etapa_t=ETAPA_T, repo=REPO)
