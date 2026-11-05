"""Gobierno › Buenas prácticas: catálogo por tecnología (desde el fabricante y los estándares), aprobado por el equipo de
Arquitectura del cliente, evaluado sobre toda la infraestructura. Lo que no se cumple genera una recomendación al dueño
del componente (plataforma o máquina con su sistema operativo)."""
import os
import re

import yaml

from .bd import ahora, auditar, ej, j, q, q1

RUTA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "buenas-practicas.yml")
_cache = {"m": 0, "d": None}


def catalogo():
    m = os.path.getmtime(RUTA)
    if _cache["m"] != m:
        _cache.update(m=m, d=yaml.safe_load(open(RUTA, encoding="utf-8")) or {})
    return _cache["d"]


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS bp_aprobaciones (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, codigo TEXT NOT NULL, version INTEGER NOT NULL, estado TEXT NOT NULL,
      por TEXT, fecha TEXT, nota TEXT DEFAULT '', UNIQUE(cliente_id, codigo, version));
    CREATE TABLE IF NOT EXISTS bp_recomendaciones (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, codigo TEXT NOT NULL, componente TEXT NOT NULL, dueno TEXT DEFAULT '',
      correo TEXT DEFAULT '', enviada TEXT, medio TEXT DEFAULT '', UNIQUE(cliente_id, codigo, componente));""")


def estado_aprobacion(cliente_id, p):
    _tabla()
    r = q1("SELECT * FROM bp_aprobaciones WHERE cliente_id=? AND codigo=? AND version=?", cliente_id, p["codigo"], p["version"])
    return r or {"estado": "propuesta"}


def aprobar(cliente_id, codigo, decision, quien, nota=""):
    _tabla()
    p = next((x for x in catalogo()["practicas"] if x["codigo"] == codigo), None)
    if not p or decision not in ("aprobada", "rechazada"):
        raise ValueError("Buena práctica o decisión no válida")
    ej("INSERT INTO bp_aprobaciones(cliente_id, codigo, version, estado, por, fecha, nota) VALUES (?,?,?,?,?,?,?) "
       "ON CONFLICT(cliente_id, codigo, version) DO UPDATE SET estado=excluded.estado, por=excluded.por, fecha=excluded.fecha, nota=excluded.nota",
       cliente_id, codigo, p["version"], decision, quien, ahora(), nota[:200])
    auditar(quien, cliente_id, f"buena práctica {decision}", f"{codigo} v{p['version']}")


def _es_rhel(e):
    so = (e.get("so") or "").lower()
    return bool(re.match(r"(red ?hat|rhel)", so)) and "coreos" not in so


def _componentes(p, parque, plataformas_de, id_plataforma):
    """[(clave, nombre visible, equipo, código de plataforma o None)] a los que aplica la práctica."""
    ap = p.get("aplica", "")
    out = []
    for e, r in parque:
        if ap in ("so:rhel", "todo") and _es_rhel(e):
            out.append((f"equipo:{e['id']}", e["nombre"], e, None))
        elif ap == "so:windows" and "windows" in (e.get("so") or "").lower():
            out.append((f"equipo:{e['id']}", e["nombre"], e, None))
        for c in set(plataformas_de(e, r)):
            if c == ap or (ap == "todo" and not c.startswith("so:") and c not in ("rhel", "windows-server")):
                out.append((f"plataforma:{id_plataforma(c, e['id'])}", id_plataforma(c, e["id"]), e, c))
    return out


def _verificar(p, e, cod, cliente_id):
    """'cumple' | 'no cumple' | 'sin dato'."""
    from .duenos import de as dueno_de
    v = p.get("verificacion") or {}
    t = v.get("tipo")
    d = j(e.get("datos"), {})
    serv = " ".join(j(e.get("servicios"), []) if isinstance(e.get("servicios"), str) else (e.get("servicios") or [])).lower()
    if t == "dato":
        x = d.get(v["clave"])
        if x is None:
            return "sin dato"
        if "max" in v:
            try:
                return "cumple" if int(x) <= int(v["max"]) else "no cumple"
            except (TypeError, ValueError):
                return "sin dato"
        return "cumple" if str(x).lower() == str(v.get("igual")).lower() else "no cumple"
    if t == "servicio":
        return ("cumple" if v["nombre"].lower() in serv else "no cumple") if serv else "sin dato"
    if t == "skill":
        return "cumple" if v["nombre"] in (e.get("skills") or []) else "no cumple"
    if t == "dueno":
        if cod:
            from .vistas import id_plataforma
            return "cumple" if dueno_de(cliente_id, "plataforma", id_plataforma(cod, e["id"])) or dueno_de(cliente_id, "equipo", e["id"]) else "no cumple"
        return "cumple" if dueno_de(cliente_id, "equipo", e["id"]) else "no cumple"
    if t == "credencial":
        from .boveda import disponible, web
        return ("cumple" if web(e["nombre"], cod) else "no cumple") if disponible() else "sin dato"
    if t == "certificado":
        try:
            from flask import g
            from .certificados_panel import de_equipo, leer
            from .vistas import equipos_visibles
            cs = de_equipo(leer(g.cliente, equipos_visibles()), e["nombre"])
            return "cumple" if any(c.get("estado") in ("vigente", "VALID", "válido") or (c.get("dias") or 0) > 0 for c in cs) else "no cumple"
        except Exception:  # noqa: BLE001
            return "sin dato"
    if t == "soporte":
        try:
            from .versiones import de as version_de
            ver = version_de(e, cod) or {}
            fase = (ver.get("ciclo") or {}).get("fase")
            return "sin dato" if not fase or fase == "desconocido" else ("cumple" if fase in ("completo", "mantencion") else "no cumple")
        except Exception:  # noqa: BLE001
            return "sin dato"
    return "sin dato"


def evaluar(cliente_id, parque, solo_tec=None):
    """Cada práctica con su aprobación y, si está aprobada, su cumplimiento por componente (con el dueño que lo recibe)."""
    from .duenos import de as dueno_de
    from .vistas import _plataformas_de, id_plataforma
    cat = catalogo()
    out = []
    for p in cat.get("practicas", []):
        if solo_tec and p["tec"] != solo_tec:
            continue
        x = dict(p, aprobacion=estado_aprobacion(cliente_id, p), resultados=[])
        comps = _componentes(p, parque, _plataformas_de, id_plataforma)
        if x["aprobacion"]["estado"] == "aprobada":
            for clave, nombre, e, cod in comps:
                du = (dueno_de(cliente_id, "plataforma", nombre) if cod else None) or dueno_de(cliente_id, "equipo", e["id"])
                rec = q1("SELECT enviada, medio FROM bp_recomendaciones WHERE cliente_id=? AND codigo=? AND componente=?", cliente_id, p["codigo"], clave)
                x["resultados"].append({"clave": clave, "nombre": nombre, "equipo": e["nombre"], "eid": e["id"], "cod": cod,
                                        "estado": _verificar(p, e, cod, cliente_id), "dueno": du, "recomendada": rec})
        x["aplica_a"] = len(comps)
        x["cumple"] = sum(1 for r in x["resultados"] if r["estado"] == "cumple")
        x["no_cumple"] = sum(1 for r in x["resultados"] if r["estado"] == "no cumple")
        out.append(x)
    return out


def resumen(cliente_id, parque):
    cat = catalogo()
    ev = evaluar(cliente_id, parque)
    cajas = []
    for k, t in cat.get("tecnologias", {}).items():
        xs = [x for x in ev if x["tec"] == k]
        if not xs:
            continue
        evaluados = sum(x["cumple"] + x["no_cumple"] for x in xs)
        cajas.append({"tec": k, "nombre": t["nombre"], "fabricante": t.get("fabricante", ""), "n": len(xs),
                      "aprobadas": sum(1 for x in xs if x["aprobacion"]["estado"] == "aprobada"),
                      "propuestas": sum(1 for x in xs if x["aprobacion"]["estado"] == "propuesta"),
                      "cumplimiento": round(100 * sum(x["cumple"] for x in xs) / evaluados) if evaluados else None,
                      "no_cumple": sum(x["no_cumple"] for x in xs)})
    return cajas


def enviar_recomendaciones(cliente, parque, codigo, quien):
    """Una recomendación por componente que no cumple: correo al dueño (si hay correo configurado) y registro en FaceOps."""
    from . import correo
    _tabla()
    x = next((p for p in evaluar(cliente["id"], parque) if p["codigo"] == codigo), None)
    if not x or x["aprobacion"]["estado"] != "aprobada":
        raise ValueError("Solo se recomiendan buenas prácticas aprobadas")
    enviadas, sin_dueno = 0, []
    for r in x["resultados"]:
        if r["estado"] != "no cumple":
            continue
        if not r["dueno"]:
            sin_dueno.append(r["nombre"])
            continue
        medio = "registro"
        if r["dueno"].get("correo") and correo.configurado():
            try:
                correo.enviar(r["dueno"]["correo"], f"FaceOps · Recomendación {x['codigo']}: {x['titulo']} · {r['nombre']}",
                              f"Hola {r['dueno']['nombre']}:\n\n{r['nombre']} (máquina {r['equipo']}) no cumple la buena práctica aprobada "
                              f"{x['codigo']} «{x['titulo']}».\n\n{x['descripcion']}\n\nRecomendación: {x['recomendacion']}\n\n"
                              f"Fuente: {x['fuente']}\n\nFaceOps · {cliente['nombre']}")
                medio = "correo"
            except Exception:  # noqa: BLE001
                medio = "registro (el correo falló)"
        ej("INSERT INTO bp_recomendaciones(cliente_id, codigo, componente, dueno, correo, enviada, medio) VALUES (?,?,?,?,?,?,?) "
           "ON CONFLICT(cliente_id, codigo, componente) DO UPDATE SET dueno=excluded.dueno, correo=excluded.correo, enviada=excluded.enviada, medio=excluded.medio",
           cliente["id"], codigo, r["clave"], r["dueno"]["nombre"], r["dueno"].get("correo") or "", ahora(), medio)
        enviadas += 1
    auditar(quien, cliente["id"], "recomendaciones de buena práctica", codigo, {"enviadas": enviadas, "sin_dueno": len(sin_dueno)})
    return enviadas, sin_dueno
