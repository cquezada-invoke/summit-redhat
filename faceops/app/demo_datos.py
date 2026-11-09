"""Datos de demostración en una instancia de cliente (p. ej. Inversiones Apolo): solo registros superficiales en la base de FaceOps,
sin tocar AAP ni máquinas. Todo registro demo queda marcado (nombre «demo-…», «DEMO · …», «[demo] …», ticket «DEMO-…») y anotado en
demo_registros para quitarlo después con un clic. La consola los resalta en ámbar (base.html)."""
import json
import random
from datetime import datetime, timedelta, timezone

from flask import flash, g, redirect, request, url_for

from .admin import bp
from . import requiere_admin
from .bd import ahora, auditar, ej, q, q1

DC = "DEMO · DC Tegucigalpa"
DC2 = "DEMO · DC San Pedro Sula"
NUBE = "DEMO · Google Cloud"
EQUIPOS = [  # nombre, so, familia, ambiente, categoría, cpu, ram GB, segmento, skills, centro de datos, forma
    ("demo-erp-app-01", "Red Hat Enterprise Linux 9.4", "RedHat", "produccion", "servidor", 8, 32, "ERP", ["jboss"], DC, "virtual"),
    ("demo-erp-app-02", "Red Hat Enterprise Linux 9.4", "RedHat", "produccion", "servidor", 8, 32, "ERP", ["jboss"], DC, "virtual"),
    ("demo-erp-db-01", "Red Hat Enterprise Linux 9.4", "RedHat", "produccion", "servidor", 16, 64, "ERP", ["oracle"], DC, "física"),
    ("demo-erp-qa-01", "Red Hat Enterprise Linux 9.4", "RedHat", "calidad", "servidor", 4, 16, "ERP", ["jboss", "postgres"], DC, "virtual"),
    ("demo-web-01", "Red Hat Enterprise Linux 8.10", "RedHat", "produccion", "servidor", 4, 8, "Portal clientes", ["nginx"], DC, "virtual"),
    ("demo-api-01", "Red Hat Enterprise Linux 9.4", "RedHat", "produccion", "servidor", 4, 16, "Portal clientes", ["tomcat", "redis"], DC, "virtual"),
    ("demo-api-db-01", "Red Hat Enterprise Linux 9.4", "RedHat", "produccion", "servidor", 8, 32, "Portal clientes", ["postgres"], DC, "virtual"),
    ("demo-cola-01", "Red Hat Enterprise Linux 9.4", "RedHat", "produccion", "servidor", 4, 16, "Portal clientes", ["kafka"], DC2, "virtual"),
    ("demo-dev-01", "Red Hat Enterprise Linux 9.4", "RedHat", "desarrollo", "servidor", 2, 8, "Portal clientes", ["postgres", "tomcat"], NUBE, "virtual"),
    ("demo-bi-01", "Windows Server 2022", "Windows", "produccion", "servidor", 8, 32, "Finanzas", ["mssql"], DC, "virtual"),
    ("demo-ad-01", "Windows Server 2022", "Windows", "produccion", "servidor", 4, 16, "Identidades", [], DC, "virtual"),
    ("demo-file-01", "Windows Server 2019", "Windows", "produccion", "servidor", 4, 8, "Identidades", [], DC2, "virtual"),
    ("demo-legacy-01", "CentOS 7.9", "RedHat", "produccion", "servidor", 2, 8, "Finanzas", ["mysql"], DC2, "virtual"),
    ("demo-kvm-01", "Red Hat Enterprise Linux 9.4", "RedHat", "produccion", "servidor", 32, 256, "", ["kvm"], DC2, "física"),
    ("demo-esxi-01", "VMware ESXi 8.0", "", "produccion", "servidor", 48, 512, "", [], DC, "física"),
    ("demo-ocp-prod", "Red Hat OpenShift 4.17", "", "produccion", "clúster openshift", 48, 192, "Portal clientes", [], DC, "virtual"),
    ("demo-ocp-qa", "Red Hat OpenShift 4.17", "", "calidad", "clúster openshift", 24, 96, "Portal clientes", [], NUBE, "virtual"),
    ("demo-fw-01", "FortiOS 7.4", "", "produccion", "firewall", 2, 4, "", [], DC, "física"),
    ("demo-fw-02", "FortiOS 7.4", "", "produccion", "firewall", 2, 4, "", [], DC2, "física"),
    ("demo-sw-core-01", "Cisco IOS XE 17", "", "produccion", "switch", 2, 4, "", [], DC, "física"),
    ("demo-rt-wan-01", "Cisco IOS XE 17", "", "produccion", "router", 2, 4, "", [], DC, "física"),
    ("demo-lb-01", "F5 BIG-IP 17", "", "produccion", "balanceador", 4, 8, "Portal clientes", [], DC, "física"),
    ("demo-nas-01", "NetApp ONTAP 9.14", "", "produccion", "almacenamiento", 8, 64, "", [], DC, "física"),
    ("demo-san-01", "Dell PowerStore 3.6", "", "produccion", "almacenamiento", 8, 64, "", [], DC2, "física"),
    ("demo-pc-fin-01", "Windows 11 Enterprise", "Windows", "produccion", "estación de trabajo", 4, 16, "Finanzas", [], DC, "física"),
    ("demo-pc-fin-02", "Windows 11 Enterprise", "Windows", "produccion", "estación de trabajo", 4, 16, "Finanzas", [], DC, "física"),
    ("demo-pc-dev-01", "Red Hat Enterprise Linux 9.4", "RedHat", "desarrollo", "estación de trabajo", 8, 32, "Portal clientes", [], DC2, "física"),
]
INFRAS = [  # nombre, tipo, centro, detalle
    ("DEMO · vCenter Tegucigalpa", "vmware", DC, {"host": "demo-vcenter.apolo.hn", "anfitrion": "10.99.0.24", "demo": True}),
    ("DEMO · KVM San Pedro Sula", "kvm", DC2, {"anfitrion": "10.99.0.23", "demo": True}),
    ("DEMO · Google Cloud", "gcp", NUBE, {"proyecto": "demo-apolo", "region": "southamerica-west1", "demo": True}),
]
SEGMENTOS = [("ERP", "aplicaciones", "Finanzas", "DEMO · ERP corporativo: aplicación JBoss, base de datos Oracle y ambiente de calidad"),
             ("Portal clientes", "aplicaciones", "Comercial", "DEMO · Portal web y API de autoatención de clientes sobre OpenShift"),
             ("Finanzas", "aplicaciones", "Finanzas", "DEMO · Reportería y BI de finanzas"),
             ("Identidades", "soluciones", "TI", "DEMO · Directorio y servidores de archivos")]
