"""Políticas de seguridad como automatizaciones versionadas. Cada política (datos/politicas.yml o editada en FaceOps)
tiene condiciones de aplicación por equipo y una versión: si cambia, los equipos que la tenían quedan desactualizados
y se vuelve a aplicar. La aplica (o verifica sin cambiar nada) «FaceOps · Aplicar política de seguridad» en AAP."""
import fnmatch
import hashlib
import json
import os

import yaml

from .bd import ahora, ej, j, q, q1

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "politicas.yml")
AUTO = "FaceOps · Aplicar política de seguridad"
CATEGORIAS = [("sistema operativo", "Sistema operativo"), ("comunicaciones", "Comunicaciones"), ("redes", "Redes"),
              ("datos", "Datos"), ("aplicaciones", "Aplicaciones"), ("identidad", "Identidad")]
ESQUEMA = """
CREATE TABLE IF NOT EXISTS politicas_def (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, codigo TEXT NOT NULL, version INTEGER NOT NULL, contenido TEXT NOT NULL,
  hash TEXT NOT NULL, creado TEXT, autor TEXT DEFAULT '', motivo TEXT DEFAULT '', UNIQUE(cliente_id, codigo, version));
CREATE TABLE IF NOT EXISTS politicas_estado (
  cliente_id INTEGER NOT NULL, codigo TEXT NOT NULL, equipo_id INTEGER NOT NULL, version INTEGER, estado TEXT, modo TEXT,
  detalle TEXT DEFAULT '[]', cuando TEXT, ticket_id INTEGER, PRIMARY KEY(cliente_id, codigo, equipo_id));
"""


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)


