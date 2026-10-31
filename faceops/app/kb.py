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
ALTER TABLE problemas ADD COLUMN IF NOT EXISTS prob_ocurrencia REAL DEFAULT 10;
ALTER TABLE problemas ADD COLUMN IF NOT EXISTS preventivas JSONB DEFAULT '[]';
ALTER TABLE problemas ADD COLUMN IF NOT EXISTS ocurrencias INTEGER DEFAULT 0;
ALTER TABLE problemas ADD COLUMN IF NOT EXISTS pasos JSONB DEFAULT '[]';
ALTER TABLE problemas ADD COLUMN IF NOT EXISTS verificacion JSONB DEFAULT '[]';
ALTER TABLE problemas ADD COLUMN IF NOT EXISTS fuentes JSONB DEFAULT '[]';
ALTER TABLE problemas ADD COLUMN IF NOT EXISTS tipo TEXT DEFAULT '';
ALTER TABLE problemas ADD COLUMN IF NOT EXISTS senales JSONB DEFAULT '[]';
CREATE TABLE IF NOT EXISTS integraciones (
  cliente_id INTEGER NOT NULL, producto TEXT NOT NULL, equipo TEXT NOT NULL, estado TEXT DEFAULT 'integrando', datos JSONB,
  ticket_id INTEGER, mensaje TEXT DEFAULT '', actualizado TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (cliente_id, producto, equipo));
CREATE TABLE IF NOT EXISTS diagnosticos (
  id SERIAL PRIMARY KEY, cliente_id INTEGER NOT NULL, producto TEXT, equipo TEXT, ticket_id INTEGER, estado TEXT DEFAULT 'en curso',
  evidencia JSONB, hallazgos JSONB, usuario TEXT DEFAULT '', creado TIMESTAMPTZ DEFAULT now(), terminado TIMESTAMPTZ);
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS verif_ticket_id INTEGER;
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS verif_ticket TEXT DEFAULT '';
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS verif JSONB;
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
                patrones = []
                for pat in x.get("patrones") or []:   # expresiones regulares de PostgreSQL: \b (Python) → \y
                    pat = pat.replace("\\b", "\\y")
                    try:
                        c.execute("SELECT '' ~* %s", (pat,))
                        patrones.append(pat)
                    except Exception:
                        pass
                c.execute("""INSERT INTO problemas(codigo, producto, titulo, sintomas, patrones, causa, solucion, version_desde, version_hasta,
                             severidad, fuente, automatizacion, parametros, diagnostico, prob_ocurrencia, preventivas, pasos, verificacion, fuentes, tipo, senales)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                             ON CONFLICT(codigo) DO UPDATE SET titulo=excluded.titulo, sintomas=excluded.sintomas, patrones=excluded.patrones,
                             causa=excluded.causa, solucion=excluded.solucion, version_desde=excluded.version_desde,
                             version_hasta=excluded.version_hasta, severidad=excluded.severidad, fuente=excluded.fuente,
                             automatizacion=excluded.automatizacion, parametros=excluded.parametros, diagnostico=excluded.diagnostico,
                             prob_ocurrencia=excluded.prob_ocurrencia, preventivas=excluded.preventivas, pasos=excluded.pasos,
                             verificacion=excluded.verificacion, fuentes=excluded.fuentes, tipo=excluded.tipo, senales=excluded.senales
                             WHERE problemas.origen='semilla'""",
                          (x["codigo"], p["codigo"], x["titulo"], x.get("sintomas", ""), patrones, x.get("causa", ""),
                           x.get("solucion", ""), str(x.get("desde", "") or ""), str(x.get("hasta", "") or ""), x.get("severidad", "media"),
                           x.get("fuente", ""), x.get("automatizacion", ""), json.dumps(x.get("parametros") or {}),
                           x.get("diagnostico", ""), float(x.get("ocurrencia", 10)), json.dumps(x.get("preventivas") or [], ensure_ascii=False),
                           json.dumps(x.get("pasos") or [], ensure_ascii=False), json.dumps(x.get("verificacion") or [], ensure_ascii=False),
                           json.dumps(x.get("fuentes") or [], ensure_ascii=False), x.get("tipo", ""),
                           json.dumps(x.get("senales") or [], ensure_ascii=False)))


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
        if puntaje < (12 if pat else 35):   # sin un patrón que calce, exige bastante coincidencia de texto
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


# ------------------------------------------------------------------ errores conocidos como tickets y buscador inteligente (todo en PostgreSQL)
CAMPOS = """p.*, 'KE-' || lpad(p.id::text, 4, '0') AS numero, pr.nombre AS producto_nombre,
            (p.exitos + 4.0) / (p.aplicaciones + 5.0) AS confianza"""


