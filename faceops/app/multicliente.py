"""FaceOps centralizado (multicliente) · DEMO: una sola instancia administra la infraestructura de varios clientes.
Solo demostración: los clientes y sus máquinas son ficticios, se marcan «demo» y no se conectan a AAP ni a nada. Cada cliente tiene sus
propios datos (equipos, hallazgos, tickets, gobierno) aislados por cliente_id; quien opera ve el consolidado y entra a cada uno.
El modo se fija por instancia (ajuste «modo_administracion»): «cliente» (un solo cliente) o «centralizado»."""
import json

from flask import abort, flash, g, redirect, render_template, request, url_for

from . import requiere_login
from .bd import ahora, ajuste, ej, q, q1
from .vistas import bp

MODOS = {"cliente": "Un cliente", "centralizado": "Centralizado (multicliente)"}

# clientes de demostración del FaceOps central de Invoke Lab: (código, nombre, país, industria, equipos [(nombre, so, ambiente, categoría)], hallazgos)
DEMO = [
    ("demo-minera", "Minera Los Andes", "Chile", "Minería",
     [("mla-erp-01", "Red Hat Enterprise Linux 9", "produccion", "servidor"), ("mla-erp-02", "Red Hat Enterprise Linux 9", "produccion", "servidor"),
      ("mla-scada-01", "Windows Server 2019", "produccion", "servidor"), ("mla-db-01", "Red Hat Enterprise Linux 8", "produccion", "servidor"),
      ("mla-ocp-01", "Red Hat OpenShift 4.16", "produccion", "plataforma"), ("mla-dev-01", "Red Hat Enterprise Linux 9", "desarrollo", "servidor"),
      ("mla-fw-01", "FortiOS 7.4", "produccion", "red"), ("mla-sw-01", "Cisco IOS XE 17", "produccion", "red")],
     [("critica", "CVE crítico sin parche en el servidor SCADA", "mla-scada-01"), ("alta", "Respaldo de la base de datos con más de 7 días", "mla-db-01"),
      ("media", "Certificado TLS vence en 20 días", "mla-erp-01")]),
    ("demo-banco", "Banco del Pacífico Sur", "Perú", "Banca",
     [("bps-core-01", "Red Hat Enterprise Linux 9", "produccion", "servidor"), ("bps-core-02", "Red Hat Enterprise Linux 9", "produccion", "servidor"),
      ("bps-db-01", "Red Hat Enterprise Linux 9", "produccion", "servidor"), ("bps-db-02", "Red Hat Enterprise Linux 9", "produccion", "servidor"),
      ("bps-ocp-01", "Red Hat OpenShift 4.17", "produccion", "plataforma"), ("bps-ocp-qa", "Red Hat OpenShift 4.17", "calidad", "plataforma"),
      ("bps-ad-01", "Windows Server 2022", "produccion", "servidor"), ("bps-fw-01", "PAN-OS 11.1", "produccion", "red"),
      ("bps-lb-01", "F5 BIG-IP 17", "produccion", "red"), ("bps-qa-01", "Red Hat Enterprise Linux 9", "calidad", "servidor")],
     [("media", "Política de contraseñas fuera de norma en el directorio", "bps-ad-01")]),
    ("demo-clinica", "Clínica Santa Lucía", "Honduras", "Salud",
     [("csl-his-01", "Red Hat Enterprise Linux 8", "produccion", "servidor"), ("csl-his-02", "CentOS 7", "produccion", "servidor"),
      ("csl-pacs-01", "Windows Server 2016", "produccion", "servidor"), ("csl-db-01", "Red Hat Enterprise Linux 8", "produccion", "servidor"),
      ("csl-fw-01", "FortiOS 7.2", "produccion", "red")],
     [("critica", "Sistema operativo sin soporte (CentOS 7)", "csl-his-02"), ("critica", "Servidor PACS sin parches hace 120 días", "csl-pacs-01"),
      ("alta", "Sin monitoreo en la base de datos clínica", "csl-db-01"), ("alta", "Credenciales locales fuera de la bóveda", "csl-his-01")]),
]


def modo():
    return ajuste("modo_administracion") or "cliente"


