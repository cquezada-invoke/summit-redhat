"""Posiciones de las cajas del mapa de un segmento: cuando alguien mueve cajas quedan guardadas en la base de FaceOps (por
segmento, ambiente y vista) y se respetan en las cargas siguientes y para todos los usuarios. El dibujo automático solo
ubica las cajas nuevas (sin posición guardada) cerca de sus vecinas. «Reordenar» borra lo guardado y vuelve al automático."""
import json

from flask import g, request, session

from . import requiere_login
from .bd import ahora, con, ej, q1
from .vistas import bp

_ok = set()
MAX_NODOS = 2000


def _tabla():
    k = id(con())
    if k in _ok:
        return
    con().executescript("""CREATE TABLE IF NOT EXISTS mapa_posiciones (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, segmento TEXT NOT NULL, ambiente TEXT NOT NULL DEFAULT '',
      vista TEXT NOT NULL DEFAULT '', posiciones TEXT NOT NULL DEFAULT '{}', por TEXT DEFAULT '', en TEXT,
      UNIQUE(cliente_id, segmento, ambiente, vista));""")
    _ok.add(k)


@bp.route("/mapa/posiciones", methods=["GET", "POST"])
@requiere_login
def mapa_posiciones():
    """GET: {posiciones: {id: {x, y}}} guardadas. POST: guardar (perfil de escritura) o, con accion=borrar, volver al
    dibujo automático."""
    from .permisos import puede_ver_segmento
    _tabla()
    seg, amb, vista = request.values.get("segmento", ""), request.values.get("ambiente", ""), request.values.get("vista", "")
    if not seg or not puede_ver_segmento(g.usuario, g.cliente["id"], seg):
        return {"error": "Segmento no encontrado"}, 404
    clave = (g.cliente["id"], seg, amb, vista)
    if request.method == "GET":
        r = q1("SELECT posiciones, por, en FROM mapa_posiciones WHERE cliente_id=? AND segmento=? AND ambiente=? AND vista=?", *clave)
        return {"posiciones": json.loads(r["posiciones"]) if r else {}, "por": (r or {}).get("por", ""), "en": (r or {}).get("en", "")}
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura para guardar el orden del mapa"}, 403
    if request.form.get("accion") == "borrar":
        ej("DELETE FROM mapa_posiciones WHERE cliente_id=? AND segmento=? AND ambiente=? AND vista=?", *clave)
        return {"ok": True}
    try:
        pos = json.loads(request.form.get("posiciones") or "{}")
        pos = {str(k)[:200]: {"x": round(float(v["x"]), 1), "y": round(float(v["y"]), 1)} for k, v in list(pos.items())[:MAX_NODOS]}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {"error": "Posiciones no válidas"}, 400
    ej("INSERT INTO mapa_posiciones(cliente_id, segmento, ambiente, vista, posiciones, por, en) VALUES (?,?,?,?,?,?,?) "
       "ON CONFLICT(cliente_id, segmento, ambiente, vista) DO UPDATE SET posiciones=excluded.posiciones, por=excluded.por, en=excluded.en",
       *clave, json.dumps(pos), g.usuario["usuario"], ahora())
    return {"ok": True, "n": len(pos)}