def _hash(p):
    base = {k: v for k, v in p.items() if k not in ("version",)}
    return hashlib.sha256(json.dumps(base, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _normalizar(p):
    p = dict(p)
    p["codigo"] = p.pop("id", None) or p.get("codigo")
    p.setdefault("aplica", {})
    p.setdefault("reglas", [])
    p.setdefault("recargar", [])
    return p


def validar(p):
    err = []
    if not p.get("codigo") or not p.get("nombre"):
        err.append("Falta id o nombre")
    if p.get("categoria") not in dict(CATEGORIAS):
        err.append("categoria debe ser: " + ", ".join(dict(CATEGORIAS)))
    tipos = {"linea", "sysctl", "servicio", "permisos", "archivo", "comando"}
    for i, r in enumerate(p.get("reglas") or []):
        if r.get("tipo") not in tipos:
            err.append(f"regla {i + 1}: tipo debe ser {', '.join(sorted(tipos))}")
    if not p.get("reglas"):
        err.append("La política no tiene reglas")
    return err


def guardar(cliente_id, p, autor="", motivo=""):
    """Nueva versión si el contenido cambió. Devuelve la versión vigente."""
    p = _normalizar(p)
    h = _hash(p)
    ult = q1("SELECT * FROM politicas_def WHERE cliente_id=? AND codigo=? ORDER BY version DESC LIMIT 1", cliente_id, p["codigo"])
    if ult and ult["hash"] == h:
        return ult["version"]
    v = (ult["version"] + 1) if ult else 1
    ej("INSERT INTO politicas_def(cliente_id, codigo, version, contenido, hash, creado, autor, motivo) VALUES (?,?,?,?,?,?,?,?)",
       cliente_id, p["codigo"], v, yaml.safe_dump({k: v_ for k, v_ in p.items() if k != "version"}, allow_unicode=True, sort_keys=False), h,
       ahora(), autor, motivo)
    return v


def cargar(cliente_id):
    """Las políticas del repositorio (datos/politicas.yml): si alguna cambió, entra como versión nueva."""
    try:
        for p in yaml.safe_load(open(RUTA, encoding="utf-8")) or []:
            guardar(cliente_id, p, "repositorio", "datos/politicas.yml")
    except OSError:
        pass


def vigentes(cliente_id):
    out = []
    for r in q("SELECT d.* FROM politicas_def d JOIN (SELECT codigo, MAX(version) v FROM politicas_def WHERE cliente_id=? GROUP BY codigo) m "
               "ON m.codigo=d.codigo AND m.v=d.version WHERE d.cliente_id=? ORDER BY d.codigo", cliente_id, cliente_id):
        p = yaml.safe_load(r["contenido"]) or {}
        p.update(version=r["version"], creado=r["creado"], autor=r["autor"], motivo=r["motivo"])
        out.append(p)
    return out


def una(cliente_id, codigo):
    return next((p for p in vigentes(cliente_id) if p["codigo"] == codigo), None)


def historial(cliente_id, codigo):
    return q("SELECT version, creado, autor, motivo, contenido FROM politicas_def WHERE cliente_id=? AND codigo=? ORDER BY version DESC",
             cliente_id, codigo)


def aplica(p, e):
    from .inventario import sistema_eq
    a = p.get("aplica") or {}
    if a.get("sistemas") and sistema_eq(e) not in a["sistemas"]:
        return False
    if a.get("categorias") and (e.get("categoria") or "servidor") not in a["categorias"]:
        return False
    if a.get("skills") and not set(a["skills"]) & set(e.get("skills") or []):
        return False
    if a.get("ambientes") and (e.get("ambiente") or "") not in a["ambientes"]:
        return False
    if a.get("segmentos") and not set(a["segmentos"]) & set(e.get("segmentos") or []):
        return False
    if a.get("nombres") and not any(fnmatch.fnmatch(e["nombre"], x) for x in a["nombres"]):
        return False
    if "kvm" in (e.get("skills") or []) or not e.get("ip"):   # el anfitrión de la plataforma no se gestiona por aquí
        return False
    return True


ETIQUETA = {"al_dia": "al día", "desactualizada": "desactualizada", "pendiente": "pendiente", "no_cumple": "no cumple", "fallida": "falló"}


def estado(p, e, filas):
    r = filas.get((p["codigo"], e["id"]))
    if not r:
        return {"estado": "pendiente", "version": None}
    if r["estado"] == "fallida":
        return {"estado": "fallida", "version": r["version"], "cuando": r["cuando"], "ticket_id": r["ticket_id"]}
    if (r["version"] or 0) < p["version"]:
        return {"estado": "desactualizada", "version": r["version"], "cuando": r["cuando"], "ticket_id": r["ticket_id"]}
    return {"estado": "no_cumple" if r["estado"] == "no_cumple" else "al_dia", "version": r["version"], "cuando": r["cuando"],
            "ticket_id": r["ticket_id"], "detalle": j(r["detalle"], [])}


def tablero(cliente_id, eqs):
    """Por política: equipos a los que aplica y su estado; por equipo: sus políticas."""
    filas = {(r["codigo"], r["equipo_id"]): r for r in q("SELECT * FROM politicas_estado WHERE cliente_id=?", cliente_id)}
    pols, por_eq = [], {}
    for p in vigentes(cliente_id):
        cuenta = {k: 0 for k in ETIQUETA}
        lista = []
        for e in eqs:
            if not aplica(p, e):
                continue
            st = estado(p, e, filas)
            cuenta[st["estado"]] += 1
            lista.append(dict(st, equipo=e))
            por_eq.setdefault(e["id"], []).append(dict(st, codigo=p["codigo"], nombre=p["nombre"], categoria=p["categoria"],
                                                        severidad=p.get("severidad", "media"), vigente=p["version"]))
        pols.append(dict(p, equipos=lista, cuenta=cuenta, total=len(lista)))
    return pols, por_eq


def solicitar(usuario, cliente, p, equipos, modo="aplicar", motivo=""):
    """Un ticket por inventario de AAP (cada uno con su credencial). Devuelve los tickets creados."""
    from .aap import AAP
    from .operacion import acceso_equipo, ejecutar, numero_ticket
    from .bd import auditar
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], AUTO)
    if not a:
        raise PermissionError(f"Falta «{AUTO}» en el catálogo (sincroniza con AAP)")
    aap = AAP.de_cliente(cliente)
    grupos = {}
    for e in equipos:
        acc = acceso_equipo(aap, cliente, e)
        grupos.setdefault(acc[1], []).append(e)
    out = []
    pol = {k: v for k, v in p.items() if k in ("codigo", "nombre", "categoria", "descripcion", "reglas", "recargar", "version")}
    pol["id"] = pol["codigo"]
    for inv_n, es in grupos.items():
        params = {"politica": pol, "politica_modo": modo, "politica_codigo": p["codigo"], "politica_version": p["version"]}
        seg = next((s for e in es for s in (e.get("segmentos") or [])), "")
        tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, motivo, creado, "
                 "valor_min, tarifa, aap_job_tipo, filtros) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 cliente["id"], numero_ticket(cliente), a["id"], usuario["id"], "en_cola", seg, ":".join(e["nombre"] for e in es),
                 json.dumps(params, ensure_ascii=False), motivo or f"{'Verificar' if modo == 'verificar' else 'Aplicar'} {p['codigo']} v{p['version']} · {p['nombre']}",
                 ahora(), a["minutos_manual"], a["tarifa"], a["aap_tipo"], "{}")
        t = q1("SELECT * FROM tickets WHERE id=?", tid)
        auditar(usuario["usuario"], cliente["id"], "solicitar política", t["numero"], {"politica": p["codigo"], "version": p["version"], "modo": modo,
                                                                                      "equipos": [e["nombre"] for e in es]})
        ejecutar(tid)
        out.append(q1("SELECT * FROM tickets WHERE id=?", tid))
    return out