HALLAZGOS = [("critica", "Paquete OpenSSL con CVE crítico sin parche", "demo-web-01"),
             ("critica", "Sistema operativo sin soporte (CentOS 7)", "demo-legacy-01"),
             ("alta", "Firmware del firewall con vulnerabilidad publicada", "demo-fw-02"),
             ("alta", "Clúster OpenShift con actualización pendiente", "demo-ocp-qa"),
             ("media", "Storage al 82 % de capacidad", "demo-nas-01"),
             ("media", "Estación sin cifrado de disco", "demo-pc-fin-02"),
             ("alta", "Respaldo de la base de datos con más de 7 días", "demo-erp-db-01"),
             ("alta", "Política de contraseñas fuera de norma en el directorio", "demo-ad-01"),
             ("media", "Certificado TLS vence en 21 días", "demo-api-01"),
             ("media", "Disco /var sobre 85 %", "demo-erp-app-02"),
             ("baja", "Zona horaria distinta al estándar", "demo-dev-01")]


def _tabla():
    ej("CREATE TABLE IF NOT EXISTS demo_registros (id INTEGER PRIMARY KEY, cliente_id INTEGER, tabla TEXT, fila_id INTEGER, creado TEXT)")


def _anotar(cid, tabla, fid):
    ej("INSERT INTO demo_registros (cliente_id, tabla, fila_id, creado) VALUES (?,?,?,?)", cid, tabla, fid, ahora())


def hay(cid):
    _tabla()
    return (q1("SELECT COUNT(*) n FROM demo_registros WHERE cliente_id=?", cid) or {}).get("n", 0)