def conocidos(productos, orden="ocurrencia"):
    """Todos los errores conocidos de los productos con soporte, como tickets."""
    orden_sql = {"ocurrencia": "p.prob_ocurrencia DESC, p.titulo", "severidad":
                 "CASE p.severidad WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END, p.prob_ocurrencia DESC",
                 "producto": "pr.nombre, p.prob_ocurrencia DESC"}.get(orden, "p.prob_ocurrencia DESC")
    return q(f"SELECT {CAMPOS} FROM problemas p JOIN productos pr ON pr.codigo=p.producto WHERE p.producto = ANY(%s) ORDER BY {orden_sql}",
             list(productos))


def buscar(texto, productos, limite=10):
    """Buscador inteligente, resuelto en PostgreSQL: patrones del error (expresiones regulares sobre el log), búsqueda de texto
    completo (títulos, síntomas, causa) y similitud por trigramas. Devuelve los errores conocidos con mayor probabilidad de
    solución = coincidencia × confianza de la solución (éxitos sobre aplicaciones, con una base de 80 %)."""
    texto = (texto or "").strip()[:6000]
    if not texto:
        return []
    filas = q(f"""
        WITH b AS (SELECT %s::text t,   -- cualquiera de las palabras del log (OR), no todas
                          to_tsquery('simple', coalesce(nullif(array_to_string(ARRAY(SELECT quote_literal(w) FROM unnest(
                            tsvector_to_array(to_tsvector('simple', unaccent(%s)))) w LIMIT 300), ' | '), ''), 'x')) tq)
        SELECT {CAMPOS},
               EXISTS (SELECT 1 FROM unnest(p.patrones) x WHERE b.t ~* x) AS por_patron,
               LEAST(1, ts_rank(p.busqueda, b.tq) * 2) AS r_txt,
               LEAST(1, similarity(b.t, p.sintomas || ' ' || p.titulo) * 3) AS r_sim
        FROM problemas p JOIN productos pr ON pr.codigo=p.producto, b
        WHERE p.producto = ANY(%s)""", texto, texto, list(productos))
    out = []
    for f in filas:
        coin = (60 if f["por_patron"] else 0) + 25 * float(f["r_txt"] or 0) + 15 * float(f["r_sim"] or 0)
        if coin < (12 if f["por_patron"] else 30):
            continue
        f["coincidencia"] = round(min(coin, 100))
        f["prob_solucion"] = round(min(coin, 100) * float(f["confianza"]))
        out.append(f)
    out.sort(key=lambda x: (-x["prob_solucion"], -x["prob_ocurrencia"]))
    return out[:limite]


def conocido(pid):
    return q1(f"SELECT {CAMPOS} FROM problemas p JOIN productos pr ON pr.codigo=p.producto WHERE p.id=%s", pid)


def ocurrio(pid):
    ej("UPDATE problemas SET ocurrencias=ocurrencias+1 WHERE id=%s", pid)


# ------------------------------------------------------------------ verificar antes de remediar
def ocurrencia_nueva(cliente_id, numero_, pid, equipo, producto, texto, usuario):
    r = ej("""INSERT INTO incidentes(cliente_id, numero, equipo, producto, texto, estado, problema_id, puntaje, usuario)
              VALUES (%s,%s,%s,%s,%s,'por verificar',%s,100,%s) RETURNING id""", cliente_id, numero_, equipo, producto, texto, pid, usuario)
    return r["id"]


def verificacion_lanzada(cliente_id, iid, ticket_id, numero_):
    ej("UPDATE incidentes SET estado='verificando', verif_ticket_id=%s, verif_ticket=%s WHERE cliente_id=%s AND id=%s",
       ticket_id, numero_, cliente_id, iid)


def seguir_verificaciones(cliente_id, ticket_de, artefactos_de):
    """Cuando termina la verificación en el equipo: confirmado (se habilita la remediación) o no confirmado."""
    for i in q("SELECT * FROM incidentes WHERE cliente_id=%s AND estado='verificando' AND verif_ticket_id IS NOT NULL", cliente_id):
        t = ticket_de(i["verif_ticket_id"])
        if not t or t["estado"] in ("en_cola", "en_ejecucion", "pendiente_aprobacion"):
            continue
        res = (artefactos_de(t) or {}).get("faceops_verificacion", {}).get(i["equipo"]) if t["estado"] == "exitoso" else None
        if res is None:
            ej("UPDATE incidentes SET estado='por verificar', nota=%s WHERE id=%s", f"La verificación terminó {t['estado']}: reintentar", i["id"])
            continue
        ej("UPDATE incidentes SET estado=%s, verif=%s WHERE id=%s", "confirmado" if res.get("confirmado") else "no confirmado",
           json.dumps(res, ensure_ascii=False), i["id"])


# ------------------------------------------------------------------ integración de productos y diagnóstico automático
def integraciones(cliente_id):
    return q("SELECT * FROM integraciones WHERE cliente_id=%s ORDER BY producto, equipo", cliente_id)


def integracion_lanzada(cliente_id, producto, equipo, ticket_id):
    ej("""INSERT INTO integraciones(cliente_id, producto, equipo, estado, ticket_id, actualizado) VALUES (%s,%s,%s,'integrando',%s,now())
          ON CONFLICT (cliente_id, producto, equipo) DO UPDATE SET estado='integrando', ticket_id=excluded.ticket_id, actualizado=now()""",
       cliente_id, producto, equipo, ticket_id)


