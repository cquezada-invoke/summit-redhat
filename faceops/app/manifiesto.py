"""Stack Tecnológico (nivel 1: manifiesto de arquitectura): principios, capas (negocio → infraestructura), gobierno transversal, conceptos y tecnologías aceptadas
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
ESTADOS = {"aceptada": "Adoptar", "evaluacion": "En evaluación", "contener": "Contener", "retirar": "Retirar"}
# chip «En el stack» de las plataformas (cajas de lab/plataformas y nivel 3 del Stack Tecnológico): una sola fuente
ESTADO_STACK = {"aceptada": ("En el stack · Adoptar", "s-ok"), "evaluacion": ("En el stack · En evaluación", "s-media"),
                "contener": ("Contener", "s-media"), "retirar": ("Retirar", "s-alta"), "fuera": ("Fuera del stack", "s-alta")}
# clave de plataforma del inventario → clave/skill del manifiesto cuando no coinciden
ALIAS_PLAT = {"postgresql": "postgres", "jboss-eap": "jboss", "wireguard": "vpn", "redis-insight": "redisinsight", "datalake": "trino",
              "data-lake": "trino", "activemq": "artemis", "aap-controller": "aap", "aap-gateway": "aap", "prometheus": "prometheus",
              "grafana": "prometheus", "samba-ad": "active-directory", "ad": "active-directory", "idm": "red-hat-idm", "bind": "dns"}
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
    return _trazar(b)


def _trazar(b):
    """Principio (N1) → capacidad (N2): cada concepto con sus principios y cada principio con sus capacidades."""
    caps = {c["clave"]: (capa, c) for capa in b["capas"] + [dict(b["gobierno"], clave="gobierno")] for c in capa["conceptos"]}
    for c in (x for _, x in caps.values()):
        c["principios"] = []
    for p in b.get("principios") or []:
        p["caps"] = []
        for k in p.get("capacidades") or []:
            if k in caps:
                capa, c = caps[k]
                c["principios"].append({"codigo": p["codigo"], "nombre": p["nombre"]})
                p["caps"].append({"clave": k, "nombre": c["nombre"], "capa": capa["nombre"]})
    return b


def tecnologia_de(ps, clave):
    """Tecnología del manifiesto para una clave de plataforma del inventario (ps = por_skill)."""
    return ps.get(clave) or ps.get(ALIAS_PLAT.get(clave, "")) or ps.get(clave.split(":")[-1])


def estado_plataforma(ps, clave):
    """(estado, (texto del chip, clase)) de una plataforma instalada según el manifiesto; «fuera» si no está."""
    t = tecnologia_de(ps, clave)
    e = (t or {}).get("estado") or "fuera"
    return e, ESTADO_STACK.get(e, ESTADO_STACK["fuera"])


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
    import re
    so_re = {"rhel": r"(?:red ?hat(?: enterprise linux)?|rhel)\D*(\d+)", "windows": r"windows server\D*(\d{4})",
             "ubuntu": r"ubuntu\D*(\d+\.\d+)", "centos": r"centos\D*(\d+)"}
    for p_, rx in so_re.items():   # sistemas operativos: desde el inventario (no hace falta el barrido)
        if por_prod.get(p_):
            continue
        cuenta = {}
        for e in eqs:
            mm = re.search(rx, e.get("so") or "", re.I)
            if mm:
                cuenta.setdefault(mm.group(1), []).append(e["nombre"])
        if cuenta:
            from .ciclo_vida import datos, evaluar_producto
            for v, ns in cuenta.items():
                ev = evaluar_producto(p_, version_ciclo(p_, v)) if p_ in datos() else None
                por_prod.setdefault(p_, []).append({"version": v, "n": len(ns), "fase": (ev or {}).get("fase", "desconocido"), "equipos": ns})
    for t in tecnologias(m):
        maqs = sorted({e["nombre"] for e in eqs if (t.get("skill") or t["clave"]) in (e.get("skills") or [])})
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
            if sk in AGENTES_SK or sk in ignorar or sk in ("app", "linux", "windows"):
                continue
            t = ps.get(sk)
            if t and t["estado"] in ("aceptada", "evaluacion", "contener"):
                continue
            x = out.setdefault(sk, {"skill": sk, "nombre": t["nombre"] if t else sk, "estado": t["estado"] if t else "fuera", "equipos": []})
            x["equipos"].append(e["nombre"])
    return sorted(out.values(), key=lambda x: (-len(x["equipos"]), x["skill"]))


# ------------------------------------------------------------------ roadmap de salida o reemplazo de lo que está fuera del manifiesto
SALIDA = {"reemplazar": "Reemplazar por una tecnología aceptada", "retirar": "Retirar del parque", "regularizar": "Regularizar (aceptarla en el manifiesto)"}
AVANCE = {"planificado": "Planificado", "en_curso": "En curso", "hecho": "Hecho"}


def _clave(skill):
    return "manifiesto:" + skill


def roadmap(cliente_id, fuera):
    """Cada tecnología instalada fuera del manifiesto con su plan de salida (decisión, reemplazo, responsable, fecha objetivo y avance).
    Lo que ya no está instalado y tenía plan queda como cumplido."""
    from .racionalizacion import _tabla
    _tabla()
    planes = {}
    for r in q("SELECT * FROM stack_decisiones WHERE cliente_id=? AND tecnologia LIKE 'manifiesto:%'", cliente_id):
        try:
            x = json.loads(r.get("nota") or "{}")
        except ValueError:
            x = {"nota": r.get("nota") or ""}
        planes[r["tecnologia"][len("manifiesto:"):]] = dict(r, avance=x.get("avance", "planificado"), nota_txt=x.get("nota", ""))
    hoy = __import__("datetime").date.today().isoformat()
    for f in fuera:
        p = planes.get(f["skill"])
        f["plan"] = p
        f["vencido"] = bool(p and p.get("fecha_objetivo") and p["fecha_objetivo"] < hoy)
    vivos = {f["skill"] for f in fuera}
    cumplidos = [dict(p, skill=s) for s, p in planes.items() if s not in vivos]
    return {"sin_plan": sum(1 for f in fuera if not f["plan"]), "vencidos": sum(1 for f in fuera if f["vencido"]), "cumplidos": cumplidos,
            "cronograma": sorted((f for f in fuera if f["plan"]), key=lambda f: f["plan"].get("fecha_objetivo") or "9999")}


def guardar_roadmap(cliente_id, skill, f, autor):
    from .racionalizacion import _tabla
    _tabla()
    d = f.get("decision", "")
    if d not in SALIDA:
        raise ValueError("Elige qué se hará: reemplazar, retirar o regularizar")
    if d == "reemplazar" and not (f.get("reemplazo") or "").strip():
        raise ValueError("Indica la tecnología aceptada que la reemplaza")
    fecha = (f.get("fecha_objetivo") or "")[:10]
    if d != "regularizar" and not fecha:
        raise ValueError("Pon una fecha objetivo para la salida")
    av = f.get("avance") if f.get("avance") in AVANCE else "planificado"
    nota = json.dumps({"avance": av, "nota": (f.get("nota") or "").strip()[:400]}, ensure_ascii=False)
    ej("INSERT INTO stack_decisiones(cliente_id, tecnologia, decision, reemplazo, responsable, fecha_objetivo, nota, autor, fecha) VALUES (?,?,?,?,?,?,?,?,?) "
       "ON CONFLICT(cliente_id, tecnologia) DO UPDATE SET decision=excluded.decision, reemplazo=excluded.reemplazo, responsable=excluded.responsable, "
       "fecha_objetivo=excluded.fecha_objetivo, nota=excluded.nota, autor=excluded.autor, fecha=excluded.fecha",
       cliente_id, _clave(skill)[:200], d, (f.get("reemplazo") or "").strip()[:120], (f.get("responsable") or "").strip()[:80], fecha, nota, autor, ahora())
