"""Base de datos de FaceOps (SQLite). Un solo archivo, multi-cliente.

Tablas principales:
  clientes, usuarios, perfiles (usuario × cliente × segmentos × modo), automatizaciones (ficha común),
  tickets (cada ejecución), auditoria, equipos (inventario inteligente), segmentos, skills, politicas, hallazgos.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

RUTA = os.environ.get("FACEOPS_BD", "/var/lib/faceops/faceops.db")
_local = threading.local()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS clientes (
  id INTEGER PRIMARY KEY, codigo TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL, descripcion TEXT DEFAULT '',
  aap_url TEXT DEFAULT '', aap_token TEXT DEFAULT '', aap_verificar INTEGER DEFAULT 0,
  tickets_tipo TEXT DEFAULT 'faceops', tickets_conf TEXT DEFAULT '{}',
  clasificacion TEXT DEFAULT '{}',          -- inventario de AAP → {proveedor, ubicacion, ambiente}
  recoleccion TEXT DEFAULT '{}',            -- inventario de AAP → [{limite, credencial}]
  moneda TEXT DEFAULT 'USD', costo_hora REAL DEFAULT 40,  -- costo hora de referencia del cliente (valor generado)
  activo INTEGER DEFAULT 1, creado TEXT
);
CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY, usuario TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL, correo TEXT DEFAULT '',
  clave_hash TEXT NOT NULL, es_admin INTEGER DEFAULT 0, activo INTEGER DEFAULT 1, creado TEXT, ultimo_ingreso TEXT
);
CREATE TABLE IF NOT EXISTS perfiles (
  id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL, cliente_id INTEGER NOT NULL,
  segmentos TEXT DEFAULT '["*"]', modo TEXT DEFAULT 'lectura', aprobador INTEGER DEFAULT 0,
  UNIQUE(usuario_id, cliente_id)
);
CREATE TABLE IF NOT EXISTS automatizaciones (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, aap_tipo TEXT NOT NULL, aap_id INTEGER NOT NULL,
  nombre_aap TEXT NOT NULL, nombre TEXT NOT NULL, descripcion TEXT DEFAULT '', categoria TEXT DEFAULT 'General',
  tipo TEXT DEFAULT 'escritura', ambito TEXT DEFAULT '', usa_limite INTEGER DEFAULT 1, inventario TEXT DEFAULT '',
  requiere_aprobacion INTEGER DEFAULT 0, minutos_manual INTEGER DEFAULT 30, tarifa REAL DEFAULT 0,
  version TEXT DEFAULT '', proyecto TEXT DEFAULT '', etiquetas TEXT DEFAULT '[]', campos TEXT DEFAULT '[]',
  info TEXT DEFAULT '', visible INTEGER DEFAULT 1, actualizado TEXT,
  UNIQUE(cliente_id, aap_tipo, aap_id)
);
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, numero TEXT NOT NULL, automatizacion_id INTEGER,
  usuario_id INTEGER, estado TEXT NOT NULL, segmento TEXT DEFAULT '', limite TEXT DEFAULT '',
  parametros TEXT DEFAULT '{}', motivo TEXT DEFAULT '', aap_job_id INTEGER, aap_job_tipo TEXT DEFAULT 'job',
  resultado TEXT DEFAULT '', creado TEXT, inicio TEXT, fin TEXT, duracion_s INTEGER,
  valor_min INTEGER DEFAULT 0, tarifa REAL DEFAULT 0, externo_ref TEXT DEFAULT '', aprobado_por INTEGER,
  respuesta TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS auditoria (
  id INTEGER PRIMARY KEY, cuando TEXT, usuario TEXT, cliente_id INTEGER, accion TEXT, objeto TEXT, detalle TEXT
);
CREATE TABLE IF NOT EXISTS segmentos (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, tipo TEXT DEFAULT 'infraestructura',
  regla TEXT DEFAULT '{}', descripcion TEXT DEFAULT '', orden INTEGER DEFAULT 0, UNIQUE(cliente_id, nombre)
);
CREATE TABLE IF NOT EXISTS skills (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, descripcion TEXT DEFAULT '',
  regla TEXT DEFAULT '{}', UNIQUE(cliente_id, nombre)
);
CREATE TABLE IF NOT EXISTS politicas (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, descripcion TEXT DEFAULT '',
  severidad TEXT DEFAULT 'media', aplica TEXT DEFAULT '{}', controles TEXT DEFAULT '[]', activa INTEGER DEFAULT 1,
  UNIQUE(cliente_id, nombre)
);
CREATE TABLE IF NOT EXISTS equipos (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, aap_host_id INTEGER,
  inventario TEXT DEFAULT '', inventarios TEXT DEFAULT '[]', grupos TEXT DEFAULT '[]', so TEXT DEFAULT '', so_familia TEXT DEFAULT '',
  so_version TEXT DEFAULT '', ip TEXT DEFAULT '', cpu INTEGER, ram_mb INTEGER, discos TEXT DEFAULT '[]',
  servicios TEXT DEFAULT '[]', datos TEXT DEFAULT '{}', proveedor TEXT DEFAULT '', ubicacion TEXT DEFAULT '',
  ambiente TEXT DEFAULT '', skills_auto TEXT DEFAULT '[]', skills_manual TEXT DEFAULT '[]',
  estado TEXT DEFAULT 'sin datos', ultima_recoleccion TEXT, visto TEXT,
  UNIQUE(cliente_id, nombre)
);
CREATE TABLE IF NOT EXISTS hallazgos (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, equipo_id INTEGER, tipo TEXT, severidad TEXT,
  titulo TEXT, detalle TEXT, politica TEXT DEFAULT '', sugerencia TEXT DEFAULT '', creado TEXT
);
CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS flujos (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, clave TEXT NOT NULL, estado TEXT, verificado TEXT, aap_job_id INTEGER,
  UNIQUE(cliente_id, clave)
);
CREATE TABLE IF NOT EXISTS arquitecturas (       -- manifiesto de arquitectura de cada segmento de negocio (versionado)
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, segmento TEXT NOT NULL, version INTEGER DEFAULT 1,
  contenido TEXT NOT NULL, hash TEXT DEFAULT '', estado TEXT DEFAULT 'propuesta',   -- propuesta | aprobada | reemplazada | rechazada
  origen TEXT DEFAULT 'faceops', autor TEXT DEFAULT '', creado TEXT, aprobado_por TEXT DEFAULT '', aprobado_en TEXT DEFAULT '',
  git TEXT DEFAULT '{}'                          -- commit del manifiesto en el repositorio (si vino de Git)
);
CREATE TABLE IF NOT EXISTS recorridos (          -- última prueba de punta a punta de cada segmento
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, segmento TEXT NOT NULL, cuando TEXT, resultado TEXT DEFAULT '{}',
  UNIQUE(cliente_id, segmento)
);
"""


def ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def con():
    c = getattr(_local, "c", None)
    if c is None:
        os.makedirs(os.path.dirname(RUTA), exist_ok=True)
        c = sqlite3.connect(RUTA, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c = c
    return c


COLUMNAS_NUEVAS = {  # migraciones simples: columnas agregadas después de la v1
    "automatizaciones": {"guia": "TEXT DEFAULT '{}'", "tecnico": "TEXT DEFAULT '{}'", "demo": "TEXT DEFAULT ''",
                         "orden": "INTEGER DEFAULT 999", "aprobacion_aap": "TEXT DEFAULT ''", "aprobacion_wf": "INTEGER",
                         "skills": "TEXT DEFAULT '[]'", "aprobacion_ambientes": "TEXT DEFAULT '[]'",
                         "habilita_skill": "TEXT DEFAULT ''", "por_inventario": "INTEGER DEFAULT 0",
                         "compatibles": "TEXT DEFAULT '[]'"},
    "tickets": {"aap_aprobacion_id": "INTEGER", "aprobado_en": "TEXT DEFAULT ''", "filtros": "TEXT DEFAULT '{}'",
                "dur_ejecucion_s": "INTEGER", "dur_aprobacion_s": "INTEGER"},
    "perfiles": {"ambientes": "TEXT DEFAULT '[\"*\"]'", "roles": "TEXT DEFAULT '[]'"},   # roles: arquitectura, infraestructura
    "arquitecturas": {"ticket_id": "INTEGER", "decision": "TEXT DEFAULT '{}'"},
    "equipos": {"skills_habilitadas": "TEXT DEFAULT '{}'", "categoria": "TEXT DEFAULT ''", "forma": "TEXT DEFAULT ''",
                "segmentos_fijos": "TEXT DEFAULT '[]'", "energia": "TEXT DEFAULT ''", "energia_visto": "TEXT DEFAULT ''",
                "codigo": "TEXT DEFAULT ''", "codigo_visto": "TEXT DEFAULT ''"},
    "skills": {"automatizacion": "TEXT DEFAULT ''"},
    "segmentos": {"sistema": "TEXT DEFAULT ''", "ficha": "TEXT DEFAULT '{}'"},
    "flujos": {"latencia_ms": "REAL", "latencia_en": "TEXT DEFAULT ''"},
}


def iniciar():
    con().executescript(ESQUEMA)
    for tabla, cols in COLUMNAS_NUEVAS.items():
        hay = {r["name"] for r in con().execute(f"PRAGMA table_info({tabla})")}
        for col, tipo in cols.items():
            if col not in hay:
                con().execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
    con().commit()


def q(sql, *args):
    return [dict(r) for r in con().execute(sql, args).fetchall()]


def q1(sql, *args):
    r = con().execute(sql, args).fetchone()
    return dict(r) if r else None


def ej(sql, *args):
    cur = con().execute(sql, args)
    con().commit()
    return cur.lastrowid


def j(v, defecto=None):
    """JSON de la BD → objeto."""
    if v is None or v == "":
        return defecto
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return defecto


def auditar(usuario, cliente_id, accion, objeto, detalle=None):
    ej("INSERT INTO auditoria(cuando, usuario, cliente_id, accion, objeto, detalle) VALUES (?,?,?,?,?,?)",
       ahora(), usuario, cliente_id, accion, objeto, json.dumps(detalle or {}, ensure_ascii=False))


def ajuste(clave, valor=None):
    if valor is None:
        r = q1("SELECT valor FROM ajustes WHERE clave=?", clave)
        return r["valor"] if r else None
    ej("INSERT INTO ajustes(clave, valor) VALUES(?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", clave, valor)
