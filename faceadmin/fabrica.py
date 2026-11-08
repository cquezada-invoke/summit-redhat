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


IMAGENES = {   # imagen del bundle → (producto, componente)
    "gateway": ("aap", "Platform gateway"), "gateway-proxy": ("aap", "Proxy del gateway"), "controller": ("aap", "Automation controller"),
    "hub": ("aap", "Automation hub"), "hub-web": ("aap", "Automation hub · web"), "eda-controller": ("aap", "Event-Driven Ansible"),
    "eda-controller-ui": ("aap", "Event-Driven Ansible · web"), "receptor": ("aap", "Receptor (malla de ejecución)"),
    "metrics-service": ("aap", "Servicio de métricas"), "ee-minimal": ("ee", "Entorno de ejecución mínimo"),
    "ee-supported": ("ee", "Entorno de ejecución soportado"), "ee-control-plane": ("ee", "Entorno del plano de control"),
    "de-minimal": ("ee", "Entorno de decisión mínimo"), "de-supported": ("ee", "Entorno de decisión soportado"),
    "lightspeed": ("ia", "Ansible Lightspeed"), "lightspeed-chatbot": ("ia", "Lightspeed · asistente"), "mcp-server": ("ia", "Servidor MCP"),
    "mcp-tools": ("ia", "Herramientas MCP"), "pcp": ("otros", "Performance Co-Pilot (métricas del sistema)")}


def productos(d):
    """Lista de materiales de esta versión de FaceOps: lo que se instala junto a él en la máquina del cliente."""
    import re
    import tarfile
    from instalacion import DIR, archivos
    ar = archivos()
    imgs = []
    b = ar.get("bundle")
    if b:   # imágenes del bundle de AAP (se leen una vez por cada bundle y se guardan)
        cache = os.path.join(DIR, b["nombre"] + ".imagenes.json")
        try:
            imgs = json.load(open(cache, encoding="utf-8"))
        except (OSError, ValueError):
            try:
                with tarfile.open(os.path.join(DIR, b["nombre"])) as t:
                    imgs = sorted({os.path.basename(m.name)[:-7] for m in t.getmembers() if "/bundle/images/" in m.name and m.name.endswith(".tar.gz")})
                json.dump(imgs, open(cache, "w", encoding="utf-8"))
            except (OSError, tarfile.TarError):
                imgs = []
    ver = re.search(r"bundle-(\d+\.\d+)-(\d+)", b["nombre"]).groups() if b else ("", "")
    comp = {"aap": [], "ee": [], "ia": [], "otros": []}
    pg = rd = ""
    for i in imgs:
        nom = re.sub(r"-rhel9$", "", i)
        if nom.startswith("postgresql-"):
            pg = nom.split("-", 1)[1]
        elif nom.startswith("redis-"):
            rd = nom.split("-", 1)[1]
        elif nom in IMAGENES:
            g, t = IMAGENES[nom]
            comp[g].append(t)
    base = d.get("base") or {}
    out = [{"prod": f"FaceOps", "version": f"v{d['faceops'].get('linea', '1.0')}.{d['faceops'].get('build', '')}", "tipo": "Producto Invoke",
            "detalle": f"{len(d['modulos'])} módulos · imagen {base.get('imagen', '').split('/')[-1]}", "estado": "ok"},
           {"prod": "Red Hat Ansible Automation Platform", "version": f"{ver[0]} (bundle {ver[0]}-{ver[1]})" if b else "—", "tipo": "Plataforma de automatización",
            "detalle": " · ".join(comp["aap"]) or "falta el bundle", "estado": "ok" if b else "falta"},
           {"prod": "Entornos de ejecución y decisión", "version": ver[0] or "—", "tipo": "Parte de AAP", "detalle": " · ".join(comp["ee"]) or "—", "estado": "ok" if comp["ee"] else "falta"},
           {"prod": "PostgreSQL", "version": pg or "—", "tipo": "Base de datos de AAP", "detalle": "bases de gateway, controller, hub, EDA y métricas", "estado": "ok" if pg else "falta"},
           {"prod": "Redis", "version": rd or "—", "tipo": "Caché de AAP", "detalle": "modo standalone", "estado": "ok" if rd else "falta"},
           {"prod": "Ansible Lightspeed y MCP", "version": ver[0] or "—", "tipo": "IA de AAP (opcional)", "detalle": " · ".join(comp["ia"]) or "—", "estado": "ok" if comp["ia"] else "falta"},
           {"prod": "Catálogo de acciones", "version": f"{(ar.get('catalogo') or {}).get('n', 0)} acciones", "tipo": "Contenido FaceOps",
            "detalle": "plantillas de AAP con sus playbooks del repositorio central", "estado": "ok" if ar.get("catalogo") else "falta"},
           {"prod": "Suscripción Red Hat (CCSP)", "version": (ar.get("manifiesto") or {}).get("fecha", "—"), "tipo": "Licencia",
            "detalle": "manifiesto de Invoke como proveedor CCSP", "estado": "ok" if ar.get("manifiesto") else "falta"},
           {"prod": "Red Hat Enterprise Linux", "version": "9", "tipo": "Sistema operativo (requisito)", "detalle": "con Podman, firewalld y SELinux en enforcing", "estado": "requisito"},
           {"prod": "Python y librerías de FaceOps", "version": base.get("imagen", "").split(":")[0].split("/")[-1].replace("python-", "Python "),
            "tipo": "Dentro de la imagen de FaceOps", "detalle": " · ".join(f"{x['nombre']} {x['version']}" for x in base.get("dependencias", [])), "estado": "ok"}]
    return out


@app.route("/fabrica")
@requiere_login
def fabrica():
    solo_invoke()
    d = datos()
    flota = estados(q("SELECT * FROM clientes WHERE activo=1 AND habilitado=1 AND COALESCE(tipo,'cliente')='cliente' ORDER BY rol='laboratorio' DESC, nombre"))
    from instalacion import archivos
    return render_template("fabrica.html", d=d, etapas=ETAPAS, etapa_t=ETAPA_T, flota=flota, repo=REPO, ar=archivos(), productos=productos(d))


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
