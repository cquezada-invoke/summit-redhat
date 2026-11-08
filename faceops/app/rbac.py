"""Accesos al estilo OpenShift. Cada funcionalidad del menú es un espacio (como un namespace): Infraestructura, Servidores,
Redes, Storage… Los usuarios están en uno o varios grupos y los grupos (o un usuario directo) se enlazan a roles:

· Roles de clúster (transversales, todo FaceOps): cluster-admin (todo, incluido Gobierno y Administración), cluster-viewer
  (ve todo, incluido Gobierno y Administración, sin cambiar nada) y roles de clúster propios (custom): un rol de espacio
  (admin, aprobador o auditor) aplicado a todos los espacios o a varios.
· Roles de espacio: admin (ve y ejecuta las acciones de esa funcionalidad), aprobador (ve y aprueba sus tickets) y
  auditor (solo ve). Un usuario de Infraestructura ve solo Infraestructura; si además es de Servidores, también Servidores.
· Gobierno y la Administración (arriba) solo los ven cluster-admin y cluster-viewer."""
import json

from .areas import SECCIONES
from .bd import ahora, con, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS rbac_grupos (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, descripcion TEXT DEFAULT '',
  correo TEXT DEFAULT '', origen TEXT DEFAULT 'local', creado TEXT, UNIQUE(cliente_id, nombre));
CREATE TABLE IF NOT EXISTS rbac_miembros (grupo_id INTEGER NOT NULL, usuario_id INTEGER NOT NULL, desde TEXT, UNIQUE(grupo_id, usuario_id));
CREATE TABLE IF NOT EXISTS rbac_roles (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, clave TEXT NOT NULL, nombre TEXT, rol TEXT NOT NULL,
  espacios TEXT DEFAULT '["*"]', descripcion TEXT DEFAULT '', UNIQUE(cliente_id, clave));
CREATE TABLE IF NOT EXISTS rbac_enlaces (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, sujeto TEXT NOT NULL, sujeto_id INTEGER NOT NULL,
  ambito TEXT NOT NULL, rol TEXT NOT NULL, creado TEXT, por TEXT, UNIQUE(cliente_id, sujeto, sujeto_id, ambito, rol));
