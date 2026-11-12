"""FaceOps · Edge: sitios remotos del cliente (tiendas, sucursales, bodegas, centros de distribución), bajo Servidores físicos.

Modelo hub & spoke con Automation Mesh de AAP: cada sitio tiene un execution node local; si no tiene salida directa pasa por un
hop node (normalmente en la DMZ de la central). El sitio inicia la conexión hacia el hop o la central (no se abren puertos en el
sitio) y las acciones bajan por ese túnel. FaceOps nunca toca las máquinas: toda acción va por AAP con ticket y aprobación.

Código técnico del sitio: PAÍS-CIUDAD-TIPO-NNN (p. ej. CL-SCL-TDA-014), inmutable; el nombre comercial es un alias. En el
inventario de Ansible el grupo va con guion bajo (cl_scl_tda_014) y los equipos heredan el código (cl-scl-tda-014-pos01, -en01…).
"""
import ipaddress
import json
import re
import time

from flask import abort, flash, g, redirect, render_template, request, session, url_for

from . import requiere_login
from .bd import ahora, auditar, ej, j, q, q1
from .vistas import bp, equipos_visibles, hallazgos_visibles, EP_ALIAS

TIPOS = {"TDA": "Tienda", "SUC": "Sucursal", "BOD": "Bodega", "CD": "Centro de distribución", "OFI": "Oficina"}
CON_UBICACIONES = ("OFI",)   # tipos con ubicaciones internas (pisos, alas)
RE_CODIGO = re.compile(r"^([A-Z]{2})-([A-Z]{3})-(TDA|SUC|BOD|CD|OFI)-(\d{3})$")
RE_NODO = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
# Acciones del sitio en AAP (nombre en AAP). Mientras no estén publicadas y certificadas aparecen deshabilitadas con su motivo.
ENERGIA_EDGE = "Edge · Encender o apagar equipos del sitio"
ACCIONES = [  # área, nombre, texto, nombre en AAP
    ("Malla", "Registrar el execution node en la malla", "Instala el execution node del sitio y lo enlaza al hop o a la central (la conexión la inicia el sitio)", "Edge · Registrar execution node"),
    ("Malla", "Verificar la ruta del sitio", "Comprueba el túnel sitio → hop → central y la latencia, sin cambiar nada", "Edge · Verificar ruta de la malla"),
    ("Inventario", "Agrupar los equipos del sitio", "Crea o actualiza el grupo del sitio en el inventario de AAP con sus equipos", "Edge · Agrupar equipos del sitio"),
]
EP_ALIAS.update({"consola.edge_ficha": "consola.edge", "consola.edge_guardar": "consola.edge", "consola.edge_accion": "consola.edge",
                 "consola.edge_energia": "consola.edge", "consola.edge_grupo_guardar": "consola.edge", "consola.edge_grupo_eliminar": "consola.edge", "consola.edge_estacion_asignar": "consola.estaciones",
                 "consola.edge_disp_guardar": "consola.edge", "consola.edge_disp_eliminar": "consola.edge",
                 "consola.edge_diagrama_guardar": "consola.edge"})

