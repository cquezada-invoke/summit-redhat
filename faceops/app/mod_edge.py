"""FaceOps · Edge: sitios remotos del cliente (tiendas, sucursales, bodegas, centros de distribución), bajo Servidores físicos.

Modelo hub & spoke con Automation Mesh de AAP: cada sitio tiene un execution node local; si no tiene salida directa pasa por un
hop node (normalmente en la DMZ de la central). El sitio inicia la conexión hacia el hop o la central (no se abren puertos en el
sitio) y las acciones bajan por ese túnel. FaceOps nunca toca las máquinas: toda acción va por AAP con ticket y aprobación.

Código técnico del sitio: PAÍS-CIUDAD-TIPO-NNN (p. ej. CL-SCL-TDA-014), inmutable; el nombre comercial es un alias. En el
inventario de Ansible el grupo va con guion bajo (cl_scl_tda_014) y los equipos heredan el código (cl-scl-tda-014-pos01, -en01…).
"""
import json
import re
import time

from flask import abort, flash, g, redirect, render_template, request, session, url_for

from . import requiere_login
from .bd import ahora, auditar, ej, j, q, q1
from .vistas import bp, equipos_visibles, hallazgos_visibles, EP_ALIAS

TIPOS = {"TDA": "Tienda", "SUC": "Sucursal", "BOD": "Bodega", "CD": "Centro de distribución"}
RE_CODIGO = re.compile(r"^([A-Z]{2})-([A-Z]{3})-(TDA|SUC|BOD|CD)-(\d{3})$")
RE_NODO = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
# Acciones del sitio en AAP (nombre en AAP). Mientras no estén publicadas y certificadas aparecen deshabilitadas con su motivo.
ENERGIA_EDGE = "Edge · Encender o apagar equipos del sitio"
ACCIONES = [  # área, nombre, texto, nombre en AAP
    ("Malla", "Registrar el execution node en la malla", "Instala el execution node del sitio y lo enlaza al hop o a la central (la conexión la inicia el sitio)", "Edge · Registrar execution node"),
    ("Malla", "Verificar la ruta del sitio", "Comprueba el túnel sitio → hop → central y la latencia, sin cambiar nada", "Edge · Verificar ruta de la malla"),
    ("Inventario", "Agrupar los equipos del sitio", "Crea o actualiza el grupo del sitio en el inventario de AAP con sus equipos", "Edge · Agrupar equipos del sitio"),
]
EP_ALIAS.update({"consola.edge_ficha": "consola.edge", "consola.edge_guardar": "consola.edge", "consola.edge_accion": "consola.edge",
                 "consola.edge_energia": "consola.edge", "consola.edge_grupo_guardar": "consola.edge", "consola.edge_grupo_eliminar": "consola.edge"})


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS edge_sitios (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, codigo TEXT NOT NULL, alias TEXT DEFAULT '', tipo TEXT NOT NULL,
      pais TEXT NOT NULL, ciudad TEXT DEFAULT '', nodo TEXT DEFAULT '', hop TEXT DEFAULT '', equipos TEXT DEFAULT '[]',
      demo INTEGER DEFAULT 0, creado TEXT, actualizado TEXT, autor TEXT DEFAULT '',
      UNIQUE(cliente_id, codigo));
    CREATE TABLE IF NOT EXISTS edge_grupos (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, descripcion TEXT DEFAULT '', demo INTEGER DEFAULT 0,
      creado TEXT, autor TEXT DEFAULT '', UNIQUE(cliente_id, nombre));""")
    if "grupo_id" not in {r["name"] for r in con().execute("PRAGMA table_info(edge_sitios)")}:
        con().execute("ALTER TABLE edge_sitios ADD COLUMN grupo_id INTEGER")   # un grupo por sitio (para focalizar)
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
    return [dict(s, equipos_l=j(s["equipos"], []) or []) for s in q("SELECT * FROM edge_sitios WHERE cliente_id=? ORDER BY codigo", cliente_id)]


def validar(cliente_id, f, actual=None):
    """Datos del formulario → fila validada. El código es inmutable: solo se valida al crear."""
    codigo = actual["codigo"] if actual else (f.get("codigo") or "").strip()
    if not actual:
        if not codigo:
            raise ValueError("Indica el código técnico del sitio")
        if codigo != codigo.upper():
            raise ValueError("El código va en mayúsculas (p. ej. CL-SCL-TDA-014)")
        if not RE_CODIGO.match(codigo):
            raise ValueError("Formato de código no válido: PAÍS-CIUDAD-TIPO-NNN, p. ej. CL-SCL-TDA-014 (tipos: TDA, SUC, BOD, CD)")
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
    return {"codigo": codigo, "alias": alias, "tipo": m.group(3), "pais": m.group(1), "ciudad": ciudad, "nodo": nodo, "hop": hop, "equipos": ids,
            "grupo_id": grupo_id}


def guardar(cliente_id, d, quien, actual=None, demo=False):
    _tabla()
    if actual:
        ej("UPDATE edge_sitios SET alias=?, ciudad=?, nodo=?, hop=?, equipos=?, grupo_id=?, actualizado=?, autor=? WHERE id=? AND cliente_id=?",
           d["alias"], d["ciudad"], d["nodo"], d["hop"], json.dumps(d["equipos"]), d.get("grupo_id"), ahora(), quien, actual["id"], cliente_id)
        auditar(quien, cliente_id, "editar sitio edge", d["codigo"], {k: d.get(k) for k in ("alias", "ciudad", "nodo", "hop", "equipos", "grupo_id")})
        return actual["id"]
    sid = ej("INSERT INTO edge_sitios(cliente_id, codigo, alias, tipo, pais, ciudad, nodo, hop, equipos, grupo_id, demo, creado, actualizado, autor) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cliente_id, d["codigo"], d["alias"], d["tipo"], d["pais"], d["ciudad"], d["nodo"], d["hop"],
             json.dumps(d["equipos"]), d.get("grupo_id"), 1 if demo else 0, ahora(), ahora(), quien)
    auditar(quien, cliente_id, "crear sitio edge", d["codigo"], {"demo": demo})
    return sid


def sembrar_demo_lab(cliente_id, quien="faceops"):
    """UN sitio de laboratorio marcado «demo» que agrupa máquinas existentes del lab (no crea infraestructura). Idempotente."""
    _tabla()
    if q1("SELECT id FROM edge_sitios WHERE cliente_id=? AND demo=1", cliente_id):
        sembrar_grupos_demo(cliente_id, quien)
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
    return sid


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


def diagrama(s, eqs, m, pcts):
    """Central (FaceOps → AAP), hop si aplica y, dentro del sitio, el execution node y los equipos REALES del inventario del
    sitio agrupados por clase, cada uno con su % y su color de salud. Nada inventado: sin equipos, el sitio va vacío."""
    from .salud import tono_salud
    W, BW, BH, GAP, POR_FILA = 960, 170, 58, 12, 5
    cajas, lineas, textos = [], [], []

    def caja(x, y, w, h, tit, sub, pct=None, url=None, tono=None, mono=True, info=""):
        t = tono or tono_salud(pct)
        nt, ns = int((w - (20 if tono else 58)) / (7.2 if mono else 7.6)), int((w - 18) / 5.9)   # caben según el ancho de la caja
        cajas.append({"x": x, "y": y, "w": w, "h": h, "tit": tit if len(tit) <= nt else tit[:nt - 1] + "…", "tit_full": tit,
                      "sub": sub if len(sub) <= ns else sub[:ns - 1] + "…", "pct": pct,
                      "tono": t, "url": url, "mono": mono, "info": info})
        return x + w / 2, y, y + h

    def nodo_pct(nombre):
        _, _, listo = estado_nodo(m, nombre)
        return None if listo is None else (100 if listo else 0)
    cx = W / 2
    _, _, c_bot = caja(cx - 160, 14, 320, BH, "Central · FaceOps → AAP", "controller · acciones con ticket y aprobación", tono="central", mono=False)
    y = c_bot + 52
    sube = c_bot
    if s["hop"]:
        hp = nodo_pct(s["hop"])
        t_, _, _ = estado_nodo(m, s["hop"])
        _, h_top, h_bot = caja(cx - 150, y, 300, BH, s["hop"], f"hop node · DMZ · {t_}", hp, mono=True)
        lineas.append({"x1": cx, "y1": h_top, "x2": cx, "y2": sube, "tunel": True, "txt": "malla 27199/tcp"})
        sube, y = h_bot, h_bot + 52
    marco_y = y
    y += 34
    en_p = nodo_pct(s["nodo"])
    t_en, _, _ = estado_nodo(m, s["nodo"])
    _, en_top, en_bot = caja(cx - 150, y, 300, BH, s["nodo"] or "sin execution node", f"execution node · {t_en}", en_p, mono=True)
    lineas.append({"x1": cx, "y1": en_top, "x2": cx, "y2": sube, "tunel": True, "txt": "malla 27199/tcp · la inicia el sitio"})
    y = en_bot + 46
    for k, nom in SECC_DIAG:
        grupo_ = [e for e in eqs if clase_diag(e) == k]
        if not grupo_:
            continue
        textos.append({"x": 40, "y": y - 8, "txt": f"{nom} · {len(grupo_)}"})
        for i0 in range(0, len(grupo_), POR_FILA):
            fila = grupo_[i0:i0 + POR_FILA]
            x0 = cx - (len(fila) * BW + (len(fila) - 1) * GAP) / 2
            for n, e in enumerate(fila):
                win = (e.get("so_familia") or "").lower() == "windows" or "windows" in (e.get("so") or "").lower()
                proto = "WinRM 5986" if win else "SSH 22"
                pc = pcts.get(e["id"])
                bx = x0 + n * (BW + GAP)
                caja(bx, y, BW, BH, e["nombre"], f"{e.get('ip') or 'sin IP'} · {proto}", pc, url=url_for("consola.equipo", eid=e["id"]),
                     info=f"{e['nombre']} · {e.get('categoria') or 'servidor'} · {e.get('so') or ''} · salud {pc if pc is not None else '—'} %")
                lineas.append({"x1": cx, "y1": en_bot, "x2": bx + BW / 2, "y2": y, "tunel": False, "txt": ""})
            y += BH + GAP
        y += 34
    if not eqs:
        textos.append({"x": cx, "y": y, "txt": "El sitio aún no tiene equipos en el inventario", "centro": True})
        y += 30
    marco = {"x": 16, "y": marco_y, "w": W - 32, "h": y - marco_y - 10, "txt": f"Sitio {s['codigo']}" + (f" · {s['alias']}" if s.get("alias") else "")}
    return {"w": W, "h": y + 6, "cajas": cajas, "lineas": lineas, "textos": textos, "marco": marco}


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
    s["equipos_l"] = j(s["equipos"], []) or []
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
    return render_template("edge_sitio.html", s=s, diag=diagrama(s, eqs, m, salud_equipos(eqs)), grupo_s=gr_, grupos=grupos(g.cliente["id"]), eqs=eqs, radar_s=r, nodo_t=nodo_t, nodo_c=nodo_c, hop_t=hop_t, hop_c=hop_c, m=m,
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
    s["equipos_l"] = j(s["equipos"], []) or []
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