"""
ESPACIOS = [(k, n) for k, n, _ in SECCIONES]
NOMBRE_ESPACIO = dict(ESPACIOS)
ROLES_ESPACIO = {"admin": "Admin", "aprobador": "Aprobador", "auditor": "Auditor"}
DESC_ROL = {"admin": "ve la funcionalidad y ejecuta sus acciones", "aprobador": "ve la funcionalidad y aprueba sus tickets",
            "auditor": "solo ve la funcionalidad", "cluster-admin": "todo FaceOps, incluidos Gobierno y Administración",
            "cluster-viewer": "ve todo FaceOps, incluidos Gobierno y Administración, sin cambiar nada"}
CLUSTER = {"cluster-admin": "cluster-admin", "cluster-viewer": "cluster-viewer"}
# funcionalidad del menú (título) → espacio
MENU_ESPACIO = {"Infraestructura": "infraestructura", "Servidores": "servidores", "Estaciones de trabajo": "servidores", "Redes": "redes",
                "Storage": "storage", "Sistemas operativos": "so", "Bases de datos": "bd", "Plataformas": "plataformas",
                "Contenedores": "contenedores", "Hipervisores": "hipervisores", "Nubes": "nube", "Aplicaciones": "aplicaciones"}
MENU_GOBIERNO = {"Gobierno de TI", "Seguridad", "Disponibilidad", "Arquitectura", "Integraciones", "Monitoreo", "DevSecOps",
                 "Respaldos y recuperación", "Buenas prácticas", "Tickets"}
_listo = {"v": False}


def iniciar():
    if _listo["v"]:
        return
    con().executescript(ESQUEMA)
    _listo["v"] = True


def roles_cluster(cliente_id):
    """Roles de clúster: los dos de base y los propios del cliente."""
    iniciar()
    out = [{"clave": "cluster-admin", "nombre": "cluster-admin", "rol": "admin", "espacios": ["*"], "base": True, "descripcion": DESC_ROL["cluster-admin"]},
           {"clave": "cluster-viewer", "nombre": "cluster-viewer", "rol": "auditor", "espacios": ["*"], "base": True, "descripcion": DESC_ROL["cluster-viewer"]}]
    for r in q("SELECT * FROM rbac_roles WHERE cliente_id=? ORDER BY clave", cliente_id):
        out.append(dict(r, espacios=j(r["espacios"], ["*"]), base=False))
    return out


def activo(cliente_id):
    """¿El cliente ya usa grupos y roles? (si no, rige el perfil clásico)."""
    iniciar()
    return bool(q1("SELECT 1 AS x FROM rbac_enlaces WHERE cliente_id=? LIMIT 1", cliente_id))


def grupos_de(usuario_id, cliente_id):
    iniciar()
    return q("SELECT g.* FROM rbac_grupos g JOIN rbac_miembros m ON m.grupo_id=g.id WHERE m.usuario_id=? AND g.cliente_id=? ORDER BY g.nombre",
             usuario_id, cliente_id)


def acceso(usuario, cliente_id):
    """{'cluster': {roles de clúster}, 'ns': {espacio: {roles}}, 'gobierno': bool, 'enlaces': [...]} efectivos del usuario."""
    iniciar()
    gids = [x["id"] for x in grupos_de(usuario["id"], cliente_id)]
    filas = q("SELECT * FROM rbac_enlaces WHERE cliente_id=? AND ((sujeto='usuario' AND sujeto_id=?)" +
              (f" OR (sujeto='grupo' AND sujeto_id IN ({','.join('?' * len(gids))}))" if gids else "") + ")", cliente_id, usuario["id"], *gids)
    rc = {r["clave"]: r for r in roles_cluster(cliente_id)}
    out = {"cluster": set(), "ns": {}, "enlaces": filas}
    if usuario.get("es_admin"):
        out["cluster"].add("cluster-admin")
    for f in filas:
        if f["ambito"] == "cluster":
            out["cluster"].add(f["rol"])
        else:
            out["ns"].setdefault(f["ambito"], set()).add(f["rol"])
    for cr in list(out["cluster"]):   # un rol de clúster se reparte en sus espacios
        r = rc.get(cr)
        if not r:
            continue
        for k in ([k for k, _ in ESPACIOS] if "*" in r["espacios"] else r["espacios"]):
            out["ns"].setdefault(k, set()).add(r["rol"])
    out["gobierno"] = bool(out["cluster"] & {"cluster-admin", "cluster-viewer"})
    out["admin_total"] = "cluster-admin" in out["cluster"]
    return out


def ve(acc, espacio):
    return acc["admin_total"] or bool(acc["ns"].get(espacio))


def administra(acc, espacio):
    return acc["admin_total"] or "admin" in acc["ns"].get(espacio, set())


def aprueba(acc, espacio):
    return acc["admin_total"] or "aprobador" in acc["ns"].get(espacio, set())


def sincronizar_perfil(usuario_id, cliente_id):
    """El perfil clásico (modo y aprobador) queda alineado con los roles: escritura si administra algo; aprobador si aprueba algo."""
    u = q1("SELECT * FROM usuarios WHERE id=?", usuario_id)
    if not u:
        return
    a = acceso(u, cliente_id)
    roles = set().union(*a["ns"].values()) if a["ns"] else set()
    modo = "escritura" if ("admin" in roles or a["admin_total"]) else "lectura"
    p = q1("SELECT * FROM perfiles WHERE usuario_id=? AND cliente_id=?", usuario_id, cliente_id)
    if p and p["modo"] == "ejecutivo":
        return
    if p:
        ej("UPDATE perfiles SET modo=?, aprobador=? WHERE usuario_id=? AND cliente_id=?", modo, int("aprobador" in roles or a["admin_total"]), usuario_id, cliente_id)
    elif a["ns"] or a["cluster"]:
        ej("INSERT INTO perfiles (usuario_id, cliente_id, segmentos, modo, aprobador) VALUES (?,?,?,?,?)", usuario_id, cliente_id, json.dumps(["*"]),
           modo, int("aprobador" in roles or a["admin_total"]))


# ------------------------------------------------------------------ administración
def crear_grupo(cliente_id, nombre, descripcion="", correo="", origen="local"):
    iniciar()
    nombre = (nombre or "").strip().lower().replace(" ", "-")[:60]
    if not nombre:
        raise ValueError("Falta el nombre del grupo")
    ej("INSERT OR IGNORE INTO rbac_grupos (cliente_id, nombre, descripcion, correo, origen, creado) VALUES (?,?,?,?,?,?)",
       cliente_id, nombre, descripcion, correo, origen, ahora())
    return q1("SELECT * FROM rbac_grupos WHERE cliente_id=? AND nombre=?", cliente_id, nombre)


def poner(grupo_id, usuario_id):
    ej("INSERT OR IGNORE INTO rbac_miembros (grupo_id, usuario_id, desde) VALUES (?,?,?)", grupo_id, usuario_id, ahora())


def quitar(grupo_id, usuario_id):
    ej("DELETE FROM rbac_miembros WHERE grupo_id=? AND usuario_id=?", grupo_id, usuario_id)


def enlazar(cliente_id, sujeto, sujeto_id, ambito, rol, por="sistema"):
    iniciar()
    claves_cl = {r["clave"] for r in roles_cluster(cliente_id)}
    if ambito == "cluster" and rol not in claves_cl:
        raise ValueError("Rol de clúster desconocido")
    if ambito != "cluster" and (ambito not in NOMBRE_ESPACIO or rol not in ROLES_ESPACIO):
        raise ValueError("Espacio o rol desconocido")
    ej("INSERT OR IGNORE INTO rbac_enlaces (cliente_id, sujeto, sujeto_id, ambito, rol, creado, por) VALUES (?,?,?,?,?,?,?)",
       cliente_id, sujeto, sujeto_id, ambito, rol, ahora(), por)


def desenlazar(cliente_id, eid):
    ej("DELETE FROM rbac_enlaces WHERE cliente_id=? AND id=?", cliente_id, eid)


def crear_rol_cluster(cliente_id, clave, rol, espacios, descripcion=""):
    iniciar()
    clave = "cluster-" + (clave or "").strip().lower().replace(" ", "-").removeprefix("cluster-")[:40]
    if clave in CLUSTER or rol not in ROLES_ESPACIO or clave == "cluster-":
        raise ValueError("Nombre o rol no válido")
    esp = ["*"] if not espacios or "*" in espacios else [e for e in espacios if e in NOMBRE_ESPACIO]
    ej("INSERT OR REPLACE INTO rbac_roles (cliente_id, clave, nombre, rol, espacios, descripcion) VALUES (?,?,?,?,?,?)",
       cliente_id, clave, clave, rol, json.dumps(esp), descripcion[:200])
    return clave


def enlaces_txt(e, cliente_id):
    if e["ambito"] == "cluster":
        return e["rol"]
    return f"{ROLES_ESPACIO.get(e['rol'], e['rol'])} · {NOMBRE_ESPACIO.get(e['ambito'], e['ambito'])}"


# ------------------------------------------------------------------ paso inicial: del modelo de grupos dueños a grupos y roles
PRUEBA = {   # usuarios y grupos ficticios para probar (origen: LDAP del laboratorio)
    "usuarios": [("ana.rojas", "Ana Rojas"), ("bruno.diaz", "Bruno Díaz"), ("carla.mena", "Carla Mena"), ("diego.soto", "Diego Soto"),
                 ("elena.vera", "Elena Vera"), ("felipe.lagos", "Felipe Lagos"), ("gabriela.pino", "Gabriela Pino"), ("hector.mora", "Héctor Mora"),
                 ("ines.campos", "Inés Campos"), ("jorge.vidal", "Jorge Vidal"), ("karen.fuentes", "Karen Fuentes"), ("luis.parra", "Luis Parra")],
    "roles": [("auditor", "auditor", ["*"], "Auditoría: ve todas las funcionalidades (sin Gobierno ni Administración)"),
              ("aprobador", "aprobador", ["*"], "Comité de cambios: aprueba en todas las funcionalidades")],
    "grupos": [("plataforma-admins", "Administradores de la plataforma FaceOps", [("cluster", "cluster-admin")], ["ana.rojas"]),
               ("gobierno-ti", "Gobierno de TI: ve todo, incluido Gobierno", [("cluster", "cluster-viewer")], ["bruno.diaz"]),
               ("auditoria-interna", "Auditoría interna", [("cluster", "cluster-auditor")], ["carla.mena"]),
               ("comite-cambios", "Comité de cambios (CAB)", [("cluster", "cluster-aprobador")], ["diego.soto"]),
               ("infraestructura-admins", "Equipo de infraestructura", [("infraestructura", "admin"), ("hipervisores", "admin"), ("storage", "admin")],
                ["elena.vera", "felipe.lagos"]),
               ("servidores-admins", "Equipo de servidores", [("servidores", "admin"), ("so", "admin")], ["felipe.lagos", "gabriela.pino"]),
               ("servidores-aprobadores", "Jefatura de servidores", [("servidores", "aprobador"), ("so", "aprobador")], ["hector.mora"]),
               ("redes-admins", "Equipo de redes", [("redes", "admin")], ["ines.campos"]),
               ("seguridad-aprobadores", "Seguridad de la información", [("seguridad", "aprobador"), ("servidores", "auditor"), ("redes", "auditor")], ["jorge.vidal"]),
               ("datos-admins", "Bases de datos y plataformas", [("bd", "admin"), ("plataformas", "auditor")], ["karen.fuentes"]),
               ("aplicaciones-auditores", "Auditoría de aplicaciones", [("aplicaciones", "auditor")], ["luis.parra"])]}


def migrar(cliente_id):
    """Una vez por cliente: grupos dueños y perfiles → grupos y roles; más los usuarios y grupos de prueba."""
    from .bd import ajuste
    iniciar()
    if ajuste(f"rbac_v1_{cliente_id}"):
        return False
    from .grupos_dueno import iniciar as gd_iniciar
    gd_iniciar()
    adm = crear_grupo(cliente_id, "cluster-admins", "Administradores de Invoke")
    enlazar(cliente_id, "grupo", adm["id"], "cluster", "cluster-admin")
    con_grupo = set()
    for r in q("SELECT m.seccion, m.rol, m.usuario_id, p.modo FROM grupo_miembros m LEFT JOIN perfiles p ON p.usuario_id=m.usuario_id AND p.cliente_id=m.cliente_id "
               "WHERE m.cliente_id=?", cliente_id):
        if r["seccion"] not in NOMBRE_ESPACIO:
            continue
        if r["rol"] == "dueño":
            gr = crear_grupo(cliente_id, f"{r['seccion']}-aprobadores", f"Dueños de {NOMBRE_ESPACIO[r['seccion']]}")
            enlazar(cliente_id, "grupo", gr["id"], r["seccion"], "admin")
            enlazar(cliente_id, "grupo", gr["id"], r["seccion"], "aprobador")
        else:
            rol = "admin" if r["modo"] == "escritura" else "auditor"
            gr = crear_grupo(cliente_id, f"{r['seccion']}-{'admins' if rol == 'admin' else 'auditores'}", f"{NOMBRE_ESPACIO[r['seccion']]}")
            enlazar(cliente_id, "grupo", gr["id"], r["seccion"], rol)
        poner(gr["id"], r["usuario_id"])
        con_grupo.add(r["usuario_id"])
    vis = crear_grupo(cliente_id, "direccion", "Dirección: ve todo, incluido Gobierno")
    enlazar(cliente_id, "grupo", vis["id"], "cluster", "cluster-viewer")
    for u_ in q("SELECT id FROM usuarios WHERE es_admin=1 AND activo=1"):
        poner(adm["id"], u_["id"])
    for p in q("SELECT p.*, u.es_admin FROM perfiles p JOIN usuarios u ON u.id=p.usuario_id WHERE p.cliente_id=?", cliente_id):
        if p["es_admin"]:
            continue
        elif p["usuario_id"] not in con_grupo and p["modo"] != "ejecutivo":
            poner(vis["id"], p["usuario_id"])
    from .semillas import limpia
    if limpia():   # instancia de un cliente: sin usuarios ni grupos de prueba
        ajuste(f"rbac_v1_{cliente_id}", ahora())
        return True
    for clave, rol, esp, desc in PRUEBA["roles"]:
        crear_rol_cluster(cliente_id, clave, rol, esp, desc)
    ids = {}
    for usr, nom in PRUEBA["usuarios"]:
        u = q1("SELECT id FROM usuarios WHERE usuario=?", usr)
        if not u:
            ej("INSERT INTO usuarios (usuario, nombre, correo, clave_hash, es_admin, activo, origen) VALUES (?,?,?,?,?,?,?)",
               usr, nom, f"{usr}@invoke.lab", "!", 0, 1, "ldap:laboratorio")
            u = q1("SELECT id FROM usuarios WHERE usuario=?", usr)
        ids[usr] = u["id"]
    for nombre, desc, enl, miembros in PRUEBA["grupos"]:
        gr = crear_grupo(cliente_id, nombre, desc, f"{nombre}@invoke.lab", "ldap:laboratorio")
        for amb, rol in enl:
            enlazar(cliente_id, "grupo", gr["id"], amb, rol)
        for m in miembros:
            poner(gr["id"], ids[m])
    for u in q("SELECT DISTINCT m.usuario_id FROM rbac_miembros m JOIN rbac_grupos g ON g.id=m.grupo_id WHERE g.cliente_id=?", cliente_id):
        sincronizar_perfil(u["usuario_id"], cliente_id)
    ajuste(f"rbac_v1_{cliente_id}", ahora())
    return True