# Otros dispositivos del sitio (impresoras, POS, AP…): solo inventario. Principio agentless: el alta registra el dispositivo y, si
# tiene IP, su estado se obtendrá sin agente (ping/SNMP, opción futura); la ficha no sondea nada en vivo. Iconos SVG propios (trazo).
TIPOS_DISP = [  # clave, nombre, trazos del icono (viewBox 24)
    ("impresora", "Impresora", '<path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M7 14h10v7H7z"/>'),
    ("pos", "Punto de venta (POS)", '<rect x="5" y="3" width="14" height="10" rx="1.5"/><path d="M3 13h18l-1 8H4z"/><path d="M8 17h8"/>'),
    ("ap", "Access point Wi-Fi", '<path d="M5 12a10 10 0 0 1 14 0"/><path d="M8 15a5.5 5.5 0 0 1 8 0"/><circle cx="12" cy="18" r="1.2"/>'),
    ("switch", "Switch", '<rect x="2" y="8" width="20" height="8" rx="1.5"/><path d="M6 12h1M9 12h1M12 12h1M15 12h1M18 12h1"/>'),
    ("router", "Router / firewall de sucursal", '<rect x="2" y="13" width="20" height="7" rx="1.5"/><path d="M6 13l-2-6M18 13l2-6"/><path d="M6 16.5h4"/>'),
    ("camara", "Cámara IP", '<rect x="3" y="7" width="13" height="8" rx="2"/><path d="M16 9.5l5-2.5v8l-5-2.5"/><path d="M8 15v4h-3"/>'),
    ("ups", "UPS", '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M13 6l-3 6h4l-3 6"/>'),
    ("kiosco", "Kiosco / terminal de autoservicio", '<rect x="6" y="2" width="12" height="13" rx="1.5"/><path d="M9 15v5h6v-5"/><path d="M5 21h14"/>'),
    ("lector", "Lector / escáner", '<path d="M3 7V4h3M21 7V4h-3M3 17v3h3M21 17v3h-3"/><path d="M7 8v8M10 8v8M13 8v8M17 8v8"/>'),
    ("otro", "Otro dispositivo", '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 12h6M12 9v6"/>'),
]
TIPOS_DISP_D = {k: n for k, n, _ in TIPOS_DISP}


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS edge_sitios (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, codigo TEXT NOT NULL, alias TEXT DEFAULT '', tipo TEXT NOT NULL,
      pais TEXT NOT NULL, ciudad TEXT DEFAULT '', nodo TEXT DEFAULT '', hop TEXT DEFAULT '', equipos TEXT DEFAULT '[]',
      demo INTEGER DEFAULT 0, creado TEXT, actualizado TEXT, autor TEXT DEFAULT '',
      UNIQUE(cliente_id, codigo));
    CREATE TABLE IF NOT EXISTS edge_grupos (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, descripcion TEXT DEFAULT '', demo INTEGER DEFAULT 0,
      creado TEXT, autor TEXT DEFAULT '', UNIQUE(cliente_id, nombre));
    CREATE TABLE IF NOT EXISTS edge_dispositivos (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, sitio_id INTEGER NOT NULL, tipo TEXT NOT NULL, nombre TEXT NOT NULL,
      ip TEXT DEFAULT '', fabricante TEXT DEFAULT '', modelo TEXT DEFAULT '', ubicacion TEXT DEFAULT '',
      estado TEXT DEFAULT '', estado_visto TEXT DEFAULT '',   -- lectura sin agente (ping/SNMP), cuando se habilite
      creado TEXT, actualizado TEXT, autor TEXT DEFAULT '', UNIQUE(cliente_id, sitio_id, nombre));
    CREATE TABLE IF NOT EXISTS edge_topologia_hist (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, sitio_id INTEGER NOT NULL, anterior TEXT DEFAULT '', nueva TEXT DEFAULT '',
      autor TEXT DEFAULT '', fecha TEXT);""")
    hay = {r["name"] for r in con().execute("PRAGMA table_info(edge_sitios)")}
    for col, tipo in (("grupo_id", "INTEGER"),                 # un grupo por sitio (para focalizar)
                      ("ubicaciones", "TEXT DEFAULT '[]'"),     # ubicaciones internas de una oficina: «Piso 1», «Ala norte»…
                      ("ubic_eq", "TEXT DEFAULT '{}'"),         # equipo_id → ubicación
                      ("topologia", "TEXT")):                    # diagrama editado: posiciones y conexiones (None = automático)
        if col not in hay:
            con().execute(f"ALTER TABLE edge_sitios ADD COLUMN {col} {tipo}")
            con().commit()


# ------------------------------------------------------------------ grupos de sitios (p. ej. «Zona Norte», «Mall»): para saber dónde focalizar
def grupos(cliente_id):
    _tabla()
    return q("SELECT * FROM edge_grupos WHERE cliente_id=? ORDER BY nombre", cliente_id)


def guardar_grupo(cliente_id, f, quien, gid=None, demo=False):
    _tabla()
    nombre = (f.get("nombre") or "").strip()[:60]
    desc = (f.get("descripcion") or "").strip()[:240]
    if not nombre:
        raise ValueError("Indica el nombre del grupo")
    otro = q1("SELECT id FROM edge_grupos WHERE cliente_id=? AND lower(nombre)=lower(?)", cliente_id, nombre)
    if otro and otro["id"] != gid:
        raise ValueError(f"Ya existe un grupo llamado «{nombre}»")
    if gid:
        if not q1("SELECT id FROM edge_grupos WHERE id=? AND cliente_id=?", gid, cliente_id):
            raise ValueError("Grupo no válido")
        ej("UPDATE edge_grupos SET nombre=?, descripcion=? WHERE id=? AND cliente_id=?", nombre, desc, gid, cliente_id)
        auditar(quien, cliente_id, "editar grupo edge", nombre)
        return gid
    gid = ej("INSERT INTO edge_grupos(cliente_id, nombre, descripcion, demo, creado, autor) VALUES (?,?,?,?,?,?)",
             cliente_id, nombre, desc, 1 if demo else 0, ahora(), quien)
    auditar(quien, cliente_id, "crear grupo edge", nombre, {"demo": demo})
    return gid


def eliminar_grupo(cliente_id, gid, quien):
    """Elimina el grupo; sus sitios quedan «Sin grupo» (no se borra ningún sitio)."""
    _tabla()
    gr = q1("SELECT * FROM edge_grupos WHERE id=? AND cliente_id=?", gid, cliente_id)
    if not gr:
        raise ValueError("Grupo no válido")
    ej("UPDATE edge_sitios SET grupo_id=NULL WHERE cliente_id=? AND grupo_id=?", cliente_id, gid)
    ej("DELETE FROM edge_grupos WHERE id=? AND cliente_id=?", gid, cliente_id)
    auditar(quien, cliente_id, "eliminar grupo edge", gr["nombre"])
    return gr


def grupo_ansible(codigo):
    return codigo.lower().replace("-", "_")


def nodo_sugerido(codigo):
    return codigo.lower() + "-en01"


def sitios(cliente_id):
    _tabla()
    return [_con_listas(s) for s in q("SELECT * FROM edge_sitios WHERE cliente_id=? ORDER BY codigo", cliente_id)]


def _con_listas(s):
    s["equipos_l"] = j(s.get("equipos"), []) or []
    s["ubicaciones_l"] = j(s.get("ubicaciones"), []) or []
    s["ubic_eq_d"] = {int(k): v for k, v in (j(s.get("ubic_eq"), {}) or {}).items()}
    return s


def validar(cliente_id, f, actual=None):
    """Datos del formulario → fila validada. El código es inmutable: solo se valida al crear."""
    codigo = actual["codigo"] if actual else (f.get("codigo") or "").strip()
    if not actual:
        if not codigo:
            raise ValueError("Indica el código técnico del sitio")
        if codigo != codigo.upper():
            raise ValueError("El código va en mayúsculas (p. ej. CL-SCL-TDA-014)")
        if not RE_CODIGO.match(codigo):
            raise ValueError("Formato de código no válido: PAÍS-CIUDAD-TIPO-NNN, p. ej. CL-SCL-TDA-014 (tipos: TDA, SUC, BOD, CD, OFI)")
        if q1("SELECT id FROM edge_sitios WHERE cliente_id=? AND codigo=?", cliente_id, codigo):
            raise ValueError(f"Ya existe un sitio con el código {codigo}")
    m = RE_CODIGO.match(codigo)
    tipo_f = (f.get("tipo") or "").strip().upper()
    if not actual:
        if not tipo_f:
            raise ValueError("Elige el tipo del sitio (tienda, sucursal, bodega o centro de distribución)")
        if tipo_f not in TIPOS:
            raise ValueError("Tipo no válido")
        if tipo_f != m.group(3):
            raise ValueError(f"El tipo elegido ({TIPOS[tipo_f]}, {tipo_f}) no coincide con el del código ({m.group(3)}): el código es PAÍS-CIUDAD-TIPO-NNN")
    elif tipo_f and tipo_f != actual["tipo"]:
        raise ValueError("El tipo es parte del código técnico, que no cambia: para otro tipo, da de alta un sitio nuevo")
    alias = (f.get("alias") or "").strip()[:80]
    ciudad = (f.get("ciudad") or "").strip()[:60] or m.group(2)
    nodo = (f.get("nodo") or "").strip().lower() or nodo_sugerido(codigo)
    hop = (f.get("hop") or "").strip().lower()
    for n_, v_ in (("execution node", nodo), ("hop node", hop)):
        if v_ and not RE_NODO.match(v_):
            raise ValueError(f"Nombre de {n_} no válido: solo minúsculas, números, puntos y guiones")
    vis = {e["id"]: e for e in equipos_visibles()}
    ids = []
    for x in f.getlist("equipos") if hasattr(f, "getlist") else (f.get("equipos") or []):
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i in vis and i not in ids:
            ids.append(i)
    otros = {i: s["codigo"] for s in sitios(cliente_id) if not actual or s["id"] != actual["id"] for i in s["equipos_l"]}
    choca = [f"{vis[i]['nombre']} ({otros[i]})" for i in ids if i in otros]
    if choca:
        raise ValueError("Estos equipos ya pertenecen a otro sitio: " + ", ".join(choca))
    grupo_id = None
    if (f.get("grupo_id") or "").strip():
        try:
            grupo_id = int(f.get("grupo_id"))
        except (TypeError, ValueError):
            grupo_id = -1
        if not q1("SELECT id FROM edge_grupos WHERE id=? AND cliente_id=?", grupo_id, cliente_id):
            raise ValueError("Grupo no válido")
    ubic, ubic_eq = [], {}
    if m.group(3) in CON_UBICACIONES:
        for u in (f.get("ubicaciones") or "").replace("\r", "").split("\n"):
            u = u.strip()[:40]
            if u and u.lower() not in {x.lower() for x in ubic}:
                ubic.append(u)
        if len(ubic) > 40:
            raise ValueError("Máximo 40 ubicaciones por sitio")
        for i in ids:
            u = (f.get(f"ubic_{i}") or "").strip()
            if u and u in ubic:
                ubic_eq[str(i)] = u
    return {"codigo": codigo, "alias": alias, "tipo": m.group(3), "pais": m.group(1), "ciudad": ciudad, "nodo": nodo, "hop": hop, "equipos": ids,
            "grupo_id": grupo_id, "ubicaciones": ubic, "ubic_eq": ubic_eq}


def guardar(cliente_id, d, quien, actual=None, demo=False):
    _tabla()
    if actual:
        ej("UPDATE edge_sitios SET alias=?, ciudad=?, nodo=?, hop=?, equipos=?, grupo_id=?, ubicaciones=?, ubic_eq=?, actualizado=?, autor=? WHERE id=? AND cliente_id=?",
           d["alias"], d["ciudad"], d["nodo"], d["hop"], json.dumps(d["equipos"]), d.get("grupo_id"), json.dumps(d.get("ubicaciones") or [], ensure_ascii=False),
           json.dumps(d.get("ubic_eq") or {}, ensure_ascii=False), ahora(), quien, actual["id"], cliente_id)
        auditar(quien, cliente_id, "editar sitio edge", d["codigo"], {k: d.get(k) for k in ("alias", "ciudad", "nodo", "hop", "equipos", "grupo_id")})
        return actual["id"]
    sid = ej("INSERT INTO edge_sitios(cliente_id, codigo, alias, tipo, pais, ciudad, nodo, hop, equipos, grupo_id, ubicaciones, ubic_eq, demo, creado, actualizado, autor) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cliente_id, d["codigo"], d["alias"], d["tipo"], d["pais"], d["ciudad"], d["nodo"], d["hop"],
             json.dumps(d["equipos"]), d.get("grupo_id"), json.dumps(d.get("ubicaciones") or [], ensure_ascii=False),
             json.dumps(d.get("ubic_eq") or {}, ensure_ascii=False), 1 if demo else 0, ahora(), ahora(), quien)
    auditar(quien, cliente_id, "crear sitio edge", d["codigo"], {"demo": demo})
    return sid


def sembrar_demo_lab(cliente_id, quien="faceops"):
    """UN sitio de laboratorio marcado «demo» que agrupa máquinas existentes del lab (no crea infraestructura). Idempotente."""
    _tabla()
    if q1("SELECT id FROM edge_sitios WHERE cliente_id=? AND demo=1", cliente_id):
        sembrar_grupos_demo(cliente_id, quien)
        sembrar_oficina_demo(cliente_id, quien)
        return None
    usados = {i for s in sitios(cliente_id) for i in s["equipos_l"]}
    cand = [e for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' ORDER BY nombre", cliente_id)
            if (e.get("categoria") or "servidor") == "servidor" and not e.get("infra_lab") and e["id"] not in usados]
    cand.sort(key=lambda e: (e.get("energia") != "encendido", e["nombre"]))
    d = {"codigo": "CL-SCL-TDA-001", "alias": "Tienda de laboratorio (demo)", "tipo": "TDA", "pais": "CL", "ciudad": "Santiago",
         "nodo": nodo_sugerido("CL-SCL-TDA-001"), "hop": "", "equipos": [e["id"] for e in cand[:3]]}
    if q1("SELECT id FROM edge_sitios WHERE cliente_id=? AND codigo=?", cliente_id, d["codigo"]):
        return None
    sid = guardar(cliente_id, d, quien, demo=True)
    sembrar_grupos_demo(cliente_id, quien)
    sembrar_oficina_demo(cliente_id, quien)
    return sid


def sembrar_oficina_demo(cliente_id, quien="faceops"):
    """UN sitio de oficina «demo» (CL-SCL-OFI-001) con Piso 1 y Piso 2 que agrupa las estaciones de trabajo que ya existen en el
    lab (no crea infraestructura). Idempotente: si ya existe, no lo toca."""
    _tabla()
    cod = "CL-SCL-OFI-001"
    if q1("SELECT id FROM edge_sitios WHERE cliente_id=? AND codigo=?", cliente_id, cod):
        return None
    usados = {i for s in sitios(cliente_id) for i in s["equipos_l"]}
    est = [e for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' ORDER BY nombre", cliente_id)
           if (e.get("categoria") or "") == "estación de trabajo" and e["id"] not in usados]
    pisos = ["Piso 1", "Piso 2"]
    g1 = q1("SELECT id FROM edge_grupos WHERE cliente_id=? AND demo=1 ORDER BY id LIMIT 1", cliente_id)
    d = {"codigo": cod, "alias": "Oficina central (demo)", "tipo": "OFI", "pais": "CL", "ciudad": "Santiago", "nodo": nodo_sugerido(cod), "hop": "",
         "equipos": [e["id"] for e in est], "grupo_id": g1["id"] if g1 else None, "ubicaciones": pisos,
         "ubic_eq": {str(e["id"]): pisos[n % 2] for n, e in enumerate(est)}}
    return guardar(cliente_id, d, quien, demo=True)


def sembrar_grupos_demo(cliente_id, quien="faceops"):
    """Dos grupos de prueba marcados «demo»; el sitio demo queda en el primero. Idempotente: no toca grupos reales."""
    _tabla()
    if q1("SELECT id FROM edge_grupos WHERE cliente_id=? AND demo=1", cliente_id):
        return None
    g1 = guardar_grupo(cliente_id, {"nombre": "Zona Centro (demo)", "descripcion": "Grupo de prueba del laboratorio: sitios de la Región Metropolitana"}, quien, demo=True)
    guardar_grupo(cliente_id, {"nombre": "Mall (demo)", "descripcion": "Grupo de prueba del laboratorio: tiendas en centros comerciales (aún sin sitios)"}, quien, demo=True)
    ej("UPDATE edge_sitios SET grupo_id=? WHERE cliente_id=? AND demo=1 AND grupo_id IS NULL", g1, cliente_id)
    return g1


# ------------------------------------------------------------------ estado de la malla (Automation Mesh) leído de AAP
_malla_cache = {}


def malla(cliente, nombres):
    """{hostname: {estado, tipo}} de los nodos de la malla en AAP (instances). None si AAP no responde (no se inventa)."""
    nombres = sorted({n for n in nombres if n})
    if not nombres:
        return {}
    k = (cliente["id"], tuple(nombres))
    if k in _malla_cache and time.time() - _malla_cache[k][0] < 60:
        return _malla_cache[k][1]
    from .aap import AAP, ErrorAAP
    try:
        a = AAP.de_cliente(cliente)
        r = a.s.get(a.base + "instances/", params={"hostname__in": ",".join(nombres), "page_size": 200}, timeout=5)
        r.raise_for_status()
        res = {x["hostname"]: {"estado": x.get("node_state") or "", "tipo": x.get("node_type") or "", "visto": x.get("last_seen") or ""}
               for x in r.json().get("results", [])}
    except (ErrorAAP, Exception):  # noqa: BLE001  sin lectura: se muestra «sin lectura», nunca un estado inventado
        res = None
    _malla_cache[k] = (time.time(), res)
    return res


ESTADO_NODO = {"ready": ("Listo", "s-ok"), "installed": ("Instalado, sin enlace", "s-media"), "provisioning": ("Aprovisionando", "s-media"),
               "unavailable": ("Sin conexión", "s-alta"), "deprovisioning": ("Dando de baja", "s-media")}


def estado_nodo(m, nombre):
    """(texto, clase chip, listo: True/False/None)."""
    if not nombre:
        return "Sin nodo", "s-alta", False
    if m is None:
        return "Sin lectura de AAP", "", None
    x = m.get(nombre)
    if not x:
        return "No registrado en la malla", "s-alta", False
    t, c = ESTADO_NODO.get(x["estado"], (x["estado"] or "desconocido", ""))
    return t, c, x["estado"] == "ready"


# ------------------------------------------------------------------ acciones (AAP, ticket y aprobación)
def _accion_aap(nombre_aap):
    from .mod_hipervisores import _accion_aap as base
    a, motivo = base(g.cliente["id"], nombre_aap)
    if motivo.startswith("Aún no disponible"):
        motivo = f"Aún no disponible: «{nombre_aap}» no está publicada en AAP"
    return a, motivo


def _ticket_sitio(aid, codigo):
    from .salud_acciones import ABIERTOS
    for t in q("SELECT id, numero, estado, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 40", g.cliente["id"], aid):
        if (j(t["parametros"], {}) or {}).get("edge_sitio") == codigo:
            return dict(t, abierto=t["estado"] in ABIERTOS) if (t["estado"] in ABIERTOS or t["estado"] == "exitoso") else None
    return None


def accion(s, nombre_aap, nombre, escritura, eqs):
    a, motivo = _accion_aap(nombre_aap)
    if not motivo and not eqs and nombre_aap != ACCIONES[0][3]:
        motivo = "El sitio aún no tiene equipos asociados (Editar sitio)"
    return {"nombre": nombre, "auto": (a or {}).get("nombre") or nombre_aap, "aid": (a or {}).get("id"), "bloqueo": motivo,
            "escritura": escritura, "ticket": _ticket_sitio(a["id"], s["codigo"]) if a else None, "nombre_aap": nombre_aap}


def radar_sitio(s, eqs, m, escritura):
    """Salud del sitio con los 6 pilares: los criterios propios del sitio (malla e inventario) y los de sus equipos."""
    from .radar import _armar, radar_equipos
    r_eq = radar_equipos(eqs, hallazgos_visibles(eqs), g.cliente) if eqs else None
    crit = []
    for p_ in (r_eq or {}).get("principios", []):
        for c in p_["criterios"]:
            nombres = ", ".join(x["nombre"] for x in c.get("faltan", [])[:4]) + ("…" if c.get("n_faltan", 0) > 4 else "")
            crit.append(dict(c, explica=c["explica"].replace("automatizaciones", "acciones"), acc={"bloqueo": f"Se corrige en la ficha de cada equipo: {nombres}" if nombres else "Se corrige en la ficha de cada equipo",
                                     "escritura": escritura, "ticket": None, "auto": c["nombre"]}))

    def c_(cid, pr, nom, exp, v, acc):
        crit.append({"id": cid, "principio": pr, "nombre": nom, "explica": exp, "faltan": [], "n_faltan": 0, "acc": acc,
                     "pct": None if v is None else (round(v) if not isinstance(v, bool) else (100 if v else 0))})
    _, _, listo = estado_nodo(m, s["nodo"])
    c_("EG1", "Disponibilidad", "Execution node listo en la malla", f"{s['nodo'] or 'Sin nodo'}: AAP lo ve listo para recibir acciones", listo,
       accion(s, ACCIONES[0][3], ACCIONES[0][1], escritura, eqs))
    if s["hop"]:
        _, _, h_ok = estado_nodo(m, s["hop"])
        c_("EG2", "Disponibilidad", "Hop node listo", f"{s['hop']}: el paso del sitio hacia la central", h_ok, accion(s, ACCIONES[1][3], ACCIONES[1][1], escritura, eqs))
    grupo = grupo_ansible(s["codigo"])
    en_g = [e for e in eqs if grupo in (j(e.get("grupos"), []) or [])]
    c_("EG3", "Excelencia operativa", "Equipos en el grupo del sitio", f"Grupo «{grupo}» del inventario de AAP", (len(en_g) * 100 / len(eqs)) if eqs else None,
       accion(s, ACCIONES[2][3], ACCIONES[2][1], escritura, eqs))
    r = _armar(crit, max(1, len(eqs)), g.cliente)
    r["remediables"] = sorted([c for c in crit if c["pct"] is not None and c["pct"] < 100], key=lambda c: c["pct"])
    return r


def _equipos_de(s, vis):
    por_id = {e["id"]: e for e in vis}
    return [por_id[i] for i in s["equipos_l"] if i in por_id]


# ------------------------------------------------------------------ diagrama de arquitectura del sitio (SVG armado en el servidor)
SECC_DIAG = [("red", "Red"), ("srv", "Servidores"), ("pos", "Puntos de venta (POS)"), ("est", "Estaciones de trabajo")]


def clase_diag(e):
    from .vistas import RED_CATS
    c = (e.get("categoria") or "servidor").lower()
    if c in RED_CATS:
        return "red"
    if "pos" in c.split() or c in ("pos", "punto de venta") or re.search(r"-pos\d", e["nombre"]):
        return "pos"
    if c == "estación de trabajo":
        return "est"
    return "srv"


def salud_equipos(eqs):
    """{equipo_id: % de salud} con el mismo radar de la ficha de la máquina (None = sin lectura)."""
    from .radar import radar_equipos
    hs = hallazgos_visibles(eqs) if eqs else []
    return {e["id"]: radar_equipos([e], [h for h in hs if h.get("equipo_id") == e["id"]], g.cliente)["pct"] for e in eqs}


def _recorte(c, ox, oy):
    """Punto del borde de la caja c en la recta de su centro hacia (ox, oy): los conectores parten y llegan al borde."""
    cx, cy = c["x"] + c["w"] / 2, c["y"] + c["h"] / 2
    dx, dy = ox - cx, oy - cy
    if not dx and not dy:
        return cx, cy
    t = min(c["w"] / 2 / abs(dx) if dx else 1e9, c["h"] / 2 / abs(dy) if dy else 1e9)
    return round(cx + dx * t, 1), round(cy + dy * t, 1)


MALLA = ("central", "hop", "en")


def conector(cajas_d, a, b):
    ca, cb = cajas_d[a], cajas_d[b]
    x1, y1 = _recorte(ca, cb["x"] + cb["w"] / 2, cb["y"] + cb["h"] / 2)
    x2, y2 = _recorte(cb, ca["x"] + ca["w"] / 2, ca["y"] + ca["h"] / 2)
    tunel = a in MALLA and b in MALLA
    return {"a": a, "b": b, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "tunel": tunel, "txt": "malla 27199/tcp" if tunel else ""}


def topologia(s):
    t = j(s.get("topologia"), None)
    return t if isinstance(t, dict) and t.get("conexiones") is not None else None


def diagrama(s, eqs, m, pcts, disps=()):
    """Central (FaceOps → AAP), hop si aplica y, dentro del sitio, el execution node, los equipos REALES del inventario del
    sitio agrupados por clase (cada uno con su % y su color de salud) y los otros dispositivos registrados (impresoras, POS…).
    Si el sitio tiene un diagrama editado (posiciones y conexiones guardadas), se respeta; los nodos nuevos van en su lugar
    automático conectados al execution node. Nada inventado: sin equipos, el sitio va vacío."""
    from .salud import tono_salud
    W, BW, BH, GAP, POR_FILA = 960, 170, 58, 12, 5
    cajas, pares, textos = [], [], []

    def caja(nid, x, y, w, h, tit, sub, pct=None, url=None, tono=None, mono=True, info=""):
        t = tono or tono_salud(pct)
        nt, ns = int((w - (20 if tono else 58)) / (7.2 if mono else 7.6)), int((w - 18) / 5.9)   # caben según el ancho de la caja
        cajas.append({"id": nid, "x": x, "y": y, "w": w, "h": h, "tit": tit if len(tit) <= nt else tit[:nt - 1] + "…", "tit_full": tit,
                      "sub": sub if len(sub) <= ns else sub[:ns - 1] + "…", "pct": pct,
                      "tono": t, "url": url, "mono": mono, "info": info})
        return x + w / 2, y, y + h

    def nodo_pct(nombre):
        _, _, listo = estado_nodo(m, nombre)
        return None if listo is None else (100 if listo else 0)
    cx = W / 2
    _, _, c_bot = caja("central", cx - 160, 14, 320, BH, "Central · FaceOps → AAP", "controller · acciones con ticket y aprobación", tono="central", mono=False)
    y = c_bot + 52
    sube = "central"
    if s["hop"]:
        hp = nodo_pct(s["hop"])
        t_, _, _ = estado_nodo(m, s["hop"])
        _, h_top, h_bot = caja("hop", cx - 150, y, 300, BH, s["hop"], f"hop node · DMZ · {t_}", hp, mono=True)
        pares.append(("hop", sube))
        sube, y = "hop", h_bot + 52
    marco_y = y
    y += 34
    en_p = nodo_pct(s["nodo"])
    t_en, _, _ = estado_nodo(m, s["nodo"])
    _, en_top, en_bot = caja("en", cx - 150, y, 300, BH, s["nodo"] or "sin execution node", f"execution node · {t_en}", en_p, mono=True)
    pares.append(("en", sube))
    y = en_bot + 46
    ubq = s.get("ubic_eq_d") or {}
    secciones = []
    for k, nom in SECC_DIAG:
        grupo_ = [("eq", e) for e in eqs if clase_diag(e) == k]
        if k == "est" and s.get("ubicaciones_l"):   # oficina: estaciones agrupadas por ubicación (piso, ala)
            for u in s["ubicaciones_l"] + [""]:
                gu = [x for x in grupo_ if (ubq.get(x[1]["id"]) or "") == u]
                if gu:
                    secciones.append((f"{nom} · {u or 'sin ubicación'}", gu))
        elif grupo_:
            secciones.append((nom, grupo_))
    for k, nom, _ in TIPOS_DISP:
        dd = [("dv", d) for d in disps if d["tipo"] == k]
        if dd:
            secciones.append((nom, dd))
    for nom, grupo_ in secciones:
        textos.append({"x": 40, "y": y - 8, "txt": f"{nom} · {len(grupo_)}"})
        for i0 in range(0, len(grupo_), POR_FILA):
            fila = grupo_[i0:i0 + POR_FILA]
            x0 = cx - (len(fila) * BW + (len(fila) - 1) * GAP) / 2
            for n, (cl, e) in enumerate(fila):
                bx = x0 + n * (BW + GAP)
                if cl == "eq":
                    win = (e.get("so_familia") or "").lower() == "windows" or "windows" in (e.get("so") or "").lower()
                    proto = "WinRM 5986" if win else "SSH 22"
                    pc = pcts.get(e["id"])
                    caja(f"eq-{e['id']}", bx, y, BW, BH, e["nombre"], f"{e.get('ip') or 'sin IP'} · {proto}", pc, url=url_for("consola.equipo", eid=e["id"]),
                         info=f"{e['nombre']} · {e.get('categoria') or 'servidor'} · {e.get('so') or ''} · salud {pc if pc is not None else '—'} %")
                    pares.append(("en", f"eq-{e['id']}"))
                else:
                    est = (e.get("est") or ("", "", ""))[0]
                    pc = 100 if est == "Responde" else (0 if est == "No responde" else None)
                    caja(f"dv-{e['id']}", bx, y, BW, BH, e["nombre"], f"{e.get('ip') or 'sin IP'} · sin agente", pc, url="#dispositivos",
                         info=f"{e['nombre']} · {TIPOS_DISP_D.get(e['tipo'], e['tipo'])} · {' '.join(x for x in (e.get('fabricante'), e.get('modelo')) if x)} · {est}")
                    pares.append(("en", f"dv-{e['id']}"))
            y += BH + GAP
        y += 34
    if not eqs and not disps:
        textos.append({"x": cx, "y": y, "txt": "El sitio aún no tiene equipos en el inventario", "centro": True})
        y += 30
    # diagrama editado: posiciones y conexiones guardadas (solo de nodos que siguen existiendo)
    topo = topologia(s)
    cd = {c["id"]: c for c in cajas}
    if topo:
        pos = topo.get("nodos") or {}
        for nid, c in cd.items():
            p_ = pos.get(nid)
            if isinstance(p_, dict) and nid != "central":
                c["x"], c["y"] = float(p_.get("x", c["x"])), float(p_.get("y", c["y"]))
        en_con = {n for x in topo["conexiones"] for n in x}
        nuevos = {nid for nid in cd if nid not in pos and nid not in en_con}
        pares = [(a, b) for a, b in (tuple(x) for x in topo["conexiones"] if len(x) == 2) if a in cd and b in cd] + \
                [(a, b) for a, b in pares if b in nuevos or a in nuevos]
        textos = []   # los rótulos de sección valen solo para la disposición automática
        y = max([c["y"] + c["h"] for c in cajas if c["id"] not in ("central", "hop")] + [y - 46]) + 46
    lineas = [conector(cd, a, b) for a, b in dict.fromkeys(pares)]
    marco = {"x": 16, "y": marco_y, "w": W - 32, "h": y - marco_y - 10, "txt": f"Sitio {s['codigo']}" + (f" · {s['alias']}" if s.get("alias") else "")}
    return {"w": W, "h": y + 6, "cajas": cajas, "lineas": lineas, "textos": textos, "marco": marco, "editado": topo,
            "huerfanos": [c["tit_full"] for c in cajas if c["id"] != "central" and not any(c["id"] in (l["a"], l["b"]) for l in lineas)]}


def guardar_topologia(cliente_id, s, datos, quien, ids_validos):
    """Guarda posiciones y conexiones del diagrama del sitio (documentación del inventario: no cambia la red real) y deja la
    versión anterior en el historial. datos=None vuelve a la disposición automática."""
    _tabla()
    if datos is None:
        nuevo = None
    else:
        nodos = {}
        for nid, p_ in (datos.get("nodos") or {}).items():
            if nid in ids_validos and isinstance(p_, dict):
                try:
                    nodos[nid] = {"x": max(0.0, min(2000.0, round(float(p_["x"]), 1))), "y": max(0.0, min(4000.0, round(float(p_["y"]), 1)))}
                except (KeyError, TypeError, ValueError):
                    raise ValueError("Posición no válida") from None
        con = []
        for x in datos.get("conexiones") or []:
            if not (isinstance(x, (list, tuple)) and len(x) == 2):
                raise ValueError("Conexión no válida")
            a, b = str(x[0]), str(x[1])
            if a not in ids_validos or b not in ids_validos:
                raise ValueError("La conexión apunta a un nodo que no está en el sitio")
            if a != b and [a, b] not in con and [b, a] not in con:
                con.append([a, b])
        if len(con) > 2000:
            raise ValueError("Demasiadas conexiones")
        nuevo = {"nodos": nodos, "conexiones": con, "autor": quien, "fecha": ahora()}
    ej("INSERT INTO edge_topologia_hist(cliente_id, sitio_id, anterior, nueva, autor, fecha) VALUES (?,?,?,?,?,?)",
       cliente_id, s["id"], s.get("topologia") or "", json.dumps(nuevo, ensure_ascii=False) if nuevo else "", quien, ahora())
    ej("UPDATE edge_sitios SET topologia=? WHERE id=? AND cliente_id=?", json.dumps(nuevo, ensure_ascii=False) if nuevo else None, s["id"], cliente_id)
    auditar(quien, cliente_id, "editar diagrama edge" if nuevo else "restablecer diagrama edge", s["codigo"],
            {"conexiones": len(nuevo["conexiones"]) if nuevo else 0})
    return nuevo


def historial_topologia(cliente_id, sid, n=8):
    _tabla()
    return q("SELECT autor, fecha, nueva FROM edge_topologia_hist WHERE cliente_id=? AND sitio_id=? ORDER BY id DESC LIMIT ?", cliente_id, sid, n)


def _filas(ss, vis, m, esc):
    filas = []
    for s in ss:
        eqs = _equipos_de(s, vis)
        t, c, listo = estado_nodo(m, s["nodo"])
        n_al = len([h for h in hallazgos_visibles(eqs)]) if eqs else 0
        filas.append(dict(s, eqs=eqs, nodo_txt=t, nodo_cls=c, nodo_listo=listo, alertas=n_al, salud=radar_sitio(s, eqs, m, esc)["pct"]))
    return filas


def resumen_grupos(filas, grs):
    """Por grupo: sitios, salud agregada (promedio de sus sitios con lectura), nodos no listos y alertas; peor primero."""
    out = []
    for gr in list(grs) + [{"id": None, "nombre": "Sin grupo", "descripcion": "", "demo": 0}]:
        fs = [f for f in filas if f.get("grupo_id") == gr["id"]]
        if gr["id"] is None and not fs:
            continue
        val = [f["salud"] for f in fs if f["salud"] is not None]
        out.append(dict(gr, filas=fs, n=len(fs), salud=round(sum(val) / len(val)) if val else None,
                        nodos_mal=sum(1 for f in fs if f["nodo_listo"] is False), alertas=sum(f["alertas"] for f in fs),
                        criticos=sum(1 for f in fs if f["salud"] is not None and f["salud"] < 50)))
    return out


# ------------------------------------------------------------------ vistas
@bp.route("/edge")
@requiere_login
def edge():
    """Sitios edge del cliente (tiendas, sucursales, bodegas, centros de distribución) con su execution node y su salud."""
    vis = equipos_visibles()
    ss = sitios(g.cliente["id"])
    esc = g.perfil["modo"] == "escritura"
    m = malla(g.cliente, [s["nodo"] for s in ss] + [s["hop"] for s in ss]) if ss else {}
    filas = _filas(ss, vis, m, esc)
    grs = grupos(g.cliente["id"])
    res = resumen_grupos(filas, grs)
    con_lect = [r for r in res if r["n"]]
    peores = sorted(con_lect, key=lambda r: (r["salud"] if r["salud"] is not None else 101, -r["nodos_mal"], -r["alertas"]))
    return render_template("edge.html", filas=filas, tipos=TIPOS, esc=esc, equipos=vis, ocupados={i: s["codigo"] for s in ss for i in s["equipos_l"]},
                           tipo_sel=request.args.get("tipo", ""), grupo_sel=request.args.get("grupo", ""), grupos=grs, res=res, peores=peores,
                           n_por_grupo={r["id"]: r["n"] for r in res})


@bp.route("/edge/<int:sid>")
@requiere_login
def edge_ficha(sid):
    """Ficha del sitio con el esquema de toda ficha de FaceOps: cabecera (código y alias), barra de salud (flotante con lo
    remediable), caja Encendido, execution node / hop, equipos, alertas y tickets. «Editar sitio» abre un flotante lateral."""
    _tabla()
    s = q1("SELECT * FROM edge_sitios WHERE id=? AND cliente_id=?", sid, g.cliente["id"])
    if not s:
        abort(404)
    _con_listas(s)
    vis = equipos_visibles()
    eqs = _equipos_de(s, vis)
    esc = g.perfil["modo"] == "escritura"
    m = malla(g.cliente, [s["nodo"], s["hop"]])
    r = radar_sitio(s, eqs, m, esc)
    nodo_t, nodo_c, _ = estado_nodo(m, s["nodo"])
    hop_t, hop_c, _ = estado_nodo(m, s["hop"]) if s["hop"] else ("Salida directa a la central", "", None)
    acciones = [dict(accion(s, na, n, esc, eqs), area=ar, texto=tx) for ar, n, tx, na in ACCIONES]
    _, energia_no = _accion_aap(ENERGIA_EDGE)
    if not energia_no and not eqs:
        energia_no = "El sitio aún no tiene equipos asociados"
    if not energia_no and not esc:
        energia_no = "Tu perfil es de solo lectura"
    ids = {e["id"] for e in eqs}
    hs = [dict(h, equipo=next((e for e in eqs if e["id"] == h.get("equipo_id")), None)) for h in hallazgos_visibles(eqs) if h.get("equipo_id") in ids]
    orden = {"critica": 0, "crítica": 0, "alta": 1, "media": 2, "baja": 3}
    hs.sort(key=lambda h: orden.get((h.get("severidad") or "").lower(), 9))
    from .vistas import tickets_visibles
    nombres = {e["nombre"] for e in eqs}
    tks = [t for t in tickets_visibles(800) if (j(t["parametros"], {}) or {}).get("edge_sitio") == s["codigo"]
           or (set((t.get("limite") or "").split(":")) & nombres)][:15]
    on_ = sum(1 for e in eqs if e.get("energia") == "encendido")
    gr_ = q1("SELECT * FROM edge_grupos WHERE id=? AND cliente_id=?", s.get("grupo_id"), g.cliente["id"]) if s.get("grupo_id") else None
    ds = [dict(d, est=estado_disp(d)) for d in dispositivos(g.cliente["id"], s["id"])]
    return render_template("edge_sitio.html", s=s, disps=ds, disp_grupos=dispositivos_por_tipo(ds), tipos_disp=TIPOS_DISP, diag=diagrama(s, eqs, m, salud_equipos(eqs), ds),
                           topo_hist=historial_topologia(g.cliente["id"], s["id"]), grupo_s=gr_, grupos=grupos(g.cliente["id"]), eqs=eqs, radar_s=r, nodo_t=nodo_t, nodo_c=nodo_c, hop_t=hop_t, hop_c=hop_c, m=m,
                           acciones=acciones, areas=list(dict.fromkeys(a["area"] for a in acciones)), energia_no=energia_no, esc=esc,
                           hallazgos=hs, tickets=tks, on_=on_, tipos=TIPOS, grupo=grupo_ansible(s["codigo"]), equipos=vis,
                           ocupados={i: x["codigo"] for x in sitios(g.cliente["id"]) if x["id"] != s["id"] for i in x["equipos_l"]})


@bp.route("/edge/guardar", methods=["POST"])
@bp.route("/edge/<int:sid>/guardar", methods=["POST"])
@requiere_login
def edge_guardar(sid=None):
    """Alta o edición de un sitio (flotante lateral). El código no cambia al editar."""
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    _tabla()
    actual = q1("SELECT * FROM edge_sitios WHERE id=? AND cliente_id=?", sid, g.cliente["id"]) if sid else None
    if sid and not actual:
        abort(404)
    try:
        d = validar(g.cliente["id"], request.form, actual)
        nid = guardar(g.cliente["id"], d, g.usuario["usuario"], actual)
    except ValueError as err:
        flash(str(err), "error")
        return redirect(url_for("consola.edge_ficha", sid=sid, editar=1) if sid else url_for("consola.edge", nuevo=1))
    flash(f"Sitio {d['codigo']} {'actualizado' if actual else 'agregado'}", "ok")
    return redirect(url_for("consola.edge_ficha", sid=nid))


def _sitio_post(sid):
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return None, ({"error": "Necesitas perfil de escritura"}, 403)
    _tabla()
    s = q1("SELECT * FROM edge_sitios WHERE id=? AND cliente_id=?", sid, g.cliente["id"])
    if not s:
        return None, ({"error": "Sitio no válido"}, 400)
    _con_listas(s)
    return s, None


def _lanzar(s, a, eqs, params, motivo):
    from .aap import ErrorAAP
    from .operacion import solicitar
    try:
        t = solicitar(g.usuario, g.cliente, a, None, dict(params, edge_sitio=s["codigo"], edge_grupo=grupo_ansible(s["codigo"]),
                                                            edge_nodo=s["nodo"], edge_hop=s["hop"]), motivo, equipos=eqs or None)
    except (PermissionError, ErrorAAP, ValueError) as err:
        return {"error": str(err)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"]}


@bp.route("/edge/<int:sid>/accion", methods=["POST"])
@requiere_login
def edge_accion(sid):
    """Lanza una acción del sitio: ticket en AAP con aprobación; baja al sitio por la malla. Valida lo mismo que muestra la ficha."""
    s, err = _sitio_post(sid)
    if err:
        return err
    eqs = _equipos_de(s, equipos_visibles())
    aid = str(request.form.get("aid") or "")
    sel = next(((n, na) for _, n, _, na in ACCIONES if str((_accion_aap(na)[0] or {}).get("id")) == aid), None)
    if not sel:
        return {"error": "Acción no válida para el sitio"}, 400
    ac = accion(s, sel[1], sel[0], True, eqs)
    if ac["ticket"] and ac["ticket"]["abierto"]:
        t = ac["ticket"]
        return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "existente": True}
    if ac["bloqueo"]:
        return {"error": ac["bloqueo"]}, 400
    return _lanzar(s, _accion_aap(sel[1])[0], eqs, {}, f"{sel[0]} en {s['codigo']}")


@bp.route("/edge/<int:sid>/energia", methods=["POST"])
@requiere_login
def edge_energia(sid):
    """Encender o apagar los equipos del sitio: ticket con la acción certificada y aprobación en AAP."""
    s, err = _sitio_post(sid)
    if err:
        return err
    ac = request.form.get("accion", "")
    if ac not in ("encender", "apagar"):
        return {"error": "Acción no válida"}, 400
    eqs = _equipos_de(s, equipos_visibles())
    a, motivo = _accion_aap(ENERGIA_EDGE)
    if motivo or not eqs:
        return {"error": motivo or "El sitio aún no tiene equipos asociados"}, 400
    return _lanzar(s, a, eqs, {"edge_accion": ac}, f"{ac.capitalize()} los equipos de {s['codigo']}")


@bp.route("/edge/grupos/guardar", methods=["POST"])
@bp.route("/edge/grupos/<int:gid>/guardar", methods=["POST"])
@requiere_login
def edge_grupo_guardar(gid=None):
    """Alta o edición de un grupo de sitios (flotante «Grupos» de la lista)."""
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    try:
        guardar_grupo(g.cliente["id"], request.form, g.usuario["usuario"], gid)
        flash(f"Grupo «{request.form.get('nombre', '').strip()}» {'actualizado' if gid else 'creado'}", "ok")
    except ValueError as err:
        flash(str(err), "error")
    return redirect(url_for("consola.edge", grupos=1))


@bp.route("/edge/grupos/<int:gid>/eliminar", methods=["POST"])
@requiere_login
def edge_grupo_eliminar(gid):
    """Elimina el grupo; sus sitios quedan «Sin grupo»."""
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    try:
        gr = eliminar_grupo(g.cliente["id"], gid, g.usuario["usuario"])
        flash(f"Grupo «{gr['nombre']}» eliminado: sus sitios quedaron sin grupo", "ok")
    except ValueError as err:
        flash(str(err), "error")
    return redirect(url_for("consola.edge", grupos=1))


# ------------------------------------------------------------------ otros dispositivos del sitio (solo inventario, sin agente)
def dispositivos(cliente_id, sid):
    _tabla()
    orden = {k: n for n, (k, _, _) in enumerate(TIPOS_DISP)}
    ds = q("SELECT * FROM edge_dispositivos WHERE cliente_id=? AND sitio_id=? ORDER BY nombre", cliente_id, sid)
    return sorted(ds, key=lambda d: (orden.get(d["tipo"], 99), d["nombre"].lower()))


def dispositivos_por_tipo(ds):
    """[(clave, nombre, icono, [dispositivos])] solo con los tipos que tienen algo, en el orden de TIPOS_DISP."""
    return [(k, n, ic, [d for d in ds if d["tipo"] == k]) for k, n, ic in TIPOS_DISP if any(d["tipo"] == k for d in ds)]


def estado_disp(d):
    """(texto, clase chip, explicación). Sin sondeo en vivo: solo la última lectura guardada, si la hay."""
    if not d.get("ip"):
        return "Sin IP", "", "Sin IP no hay lectura de estado: solo queda registrado en el inventario"
    if d.get("estado"):
        ok = d["estado"] == "responde"
        return ("Responde" if ok else "No responde"), ("s-ok" if ok else "s-alta"), f"Última lectura sin agente: {d.get('estado_visto') or '—'}"
    return "Sin lectura", "", "El estado se obtiene sin agente (ping o SNMP) cuando se habilite el sondeo; no se instala nada en el dispositivo"


def guardar_dispositivo(cliente_id, s, f, quien, did=None):
    _tabla()
    tipo = (f.get("tipo") or "").strip()
    if tipo not in TIPOS_DISP_D:
        raise ValueError("Elige el tipo de dispositivo")
    nombre = (f.get("nombre") or "").strip()[:80]
    if not nombre:
        raise ValueError("Indica el nombre del dispositivo")
    ip = (f.get("ip") or "").strip()
    if ip:
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            raise ValueError(f"IP no válida: {ip}") from None
    fab = (f.get("fabricante") or "").strip()[:60]
    mod = (f.get("modelo") or "").strip()[:60]
    ubic = (f.get("ubicacion") or "").strip()[:60]
    if s.get("ubicaciones_l") and ubic and ubic not in s["ubicaciones_l"]:
        raise ValueError("Ubicación no válida para el sitio")
    otro = q1("SELECT id FROM edge_dispositivos WHERE cliente_id=? AND sitio_id=? AND lower(nombre)=lower(?)", cliente_id, s["id"], nombre)
    if otro and otro["id"] != did:
        raise ValueError(f"Ya hay un dispositivo llamado «{nombre}» en el sitio")
    det = {"tipo": tipo, "ip": ip, "fabricante": fab, "modelo": mod, "ubicacion": ubic}
    if did:
        act = q1("SELECT * FROM edge_dispositivos WHERE id=? AND cliente_id=? AND sitio_id=?", did, cliente_id, s["id"])
        if not act:
            raise ValueError("Dispositivo no válido")
        ej("UPDATE edge_dispositivos SET tipo=?, nombre=?, ip=?, fabricante=?, modelo=?, ubicacion=?, estado=?, estado_visto=?, actualizado=?, autor=? WHERE id=?",
           tipo, nombre, ip, fab, mod, ubic, act["estado"] if ip == act["ip"] else "", act["estado_visto"] if ip == act["ip"] else "", ahora(), quien, did)
        auditar(quien, cliente_id, "editar dispositivo edge", f"{s['codigo']} · {nombre}", det)
        return did
    did = ej("INSERT INTO edge_dispositivos(cliente_id, sitio_id, tipo, nombre, ip, fabricante, modelo, ubicacion, creado, actualizado, autor) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?)", cliente_id, s["id"], tipo, nombre, ip, fab, mod, ubic, ahora(), ahora(), quien)
    auditar(quien, cliente_id, "agregar dispositivo edge", f"{s['codigo']} · {nombre}", det)
    return did


@bp.route("/edge/<int:sid>/dispositivo", methods=["POST"])
@bp.route("/edge/<int:sid>/dispositivo/<int:did>", methods=["POST"])
@requiere_login
def edge_disp_guardar(sid, did=None):
    """Alta o edición de un dispositivo del sitio (impresora, POS, AP…). Registro directo en el inventario de FaceOps: no toca el
    dispositivo ni instala nada en él."""
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    _tabla()
    s = q1("SELECT * FROM edge_sitios WHERE id=? AND cliente_id=?", sid, g.cliente["id"])
    if not s:
        abort(404)
    _con_listas(s)
    try:
        guardar_dispositivo(g.cliente["id"], s, request.form, g.usuario["usuario"], did)
        flash(f"Dispositivo «{request.form.get('nombre', '').strip()}» {'actualizado' if did else 'agregado'}", "ok")
    except ValueError as err:
        flash(str(err), "error")
    return redirect(url_for("consola.edge_ficha", sid=sid) + "#dispositivos")


@bp.route("/edge/<int:sid>/diagrama", methods=["POST"])
@requiere_login
def edge_diagrama_guardar(sid):
    """Guarda el diagrama editado del sitio (JSON: nodos con x/y y conexiones [a, b]); {"restablecer": true} vuelve al automático.
    Es documentación del inventario: guarda directo (sin ticket) y registra quién y cuándo."""
    d = request.get_json(silent=True) or {}
    if d.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    _tabla()
    s = q1("SELECT * FROM edge_sitios WHERE id=? AND cliente_id=?", sid, g.cliente["id"])
    if not s:
        return {"error": "Sitio no válido"}, 400
    _con_listas(s)
    eqs = _equipos_de(s, equipos_visibles())
    ids = {"central", "en"} | ({"hop"} if s["hop"] else set()) | {f"eq-{e['id']}" for e in eqs} | \
          {f"dv-{x['id']}" for x in dispositivos(g.cliente["id"], sid)}
    try:
        t = guardar_topologia(g.cliente["id"], s, None if d.get("restablecer") else d, g.usuario["usuario"], ids)
    except ValueError as err:
        return {"error": str(err)}, 400
    if t:
        con = {n for c in t["conexiones"] for n in c}
        return {"ok": True, "huerfanos": sorted(ids - con - {"central"})}
    return {"ok": True}


@bp.route("/edge/<int:sid>/dispositivo/<int:did>/eliminar", methods=["POST"])
@requiere_login
def edge_disp_eliminar(sid, did):
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    _tabla()
    d = q1("SELECT d.*, s.codigo FROM edge_dispositivos d JOIN edge_sitios s ON s.id=d.sitio_id WHERE d.id=? AND d.sitio_id=? AND d.cliente_id=?",
           did, sid, g.cliente["id"])
    if not d:
        abort(404)
    ej("DELETE FROM edge_dispositivos WHERE id=?", did)
    auditar(g.usuario["usuario"], g.cliente["id"], "quitar dispositivo edge", f"{d['codigo']} · {d['nombre']}")
    flash(f"Dispositivo «{d['nombre']}» quitado del sitio", "ok")
    return redirect(url_for("consola.edge_ficha", sid=sid) + "#dispositivos")


# ------------------------------------------------------------------ estaciones de trabajo por sitio y ubicación (Edge × Estaciones)
def estaciones_por_sitio():
    """Las estaciones de trabajo visibles agrupadas por sitio Edge y ubicación (piso), con la salud de cada una (la misma de la
    lista de máquinas) y la agregada de cada grupo. Las que no están en ningún sitio van en «Sin sitio»."""
    from .vistas import _con_salud, seccion_eq
    cid = g.cliente["id"]
    est = [e for e in equipos_visibles() if seccion_eq(e) == "estaciones"]
    _con_salud(est)
    ss, grs = sitios(cid), {x["id"]: x for x in grupos(cid)}
    donde = {}
    for s in ss:
        for i in s["equipos_l"]:
            donde[i] = (s, s["ubic_eq_d"].get(i, ""))
    bloques = []

    def bloque(s, u, lista):
        val = [e["salud"]["salud"] for e in lista if e.get("salud") and e["salud"].get("salud") is not None]
        bloques.append({"sitio": s, "ubic": u, "grupo": grs.get((s or {}).get("grupo_id")), "estaciones": lista,
                        "salud": round(sum(val) / len(val)) if val else None})
    for s in ss:
        mios = [e for e in est if donde.get(e["id"], (None,))[0] is s]
        for u in s["ubicaciones_l"] + [""]:
            lu = [e for e in mios if (donde[e["id"]][1] or "") == u]
            if lu:
                bloque(s, u, lu)
    sin = [e for e in est if e["id"] not in donde]
    if sin:
        bloque(None, "", sin)
    return {"bloques": bloques, "sitios": ss, "grupos": list(grs.values()), "total": len(est)}


@bp.app_context_processor
def _ctx_edge():
    return {"edge_estaciones": estaciones_por_sitio, "edge_sitios_alta": lambda: [s for s in sitios(g.cliente["id"])] if g.get("cliente") else []}


@bp.route("/edge/estacion", methods=["POST"])
@requiere_login
def edge_estacion_asignar():
    """Tras el alta de una estación de trabajo: la asigna a un sitio Edge y a su ubicación (piso). Solo registra la pertenencia
    en FaceOps: no cambia nada en el equipo."""
    d = request.get_json(silent=True) or {}
    if d.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    _tabla()
    from .vistas import seccion_eq
    e = next((x for x in equipos_visibles() if x["nombre"] == (d.get("nombre") or "").strip()), None)
    if not e or seccion_eq(e) != "estaciones":
        return {"error": "Estación de trabajo no encontrada"}, 400
    try:
        sid = int(d.get("sitio") or 0)
    except (TypeError, ValueError):
        sid = 0
    s = q1("SELECT * FROM edge_sitios WHERE id=? AND cliente_id=?", sid, g.cliente["id"])
    if not s:
        return {"error": "Sitio no válido"}, 400
    _con_listas(s)
    otro = next((x for x in sitios(g.cliente["id"]) if x["id"] != s["id"] and e["id"] in x["equipos_l"]), None)
    if otro:
        return {"error": f"{e['nombre']} ya pertenece a {otro['codigo']}"}, 400
    u = (d.get("ubic") or "").strip()
    if u and u not in s["ubicaciones_l"]:
        return {"error": "Ubicación no válida para el sitio"}, 400
    eqs = s["equipos_l"] + ([e["id"]] if e["id"] not in s["equipos_l"] else [])
    ub = {str(k): v for k, v in s["ubic_eq_d"].items()}
    if u:
        ub[str(e["id"])] = u
    else:
        ub.pop(str(e["id"]), None)
    ej("UPDATE edge_sitios SET equipos=?, ubic_eq=?, actualizado=?, autor=? WHERE id=?", json.dumps(eqs), json.dumps(ub, ensure_ascii=False), ahora(),
       g.usuario["usuario"], s["id"])
    auditar(g.usuario["usuario"], g.cliente["id"], "asignar estación a sitio edge", s["codigo"], {"equipo": e["nombre"], "ubicacion": u})
    return {"ok": True}
