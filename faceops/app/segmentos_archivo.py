"""Archivar un segmento de negocio: sale de las listas, del mapa y de la facturación, y libera sus máquinas (dejan de
estar reservadas para él y quedan disponibles para asignarse a otros segmentos). NO elimina máquinas virtuales ni toca
nada en AAP: es una decisión de gobierno, reversible con «Restaurar»."""
import json

from .bd import ahora, auditar, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS segmentos_archivados (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, fila TEXT NOT NULL, equipos TEXT DEFAULT '[]',
  arquitectura_version INTEGER, motivo TEXT DEFAULT '', usuario TEXT, fecha TEXT);
"""


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)


def archivado(cliente_id, nombre):
    return q1("SELECT * FROM segmentos_archivados WHERE cliente_id=? AND nombre=? ORDER BY id DESC LIMIT 1", cliente_id, nombre)


def lista(cliente_id):
    out = q("SELECT * FROM segmentos_archivados WHERE cliente_id=? ORDER BY id DESC", cliente_id)
    for x in out:
        x["equipos_l"] = j(x["equipos"], [])
        x["fila_d"] = j(x["fila"], {})
    return out


def archivar(usuario, cliente, nombre, motivo, equipos):
    """equipos: los nombres de las máquinas que tenía (quedan anotadas para poder restaurar y para la auditoría)."""
    s = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", cliente["id"], nombre)
    if not s:
        raise ValueError("No existe ese segmento")
    if not (motivo or "").strip():
        raise ValueError("Indica el motivo del archivo")
    abiertos = q1("SELECT COUNT(*) n FROM tickets WHERE cliente_id=? AND segmento=? AND estado IN ('en_cola','en_ejecucion','pendiente_aprobacion')",
                  cliente["id"], nombre)["n"]
    if abiertos:
        raise ValueError(f"Tiene {abiertos} ticket(s) en curso: espera que terminen o cancélalos antes de archivar")
    v = q1("SELECT MAX(version) v FROM arquitecturas WHERE cliente_id=? AND segmento=? AND estado='aprobada'", cliente["id"], nombre)
    ej("INSERT INTO segmentos_archivados(cliente_id, nombre, fila, equipos, arquitectura_version, motivo, usuario, fecha) VALUES (?,?,?,?,?,?,?,?)",
       cliente["id"], nombre, json.dumps(s, ensure_ascii=False, default=str), json.dumps(sorted(equipos), ensure_ascii=False),
       v["v"] if v else None, motivo.strip(), usuario["usuario"], ahora())
    ej("UPDATE arquitecturas SET estado='archivada' WHERE cliente_id=? AND segmento=? AND estado IN ('aprobada','propuesta')", cliente["id"], nombre)
    ej("DELETE FROM segmentos WHERE id=?", s["id"])
    auditar(usuario["usuario"], cliente["id"], "archivar segmento", nombre, {"motivo": motivo.strip(), "equipos_liberados": sorted(equipos)})


def restaurar(usuario, cliente, aid):
    a = q1("SELECT * FROM segmentos_archivados WHERE id=? AND cliente_id=?", aid, cliente["id"])
    if not a:
        raise ValueError("No existe")
    if q1("SELECT 1 FROM segmentos WHERE cliente_id=? AND nombre=?", cliente["id"], a["nombre"]):
        raise ValueError(f"Ya existe un segmento activo llamado «{a['nombre']}»")
    f = j(a["fila"], {})
    cols = [k for k in f if k != "id"]
    ej(f"INSERT INTO segmentos({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", *[f[k] for k in cols])
    if a["arquitectura_version"]:
        ej("UPDATE arquitecturas SET estado='aprobada' WHERE cliente_id=? AND segmento=? AND version=? AND estado='archivada'",
           cliente["id"], a["nombre"], a["arquitectura_version"])
    ej("DELETE FROM segmentos_archivados WHERE id=?", aid)
    auditar(usuario["usuario"], cliente["id"], "restaurar segmento", a["nombre"], {})
    return a["nombre"]
