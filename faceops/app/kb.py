"""Base de conocimiento de incidentes (PostgreSQL).

Por producto y versión: problemas conocidos (síntomas, patrones que los delatan en un log o alerta, causa, solución) y la
automatización predefinida que los resuelve. El equipo de Managed Service pega el error o la alerta de un equipo, FaceOps
reconoce el problema (patrones + búsqueda de texto completo + similitud) y propone la solución; al aplicarla queda un ticket,
y el resultado alimenta la efectividad de cada solución. Solo para los productos que el cliente contrató con soporte."""
import glob
import json
import os
import re

import yaml

DSN = os.environ.get("FACEOPS_KB_DSN", "")
DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "kb")

ESQUEMA = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE TABLE IF NOT EXISTS productos (
  codigo TEXT PRIMARY KEY, nombre TEXT NOT NULL, fabricante TEXT DEFAULT '', skill TEXT DEFAULT '', descripcion TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS problemas (
  id SERIAL PRIMARY KEY, codigo TEXT UNIQUE NOT NULL, producto TEXT REFERENCES productos(codigo), titulo TEXT NOT NULL,
  sintomas TEXT DEFAULT '', patrones TEXT[] DEFAULT '{}', causa TEXT DEFAULT '', solucion TEXT DEFAULT '',
  version_desde TEXT DEFAULT '', version_hasta TEXT DEFAULT '', severidad TEXT DEFAULT 'media', fuente TEXT DEFAULT '',
  automatizacion TEXT DEFAULT '', parametros JSONB DEFAULT '{}', diagnostico TEXT DEFAULT '',
  aplicaciones INTEGER DEFAULT 0, exitos INTEGER DEFAULT 0, origen TEXT DEFAULT 'semilla', creado TIMESTAMPTZ DEFAULT now(),
  busqueda TSVECTOR GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', coalesce(titulo, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(sintomas, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(causa, '')), 'C')) STORED);
CREATE INDEX IF NOT EXISTS problemas_busqueda ON problemas USING GIN (busqueda);
CREATE INDEX IF NOT EXISTS problemas_trgm ON problemas USING GIN (sintomas gin_trgm_ops);
CREATE TABLE IF NOT EXISTS incidentes (
  id SERIAL PRIMARY KEY, cliente_id INTEGER NOT NULL, numero TEXT, equipo TEXT DEFAULT '', producto TEXT, version TEXT DEFAULT '',
  texto TEXT NOT NULL, estado TEXT DEFAULT 'abierto', problema_id INTEGER REFERENCES problemas(id), puntaje REAL,
  candidatos JSONB DEFAULT '[]', ticket_id INTEGER, ticket TEXT DEFAULT '', usuario TEXT DEFAULT '', origen TEXT DEFAULT 'faceops',
  nota TEXT DEFAULT '', creado TIMESTAMPTZ DEFAULT now(), resuelto TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS incidentes_cliente ON incidentes(cliente_id, creado DESC);
CREATE TABLE IF NOT EXISTS cliente_productos (
  cliente_id INTEGER NOT NULL, producto TEXT REFERENCES productos(codigo), desde TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (cliente_id, producto));
"""


class SinKB(RuntimeError):
    pass


def disponible():
    return bool(DSN)


def con():
    if not DSN:
        raise SinKB("La base de conocimiento de incidentes no está configurada (FACEOPS_KB_DSN)")
    import psycopg
    from psycopg.rows import dict_row
    try:
        return psycopg.connect(DSN, row_factory=dict_row, autocommit=True, connect_timeout=5)
    except psycopg.Error as e:
        raise SinKB(f"No responde la base de conocimiento de incidentes: {str(e).splitlines()[0][:160]}") from e


def q(sql, *args):
    with con() as c:
        return c.execute(sql, args).fetchall()


def q1(sql, *args):
    r = q(sql, *args)
    return r[0] if r else None


def ej(sql, *args):
    with con() as c:
        cur = c.execute(sql, args)
        try:
            return cur.fetchone()
        except Exception:
            return None


def iniciar():
    """Esquema y semilla (datos/kb/<producto>.yml): los problemas de la semilla se actualizan; los aprendidos no se tocan."""
    if not DSN:
        return
    with con() as c:
        c.execute(ESQUEMA)
        for ruta in sorted(glob.glob(os.path.join(DIR, "*.yml"))):
            d = yaml.safe_load(open(ruta, encoding="utf-8")) or {}
            p = d.get("producto") or {}
            if not p.get("codigo"):
                continue
            c.execute("INSERT INTO productos(codigo, nombre, fabricante, skill, descripcion) VALUES (%s,%s,%s,%s,%s) "
                      "ON CONFLICT(codigo) DO UPDATE SET nombre=excluded.nombre, fabricante=excluded.fabricante, skill=excluded.skill, "
                      "descripcion=excluded.descripcion", (p["codigo"], p["nombre"], p.get("fabricante", ""), p.get("skill", ""), p.get("descripcion", "")))
            for x in d.get("problemas") or []:
                c.execute("""INSERT INTO problemas(codigo, producto, titulo, sintomas, patrones, causa, solucion, version_desde, version_hasta,
                             severidad, fuente, automatizacion, parametros, diagnostico) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                             ON CONFLICT(codigo) DO UPDATE SET titulo=excluded.titulo, sintomas=excluded.sintomas, patrones=excluded.patrones,
                             causa=excluded.causa, solucion=excluded.solucion, version_desde=excluded.version_desde,
                             version_hasta=excluded.version_hasta, severidad=excluded.severidad, fuente=excluded.fuente,
                             automatizacion=excluded.automatizacion, parametros=excluded.parametros, diagnostico=excluded.diagnostico
                             WHERE problemas.origen='semilla'""",
                          (x["codigo"], p["codigo"], x["titulo"], x.get("sintomas", ""), x.get("patrones") or [], x.get("causa", ""),
                           x.get("solucion", ""), str(x.get("desde", "") or ""), str(x.get("hasta", "") or ""), x.get("severidad", "media"),
                           x.get("fuente", ""), x.get("automatizacion", ""), json.dumps(x.get("parametros") or {}),
                           x.get("diagnostico", "")))


def resumen():
    if not DSN:
        return "sin configurar"
    r = q1("SELECT (SELECT count(*) FROM productos) productos, (SELECT count(*) FROM problemas) problemas, "
           "(SELECT count(*) FROM incidentes) incidentes")
    return dict(r)


# ------------------------------------------------------------------ versiones
def _v(s):
    return tuple(int(x) for x in re.findall(r"\d+", str(s or ""))[:3]) or None


def version_aplica(p, version):
    """None si no se sabe la versión del equipo; True/False según el rango del problema (desde ≤ versión ≤ hasta)."""
    v = _v(version)
    if not v:
        return None
    d, h = _v(p.get("version_desde")), _v(p.get("version_hasta"))
    if d and v[:len(d)] < d:
        return False
    if h and v[:len(h)] > h:
        return False
    return True


# ------------------------------------------------------------------ reconocimiento
def reconocer(texto, productos, version="", limite=5):
    """Candidatos ordenados por puntaje (0-100): patrón exacto en el texto (lo más fuerte), coincidencia de texto completo y
    similitud. Descarta los que no aplican a la versión del equipo."""
    texto = (texto or "").strip()
    if not texto or not productos:
        return []
    filas = q("""SELECT p.*, ts_rank(busqueda, plainto_tsquery('simple', unaccent(%s))) r_txt,
                        similarity(sintomas || ' ' || titulo, %s) r_sim
                 FROM problemas p WHERE producto = ANY(%s)""", texto[:4000], texto[:4000], list(productos))
    out = []
    for p in filas:
        pat = [x for x in (p["patrones"] or []) if _busca(x, texto)]
        txt = min(1.0, float(p["r_txt"] or 0) * 4)
        sim = float(p["r_sim"] or 0)
        puntaje = (60 if pat else 0) + 25 * txt + 15 * min(1.0, sim * 3)
        va = version_aplica(p, version)
        if va is False:
            continue
        if va is None and (p["version_desde"] or p["version_hasta"]):
            puntaje *= 0.9   # sin versión del equipo: un poco menos de certeza
        if puntaje < 12:
            continue
        efect = (p["exitos"] / p["aplicaciones"]) if p["aplicaciones"] else None
        out.append({"id": p["id"], "codigo": p["codigo"], "producto": p["producto"], "titulo": p["titulo"], "causa": p["causa"],
                    "solucion": p["solucion"], "automatizacion": p["automatizacion"], "parametros": p["parametros"] or {},
                    "severidad": p["severidad"], "fuente": p["fuente"], "diagnostico": p["diagnostico"], "patrones": pat,
                    "rango": _rango(p), "puntaje": round(min(puntaje, 100)), "aplicaciones": p["aplicaciones"], "efectividad": efect})
    out.sort(key=lambda x: (-x["puntaje"], -(x["efectividad"] or 0)))
    return out[:limite]


def _busca(patron, texto):
    try:
        return re.search(patron, texto, re.I | re.M) is not None
    except re.error:
        return patron.lower() in texto.lower()


def _rango(p):
    d, h = p.get("version_desde"), p.get("version_hasta")
    return f"{d or 'cualquiera'} – {h or 'actual'}" if (d or h) else "todas las versiones"


# ------------------------------------------------------------------ productos con soporte por cliente
def productos():
    return q("SELECT p.*, (SELECT count(*) FROM problemas x WHERE x.producto=p.codigo) problemas FROM productos p ORDER BY nombre")


def productos_cliente(cliente_id):
    return [r["producto"] for r in q("SELECT producto FROM cliente_productos WHERE cliente_id=%s ORDER BY producto", cliente_id)]


def fijar_productos(cliente_id, codigos):
    with con() as c:
        c.execute("DELETE FROM cliente_productos WHERE cliente_id=%s AND NOT (producto = ANY(%s))", (cliente_id, list(codigos)))
        for k in codigos:
            c.execute("INSERT INTO cliente_productos(cliente_id, producto) VALUES (%s,%s) ON CONFLICT DO NOTHING", (cliente_id, k))


def producto_de_equipo(e, contratados):
    """Productos del equipo según sus skills y su sistema operativo, de los que el cliente contrató."""
    skills = set(e.get("skills") or [])
    so = (e.get("so") or "").lower()
    out = []
    for p in productos():
        if p["codigo"] not in contratados:
            continue
        if (p["skill"] and p["skill"] in skills) or (p["codigo"] == "rhel" and ("red hat" in so or so.startswith("rhel"))) \
                or (p["codigo"] == "windows-server" and "windows" in so) or (p["codigo"] == "openshift" and "openshift" in so):
            out.append(p["codigo"])
    return out


# ------------------------------------------------------------------ incidentes
def registrar(cliente_id, numero, equipo, producto, version, texto, candidatos, usuario, origen="faceops"):
    mejor = candidatos[0] if candidatos else None
    r = ej("""INSERT INTO incidentes(cliente_id, numero, equipo, producto, version, texto, estado, problema_id, puntaje, candidatos,
              usuario, origen) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
           cliente_id, numero, equipo, producto, version, texto, "reconocido" if mejor else "sin coincidencia",
           mejor["id"] if mejor else None, mejor["puntaje"] if mejor else None,
           json.dumps([{"id": c["id"], "puntaje": c["puntaje"]} for c in candidatos]), usuario, origen)
    return r["id"]


def incidente(cliente_id, iid):
    return q1("""SELECT i.*, p.titulo, p.causa, p.solucion, p.automatizacion, p.parametros, p.codigo pcodigo, p.diagnostico
                 FROM incidentes i LEFT JOIN problemas p ON p.id=i.problema_id WHERE i.cliente_id=%s AND i.id=%s""", cliente_id, iid)


def incidentes(cliente_id, limite=200):
    return q("""SELECT i.*, p.titulo, p.automatizacion FROM incidentes i LEFT JOIN problemas p ON p.id=i.problema_id
                WHERE i.cliente_id=%s ORDER BY i.creado DESC LIMIT %s""", cliente_id, limite)


def problema(pid):
    return q1("SELECT * FROM problemas WHERE id=%s", pid)


def elegir(cliente_id, iid, pid):
    ej("UPDATE incidentes SET problema_id=%s, estado='reconocido' WHERE cliente_id=%s AND id=%s", pid, cliente_id, iid)


def solucion_lanzada(cliente_id, iid, ticket_id, numero):
    ej("UPDATE incidentes SET estado='en solución', ticket_id=%s, ticket=%s WHERE cliente_id=%s AND id=%s", ticket_id, numero, cliente_id, iid)
    ej("UPDATE problemas SET aplicaciones=aplicaciones+1 WHERE id=(SELECT problema_id FROM incidentes WHERE id=%s)", iid)


def cerrar(cliente_id, iid, estado, nota=""):
    i = q1("SELECT * FROM incidentes WHERE cliente_id=%s AND id=%s", cliente_id, iid)
    if not i or i["estado"] in ("resuelto", "no resuelto"):
        return
    ej("UPDATE incidentes SET estado=%s, nota=%s, resuelto=now() WHERE id=%s", estado, nota, iid)
    if estado == "resuelto" and i["problema_id"] and i["ticket_id"]:
        ej("UPDATE problemas SET exitos=exitos+1 WHERE id=%s", i["problema_id"])


def seguir_tickets(cliente_id, estado_de_ticket):
    """Incidentes con la solución en curso: cuando su ticket termina, se cierran solos (y suma la efectividad)."""
    for i in q("SELECT id, ticket_id FROM incidentes WHERE cliente_id=%s AND estado='en solución' AND ticket_id IS NOT NULL", cliente_id):
        st = estado_de_ticket(i["ticket_id"])
        if st == "exitoso":
            cerrar(cliente_id, i["id"], "resuelto", "La automatización de la solución terminó bien")
        elif st in ("fallido", "rechazado", "vencido", "cancelado"):
            ej("UPDATE incidentes SET estado='reconocido', nota=%s WHERE id=%s", f"La solución terminó {st}: revisar", i["id"])


def aprender(producto, titulo, sintomas, causa, solucion, automatizacion, usuario, patrones=None):
    """Un incidente resuelto a mano se vuelve problema conocido (origen 'aprendido': la semilla no lo pisa)."""
    cod = f"{producto}-{re.sub(r'[^a-z0-9]+', '-', titulo.lower()).strip('-')[:40]}"
    r = ej("""INSERT INTO problemas(codigo, producto, titulo, sintomas, patrones, causa, solucion, automatizacion, fuente, origen)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'aprendido') ON CONFLICT(codigo) DO NOTHING RETURNING id""",
           cod, producto, titulo, sintomas, patrones or [], causa, solucion, automatizacion, f"Managed Service · {usuario}")
    return r["id"] if r else None


def numero(cliente_id, codigo):
    n = (q1("SELECT count(*) n FROM incidentes WHERE cliente_id=%s", cliente_id) or {"n": 0})["n"] + 1
    return f"INC-{codigo.upper()}-{n:05d}"
