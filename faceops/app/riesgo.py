"""Riesgo de cada máquina ante caídas o fallas: los errores conocidos que le aplican según su sistema operativo, sus
paquetes y las plataformas que corre (skills y servicios), cuáles tienen su prevención ejecutada, cuáles están en deuda de
aprobación y cuáles tienen la condición activa ahora (p. ej. disco lleno). Se reevalúa en cada recolección: si una condición
vuelve a darse (el disco se llena otra vez), vuelve a alertar como hallazgo."""
import json
import time

from .bd import j

PESO = {"critica": 1.0, "alta": 0.8, "media": 0.5, "baja": 0.3}
PREVENIDO = 0.25          # un error con su prevención ejecutada pesa un cuarto en el riesgo
_cache = {"t": 0, "problemas": [], "productos": []}


def _base():
    from . import kb
    if time.time() - _cache["t"] > 120:
        _cache["productos"] = kb.q("SELECT codigo, nombre, skill, detecta FROM productos")
        _cache["problemas"] = kb.q(f"SELECT {kb.CAMPOS} FROM problemas p JOIN productos pr ON pr.codigo=p.producto")
        _cache["t"] = time.time()
    return _cache["productos"], _cache["problemas"]


def productos_de(e, productos):
    """Tecnologías del equipo: sistema operativo, skills y servicios en ejecución."""
    so = f"{e.get('so') or ''} {e.get('so_familia') or ''}".lower()
    skills = {s.lower() for s in (e.get("skills") or [])}
    servicios = " ".join(j(e.get("servicios"), []) if isinstance(e.get("servicios"), str) else (e.get("servicios") or [])).lower()
    out = []
    for p in productos:
        cod = p["codigo"]
        if cod == "rhel":
            ok = "red hat" in so or "redhat" in so or so.startswith("rhel")
        elif cod == "openshift":
            ok = "openshift" in so or (e.get("categoria") or "") == "clúster openshift"
        else:
            alias = [a.lower() for a in (p.get("detecta") or [])] + ([p["skill"].lower()] if p.get("skill") else [])
            ok = any(a in skills for a in alias) or any(a and a in servicios for a in alias)
        if ok:
            out.append(cod)
    return out


def datos(e):
    """Lo que se sabe del equipo por la recolección (sin conectarse): uso de disco, parches pendientes, versión."""
    discos = j(e.get("discos"), []) if isinstance(e.get("discos"), str) else (e.get("discos") or [])
    mx = max(discos, key=lambda d: d.get("uso") or 0) if discos else None
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    try:
        mayor = int(str(e.get("so_version") or "").split(".")[0])
    except ValueError:
        mayor = None
    return {"disco_max_pct": (mx or {}).get("uso"), "punto": (mx or {}).get("punto", ""),
            "parches_seguridad": d.get("parches_seguridad"), "so_mayor": mayor}


OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b}


def condicion_activa(p, dat):
    for c in p.get("condiciones") or []:
        v = dat.get(c.get("dato"))
        try:
            if v is not None and OPS.get(c.get("op", ">="), OPS[">="])(float(v), float(c["valor"])):
                return c.get("texto", "{v}").replace("{v}", str(v)).replace("{punto}", dat.get("punto") or "")
        except (TypeError, ValueError):
            continue
    return None


def evaluar(cliente_id, e, con_cobertura=True):
    """Errores conocidos que aplican al equipo, su estado (activo / prevenido / en deuda / sin prevención) y el riesgo total."""
    from . import kb
    if not kb.disponible():
        return None
    productos, problemas = _base()
    prods = productos_de(e, productos)
    dat = datos(e)
    diag = {}
    try:   # lo que vio el último diagnóstico integrado del equipo (24 h): también es condición activa
        for dg in kb.q("SELECT hallazgos FROM diagnosticos WHERE cliente_id=%s AND equipo=%s AND estado='terminado' "
                       "AND creado > now() - interval '24 hours' ORDER BY creado DESC LIMIT 3", cliente_id, e["nombre"]):
            for h in dg["hallazgos"] or []:
                diag.setdefault(h["id"], "; ".join(h.get("senales") or []) or f"{h.get('n_lineas', 0)} línea(s) del log")
    except Exception:  # noqa: BLE001
        pass
    from .deuda import cobertura
    filas, sin_riesgo = [], 1.0
    for p in problemas:
        if p["producto"] not in prods:
            continue
        activa = condicion_activa(p, dat) or diag.get(p["id"])
        cob = cobertura(cliente_id, e["nombre"], p) if con_cobertura else {"estado": "—"}
        base = float(p["prob_ocurrencia"] or 0) / 100 * PESO.get(p["severidad"], 0.5)
        expo = 1.0 * PESO.get(p["severidad"], 0.5) if activa else base * (PREVENIDO if cob.get("estado") == "cubierto" else 1.0)
        sin_riesgo *= (1 - min(expo, 0.99))
        filas.append({"id": p["id"], "numero": p["numero"], "titulo": p["titulo"], "producto": p["producto_nombre"], "tipo": p["tipo"],
                      "severidad": p["severidad"], "prob_ocurrencia": p["prob_ocurrencia"], "automatizacion": p["automatizacion"],
                      "solucion": p["solucion"], "activa": activa, "cobertura": cob, "exposicion": round(expo * 100)})
    filas.sort(key=lambda x: (not x["activa"], -x["exposicion"]))
    riesgo = round((1 - sin_riesgo) * 100)
    return {"riesgo": riesgo, "nivel": "alto" if riesgo >= 60 else ("medio" if riesgo >= 30 else "bajo"), "productos": prods,
            "errores": filas, "activos": sum(1 for f in filas if f["activa"]),
            "prevenidos": sum(1 for f in filas if f["cobertura"].get("estado") == "cubierto"),
            "en_deuda": sum(1 for f in filas if f["cobertura"].get("estado") == "en deuda")}


def hallazgos(cliente_id, e, nuevo):
    """En cada evaluación del inventario: un hallazgo por cada error conocido con su condición activa (vuelve a alertar si
    la condición reaparece)."""
    try:
        r = evaluar(cliente_id, e, con_cobertura=False)
    except Exception:  # noqa: BLE001 - sin la base de errores conocidos, el inventario sigue
        return
    for f in (r or {}).get("errores", []):
        if f["activa"]:
            nuevo(cliente_id, e, "error conocido", {"critica": "alta"}.get(f["severidad"], f["severidad"]),
                  f"{f['numero']} · {f['titulo']}", f"{f['activa']}. {f['solucion']}", sugerencia=f["automatizacion"] or "")