def cargar(cid, uid=None):
    """Carga (o recarga) el juego completo de datos demo: lo anterior marcado como demo se quita primero."""
    _tabla()
    quitar(cid)
    rnd = random.Random(cid)
    n = 0
    for nom, tipo, area, desc in SEGMENTOS:
        snom = "DEMO · " + nom
        if not q1("SELECT id FROM segmentos WHERE cliente_id=? AND nombre=?", cid, snom):
            _anotar(cid, "segmentos", ej("INSERT INTO segmentos (cliente_id, nombre, tipo, regla, descripcion, orden, sistema, ficha, grupo, area) "
                                         "VALUES (?,?,?,?,?,?,?,?,?,?)", cid, snom, "negocio",
                                         json.dumps({"nombre": [e[0] for e in EQUIPOS if e[7] == nom]}), desc, 90, "",
                                         json.dumps({"criticidad": "alta" if nom == "ERP" else "media", "dueno": "DEMO"}), tipo, area))
            n += 1
    ids = {}
    for nomi, tipo, centro, det in INFRAS:
        if not q1("SELECT id FROM infraestructuras WHERE cliente_id=? AND nombre=?", cid, nomi):
            _anotar(cid, "infraestructuras", ej("INSERT INTO infraestructuras (cliente_id, nombre, tipo, region, detalle, credencial, inventario, ubicacion, estado, "
                                                "creado, centro_datos, mensaje) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", cid, nomi, tipo, det.get("region", ""),
                                                json.dumps(det, ensure_ascii=False), "", "", centro, "ok", ahora(), centro, "DEMO · infraestructura ficticia"))
            n += 1
    for i, (nom, so, fam, amb, cat, cpu, ram, seg, sks, centro, forma) in enumerate(EQUIPOS):
        if q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=?", cid, nom):
            continue
        fid = ej("INSERT INTO equipos (cliente_id, nombre, so, so_familia, ip, cpu, ram_mb, datos, ubicacion, ambiente, categoria, forma, estado, energia, "
                 "descripcion, segmentos_fijos, skills_manual, ultima_recoleccion) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cid, nom, so, fam,
                 f"10.99.0.{10 + i}", cpu, ram * 1024, json.dumps({"demo": True, "rol": "Máquina ficticia (dato de demostración)"}), centro, amb, cat,
                 "virtual" if forma == "virtual" else "física", "con datos", "apagado" if nom == "demo-dev-01" else "encendido",
                 "DEMO · equipo ficticio, sin conexión a AAP", json.dumps(["DEMO · " + seg] if seg else []), json.dumps(sks), ahora())
        ids[nom] = fid
        _anotar(cid, "equipos", fid)
        n += 1
    for sev, tit, eq in HALLAZGOS:
        _anotar(cid, "hallazgos", ej("INSERT INTO hallazgos (cliente_id, equipo_id, tipo, severidad, titulo, detalle, creado) VALUES (?,?,?,?,?,?,?)",
                                     cid, ids.get(eq), "seguridad", sev, "[demo] " + tit, "Dato de demostración", ahora()))
        n += 1
    autos = q("SELECT id, nombre, minutos_manual FROM automatizaciones WHERE cliente_id=? AND COALESCE(visible,1)=1 ORDER BY id LIMIT 8", cid)
    from flask import has_app_context
    if not uid and has_app_context() and g.get("usuario"):
        uid = g.usuario["id"]
    uid = uid or (q1("SELECT id FROM usuarios WHERE es_admin=1 ORDER BY id LIMIT 1") or {}).get("id")
    hoy = datetime.now(timezone.utc)
    for k in range(40 if autos else 0):
        a = autos[k % len(autos)]
        t0 = hoy - timedelta(days=rnd.randint(0, 40), hours=rnd.randint(0, 20))
        est = "pendiente_aprobacion" if k < 3 else ("fallido" if k % 9 == 4 else "exitoso")
        dur = rnd.randint(40, 600)
        _anotar(cid, "tickets", ej(
            "INSERT INTO tickets (cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, motivo, resultado, creado, inicio, fin, "
            "duracion_s, valor_min, aprobado_por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cid, f"DEMO-{k + 1:04d}", a["id"], uid, est,
            "DEMO · " + SEGMENTOS[k % len(SEGMENTOS)][0], rnd.choice([e[0] for e in EQUIPOS[:13]]), "{}", "[demo] ticket de demostración",
            "" if est == "pendiente_aprobacion" else "[demo] resultado de demostración", t0.isoformat(timespec="seconds"),
            None if est == "pendiente_aprobacion" else t0.isoformat(timespec="seconds"),
            None if est == "pendiente_aprobacion" else (t0 + timedelta(seconds=dur)).isoformat(timespec="seconds"),
            None if est == "pendiente_aprobacion" else dur, a.get("minutos_manual") or 30, None if est == "pendiente_aprobacion" else "demo"))
        n += 1
    return n


def quitar(cid):
    _tabla()
    n = 0
    for r in q("SELECT tabla, fila_id FROM demo_registros WHERE cliente_id=?", cid):
        if r["tabla"] in ("segmentos", "equipos", "hallazgos", "tickets", "infraestructuras"):
            ej(f"DELETE FROM {r['tabla']} WHERE id=? AND cliente_id=?", r["fila_id"], cid)
            n += 1
    ej("DELETE FROM demo_registros WHERE cliente_id=?", cid)
    return n


@bp.route("/datos-demo", methods=["POST"])
@requiere_admin
def datos_demo():
    cid = (g.get("cliente") or {}).get("id") or (q1("SELECT id FROM clientes ORDER BY id LIMIT 1") or {}).get("id")
    if not cid:
        flash("No hay cliente en esta instancia", "error")
    elif request.form.get("accion") == "quitar":
        n = quitar(cid)
        auditar(g.usuario["usuario"], cid, "datos demo quitados", "demo", {"registros": n})
        flash(f"Datos demo quitados: {n} registros", "ok")
    else:
        n = cargar(cid)
        auditar(g.usuario["usuario"], cid, "datos demo cargados", "demo", {"registros": n})
        flash(f"Datos demo cargados: {n} registros, marcados en ámbar" if n else "Los datos demo ya estaban cargados", "ok")
    return redirect(url_for("admin.panel"))