def registrar(aap, cliente, t, est):
    """Al cerrar el ticket: el estado de la política en cada equipo (qué versión quedó, si cumple)."""
    params = j(t["parametros"], {})
    codigo, version, modo = params.get("politica_codigo"), params.get("politica_version"), params.get("politica_modo", "aplicar")
    if not codigo:
        return ""
    art = (aap.get(f"jobs/{t['aap_job_id']}/").get("artifacts") or {}) if t["aap_job_tipo"] == "job" else {}
    res = art.get("faceops_politica") or {}
    lineas = []
    for n in [x for x in (t["limite"] or "").split(":") if x]:
        e = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], n)
        if not e:
            continue
        r = res.get(n)
        if r is None:
            if modo == "verificar":
                continue
            st, det = "fallida", []
        elif modo == "verificar":
            st, det = ("al_dia" if r.get("cumple") else "no_cumple"), r.get("incumplia", [])
        else:
            st, det = "al_dia", r.get("incumplia", [])
        prev = q1("SELECT * FROM politicas_estado WHERE cliente_id=? AND codigo=? AND equipo_id=?", cliente["id"], codigo, e["id"])
        # verificar no sube la versión aplicada: si estaba en una versión anterior, sigue desactualizada aunque hoy cumpla
        ver = version if (modo == "aplicar" or not prev or prev["estado"] == "fallida") else prev["version"]
        ej("INSERT INTO politicas_estado(cliente_id, codigo, equipo_id, version, estado, modo, detalle, cuando, ticket_id) VALUES (?,?,?,?,?,?,?,?,?) "
           "ON CONFLICT(cliente_id, codigo, equipo_id) DO UPDATE SET version=excluded.version, estado=excluded.estado, modo=excluded.modo, "
           "detalle=excluded.detalle, cuando=excluded.cuando, ticket_id=excluded.ticket_id",
           cliente["id"], codigo, e["id"], ver, st, modo, json.dumps(det, ensure_ascii=False), ahora(), t["id"])
        lineas.append(f"{n}: {ETIQUETA[st]}" + (f" ({', '.join(det[:4])})" if det else ""))
    return f"Política {codigo} v{version} ({modo}): " + " · ".join(lineas)
