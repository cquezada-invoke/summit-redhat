"""Ficha de nube › máquinas y productos: crear máquinas con la lógica de la nube (GCP), operarlas (encender, apagar,
reiniciar, redimensionar, discos, etiquetas, snapshot, firewall, eliminar) y agregar productos adicionales (Cloud SQL,
buckets, GKE, Pub/Sub…) desde un catálogo estilo OperatorHub (datos/nube-productos.yml).

Todo pasa por AAP con ticket y aprobación y con la credencial cifrada de la conexión de la nube; FaceOps nunca toca la
nube directo. Las opciones (regiones, zonas, tipos con precio, imágenes, redes, cuentas de servicio) y los permisos que
le faltan a la cuenta de servicio se leen con «Nube · Leer opciones de la nube» (solo lectura, sin ticket) y quedan en
caché. El consumo de cada máquina y producto se lee de la fuente de «Nube · Recuperar consumo y estado» (nube_consumo):
aquí no se calcula."""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import yaml
from flask import abort, g, request, session, url_for
from markupsafe import Markup

from . import requiere_login
from .bd import ahora, auditar, con, ej, j, q, q1
from .vistas import bp, equipos_visibles

CREAR = "Nube · Crear máquina"
OPERAR = "Nube · Operar máquina"
PRODUCTO = "Nube · Producto de la nube"
OPCIONES = "Nube · Leer opciones de la nube"
ACCIONES_AAP = (CREAR, OPERAR, PRODUCTO)
TTL_OPCIONES_H = 6
ACCIONES_MAQ = {"encender": "Encender", "apagar": "Apagar", "reiniciar": "Reiniciar", "redimensionar": "Cambiar tipo de máquina",
                "disco_agregar": "Agregar disco", "disco_ampliar": "Ampliar disco", "etiquetas": "Cambiar etiquetas", "snapshot": "Snapshot",
                "firewall": "Regla de firewall", "eliminar": "Eliminar máquina"}
# Imágenes cuyo camino completo (crear, vincular, inventariar) no se ha probado de punta a punta: quedan sin certificar
SIN_CERTIFICAR_IMAGEN = {"windows": "Windows: la creación y su vínculo por WinRM aún no pasan su prueba real (sin certificar)"}
_RUTA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "nube-productos.yml")
_cache = {}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS nube_opciones (cliente_id INTEGER, infra_id INTEGER, ts TEXT, datos TEXT, PRIMARY KEY (cliente_id, infra_id));
CREATE TABLE IF NOT EXISTS nube_opciones_trabajos (id INTEGER PRIMARY KEY, cliente_id INTEGER, infra_id INTEGER, job INTEGER, creado TEXT, estado TEXT DEFAULT 'en curso');
CREATE TABLE IF NOT EXISTS nube_productos (id INTEGER PRIMARY KEY, cliente_id INTEGER, infra_id INTEGER, producto TEXT, nombre TEXT, region TEXT,
  zona TEXT, parametros TEXT, estado TEXT, ticket_id INTEGER, creado TEXT, salud TEXT DEFAULT '', salud_ts TEXT DEFAULT '', costo_usd REAL);