def diagnostico_lanzado(cliente_id, producto, equipo, ticket_id, usuario):
    return ej("INSERT INTO diagnosticos(cliente_id, producto, equipo, ticket_id, usuario) VALUES (%s,%s,%s,%s,%s) RETURNING id",
              cliente_id, producto, equipo, ticket_id, usuario)["id"]


def diagnostico(cliente_id, did):
    return q1("SELECT * FROM diagnosticos WHERE cliente_id=%s AND id=%s", cliente_id, did)


def diagnosticos(cliente_id, limite=10):
    return q("SELECT * FROM diagnosticos WHERE cliente_id=%s ORDER BY creado DESC LIMIT %s", cliente_id, limite)


def _py_regex(patron):
    return patron.replace("\\y", "\\b")


OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b, "==": lambda a, b: a == b}


def evaluar(producto, evidencia):
    """Compara la evidencia recolectada por Ansible (métricas y líneas del log) con los errores conocidos del producto.
    Señales que se cumplen (métricas) y líneas del log que calzan con los patrones → puntaje y evidencia por error conocido."""
    out = []
    lineas = evidencia.get("log_lineas") or []
    for p in q(f"SELECT {CAMPOS} FROM problemas p JOIN productos pr ON pr.codigo=p.producto WHERE p.producto=%s", producto):
        senales = []
        for sn in p["senales"] or []:
            v = evidencia.get(sn.get("metrica"))
            try:
                if v is not None and OPS.get(sn.get("op", ">="), OPS[">="])(float(v), float(sn["valor"])):
                    senales.append(sn.get("texto", "{v}").replace("{v}", str(v)))
            except (TypeError, ValueError):
                pass
        calzan = []
        for pat in p["patrones"] or []:
            try:
                rx = re.compile(_py_regex(pat), re.I)
            except re.error:
                continue
            calzan += [l for l in lineas if rx.search(l)]
        calzan = list(dict.fromkeys(calzan))
        if not senales and not calzan:
            continue
        puntaje = min(100, (55 if senales else 0) + (45 if calzan else 0) + min(15, len(calzan)))
        out.append({"id": p["id"], "numero": p["numero"], "titulo": p["titulo"], "tipo": p["tipo"], "severidad": p["severidad"],
                    "prob_ocurrencia": p["prob_ocurrencia"], "automatizacion": p["automatizacion"], "solucion": p["solucion"],
                    "senales": senales, "lineas": calzan[:5], "n_lineas": len(calzan), "puntaje": puntaje,
                    "prob_solucion": round(puntaje * float(p["confianza"]))})
    out.sort(key=lambda x: (-x["puntaje"], -x["prob_ocurrencia"]))
    return out


def seguir_soporte(cliente_id, ticket_de, artefactos_de, clave="faceops_postgresql"):
    """Integraciones y diagnósticos en curso: al terminar su ejecución en AAP se guardan los datos y se evalúa la evidencia."""
    for i in q("SELECT * FROM integraciones WHERE cliente_id=%s AND estado='integrando' AND ticket_id IS NOT NULL", cliente_id):
        t = ticket_de(i["ticket_id"])
        if not t or t["estado"] in ("en_cola", "en_ejecucion", "pendiente_aprobacion"):
            continue
        d = (artefactos_de(t) or {}).get(clave, {}).get(i["equipo"]) if t["estado"] == "exitoso" else None
        if d:
            ej("UPDATE integraciones SET estado='integrado', datos=%s, mensaje='', actualizado=now() WHERE cliente_id=%s AND producto=%s AND equipo=%s",
               json.dumps({k: v for k, v in d.items() if k != "log_lineas"}, ensure_ascii=False), cliente_id, i["producto"], i["equipo"])
        else:
            ej("UPDATE integraciones SET estado='error', mensaje=%s, actualizado=now() WHERE cliente_id=%s AND producto=%s AND equipo=%s",
               f"La integración terminó {t['estado']}", cliente_id, i["producto"], i["equipo"])
    for dg in q("SELECT * FROM diagnosticos WHERE cliente_id=%s AND estado='en curso' AND ticket_id IS NOT NULL", cliente_id):
        t = ticket_de(dg["ticket_id"])
        if not t or t["estado"] in ("en_cola", "en_ejecucion", "pendiente_aprobacion"):
            continue
        d = (artefactos_de(t) or {}).get(clave, {}).get(dg["equipo"]) if t["estado"] == "exitoso" else None
        if d is None:
            ej("UPDATE diagnosticos SET estado='error', terminado=now() WHERE id=%s", dg["id"])
            continue
        ej("UPDATE diagnosticos SET estado='terminado', evidencia=%s, hallazgos=%s, terminado=now() WHERE id=%s",
           json.dumps(d, ensure_ascii=False), json.dumps(evaluar(dg["producto"], d), ensure_ascii=False, default=float), dg["id"])
