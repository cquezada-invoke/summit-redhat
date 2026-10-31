"""Evaluación del sistema operativo de cada máquina para una posible actualización: lo último que dejó la automatización
«Migración · Evaluar sistema operativo» (o una migración), separado por categoría, para tenerlo a la mano en el equipo."""
import json

from .bd import ahora, con, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS evaluaciones_so (
  cliente_id INTEGER NOT NULL, equipo TEXT NOT NULL, ticket_id INTEGER, datos TEXT, fecha TEXT, PRIMARY KEY (cliente_id, equipo));
"""
AUTOS = ("Migración · Evaluar sistema operativo", "Migración · RHEL a versión soportada", "Migración · CentOS a RHEL")
# categoría → (qué se revisa, datos recolectados que la describen)
CATEGORIAS = [
    ("Kernel y hardware", "Drivers y módulos, módulos de terceros, arquitectura y CPU (x86-64-v2 para RHEL 9, v3 para RHEL 10)",
     [("Kernel", "kernel"), ("Arquitectura", "arch"), ("Módulos de terceros", "modulos_terceros")]),
    ("Paquetes y repositorios", "Paquetes retirados o reemplazados, repositorios de terceros, RPM propios",
     [("Repositorios habilitados", "repos"), ("Repositorios de terceros", "repos_terceros")]),
    ("Lenguajes y librerías", "Python 2, versiones de Java, librerías base: lo que más rompe aplicaciones",
     [("Python 2", "python2"), ("Java", "java")]),
    ("Criptografía", "Políticas criptográficas del sistema (SHA-1, TLS 1.0/1.1), FIPS, endurecimiento de SSH",
     [("FIPS", "fips")]),
    ("Red", "network-scripts → NetworkManager, iptables → nftables, ifcfg → keyfile",
     [("Archivos ifcfg", "ifcfg"), ("network-scripts", "network_scripts"), ("iptables-services", "iptables")]),
    ("Almacenamiento", "Espacio libre para el upgrade, LUKS, montajes NFS",
     [("/var libre (MB)", "var_libre_mb"), ("/boot libre (MB)", "boot_libre_mb"), ("Discos LUKS", "luks"), ("Montajes NFS", "nfs")]),
    ("Servicios y arranque", "Units de systemd, scripts init, ntpd → chrony",
     [("ntpd", "ntpd")]),
    ("Identidad y acceso", "sssd, AD/IdM, PAM, sudoers, acceso de root por SSH",
     [("PermitRootLogin implícito", "root_ssh_implicito")]),
    ("Herramientas y agentes", "Monitoreo, respaldo, antivirus/EDR: soporte del fabricante en la versión de destino",
     [("Agentes detectados", "agentes")]),
    ("Aplicaciones", "Certificación de la aplicación en la versión de destino (JBoss, bases de datos, Oracle…)",
     [("Aplicaciones detectadas", "apps")]),
    ("Suscripción", "Registro (CDN o Satellite), ELS y versión menor de destino",
     [("Registro", "registrado"), ("Add-on ELS", "els")]),
    ("Operación", "Snapshot previo, vuelta atrás, ventana de mantención y pruebas posteriores", []),
]


def iniciar():
    con().executescript(ESQUEMA)


def guardar(t, artefactos):
    for equipo, d in ((artefactos or {}).get("faceops_migracion") or {}).items():
        ej("INSERT INTO evaluaciones_so(cliente_id, equipo, ticket_id, datos, fecha) VALUES (?,?,?,?,?) ON CONFLICT(cliente_id, equipo) "
           "DO UPDATE SET ticket_id=excluded.ticket_id, datos=excluded.datos, fecha=excluded.fecha",
           t["cliente_id"], equipo, t["id"], json.dumps(d, ensure_ascii=False), ahora())


def de_equipo(cliente_id, equipo):
    r = q1("SELECT * FROM evaluaciones_so WHERE cliente_id=? AND equipo=?", cliente_id, equipo)
    if not r:
        return None
    d = j(r["datos"], {})
    datos = d.get("datos") or {}
    hall = d.get("hallazgos") or []
    cats = []
    for nombre, que, campos in CATEGORIAS:
        hs = [h for h in hall if h.get("categoria") == nombre or (nombre == "Kernel y hardware" and h.get("categoria") == "Sistema")]
        peor = "rojo" if any(h["nivel"] == "rojo" for h in hs) else ("amarillo" if any(h["nivel"] == "amarillo" for h in hs) else
                                                                    ("info" if hs else "verde"))
        cats.append({"nombre": nombre, "que": que, "estado": peor, "hallazgos": hs,
                     "datos": [(et, str(datos.get(k)).strip() or "—") for et, k in campos if datos.get(k) not in (None,)]})
    return {"fecha": r["fecha"], "ticket_id": r["ticket_id"], "plan": d, "categorias": cats}
