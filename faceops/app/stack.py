"""Evaluación del stack tecnológico del cliente: barrido de versiones de sistemas operativos, plataformas y lenguajes de
todas sus máquinas (ticket «Arquitectura · Evaluar stack tecnológico» en AAP) más lo que FaceOps ya sabe de equipos de
red y clústeres; se cruza con el ciclo de vida del fabricante (datos/ciclo-vida.yml) y se mide la obsolescencia."""
import json
import os
import re
from datetime import date

from .bd import ahora, con, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS stack_eval (id INTEGER PRIMARY KEY, cliente_id INTEGER, ticket_id INTEGER, creado TEXT, datos TEXT);
"""
AUTOMATIZACION = "Arquitectura · Evaluar stack tecnológico"
PESO = {"fuera": 1.0, "extendido": 0.7, "mantencion": 0.35, "completo": 0.0}
# paquete RPM → (producto del ciclo de vida, nombre a mostrar, tipo)
PAQUETES = [(r"^eap(\d)", "jboss-eap", "JBoss EAP", "plataforma"), (r"^jws\d?-tomcat$|^tomcat$", "tomcat", "Apache Tomcat", "plataforma"),
            (r"^postgresql\d*-server$", "postgresql", "PostgreSQL", "base de datos"), (r"^redis$|^valkey$", "redis", "Redis / Valkey", "base de datos"),
            (r"^nginx$", "nginx", "nginx", "plataforma"), (r"^httpd$", "httpd", "Apache httpd", "plataforma"),
            (r"^java-([\d.]+)-openjdk-headless$", "java", "OpenJDK", "lenguaje"), (r"^python3(\.\d+)?$", "python", "Python", "lenguaje"),
            (r"^nodejs$", "nodejs", "Node.js", "lenguaje"), (r"^mariadb-server$|^mysql-server$", "mysql", "MariaDB / MySQL", "base de datos"),
            (r"^openssl$", "openssl", "OpenSSL", "biblioteca"), (r"^podman$", "podman", "Podman", "plataforma"),
            (r"^kafka|^amq-streams", "kafka", "Kafka / AMQ Streams", "mensajería"), (r"^rh-sso|^rhbk|^keycloak", "keycloak", "Keycloak / RHBK", "identidad")]


def iniciar():
    con().executescript(ESQUEMA)


def _componentes(cliente_id, artefactos):
    """Lista de componentes encontrados: {equipo, tipo, producto, nombre, version}."""
    from .ciclo_vida import producto_de
    out = []
    eqs = {e["nombre"]: e for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cliente_id)}
    for host, d in (artefactos or {}).items():
        e = eqs.get(host) or {"nombre": host, "so": d.get("so")}
        p, v = producto_de(dict(e, so=d.get("so") or e.get("so")))
        out.append({"equipo": host, "tipo": "sistema operativo", "producto": p or "", "nombre": d.get("so") or e.get("so") or "?", "version": v})
        for pk in d.get("paquetes") or []:
            for pat, prod, nom, tipo in PAQUETES:
                m = re.search(pat, pk.get("name", ""))
                if m:
                    ver = pk.get("version", "")
                    if prod == "jboss-eap" and m.groups():
                        ver = {"7": "7.4", "8": "8.0"}.get(m.group(1), m.group(1))
                    if prod == "java" and m.groups():
                        ver = m.group(1)
                    out.append({"equipo": host, "tipo": tipo, "producto": prod, "nombre": nom, "version": ver})
                    break
        if d.get("eap_manual"):
            mv = re.search(r"(\d+\.\d+)", d["eap_manual"])
            out.append({"equipo": host, "tipo": "plataforma", "producto": "jboss-eap", "nombre": "JBoss EAP", "version": mv.group(1) if mv else d["eap_manual"]})
    # lo que FaceOps ya sabe de equipos que el barrido no toca (red, clústeres, Windows…)
    vistos = set((artefactos or {}).keys())
    for n, e in eqs.items():
        if n in vistos or not e.get("so"):
            continue
        p, v = producto_de(e)
        out.append({"equipo": n, "tipo": "sistema operativo" if (e.get("categoria") or "servidor") == "servidor" else (e.get("categoria") or "equipo"),
                    "producto": p or "", "nombre": e["so"], "version": v, "origen": "inventario"})
    return out


def evaluar(cliente_id, artefactos, hoy=None):
    from .ciclo_vida import datos, evaluar_producto, version_ciclo
    hoy = hoy or date.today()
    comps = _componentes(cliente_id, artefactos)
    grupos = {}
    for c in comps:
        vciclo = version_ciclo(c["producto"], c["version"]) if c["producto"] in datos() else c["version"]
        k = (c["tipo"], c["nombre"] if not c["producto"] else c["producto"], vciclo)
        g_ = grupos.setdefault(k, {"tipo": c["tipo"], "nombre": (datos().get(c["producto"]) or {}).get("nombre") or c["nombre"], "producto": c["producto"],
                                   "version": vciclo, "versiones": set(), "equipos": []})
        g_["versiones"].add(str(c["version"]))
        g_["equipos"].append(c["equipo"])
    filas = []
    for g_ in grupos.values():
        ev = evaluar_producto(g_["producto"], g_["version"], hoy) if g_["producto"] in datos() else None
        fase = ev["fase"] if ev else "desconocido"
        filas.append({"tipo": g_["tipo"], "nombre": g_["nombre"], "version": g_["version"], "instaladas": sorted(g_["versiones"]),
                      "equipos": sorted(set(g_["equipos"])), "n": len(set(g_["equipos"])), "fase": fase,
                      "fin_soporte": str(ev["fin_soporte"]) if ev and ev.get("fin_soporte") else "", "dias": ev["dias"] if ev else None,
                      "siguiente": ev["siguiente"] if ev else "", "fuente": ev["fuente"] if ev else ""})
    orden = {"fuera": 0, "extendido": 1, "mantencion": 2, "completo": 3, "desconocido": 4}
    filas.sort(key=lambda f: (orden[f["fase"]], f["dias"] if f["dias"] is not None else 99999, f["nombre"]))
    medibles = [f for f in filas if f["fase"] in PESO]
    tot = sum(f["n"] for f in medibles)
    indice = round(100 * sum(PESO[f["fase"]] * f["n"] for f in medibles) / tot) if tot else 0
    cuenta = {k: sum(f["n"] for f in filas if f["fase"] == k) for k in orden}
    return {"fecha": ahora(), "indice": indice, "filas": filas, "cuenta": cuenta, "equipos": len({c["equipo"] for c in comps}),
            "componentes": len(comps), "sin_ciclo": sum(1 for f in filas if f["fase"] == "desconocido")}


def guardar(t, artefactos):
    iniciar()
    r = evaluar(t["cliente_id"], (artefactos or {}).get("faceops_stack") or {})
    ej("INSERT INTO stack_eval(cliente_id, ticket_id, creado, datos) VALUES (?,?,?,?)", t["cliente_id"], t["id"], ahora(), json.dumps(r, ensure_ascii=False))
    try:
        informe_pdf(t, r)
    except Exception as x:  # noqa: BLE001 - sin PDF la evaluación queda igual en FaceOps
        print("Informe de stack:", str(x)[:200])
    return r


def ultimo(cliente_id):
    iniciar()
    x = q1("SELECT * FROM stack_eval WHERE cliente_id=? ORDER BY id DESC", cliente_id)
    return dict(j(x["datos"], {}), id=x["id"], ticket_id=x["ticket_id"]) if x else None


def ruta_informe(eid):
    d = os.environ.get("FACEOPS_INFORMES", "/var/lib/faceops/informes")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"stack-{eid}.pdf")


def informe_pdf(t, r):
    """Informe PDF de obsolescencia: índice, cuenta por fase y la tabla de componentes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    x = q1("SELECT id FROM stack_eval WHERE ticket_id=? ORDER BY id DESC", t["id"])
    c = q1("SELECT nombre FROM clientes WHERE id=?", t["cliente_id"])
    st = getSampleStyleSheet()
    doc = SimpleDocTemplate(ruta_informe(x["id"]), pagesize=landscape(A4), title="Evaluación del stack tecnológico")
    fase_n = {"fuera": "Fuera de soporte", "extendido": "Solo extendido", "mantencion": "Mantención", "completo": "Soporte completo", "desconocido": "Sin datos"}
    els = [Paragraph(f"Evaluación del stack tecnológico · {c['nombre']}", st["Title"]),
           Paragraph(f"Ticket {t['numero']} · {r['fecha'][:16].replace('T', ' ')} · {r['equipos']} equipos · {r['componentes']} componentes", st["Normal"]),
           Spacer(1, 8),
           Paragraph(f"<b>Índice de obsolescencia: {r['indice']} %</b> (0 % = todo con soporte completo; 100 % = todo fuera de soporte)", st["Heading2"]),
           Paragraph(" · ".join(f"{fase_n[k]}: {v}" for k, v in r["cuenta"].items() if v), st["Normal"]), Spacer(1, 10)]
    filas = [["Tipo", "Componente", "Versión", "Equipos", "Fase", "Fin de soporte", "Días", "Ir a"]]
    for f in r["filas"]:
        filas.append([f["tipo"], f["nombre"], f["version"], str(f["n"]), fase_n[f["fase"]], f["fin_soporte"] or "—",
                      "" if f["dias"] is None else str(f["dias"]), f["siguiente"] or "—"])
    tb = Table(filas, repeatRows=1)
    sty = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#151515")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
           ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc"))]
    for i, f in enumerate(r["filas"], 1):
        col = {"fuera": "#fde2e1", "extendido": "#fde9d4", "mantencion": "#fff6d6"}.get(f["fase"])
        if col:
            sty.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor(col)))
    tb.setStyle(TableStyle(sty))
    els += [tb, Spacer(1, 8), Paragraph("Fechas del ciclo de vida referenciales (fuente de cada fabricante en FaceOps › Ciclo de vida). "
                                        "Verificar antes de comprometer fechas.", st["Italic"])]
    doc.build(els)
