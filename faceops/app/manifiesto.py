"""Manifiesto de arquitectura: capas (negocio → infraestructura), gobierno transversal, conceptos y tecnologías aceptadas
con sus versiones. Base en datos/manifiesto-arquitectura.yml; cada cliente lo ajusta (tabla manifiesto_ajustes, auditado).
FaceOps lo usa para decidir qué plataformas se pueden instalar y para marcar lo instalado fuera del manifiesto."""
import json
import os

import yaml

from .bd import ahora, con, ej, q

ESQUEMA = """
CREATE TABLE IF NOT EXISTS manifiesto_ajustes (id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, tecnologia TEXT NOT NULL,
  estado TEXT, versiones TEXT DEFAULT '[]', motivo TEXT DEFAULT '', usuario TEXT, fecha TEXT, UNIQUE(cliente_id, tecnologia));
"""
ESTADOS = {"aceptada": "Aceptada", "evaluacion": "En evaluación", "contener": "Contener", "retirar": "Retirar"}
INSTALABLE = ("aceptada", "evaluacion")
_base = {"v": None, "t": 0}


def iniciar():
    con().executescript(ESQUEMA)


def _cargar_base():
    ruta = os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "manifiesto-arquitectura.yml")
    t = os.path.getmtime(ruta)
    if _base["v"] is None or t != _base["t"]:
        _base.update(v=yaml.safe_load(open(ruta, encoding="utf-8")), t=t)
    return _base["v"]


def manifiesto(cliente_id):
    """{'capas': [...], 'gobierno': {...}} con los ajustes del cliente aplicados; cada tecnología con capa y concepto."""
    try:
        iniciar()
        aj = {x["tecnologia"]: x for x in q("SELECT * FROM manifiesto_ajustes WHERE cliente_id=?", cliente_id)}
    except Exception:  # noqa: BLE001
        aj = {}
    b = json.loads(json.dumps(_cargar_base()))
    for capa in b["capas"] + [dict(b["gobierno"], clave="gobierno")]:
        for c in capa["conceptos"]:
            for t in c["tecnologias"]:
                t.setdefault("versiones", [])
                t["capa"], t["concepto"] = capa["nombre"], c["nombre"]
                if t["clave"] in aj:
                    a = aj[t["clave"]]
                    t["estado"] = a["estado"] or t["estado"]
                    t["versiones"] = json.loads(a["versiones"] or "[]")
                    t["ajuste"] = {"usuario": a["usuario"], "fecha": a["fecha"], "motivo": a["motivo"]}
    return b


def tecnologias(m):
    for capa in m["capas"] + [m["gobierno"]]:
        for c in capa["conceptos"]:
            yield from c["tecnologias"]


def por_skill(cliente_id):
    """skill de FaceOps (o clave de la tecnología) → tecnología del manifiesto."""
    out = {}
    for t in tecnologias(manifiesto(cliente_id)):
        out.setdefault(t["clave"], t)
        if t.get("skill"):
            out[t["skill"]] = t
    return out


def permite(cliente_id, skill):
    """(bool, motivo): ¿se puede instalar la plataforma de esta skill según el manifiesto?"""
    t = por_skill(cliente_id).get(skill)
    if not t:
        return False, "no está en el manifiesto de arquitectura"
    if t["estado"] not in INSTALABLE:
        return False, f"en el manifiesto está como «{ESTADOS.get(t['estado'], t['estado'])}»: no se instala nueva"
    return True, ("en evaluación: requiere aprobación de Arquitectura" if t["estado"] == "evaluacion" else "")


def ajustar(cliente_id, clave, estado, versiones, motivo, usuario):
    if estado not in ESTADOS:
        raise ValueError("Estado no válido")
    if clave not in {t["clave"] for t in tecnologias(manifiesto(cliente_id))}:
        raise ValueError("Tecnología desconocida")
    vs = [v.strip() for v in (versiones or "").replace(";", ",").split(",") if v.strip()]
    iniciar()
    ej("INSERT INTO manifiesto_ajustes (cliente_id, tecnologia, estado, versiones, motivo, usuario, fecha) VALUES (?,?,?,?,?,?,?) "
       "ON CONFLICT(cliente_id, tecnologia) DO UPDATE SET estado=excluded.estado, versiones=excluded.versiones, motivo=excluded.motivo, "
       "usuario=excluded.usuario, fecha=excluded.fecha", cliente_id, clave, estado, json.dumps(vs), (motivo or "")[:300], usuario, ahora())


def con_instalado(m, eqs, filas_stack):
    """Agrega a cada tecnología lo instalado en el parque: máquinas (por skill) y versiones/fase (por producto del ciclo de vida)."""
    from .ciclo_vida import version_ciclo
    por_prod = {}
    for f in filas_stack or []:
        if f.get("producto"):
            por_prod.setdefault(f["producto"], []).append(f)
    for t in tecnologias(m):
        maqs = sorted({e["nombre"] for e in eqs if t.get("skill") and t["skill"] in (e.get("skills") or [])})
        fs = por_prod.get(t.get("producto"), [])
        vers = []
        for f in fs:
            fuera_v = bool(t["versiones"]) and version_ciclo(t["producto"], f["version"]) not in t["versiones"] and str(f["version"]) not in t["versiones"]
            vers.append({"version": f["version"], "n": f["n"], "fase": f["fase"], "fuera": fuera_v})
            maqs = sorted(set(maqs) | set(f.get("equipos") or []))
        t["instalado"] = {"n": len(maqs), "equipos": maqs[:30], "versiones": vers,
                          "fuera_version": sum(v["n"] for v in vers if v["fuera"])}
    return m


def fuera_del_manifiesto(cliente_id, eqs, ignorar=()):
    """Plataformas instaladas (skills de FaceOps) que el manifiesto no acepta: [{skill, estado, equipos}]."""
    from .vistas import AGENTES_SK
    ps = por_skill(cliente_id)
    out = {}
    for e in eqs:
        for sk in e.get("skills") or []:
            if sk in AGENTES_SK or sk in ignorar:
                continue
            t = ps.get(sk)
            if t and t["estado"] in ("aceptada", "evaluacion", "contener"):
                continue
            x = out.setdefault(sk, {"skill": sk, "nombre": t["nombre"] if t else sk, "estado": t["estado"] if t else "fuera", "equipos": []})
            x["equipos"].append(e["nombre"])
    return sorted(out.values(), key=lambda x: (-len(x["equipos"]), x["skill"]))