CREATE TABLE IF NOT EXISTS nube_productos_cert (proveedor TEXT, producto TEXT, huella TEXT, tickets TEXT, fecha TEXT, por TEXT, PRIMARY KEY (proveedor, producto));
"""
_listo = set()


def _esquema():
    if "ok" not in _listo:
        con().executescript(ESQUEMA)
        _listo.add("ok")


# ------------------------------------------------------------------ catálogo (datos/nube-productos.yml)
def catalogo(tipo):
    m = os.path.getmtime(_RUTA)
    if _cache.get("m") != m:
        _cache.update(m=m, d=yaml.safe_load(open(_RUTA, encoding="utf-8")) or {})
    return ((_cache["d"].get("proveedores") or {}).get(tipo)) or {}


def productos(tipo):
    return [dict(p, categoria=c["nombre"]) for c in catalogo(tipo).get("categorias") or [] for p in c.get("productos") or []]


def producto_def(tipo, pid):
    return next((p for p in productos(tipo) if p["id"] == pid), None)


def huella_producto(p):
    """Huella de la definición del producto (formulario y pasos): si cambia, su certificación deja de valer."""
    import hashlib
    d = {k: p.get(k) for k in ("campos", "crear", "estado", "eliminar", "patron_nombre")}
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def certificado_producto(tipo, p):
    """(certificado, motivo). Los caros solo validan su formulario: quedan sin certificar."""
    _esquema()
    if os.environ.get("FACEOPS_MAQUETA") == "1":
        return True, ""
    if p.get("caro"):
        return False, "Sin certificar: por su costo, su prueba solo valida el formulario (no crea recursos)"
    r = q1("SELECT * FROM nube_productos_cert WHERE proveedor=? AND producto=?", tipo, p["id"])
    if not r:
        return False, "Sin certificar: aún no pasa su prueba real (crear, verificar y eliminar)"
    if r["huella"] != huella_producto(p):
        return False, "Sin certificar: su definición cambió desde la última prueba"
    return True, ""


def certificar_producto(tipo, pid, tickets, por):
    """Lo llama el arnés de prueba cuando el producto pasó crear → salud → eliminar → verificar que no quedó."""
    _esquema()
    p = producto_def(tipo, pid)
    ej("INSERT OR REPLACE INTO nube_productos_cert(proveedor, producto, huella, tickets, fecha, por) VALUES (?,?,?,?,?,?)",
       tipo, pid, huella_producto(p), json.dumps(tickets), ahora(), por)


def permisos_requeridos(tipo):
    cat = catalogo(tipo)
    ps = {p_ for v in ((cat.get("maquinas") or {}).get("acciones") or {}).values() for p_ in v}
    ps |= {p_ for p in productos(tipo) for p_ in p.get("permisos") or []}
    return sorted(ps)


def rol_de(tipo, permiso):
    m = catalogo(tipo).get("roles_por_permiso") or {}
    k = max((k for k in m if permiso == k or (k.endswith(".") and permiso.startswith(k))), key=len, default=None)
    return m[k] if k else f"un rol con {permiso}"


def falta_rol(tipo, op, permisos):
    """Motivo «falta el rol X en la cuenta de servicio» si la última lectura dice que faltan permisos; '' si no."""
    falt = (op or {}).get("permisos_faltan")
    if not falt:
        return ""
    roles = sorted({rol_de(tipo, p_) for p_ in permisos if p_ in falt})
    return ("falta el rol " + ", ".join(roles) + " en la cuenta de servicio") if roles else ""


# ------------------------------------------------------------------ nube y opciones (caché)
def _nube(iid):
    from .infraestructuras import lista
    x = next((i for i in lista(g.cliente["id"], equipos_visibles()) if i["id"] == iid and i["ambito"] == "nube"), None)
    if not x:
        abort(404)
    return x


def _det(inf):
    return inf.get("detalle_d") or (j(inf.get("detalle"), {}) if isinstance(inf.get("detalle"), str) else inf.get("detalle") or {})


def _region(inf):
    d = _det(inf)
    z = d.get("zona") or ""
    return d.get("region") if d.get("region") and "-" in str(d.get("region")) else (z.rsplit("-", 1)[0] if z.count("-") >= 2 else "")


def opciones(cid, iid):
    _esquema()
    r = q1("SELECT * FROM nube_opciones WHERE cliente_id=? AND infra_id=?", cid, iid)
    if not r:
        return None
    d = j(r["datos"], {}) or {}
    d["_ts"] = r["ts"]
    return d


def credencial(aap, inf):
    """Id en AAP de la credencial cifrada de la conexión de la nube (el secreto nunca sale de AAP)."""
    from .aap import ErrorAAP
    cr = (aap.get("credentials/", name=inf.get("credencial") or "").get("results") or [None])[0] if inf.get("credencial") else None
    if not cr:
        raise ErrorAAP(f"{inf['nombre']} no tiene su credencial en AAP: usa «Conectar» en la ficha de la nube")
    return cr["id"]


def _lista_auto(cid, nombre):
    from .mod_nubes import _accion_lista
    return _accion_lista(cid, nombre)


def lanzar_opciones(c, inf, regiones=None, prueba=False):
    """Lanza la lectura (solo lectura, sin ticket) con la credencial de la conexión. Devuelve (job, motivo)."""
    from .aap import AAP, ErrorAAP
    _esquema()
    a, motivo = _lista_auto(c["id"], OPCIONES)
    if motivo and not (prueba and a):
        return None, motivo
    if q1("SELECT 1 x FROM nube_opciones_trabajos WHERE cliente_id=? AND infra_id=? AND estado='en curso' AND creado>?", c["id"], inf["id"],
          (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec="seconds")):
        return None, "Ya hay una lectura en curso"
    tipo = inf["tipo"]
    if tipo != "gcp":
        return None, f"Por ahora la creación y operación de máquinas y productos está disponible para GCP ({tipo}: próximamente)"
    cat = catalogo(tipo)
    det = _det(inf)
    regs = [r for r in dict.fromkeys([_region(inf)] + list(regiones or []) + [m.get("zona", "").rsplit("-", 1)[0] for m in (opciones(c["id"], inf["id"]) or {}).get("maquinas", [])]) if r][:4]
    prods = []
    for r in q("SELECT * FROM nube_productos WHERE cliente_id=? AND infra_id=? AND estado IN ('activo','creando','fallido','eliminando')", c["id"], inf["id"]):
        p = producto_def(tipo, r["producto"])
        if p and (p.get("estado") or {}).get("url"):
            e_ = p["estado"]
            prods.append({"clave": str(r["id"]), "url": _formato(e_["url"], _valores(inf, r)), "campo": e_.get("campo"), "ok": e_.get("ok")})
    ent = {"regiones": regs, "imagenes": [{"id": i["id"], "imagen": i["imagen"]} for i in (cat.get("maquinas") or {}).get("imagenes") or []],
           "permisos": permisos_requeridos(tipo), "productos": prods}
    try:
        aap = AAP.de_cliente(c)
        job = aap.lanzar("job", a["aap_id"], extra_vars={"nb_infra_id": inf["id"], "nb_proyecto": det.get("proyecto", ""), "nb_entrada": ent},
                         credenciales=[credencial(aap, inf)])
    except ErrorAAP as e:
        return None, f"No se pudo lanzar la lectura en AAP: {str(e)[:200]}"
    ej("INSERT INTO nube_opciones_trabajos(cliente_id, infra_id, job, creado) VALUES (?,?,?,?)", c["id"], inf["id"], job, ahora())
    return job, ""


def recoger_opciones(c, iid):
    """Recoge las lecturas terminadas (se llama al abrir la ficha o al consultar su estado)."""
    from .aap import AAP, ErrorAAP
    _esquema()
    pend = q("SELECT * FROM nube_opciones_trabajos WHERE cliente_id=? AND infra_id=? AND estado='en curso'", c["id"], iid)
    if not pend:
        return
    try:
        aap = AAP.de_cliente(c)
    except ErrorAAP:
        return
    for t in pend:
        try:
            jb = aap.get(f"jobs/{t['job']}/")
        except ErrorAAP:
            continue
        st = jb.get("status")
        if st in ("pending", "waiting", "running", "new"):
            continue
        d = (jb.get("artifacts") or {}).get("faceops_nube_opciones")
        if st == "successful" and isinstance(d, dict) and d.get("ok"):
            d.pop("cuenta_servicio", None)
            ej("INSERT OR REPLACE INTO nube_opciones(cliente_id, infra_id, ts, datos) VALUES (?,?,?,?)", c["id"], iid, ahora(), json.dumps(d, ensure_ascii=False))
            for k_, s_ in (d.get("productos") or {}).items():   # salud de cada producto creado por FaceOps
                ej("UPDATE nube_productos SET salud=?, salud_ts=?, estado=CASE WHEN ?='no existe' AND estado='activo' THEN 'eliminado' ELSE estado END "
                   "WHERE id=? AND cliente_id=?", s_.get("salud", ""), ahora(), s_.get("salud", ""), int(k_), c["id"])
        else:
            auditar("sistema", c["id"], "lectura de opciones de nube sin resultado", str(iid), {"job": t["job"], "estado": st,
                                                                                               "error": (d or {}).get("error", "") if isinstance(d, dict) else ""})
        ej("UPDATE nube_opciones_trabajos SET estado=? WHERE id=?", "ok" if st == "successful" else "falló", t["id"])


def vigente(op):
    if not op or not op.get("_ts"):
        return False
    try:
        return datetime.fromisoformat(op["_ts"].replace("Z", "+00:00")) > datetime.now(timezone.utc) - timedelta(hours=TTL_OPCIONES_H)
    except ValueError:
        return False


def _caducar(cid, iid):
    ej("UPDATE nube_opciones SET ts='2000-01-01T00:00:00+00:00' WHERE cliente_id=? AND infra_id=?", cid, iid)


# ------------------------------------------------------------------ costos estimados y consumo (de la fuente de nube_consumo)
def _precio_region(cat, op, region):
    """(precio_hora por familia [vcpu, gb], disco {tipo: gb-mes}, ip hora, fuente)."""
    mq = cat.get("maquinas") or {}
    leido = ((op or {}).get("precios") or {}).get(region) or {}
    f = (mq.get("factor_region") or {}).get(region, 1.15)
    base = {k: [v[0] * f, v[1] * f] for k, v in (mq.get("precio_hora") or {}).items()}
    fam = {k: v for k, v in leido.items() if isinstance(v, list)} or {}
    disco = {d["id"]: d["usd_gb_mes"] * f for d in mq.get("discos") or []}
    disco.update({k: v for k, v in (leido.get("disco") or {}).items() if v})
    fuente = "precios de lista de la región (Cloud Billing)" if fam else f"precios de lista us-central1 × {f:.2f} ({region or 'región'})"
    return dict(base, **fam), disco, leido.get("ip") or (mq.get("ip_publica_hora") or 0.005) * (1 if leido else f), fuente


def costo_maquina(tipo_nube, op, region, tipo, imagen_id, disco_tipo, disco_gb, publica):
    cat = catalogo(tipo_nube)
    mq = cat.get("maquinas") or {}
    tipos = dict({k: {"cpu": v[0], "ram": v[1]} for k, v in (mq.get("tipos") or {}).items()}, **((op or {}).get("tipos") or {}))
    t = tipos.get(tipo)
    if not t:
        return None
    ph, disco, ip_h, fuente = _precio_region(cat, op, region)
    fam = tipo.split("-")[0]
    h = mq.get("horas_mes") or 730
    if tipo in (mq.get("precio_hora_compartido") or {}) and fam not in ((op or {}).get("precios") or {}).get(region, {}):
        f = (mq.get("factor_region") or {}).get(region, 1.15)
        comp = mq["precio_hora_compartido"][tipo] * f
    else:
        cpu = {"e2-micro": 0.25, "e2-small": 0.5, "e2-medium": 1}.get(tipo, t["cpu"])
        pr = ph.get(fam)
        if not pr:
            return None
        comp = cpu * pr[0] + t["ram"] * pr[1]
    im = next((i for i in mq.get("imagenes") or [] if i["id"] == imagen_id), {})
    lic = mq.get("licencia_hora") or {}
    lic_h = (lic.get("windows_por_vcpu", 0) * max(1, t["cpu"]) if im.get("licencia") == "windows"
             else (lic.get("rhel_hasta_4", 0) if t["cpu"] <= 4 else lic.get("rhel_mas_de_4", 0)) if im.get("licencia") == "rhel" else 0)
    det = {"Cómputo": comp * h, "Licencia": lic_h * h, "Disco": disco.get(disco_tipo, 0.1) * float(disco_gb or 0), "IP pública": ip_h * h if publica else 0}
    return {"usd_mes": round(sum(det.values()), 2), "detalle": {k: round(v, 2) for k, v in det.items() if v}, "fuente": fuente,
            "cpu": t["cpu"], "ram": t["ram"]}


def costo_producto(p, valores):
    c = p.get("costo") or {}
    tot = float(c.get("fijo") or 0)
    for campo, tabla in (c.get("por") or {}).items():
        tot += float((tabla or {}).get(str(valores.get(campo, "")), 0) or 0)
    u = c.get("por_unidad")
    if u:
        try:
            tot += float(valores.get(u["campo"]) or 0) * float(u["usd"])
        except (TypeError, ValueError):
            pass
    return {"usd_mes": round(tot, 2), "nota": c.get("nota", "")}


def consumo(cid, iid, nombre):
    """Consumo del mes en curso hasta la última muestra (USD) y la hora de esa muestra, leído de la fuente del consumo de la
    nube (nube_consumo). None si aún no hay muestra: la ficha muestra «sin muestra» enlazado a esa fuente."""
    try:
        from .nube_consumo import costo_de
        r = costo_de(cid, nombre, iid)
    except Exception:  # noqa: BLE001 - la fuente aún no existe o no tiene el dato
        return None
    if not r or r.get("mes_actual_usd") is None:
        return None
    from .hora_local import html
    ts = r.get("muestra_ts") or ""
    return {"usd": r["mes_actual_usd"], "ts": ts, "fuente": r.get("fuente") or "",
            "html": str(Markup(f"{usd(r['mes_actual_usd'])} <span class=\"sub\">· muestra {html(ts)}{' · estimado' if r.get('fuente') == 'estimado' else ''}</span>"))}


def usd(v):
    """«USD 1.234,56» (separador chileno)."""
    if v is None:
        return "—"
    return "USD " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------------ bloqueos (regla de prueba, perfil y permisos)
def bloqueo(cid, tipo, op, accion):
    """Motivo por el que la acción no se puede usar ('' si se puede). accion: crear, una de ACCIONES_MAQ o producto:<id>."""
    if g.perfil["modo"] != "escritura":
        return "Tu perfil es de solo lectura"
    if tipo != "gcp":
        return f"Disponible por ahora solo en GCP ({tipo}: próximamente)"
    nombre = CREAR if accion == "crear" else PRODUCTO if accion.startswith("producto:") else OPERAR
    _a, mot = _lista_auto(cid, nombre)
    if mot:
        return mot
    cat = catalogo(tipo)
    if accion.startswith("producto:"):
        p = producto_def(tipo, accion.split(":", 1)[1])
        if not p:
            return "Producto desconocido"
        ok, mot = certificado_producto(tipo, p)
        if not ok:
            return mot
        perms = p.get("permisos") or []
        if op and op.get("apis") is not None and p.get("servicio") and p["servicio"] not in op["apis"]:
            return f"La API {p['servicio']} no está habilitada en el proyecto"
    else:
        perms = ((cat.get("maquinas") or {}).get("acciones") or {}).get(accion) or []
    if accion == "crear" and not op:
        return "Aún no se leen las opciones de la nube (regiones, tipos e imágenes): usa «Actualizar opciones»"
    f_ = falta_rol(tipo, op, perms)
    return f_[0].upper() + f_[1:] if f_ else ""


# ------------------------------------------------------------------ contexto de la ficha
def _tickets_nube(cid, iid, dias=30):
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat(timespec="seconds")
    out = []
    for t in q("SELECT t.id, t.numero, t.estado, t.parametros, t.creado, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
               "WHERE t.cliente_id=? AND a.nombre_aap IN (?,?,?) AND t.creado>? ORDER BY t.id DESC", cid, *ACCIONES_AAP, desde):
        p = j(t["parametros"], {})
        if str(p.get("nb_infra_id")) != str(iid):
            continue
        out.append(dict(t, p=p))
    return out


def estado_ticket(t):
    e = t["estado"]
    if e in ("pendiente_aprobacion", "en_cola", "en_ejecucion", "pendiente"):
        return "curso", "En curso"
    if e == "exitoso":
        return "ok", "Aplicada"
    if e in ("rechazado", "cancelado"):
        return "nd", e.capitalize()
    return "mal", "Falló"


def contexto(x, maqs):
    """Lo que necesita _nube_recursos.html: catálogo con bloqueos y costos, productos creados, operaciones por máquina,
    opciones leídas y los datos para los formularios."""
    if not x or x.get("ambito") != "nube":
        return None
    _esquema()
    cid, tipo = g.cliente["id"], x["tipo"]
    try:
        recoger_opciones(g.cliente, x["id"])
    except Exception:  # noqa: BLE001 - la ficha se ve igual con la caché anterior
        pass
    op = opciones(cid, x["id"])
    lect = q1("SELECT * FROM nube_opciones_trabajos WHERE cliente_id=? AND infra_id=? ORDER BY id DESC LIMIT 1", cid, x["id"])
    if tipo == "gcp" and x.get("credencial") and not vigente(op) and not (lect and lect["estado"] == "en curso"):
        try:   # lectura en segundo plano (solo lectura); la próxima visita ya la tiene
            lanzar_opciones(g.cliente, dict(x, detalle=json.dumps(_det(x))))
            lect = q1("SELECT * FROM nube_opciones_trabajos WHERE cliente_id=? AND infra_id=? ORDER BY id DESC LIMIT 1", cid, x["id"])
        except Exception:  # noqa: BLE001
            pass
    cat = catalogo(tipo)
    tks = _tickets_nube(cid, x["id"])
    por_maq = {}
    for t in tks:
        if t["nombre_aap"] in (OPERAR, CREAR):
            n = (t["p"].get("nb_entrada") or {}).get("maquina") or t["p"].get("nm_nombre")
            if n and n not in por_maq:
                cls, txt = estado_ticket(t)
                ac = (t["p"].get("nb_entrada") or {}).get("accion") or "crear"
                por_maq[n] = {"id": t["id"], "numero": t["numero"], "cls": cls, "txt": txt, "accion": ACCIONES_MAQ.get(ac, "Crear máquina"),
                              "url": url_for("consola.ticket", tid=t["id"])}
    bloq = {a: bloqueo(cid, tipo, op, a) for a in ["crear"] + list(ACCIONES_MAQ)}
    cats = []
    for c in cat.get("categorias") or []:
        ps = []
        for p in c.get("productos") or []:
            cert, mot_c = certificado_producto(tipo, p)
            ps.append({"id": p["id"], "nombre": p["nombre"], "resumen": p.get("resumen", ""), "que_es": p.get("que_es", ""), "para_que": p.get("para_que") or [],
                       "precios": p.get("precios") or [], "limites": p.get("limites") or [], "doc": p.get("doc", ""), "caro": bool(p.get("caro")),
                       "campos": p.get("campos") or [], "patron_nombre": p.get("patron_nombre") or "^[a-z]([-a-z0-9]{1,61}[a-z0-9])$",
                       "costo": p.get("costo") or {}, "certificado": cert, "bloqueo": bloqueo(cid, tipo, op, "producto:" + p["id"]),
                       "servicio": p.get("servicio", ""), "sin_eliminar": bool(p.get("sin_eliminar")), "categoria": c["nombre"]})
        cats.append({"nombre": c["nombre"], "productos": ps})
    creados = []
    for r in q("SELECT * FROM nube_productos WHERE cliente_id=? AND infra_id=? AND estado!='eliminado' ORDER BY id DESC", cid, x["id"]):
        p = producto_def(tipo, r["producto"]) or {"nombre": r["producto"]}
        t = next((t for t in tks if t["nombre_aap"] == PRODUCTO and (t["p"].get("nb_entrada") or {}).get("nombre") == r["nombre"]), None)
        cls, txt = estado_ticket(t) if t else ("nd", "")
        creados.append(dict(r, nombre_producto=p["nombre"], ticket=t and {"numero": t["numero"], "url": url_for("consola.ticket", tid=t["id"]), "id": t["id"], "cls": cls, "txt": txt,
                                                                      "accion": (t["p"].get("nb_entrada") or {}).get("accion", "")},
                            consumo=consumo(cid, x["id"], r["nombre"]), sin_eliminar=bool(p.get("sin_eliminar")),
                            bloqueo_el=bloqueo(cid, tipo, op, "producto:" + r["producto"]) if not p.get("caro") else ""))
    consumo_maq = {m["nombre"]: consumo(cid, x["id"], m["nombre"]) for m in maqs}
    mq = cat.get("maquinas") or {}
    faltan_roles = sorted({rol_de(tipo, p_) for p_ in (op or {}).get("permisos_faltan") or []})
    det = _det(x)
    datos_js = {"region": _region(x), "zona": det.get("zona", ""), "red": det.get("red") or det.get("vpc") or "default",
                "regiones": (op or {}).get("regiones") or [_region(x)], "zonas": (op or {}).get("zonas") or {},
                "tipos": (op or {}).get("tipos") or {}, "tipos_ref": mq.get("tipos") or {}, "familias": mq.get("familias") or [],
                "imagenes": [dict(i, ok=((op or {}).get("imagenes") or {}).get(i["id"], {}).get("ok", True), bloqueo=SIN_CERTIFICAR_IMAGEN.get(i["familia"], ""))
                             for i in mq.get("imagenes") or []],
                "discos": mq.get("discos") or [], "redes": (op or {}).get("redes") or [], "subredes": (op or {}).get("subredes") or [],
                "cuentas": (op or {}).get("cuentas") or [], "firewall": (op or {}).get("firewall") or [],
                "maquinas": {m["nombre"]: m for m in (op or {}).get("maquinas") or []}, "snapshots": (op or {}).get("snapshots") or [],
                "regiones_nombre": mq.get("regiones_nombre") or {}, "bloqueos": bloq, "categorias": cats}
    return {"op": op, "lectura": lect, "bloq": bloq, "categorias": cats, "creados": creados, "por_maq": por_maq, "consumo_maq": consumo_maq,
            "faltan_roles": faltan_roles, "datos_js": datos_js, "n_productos": sum(len(c["productos"]) for c in cats), "proveedor": cat.get("nombre", tipo)}


@bp.app_context_processor
def _ctx_nube_recursos():
    return {"nube_recursos_ctx": contexto}


# ------------------------------------------------------------------ validación de formularios
NOMBRE_VM = re.compile(r"^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$")
ETIQ_K = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
ETIQ_V = re.compile(r"^[a-z0-9_-]{0,63}$")


def _etiquetas(txt):
    """«clave=valor» por línea o separados por coma → dict validado (reglas de etiquetas de GCP)."""
    out = {}
    for par in re.split(r"[\n,]+", txt or ""):
        par = par.strip()
        if not par:
            continue
        k, _, v = par.partition("=")
        k, v = k.strip().lower(), v.strip().lower()
        if not ETIQ_K.match(k) or not ETIQ_V.match(v):
            raise ValueError(f"Etiqueta no válida «{par}»: clave y valor en minúsculas, números, guion o guion bajo (clave empieza con letra)")
        out[k] = v
    if len(out) > 60:
        raise ValueError("Máximo 60 etiquetas")
    return out


def _etiquetas_red(txt):
    out = [t.strip().lower() for t in re.split(r"[\s,]+", txt or "") if t.strip()]
    for t in out:
        if not NOMBRE_VM.match(t):
            raise ValueError(f"Etiqueta de red no válida «{t}»")
    return list(dict.fromkeys(out))[:64]


def validar_crear(inf, op, f):
    """Formulario «Crear máquina» → parámetros del ticket (o ValueError con el motivo)."""
    cat = catalogo(inf["tipo"])
    mq = cat.get("maquinas") or {}
    nombre = (f.get("nombre") or "").strip().lower()
    if not NOMBRE_VM.match(nombre):
        raise ValueError("Nombre: minúsculas, números y guion; empieza con letra (hasta 63)")
    if nombre in ((op or {}).get("maquinas") and {m["nombre"] for m in op["maquinas"]} or set()):
        raise ValueError(f"Ya existe una máquina {nombre} en la nube")
    region, zona = f.get("region", ""), f.get("zona", "")
    zonas = (op or {}).get("zonas") or {}
    if zonas and zona not in zonas.get(region, []):
        raise ValueError("Elige una zona de la región")
    tipo = f.get("tipo", "")
    tipos = (op or {}).get("tipos") or {}
    t = tipos.get(tipo) or ({"cpu": mq["tipos"][tipo][0], "ram": mq["tipos"][tipo][1]} if tipo in (mq.get("tipos") or {}) and not tipos else None)
    if not t:
        raise ValueError("Elige un tipo de máquina de la lista")
    if tipos and zona not in (tipos[tipo].get("zonas") or [zona]):
        raise ValueError(f"{tipo} no está disponible en {zona}")
    im = next((i for i in mq.get("imagenes") or [] if i["id"] == f.get("imagen")), None)
    if not im:
        raise ValueError("Elige una imagen")
    if SIN_CERTIFICAR_IMAGEN.get(im["familia"]):
        raise ValueError(SIN_CERTIFICAR_IMAGEN[im["familia"]])
    if t["ram"] < (im.get("ram_min") or 0):
        raise ValueError(f"{im['nombre']} necesita al menos {im['ram_min']} GB de RAM")
    try:
        gb = int(f.get("disco_gb") or 0)
    except ValueError:
        gb = 0
    if gb < max(10, im.get("disco_min") or 10) or gb > 2000:
        raise ValueError(f"Disco: entre {max(10, im.get('disco_min') or 10)} y 2000 GB para {im['nombre']}")
    dt = next((d for d in mq.get("discos") or [] if d["id"] == f.get("disco_tipo")), None)
    if not dt or (dt.get("familias") and tipo.split("-")[0] not in dt["familias"]):
        raise ValueError("Tipo de disco no válido para esa familia")
    if tipo.split("-")[0] in ("n4",) and dt["id"] != "hyperdisk-balanced":
        raise ValueError("La familia N4 solo admite Hyperdisk")
    red, sub = f.get("red", ""), f.get("subred", "")
    if not NOMBRE_VM.match(red) or (sub and not NOMBRE_VM.match(sub)):
        raise ValueError("Elige la red y la subred")
    if (op or {}).get("subredes") and sub and not any(s["nombre"] == sub and s["red"] == red and s["region"] == region for s in op["subredes"]):
        raise ValueError(f"La subred {sub} no es de la red {red} en {region}")
    cuenta = (f.get("cuenta") or "").strip()
    if cuenta and not re.match(r"^[a-z0-9-]+@[a-z0-9.-]+\.gserviceaccount\.com$", cuenta):
        raise ValueError("Cuenta de servicio no válida")
    publica = f.get("publica") in ("1", "si", "on", "true")
    amb = f.get("ambiente") if f.get("ambiente") in ("produccion", "qa", "desarrollo") else "desarrollo"
    costo = costo_maquina(inf["tipo"], op, region, tipo, im["id"], dt["id"], gb, publica)
    det = _det(inf)
    return {"nb_infra_id": inf["id"], "nm_nombre": nombre, "nm_zona": zona, "nm_tipo": tipo, "nm_imagen": im["imagen"], "nm_so": im["so"],
            "nm_familia_so": im["familia"], "nm_disco_gb": gb, "nm_disco_tipo": dt["id"], "nm_red": red, "nm_subred": sub or red,
            "nm_publica": publica, "nm_etiquetas": _etiquetas(f.get("etiquetas")), "nm_etiquetas_red": _etiquetas_red(f.get("etiquetas_red")),
            "nm_cuenta": cuenta, "hab_ambiente": amb, "hab_destino": "gcp", "hab_so": im["so"], "hab_infraestructura": inf["nombre"],
            "hab_ubicacion": inf.get("ubicacion") or "", "hab_gcp_proyecto": det.get("proyecto", ""),
            "nm_costo_usd_mes": (costo or {}).get("usd_mes")}


def validar_operar(inf, op, maquina, accion, f):
    if accion not in ACCIONES_MAQ:
        raise ValueError("Acción no válida")
    m = ((op or {}).get("maquinas") and {x["nombre"]: x for x in op["maquinas"]}.get(maquina))
    if not m:
        raise ValueError(f"{maquina} no aparece en la última lectura de la nube: usa «Actualizar opciones»")
    e = {"accion": accion, "maquina": maquina, "zona": m["zona"]}
    if accion == "redimensionar":
        tipo = f.get("tipo", "")
        tipos = (op or {}).get("tipos") or {}
        if tipo not in tipos or m["zona"] not in tipos[tipo].get("zonas", []):
            raise ValueError(f"Elige un tipo disponible en {m['zona']}")
        if m["estado"] != "TERMINATED" and f.get("detener") not in ("1", "on", "si"):
            raise ValueError("Cambiar el tipo requiere apagar la máquina: confirma el apagado")
        e.update(tipo=tipo, detener=True)
    elif accion == "disco_agregar":
        dt = f.get("disco_tipo", "pd-balanced")
        if dt not in {d["id"] for d in (catalogo(inf["tipo"]).get("maquinas") or {}).get("discos") or []}:
            raise ValueError("Tipo de disco no válido")
        gb = int(f.get("gb") or 0)
        if gb < 10 or gb > 4000:
            raise ValueError("Disco nuevo: entre 10 y 4000 GB")
        nombre = (f.get("disco") or f"{maquina}-d{len(m.get('discos') or []) + 1}").strip().lower()[:63]
        if not NOMBRE_VM.match(nombre):
            raise ValueError("Nombre de disco no válido")
        e["disco"] = {"nombre": nombre, "gb": gb, "tipo": dt}
    elif accion == "disco_ampliar":
        d = next((d for d in m.get("discos") or [] if d["nombre"] == f.get("disco")), None)
        gb = int(f.get("gb") or 0)
        if not d:
            raise ValueError("Elige un disco de la máquina")
        if gb <= int(d["gb"] or 0) or gb > 64000:
            raise ValueError(f"Un disco solo se amplía: más de {d['gb']} GB")
        e["disco"] = {"nombre": d["nombre"], "gb": gb}
    elif accion == "etiquetas":
        e["etiquetas"] = dict(_etiquetas(f.get("etiquetas")), origen=(m.get("etiquetas") or {}).get("origen", "faceops"))
        e["etiquetas_red"] = _etiquetas_red(f.get("etiquetas_red"))
    elif accion == "snapshot":
        d = next((d for d in m.get("discos") or [] if d["nombre"] == f.get("disco")), None)
        if not d:
            raise ValueError("Elige el disco del snapshot")
        e["snapshot"] = {"disco": d["nombre"], "nombre": f"{d['nombre'][:40]}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"}
    elif accion == "firewall":
        modo = "quitar" if f.get("modo") == "quitar" else "agregar"
        nom = re.sub(r"[^a-z0-9-]", "-", (f.get("regla") or "").strip().lower())[:20].strip("-")
        if not nom:
            raise ValueError("Pon un nombre corto a la regla (p. ej. web)")
        regla = f"fo-{maquina}-{nom}"[:63].rstrip("-")
        e["firewall"] = {"nombre": regla, "etiqueta": regla, "modo": modo}
        if modo == "agregar":
            prot = f.get("protocolo", "tcp")
            if prot not in ("tcp", "udp", "icmp"):
                raise ValueError("Protocolo: tcp, udp o icmp")
            puertos = [p_.strip() for p_ in (f.get("puertos") or "").split(",") if p_.strip()]
            if prot != "icmp" and (not puertos or not all(re.match(r"^\d{1,5}(-\d{1,5})?$", p_) for p_ in puertos)):
                raise ValueError("Puertos: números o rangos separados por coma (p. ej. 80,443 o 8000-8080)")
            origenes = [o.strip() for o in (f.get("origenes") or "").split(",") if o.strip()]
            if not origenes or not all(re.match(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$", o) for o in origenes):
                raise ValueError("Orígenes: rangos CIDR separados por coma (p. ej. 10.0.0.0/8)")
            e["firewall"].update(protocolo=prot, puertos=puertos if prot != "icmp" else [], origenes=origenes)
    elif accion == "eliminar":
        if (f.get("confirmar") or "").strip() != maquina or f.get("entiendo") not in ("1", "on", "si"):
            raise ValueError(f"Para eliminar escribe el nombre exacto ({maquina}) y marca que entiendes que no se puede deshacer")
    return {"nb_infra_id": inf["id"], "nb_entrada": e}


def _valores(inf, r):
    det = _det(inf)
    v = dict(j(r["parametros"], {}) if isinstance(r.get("parametros"), str) else (r.get("parametros") or {}))
    v.update(proyecto=det.get("proyecto", ""), region=r.get("region") or _region(inf), zona=r.get("zona") or det.get("zona", ""),
             red=v.get("red") or det.get("red") or "default", nombre=r["nombre"])
    return v


def _formato(obj, v):
    """Reemplaza {campo} en el texto o en la estructura; un valor que es solo "{campo}" conserva su tipo."""
    if isinstance(obj, str):
        m = re.fullmatch(r"\{(\w+)\}", obj)
        if m and m.group(1) in v:
            return v[m.group(1)]
        return re.sub(r"\{(\w+)\}", lambda m_: str(v.get(m_.group(1), m_.group(0))), obj)
    if isinstance(obj, list):
        return [_formato(x, v) for x in obj]
    if isinstance(obj, dict):
        return {k: _formato(x, v) for k, x in obj.items()}
    return obj


def validar_producto(inf, op, pid, f):
    tipo = inf["tipo"]
    p = producto_def(tipo, pid)
    if not p:
        raise ValueError("Producto desconocido")
    nombre = (f.get("nombre") or "").strip().lower()
    if not re.match(p.get("patron_nombre") or r"^[a-z]([-a-z0-9]{1,61}[a-z0-9])$", nombre):
        raise ValueError(f"Nombre no válido para {p['nombre']}")
    vals = {}
    for c in p.get("campos") or []:
        v = (f.get(c["var"]) if f.get(c["var"]) not in (None, "") else c.get("defecto"))
        if c.get("requerido") and v in (None, ""):
            raise ValueError(f"Falta «{c['etiqueta']}»")
        if v in (None, ""):
            continue
        if c.get("tipo") == "number":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"«{c['etiqueta']}» debe ser un número")
            if (c.get("min") is not None and v < c["min"]) or (c.get("max") is not None and v > c["max"]):
                raise ValueError(f"«{c['etiqueta']}»: entre {c.get('min')} y {c.get('max')}")
        elif c.get("tipo") == "select" and str(v) not in [str(o) for o in c.get("opciones") or []]:
            raise ValueError(f"«{c['etiqueta']}»: elige una opción de la lista")
        elif c.get("patron") and not re.match(c["patron"], str(v)):
            raise ValueError(f"«{c['etiqueta']}» no tiene el formato esperado")
        vals[c["var"]] = v
    region = f.get("region") or _region(inf)
    if (op or {}).get("regiones") and region not in op["regiones"]:
        raise ValueError("Región no válida")
    det = _det(inf)
    zona = f.get("zona") or (det.get("zona") if (det.get("zona") or "").startswith(region) else ((op or {}).get("zonas") or {}).get(region, [""])[0])
    vals.update(red=f.get("red") or det.get("red") or "default")
    if p["id"] == "balanceador":   # máquinas del balanceador: URLs de instancias de una zona
        maqs = {m["nombre"]: m for m in (op or {}).get("maquinas") or []}
        ns = [n for n in str(vals.get("maquinas", "")).split(",") if n]
        if not ns or any(n not in maqs for n in ns) or len({maqs[n]["zona"].rsplit("-", 1)[0] for n in ns}) != 1:
            raise ValueError("Máquinas del balanceador: nombres existentes en la nube y de una misma región")
        region = maqs[ns[0]]["zona"].rsplit("-", 1)[0]
        vals["_maquinas_urls"] = [f"projects/{det.get('proyecto')}/zones/{maqs[n]['zona']}/instances/{n}" for n in ns]
    if q1("SELECT 1 x FROM nube_productos WHERE cliente_id=? AND infra_id=? AND producto=? AND nombre=? AND estado IN ('activo','creando')",
          g.cliente["id"], inf["id"], pid, nombre):
        raise ValueError(f"Ya existe {p['nombre']} «{nombre}» en esta nube")
    r = {"nombre": nombre, "region": region, "zona": zona, "parametros": vals}
    v = _valores(inf, r)
    pasos = _formato(p.get("crear") or [], v)
    est = p.get("estado") or {}
    return p, r, {"nb_infra_id": inf["id"], "nb_entrada": {"accion": "crear", "producto": pid, "nombre": nombre, "pasos": pasos,
                                                           "estado": {"url": _formato(est.get("url", ""), v), "campo": est.get("campo"), "ok": est.get("ok")},
                                                           "espera_min": 25},
                  "np_costo_usd_mes": costo_producto(p, vals)["usd_mes"]}


def params_eliminar_producto(inf, r):
    p = producto_def(inf["tipo"], r["producto"])
    v = _valores(inf, r)
    est = p.get("estado") or {}
    return {"nb_infra_id": inf["id"], "nb_entrada": {"accion": "eliminar", "producto": r["producto"], "nombre": r["nombre"],
                                                     "pasos": _formato(p.get("eliminar") or [], v),
                                                     "estado": {"url": _formato(est.get("url", ""), v), "campo": est.get("campo"), "ok": est.get("ok")},
                                                     "espera_min": 25}}


# ------------------------------------------------------------------ lanzar (desde operacion._ejecutar) y cierre del ticket
def lanzar(aap, c, a, params, amb):
    """Lanza la acción con la credencial cifrada de la conexión de la nube; con aprobación en AAP (flujo por nube)."""
    from .aap import ErrorAAP
    from .operacion import requiere_aprobacion
    inf = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND id=?", c["id"], int(params.get("nb_infra_id") or 0))
    if not inf or inf["tipo"] != "gcp":
        raise ErrorAAP("La nube de la acción no existe en FaceOps o no es GCP")
    params["nb_proyecto"] = _det(inf).get("proyecto", "")
    cr = credencial(aap, inf)
    if requiere_aprobacion(a, amb):
        return "workflow", aap.lanzar("workflow", aap.asegurar_flujo_aprobacion(a, credenciales=[cr], sufijo=inf["nombre"]), extra_vars=params)
    return "job", aap.lanzar("job", a["aap_id"], extra_vars=params, credenciales=[cr])


def al_terminar(aap, c, t, a, est, res):
    """Cierre de un ticket de máquina o producto: alta y vínculo de la máquina creada, estado en FaceOps, registro del
    producto y caducar la caché de opciones (la próxima visita relee la nube). Devuelve (estado, resultado)."""
    from .operacion import artefactos_ticket
    _esquema()
    p = j(t["parametros"], {})
    iid = int(p.get("nb_infra_id") or 0)
    art = artefactos_ticket(aap, t) if est in ("exitoso", "fallido") else {}
    if a["nombre_aap"] == CREAR and est == "exitoso":
        from .operacion import registrar_servidores, vincular_creados
        try:
            alta = registrar_servidores(aap, c, t)
            res += "\n" + alta
            ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
            vincular_creados(c, t, alta)   # Linux: bóveda por SSH; Windows: «Incorporar máquinas» (WinRM con la clave cifrada)
        except Exception as x:  # noqa: BLE001 - sin alta la máquina no queda gestionada: el ticket no puede quedar exitoso
            est = "fallido"
            res += f"\nLa máquina se creó, pero no se pudo dar de alta en AAP ni en FaceOps: {str(x)[:300]}"
            ej("UPDATE tickets SET estado=?, resultado=? WHERE id=?", est, res, t["id"])
    if a["nombre_aap"] == OPERAR:
        e_ = p.get("nb_entrada") or {}
        o = art.get("faceops_nube_operacion") or {}
        eq = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", c["id"], e_.get("maquina", ""))
        if est == "exitoso" and eq:
            if e_.get("accion") == "eliminar":
                for h in aap.get("hosts/", name=eq["nombre"], page_size=50).get("results", []):
                    try:
                        aap._r("DELETE", f"hosts/{h['id']}/")
                    except Exception:  # noqa: BLE001
                        pass
                ej("UPDATE equipos SET estado='retirado' WHERE id=?", eq["id"])
                res += f"\n{eq['nombre']} retirada del inventario de FaceOps y de AAP"
            elif o.get("estado"):
                ej("UPDATE equipos SET energia=? WHERE id=?", "encendido" if o["estado"] == "RUNNING" else "apagado", eq["id"])
            ej("UPDATE tickets SET resultado=? WHERE id=?", res, t["id"])
    if a["nombre_aap"] == PRODUCTO:
        e_ = p.get("nb_entrada") or {}
        o = art.get("faceops_nube_producto") or {}
        r = q1("SELECT * FROM nube_productos WHERE cliente_id=? AND infra_id=? AND producto=? AND nombre=? ORDER BY id DESC LIMIT 1",
               c["id"], iid, e_.get("producto", ""), e_.get("nombre", ""))
        if r:
            if e_.get("accion") == "crear":
                nuevo = "activo" if est == "exitoso" else ("fallido" if est == "fallido" else "eliminado" if est in ("rechazado", "cancelado") else r["estado"])
            else:
                nuevo = "eliminado" if est == "exitoso" else "activo"
            ej("UPDATE nube_productos SET estado=?, salud=?, salud_ts=? WHERE id=?", nuevo, ((o.get("salud") or {}).get("salud") or r["salud"] or ""), ahora(), r["id"])
    if iid and est in ("exitoso", "fallido"):
        _caducar(c["id"], iid)
    return est, res


# ------------------------------------------------------------------ rutas
def _csrf_ok():
    return request.form.get("_csrf") == session.get("csrf") and g.perfil["modo"] == "escritura"


def _solicitar(nombre_aap, params, motivo):
    from .aap import ErrorAAP
    from .operacion import solicitar
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre_aap)
    if not a:
        raise ValueError(f"La acción «{nombre_aap}» no está publicada en AAP")
    try:
        t = solicitar(g.usuario, g.cliente, a, "", params, motivo)
    except (PermissionError, ErrorAAP) as e:
        raise ValueError(str(e)) from e
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": nombre_aap}


@bp.route("/nubes/<int:iid>/opciones", methods=["GET", "POST"])
@requiere_login
def nube_opciones(iid):
    """GET: estado de la lectura de opciones; POST: relee la nube (solo lectura, sin ticket)."""
    x = _nube(iid)
    recoger_opciones(g.cliente, iid)
    if request.method == "POST":
        if request.form.get("_csrf") != session.get("csrf"):
            return {"error": "Sesión vencida"}, 403
        reg = request.form.get("region", "")
        job, mot = lanzar_opciones(g.cliente, dict(x, detalle=json.dumps(_det(x))), [reg] if re.match(r"^[a-z]+-[a-z]+\d+$", reg) else None)
        if mot:
            return {"error": mot}, 400
        return {"ok": True, "job": job}
    op = opciones(g.cliente["id"], iid)
    lect = q1("SELECT * FROM nube_opciones_trabajos WHERE cliente_id=? AND infra_id=? ORDER BY id DESC LIMIT 1", g.cliente["id"], iid)
    return {"ts": (op or {}).get("_ts"), "en_curso": bool(lect and lect["estado"] == "en curso"), "ultima": lect and lect["estado"]}


@bp.route("/nubes/<int:iid>/maquinas/crear", methods=["POST"])
@requiere_login
def nube_maquina_crear(iid):
    if not _csrf_ok():
        return {"error": "Necesitas perfil de escritura"}, 403
    x = _nube(iid)
    op = opciones(g.cliente["id"], iid)
    mot = bloqueo(g.cliente["id"], x["tipo"], op, "crear")
    if mot:
        return {"error": mot}, 400
    try:
        pr = validar_crear(dict(x, detalle=json.dumps(_det(x))), op, request.form)
        c_ = pr.get("nm_costo_usd_mes")
        t = _solicitar(CREAR, pr, f"Crear máquina {pr['nm_nombre']} ({pr['nm_tipo']}, {pr['nm_zona']}) en {x['nombre']}"
                       + (f" · ≈ USD {c_:,.2f}/mes estimado" if c_ is not None else ""))
    except ValueError as e:
        return {"error": str(e)}, 400
    return dict(t, maquina=pr["nm_nombre"])


@bp.route("/nubes/<int:iid>/maquinas/<maquina>/<accion>", methods=["POST"])
@requiere_login
def nube_maquina_accion(iid, maquina, accion):
    if not _csrf_ok():
        return {"error": "Necesitas perfil de escritura"}, 403
    x = _nube(iid)
    op = opciones(g.cliente["id"], iid)
    mot = bloqueo(g.cliente["id"], x["tipo"], op, accion) if accion in ACCIONES_MAQ else "Acción no válida"
    if mot:
        return {"error": mot}, 400
    try:
        pr = validar_operar(x, op, maquina, accion, request.form)
        det = {"redimensionar": lambda e: f" a {e['tipo']} (con apagado)", "disco_agregar": lambda e: f" · {e['disco']['gb']} GB {e['disco']['tipo']}",
               "disco_ampliar": lambda e: f" · {e['disco']['nombre']} a {e['disco']['gb']} GB",
               "firewall": lambda e: f" · {e['firewall']['modo']} {e['firewall']['nombre']}"}.get(accion, lambda e: "")(pr["nb_entrada"])
        t = _solicitar(OPERAR, pr, f"{ACCIONES_MAQ[accion]} · {maquina} en {x['nombre']}{det}")
    except ValueError as e:
        return {"error": str(e)}, 400
    return dict(t, maquina=maquina, accion=ACCIONES_MAQ[accion])


@bp.route("/nubes/<int:iid>/productos", methods=["POST"])
@requiere_login
def nube_producto_crear(iid):
    if not _csrf_ok():
        return {"error": "Necesitas perfil de escritura"}, 403
    x = _nube(iid)
    op = opciones(g.cliente["id"], iid)
    pid = request.form.get("producto", "")
    mot = bloqueo(g.cliente["id"], x["tipo"], op, "producto:" + pid)
    if mot:
        return {"error": mot}, 400
    try:
        p, r, pr = validar_producto(x, op, pid, request.form)
        t = _solicitar(PRODUCTO, pr, f"Agregar {p['nombre']} «{r['nombre']}» en {x['nombre']} · ≈ USD {pr['np_costo_usd_mes']:,.2f}/mes estimado")
    except ValueError as e:
        return {"error": str(e)}, 400
    _esquema()
    ej("INSERT INTO nube_productos(cliente_id, infra_id, producto, nombre, region, zona, parametros, estado, ticket_id, creado, costo_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
       g.cliente["id"], iid, pid, r["nombre"], r["region"], r["zona"], json.dumps(r["parametros"], ensure_ascii=False), "creando", t["id"], ahora(), pr["np_costo_usd_mes"])
    return dict(t, producto=p["nombre"], nombre=r["nombre"])


@bp.route("/nubes/<int:iid>/productos/<int:rid>/eliminar", methods=["POST"])
@requiere_login
def nube_producto_eliminar(iid, rid):
    if not _csrf_ok():
        return {"error": "Necesitas perfil de escritura"}, 403
    x = _nube(iid)
    _esquema()
    r = q1("SELECT * FROM nube_productos WHERE id=? AND cliente_id=? AND infra_id=?", rid, g.cliente["id"], iid)
    if not r:
        abort(404)
    p = producto_def(x["tipo"], r["producto"]) or {}
    if p.get("sin_eliminar") or not p.get("eliminar"):
        return {"error": f"{p.get('nombre', r['producto'])} no se puede eliminar desde la nube"}, 400
    mot = bloqueo(g.cliente["id"], x["tipo"], opciones(g.cliente["id"], iid), "producto:" + r["producto"])
    if mot:
        return {"error": mot}, 400
    if (request.form.get("confirmar") or "").strip() != r["nombre"]:
        return {"error": f"Escribe el nombre exacto ({r['nombre']}) para eliminar"}, 400
    try:
        t = _solicitar(PRODUCTO, params_eliminar_producto(x, r), f"Eliminar {p.get('nombre')} «{r['nombre']}» de {x['nombre']}")
    except ValueError as e:
        return {"error": str(e)}, 400
    ej("UPDATE nube_productos SET estado='eliminando', ticket_id=? WHERE id=?", t["id"], rid)
    return t


@bp.route("/nubes/<int:iid>/maquinas/costo")
@requiere_login
def nube_maquina_costo(iid):
    """Precio estimado por mes de la máquina que se está armando en «Crear máquina» (o al cambiar su tipo)."""
    x = _nube(iid)
    a_ = request.args
    c_ = costo_maquina(x["tipo"], opciones(g.cliente["id"], iid), a_.get("region", ""), a_.get("tipo", ""), a_.get("imagen", ""),
                       a_.get("disco_tipo", "pd-balanced"), a_.get("disco_gb") or 0, a_.get("publica") in ("1", "true", "on"))
    if not c_:
        return {"error": "Sin precio para ese tipo en la región"}
    return dict(c_, texto=usd(c_["usd_mes"]) + "/mes",
                detalle_txt=" · ".join(f"{k} {usd(v)}" for k, v in c_["detalle"].items()))


# ------------------------------------------------------------------ campana de alertas y botón Skills de la ficha de nube
AMBITO_NUBE = "plataforma:nube"
MARCA_SKILL = "Skill de la nube · "


def objeto_nube(x):
    """La nube como objeto de la campana de alertas (app/alertas_objeto.py) y del botón Skills: nombre estable para
    Prometheus (nb<id>-<nombre>); sus métricas las publica FaceOps en el anfitrión (sin agente, 127.0.0.1). El reflejo de
    lo aplicado vive en el detalle de la infraestructura. Nunca lleva secretos."""
    d_ = _det(x)
    slug = re.sub(r"[^a-z0-9]+", "-", x["nombre"].lower().translate(str.maketrans("áéíóúñü·", "aeiounu-"))).strip("-")
    return {"id": x["id"], "nombre": f"nb{x['id']}-{slug}"[:63], "categoria": "nube", "nb_tipo": x["tipo"], "nb_nombre": x["nombre"],
            "so": (x.get("prov") or {}).get("nombre", x["tipo"]), "ip": "127.0.0.1", "ambiente": "", "segmentos": [],
            "datos": json.dumps({"hostname": x["nombre"], "alertas_plat": {AMBITO_NUBE: d_.get("alertas_nube") or {}}}, ensure_ascii=False),
            "url_probar": url_for("consola.nube_alerta_probar", iid=x["id"]), "url_regla": url_for("consola.nube_alerta_regla", iid=x["id"])}


def alertas_nube(x):
    from . import alertas_objeto as ao
    try:
        return ao.contexto(g.cliente["id"], objeto_nube(x), g.perfil["modo"] == "escritura", AMBITO_NUBE, "nube")
    except Exception:  # noqa: BLE001 - la ficha se ve igual sin la campana
        return None


def _skill_auto(x, h):
    """(automatización, motivo) de una skill de la nube: la primera acción publicada; motivo vacío si se puede pedir."""
    from .pruebas import estado, exceptuada
    a = next((r for n_ in h.get("nombre_aap") or [] for r in [q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], n_)] if r), None)
    if not a:
        return None, "acción aún no disponible (no está publicada en AAP)"
    ep = estado(dict(a, cliente_id=g.cliente["id"]))["estado"]
    if ep != "certificada" and not exceptuada(a["nombre_aap"]):
        return a, {"cambiada": "cambió desde su última prueba; debe probarse de nuevo", "fallida": "su última prueba falló"}.get(ep, "sin plan de prueba aprobado")
    if h.get("por_maquina"):
        return a, "se pide desde la ficha de cada máquina (elige el servidor)"
    if x.get("estado") != "conectada":
        return a, "la nube no está conectada: valida su conexión"
    falta = [c["etiqueta"] for c in j(a.get("campos"), []) if c.get("requerido") and c.get("defecto") in (None, "")
             and c["var"] not in (h.get("parametros") or {})]
    if falta:
        return a, "pide datos (" + ", ".join(falta[:2]) + ")"
    return a, ""


def _ticket_skill(x, aid):
    for t in q("SELECT id, numero, estado, motivo FROM tickets WHERE cliente_id=? AND automatizacion_id=? AND motivo LIKE ? ORDER BY id DESC LIMIT 20",
               g.cliente["id"], aid, f"{MARCA_SKILL}% en {x['nombre']}"):
        abierto = t["estado"] in ("pendiente_aprobacion", "en_cola", "en_ejecucion", "pendiente")
        if abierto or t["estado"] == "exitoso":
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "abierto": abierto}
        return None
    return None


def habilidades_nube(x):
    """Skills de la ficha de nube (datos/nube-productos.yml › skills), mismas columnas y estados que las de máquinas y
    plataformas. Las que no tienen acción publicada o certificada se muestran deshabilitadas con su motivo."""
    esc = g.perfil["modo"] == "escritura"
    out = []
    for h in catalogo(x["tipo"]).get("skills") or []:
        a, mot = _skill_auto(x, h)
        out.append({"id": h["id"], "tipo": h.get("tipo") or "quick win", "grupo": h.get("grupo") or "", "nombre": h["nombre"], "valor": h.get("valor") or "",
                    "auto": (a or {}).get("nombre") or h["nombre_aap"][0], "aid": (a or {}).get("id"), "ui": "", "bloqueo": mot,
                    "ticket": _ticket_skill(x, a["id"]) if a else None, "escritura": esc, "url": url_for("consola.nube_habilidad", iid=x["id"]), "campos": []})
    out.sort(key=lambda s: (s["tipo"] != "diferenciador", bool(s["bloqueo"])))
    return out


@bp.app_context_processor
def _ctx_nube_iconos():
    return {"alertas_nube": alertas_nube, "habilidades_nube": habilidades_nube}


@bp.route("/nubes/<int:iid>/habilidad", methods=["POST"])
@requiere_login
def nube_habilidad(iid):
    """Botón Skills de la nube: ticket con aprobación en AAP; si ya hay uno abierto de esa skill, devuelve ese."""
    if not _csrf_ok():
        return {"error": "Necesitas perfil de escritura"}, 403
    x = _nube(iid)
    h = next((s for s in catalogo(x["tipo"]).get("skills") or [] if s["id"] == request.form.get("hid")), None)
    if not h:
        return {"error": "Skill no válida"}, 400
    a, mot = _skill_auto(x, h)
    if mot:
        return {"error": f"«{h['nombre']}»: {mot}"}, 400
    t0 = _ticket_skill(x, a["id"])
    if t0 and t0["abierto"]:
        return dict(t0, existente=True, url=url_for("consola.ticket", tid=t0["id"]))
    det = _det(x)
    v = {"proyecto": det.get("proyecto", ""), "zona": det.get("zona", ""), "region": _region(x), "nube": x["nombre"]}
    params = dict(_formato(h.get("parametros") or {}, v), nb_infra_id=x["id"])
    try:
        from .aap import ErrorAAP
        from .operacion import solicitar
        t = solicitar(g.usuario, g.cliente, a, "", params, f"{MARCA_SKILL}{h['nombre']} en {x['nombre']}")
    except (PermissionError, ValueError, ErrorAAP) as e:
        return {"error": str(e)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"]}


@bp.route("/nubes/<int:iid>/alertas", methods=["POST"])
@requiere_login
def nube_alertas(iid):
    """Campana de la nube: un ticket a «Monitoreo · Configurar alertas del equipo» con las alertas marcadas (sin agente:
    Prometheus lee lo que FaceOps publica → Alertmanager → EDA → FaceOps). On/off no se bloquea por la regla de prueba."""
    from . import alertas_objeto as ao
    from .vistas import _alertas_pedir
    if not _csrf_ok():
        return {"error": "Necesitas perfil de escritura"}, 403
    e = objeto_nube(_nube(iid))
    ctx = ao.contexto(g.cliente["id"], e, True, AMBITO_NUBE)
    if not ctx or not ctx["disponible"]:
        return {"error": (ctx or {}).get("por_que") or "Nube sin alertas"}, 400
    try:
        t, a = _alertas_pedir(e, AMBITO_NUBE, request.form)
    except (ValueError, PermissionError) as err:
        return {"error": str(err)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/nubes/<int:iid>/alertas/probar", methods=["POST"])
@requiere_login
def nube_alerta_probar(iid):
    """«Probar» de una alerta activa de la nube: alerta sintética por la cadena real (Alertmanager → EDA → acción)."""
    from flask import current_app
    from .alerta_prueba import lanzar as lanzar_prueba
    if not _csrf_ok():
        return {"error": "Necesitas perfil de escritura"}, 403
    e = objeto_nube(_nube(iid))
    try:
        pid = lanzar_prueba(current_app._get_current_object(), g.cliente, e, request.form.get("ambito") or AMBITO_NUBE, (request.form.get("aid") or "").strip(), g.usuario)
    except ValueError as err:
        return {"error": str(err)}, 400
    return {"id": pid, "url": url_for("consola.nube_alerta_prueba", iid=iid, pid=pid)}


@bp.route("/nubes/<int:iid>/alertas/prueba/<int:pid>")
@requiere_login
def nube_alerta_prueba(iid, pid):
    from .alerta_prueba import leer
    e = objeto_nube(_nube(iid))
    p_ = leer(pid)
    if not p_ or p_["cliente_id"] != g.cliente["id"] or p_["equipo"] != e["nombre"]:
        abort(404)
    return {"estado": p_["estado"], "pasos": p_["pasos"], "inicio": p_["inicio"]}


@bp.route("/nubes/<int:iid>/alertas/regla")
@requiere_login
def nube_alerta_regla(iid):
    """La regla de una alerta de la nube tal como está cargada en Prometheus (solo lectura)."""
    from flask import render_template
    from . import alertas_objeto as ao
    e = objeto_nube(_nube(iid))
    aid = (request.args.get("aid") or "").strip()
    a = next((y for y in ao.catalogo()["alertas"] if y["id"] == aid and "nube" in y.get("tipos", [])), None)
    if not a:
        abort(404)
    mon = ao.reglas_monitoreo(e, AMBITO_NUBE, "nube")
    return render_template("alerta_regla.html", e=e, a=a, aid=aid, ambito=AMBITO_NUBE, mon=mon, d=(mon or {}).get(aid), grafana=ao.url_grafana(e["nombre"], aid, AMBITO_NUBE),
                           grupo=ao.grupo_monitoreo(e, AMBITO_NUBE), archivo=f"{ao.REGLAS}/{e['nombre']}{ao.sufijo(AMBITO_NUBE)}.yml", volver=url_for("consola.nube", iid=iid))


# ------------------------------------------------------------------ métricas sin agente de las nubes (/metricas/plataformas)
def metricas_nube(esc):
    """Indicadores de cada nube para sus alertas: consumo del mes y contra el presupuesto, cuota más usada, llave más antigua,
    máquinas detenidas sin ticket, APIs que faltan y edad de la lectura. Todo de lo que AAP ya leyó (sin llamar a la nube)."""
    _esquema()
    lin = []
    ahora_ = datetime.now(timezone.utc)
    for inf in q("SELECT i.*, c.codigo FROM infraestructuras i JOIN clientes c ON c.id=i.cliente_id WHERE i.tipo IN ('gcp','aws','azure') AND c.activo=1"):
        slug = re.sub(r"[^a-z0-9]+", "-", inf["nombre"].lower().translate(str.maketrans("áéíóúñü·", "aeiounu-"))).strip("-")
        eq = ("nb%d-%s" % (inf["id"], slug))[:63]
        base = 'faceops_cliente="%s",faceops_equipo="%s",faceops_plataforma="nube"' % (esc((inf["codigo"] or "").lower()), esc(eq))
        val = {}
        res = None
        try:
            from .nube_consumo import de_nube
            res = de_nube(inf["cliente_id"], inf["id"])
        except Exception:  # noqa: BLE001
            res = None
        if res:
            c = res.get("consumo") or {}
            if c.get("mes_actual_usd") is not None:
                val["consumo_mes_usd"] = float(c["mes_actual_usd"])
                p = res.get("presupuesto") or {}
                if p.get("ok") and p.get("usd_mes"):
                    val["consumo_pct_presupuesto"] = round(100 * float(c["mes_actual_usd"]) / float(p["usd_mes"]), 1)
            cu = (res.get("cuotas") or {}).get("lista") or []
            if (res.get("cuotas") or {}).get("ok"):
                val["cuota_max_pct"] = max([float(x.get("pct") or 0) for x in cu] or [0])
            ll = [x.get("dias") for x in (res.get("claves") or {}).get("lista") or [] if x.get("dias") is not None]
            if (res.get("claves") or {}).get("ok"):
                val["llave_max_dias"] = max(ll or [0])
            try:
                val["lectura_edad_horas"] = round((ahora_ - datetime.fromisoformat(str(res.get("_ts")).replace("Z", "+00:00"))).total_seconds() / 3600, 1)
            except (TypeError, ValueError):
                pass
        op = opciones(inf["cliente_id"], inf["id"])
        if op:
            enc = {e["nombre"] for e in q("SELECT nombre FROM equipos WHERE cliente_id=? AND estado!='retirado' AND energia='encendido'", inf["cliente_id"])}
            val["vm_detenidas_inesperadas"] = sum(1 for m in op.get("maquinas") or [] if m.get("estado") == "TERMINATED" and m["nombre"] in enc)
            if op.get("apis") is not None:
                nec = {"compute.googleapis.com"} | {(producto_def(inf["tipo"], r["producto"]) or {}).get("servicio") for r in
                                                    q("SELECT producto FROM nube_productos WHERE cliente_id=? AND infra_id=? AND estado='activo'", inf["cliente_id"], inf["id"])}
                val["apis_faltantes"] = len({s for s in nec if s} - set(op["apis"]))
        for k, v in sorted(val.items()):
            lin.append(f'faceops_plataforma_indicador{{{base},indicador="{k}"}} {float(v):g}')
    return lin