def sembrar_demo():
    """Solo en el FaceOps de Invoke Lab (no en la instancia de un cliente): tres clientes ficticios administrados desde aquí."""
    from .semillas import gobierno_base, limpia
    try:
        ej("ALTER TABLE clientes ADD COLUMN pais TEXT DEFAULT ''")
    except Exception:  # noqa: BLE001  (ya existe)
        pass
    ej("UPDATE clientes SET pais='Chile' WHERE COALESCE(pais,'')='' AND codigo NOT LIKE 'demo-%'")
    if limpia() or ajuste("demo_multicliente_v1"):
        return
    admin = q1("SELECT id FROM usuarios WHERE es_admin=1 ORDER BY id LIMIT 1")
    for cod, nom, pais, ind, eqs, hs in DEMO:
        c = q1("SELECT id FROM clientes WHERE codigo=?", cod)
        cid = c["id"] if c else ej("INSERT INTO clientes(codigo, nombre, descripcion, moneda, costo_hora, creado, aap_url, aap_token, pais) VALUES (?,?,?,?,?,?,?,?,?)",
                                   cod, f"{nom} (demo)", f"DEMO · cliente ficticio ({ind}, {pais}) administrado desde el FaceOps central de Invoke Lab. "
                                   "Sin conexión a AAP: sus datos son de demostración.", "USD", 40, ahora(), "", "", pais)
        if not c:
            gobierno_base(cid)
        if admin:
            ej("INSERT OR IGNORE INTO perfiles(usuario_id, cliente_id, segmentos, ambientes, modo, aprobador) VALUES (?,?,?,?,?,?)",
               admin["id"], cid, '["*"]', '["*"]', "escritura", 1)
        ids = {}
        for i, (n, so, amb, cat) in enumerate(eqs):
            fam = "RedHat" if "Red Hat" in so or "CentOS" in so else ("Windows" if "Windows" in so else "")
            n = "demo-" + n   # toda máquina de la demo lo dice en su nombre
            ej("INSERT OR IGNORE INTO equipos (cliente_id, nombre, so, so_familia, ip, cpu, ram_mb, datos, ubicacion, ambiente, categoria, forma, estado, energia) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cid, n, so, fam, f"10.{20 + DEMO.index(next(d for d in DEMO if d[0] == cod))}.0.{10 + i}", 4, 16384,
               json.dumps({"demo": True, "rol": "Máquina ficticia de la demo multicliente"}), f"DEMO · {pais}", amb, cat, "virtual", "con datos",
               "apagado" if i == len(eqs) - 1 else "encendido")
            ids[n] = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=?", cid, n)["id"]
        if not q1("SELECT id FROM hallazgos WHERE cliente_id=?", cid):
            for sev, tit, eq in hs:
                ej("INSERT INTO hallazgos (cliente_id, equipo_id, tipo, severidad, titulo, detalle, creado) VALUES (?,?,?,?,?,?,?)",
                   cid, ids.get("demo-" + eq), "seguridad", sev, f"[demo] {tit}", "Dato de demostración", ahora())
    ajuste("modo_administracion", "centralizado")
    ajuste("demo_multicliente_v1", ahora())


def es_demo(c):
    return bool(c) and (c.get("codigo") or "").startswith("demo-")


def _pais(c):
    return c.get("pais") or "—"


def resumen(c):
    eqs = q("SELECT id, ambiente, categoria, estado, energia FROM equipos WHERE cliente_id=?", c["id"])
    hs = q("SELECT severidad FROM hallazgos WHERE cliente_id=?", c["id"])
    sev = {k: sum(1 for h in hs if (h["severidad"] or "").lower() == k) for k in ("critica", "alta", "media", "baja")}
    n = len(eqs)
    pen = sev["critica"] * 3 + sev["alta"] * 2 + sev["media"]
    salud = round(100 * (1 - min(1, pen / (n * 1.5)))) if n else 0
    tk = q1("SELECT COUNT(*) n, SUM(estado='pendiente_aprobacion') p FROM tickets WHERE cliente_id=? AND creado >= date('now','-30 day')", c["id"]) or {}
    return {"c": c, "pais": _pais(c), "equipos": n, "on": sum(1 for e in eqs if e.get("energia") == "encendido"), "prod": sum(1 for e in eqs if e["ambiente"] == "produccion"), "sev": sev, "salud": salud,
            "tickets": tk.get("n") or 0, "pend": tk.get("p") or 0, "demo": es_demo(c)}


@bp.route("/clientes", methods=["GET", "POST"])
@requiere_login
def multicliente():
    if request.method == "POST":
        if not g.usuario["es_admin"] or request.form.get("modo") not in MODOS:
            abort(403)
        ajuste("modo_administracion", request.form["modo"])
        flash(f"Modo de administración: {MODOS[request.form['modo']]}", "ok")
        return redirect(url_for("consola.multicliente"))
    filas = sorted((resumen(c) for c in g.clientes), key=lambda r: r["salud"])
    tot = {"clientes": len(filas), "equipos": sum(r["equipos"] for r in filas), "criticos": sum(r["sev"]["critica"] for r in filas),
           "pend": sum(r["pend"] for r in filas),
           "salud": round(sum(r["salud"] * r["equipos"] for r in filas) / max(1, sum(r["equipos"] for r in filas)))}
    return render_template("multicliente.html", filas=filas, tot=tot, modo=modo(), modos=MODOS)
