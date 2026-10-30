"""Plano de ejecución: FaceOps → AAP → nodos de ejecución → equipos. Un nodo de ejecución por infraestructura y
ambiente, dentro del segmento de red de ese ambiente (en las nubes, detrás de un nodo hop). Así cada automatización corre
desde dentro del segmento que toca, con la credencial de esa infraestructura y sin abrir la red entre segmentos."""
import re
from collections import Counter

from .bd import j

AMB_ORDEN = {"produccion": 0, "qa": 1, "desarrollo": 2}
AMB_NOMBRE = {"produccion": "Producción", "qa": "QA", "desarrollo": "Desarrollo"}


AMB_CORTO = {"produccion": "prod", "desarrollo": "dev", "qa": "qa"}
VACIAS = {"de", "del", "la", "el", "los", "las", "y"}


def slug(t):
    t = t.lower().translate(str.maketrans("áéíóúñ", "aeioun"))
    pal = [p for p in re.split(r"[^a-z0-9]+", t) if p and p not in VACIAS][:2]
    return "-".join(pal)[:20] or "infra"


def _red_de(e, redes):
    for r in redes:
        if r.get("prefijo") and (e.get("ip") or "").startswith(r["prefijo"]):
            return r
        if r.get("categorias") and (e.get("categoria") or "") in r["categorias"]:
            return r
    return None


TRANSVERSALES = [("routers", "routers"), ("switches", "switches"), ("firewalls", "firewalls"), ("balanceadores", "balanceadores"), ("storage", "storage")]


def plano(cliente, eqs, infras, instancias):
    from .vistas import clase_eq, grupo_periferico
    redes = (j(cliente["clasificacion"], {}).get("_topologia") or {}).get("redes", [])
    nombres_inst = {i["hostname"]: i for i in instancias}
    cols = []
    for x in infras:
        todos_ = [e for e in eqs if (e.get("ubicacion") or "") == x["ubicacion"]]
        trans = [e for e in todos_ if grupo_periferico(e) in dict(TRANSVERSALES)]
        es = [e for e in todos_ if e not in trans]
        ambs = sorted({e.get("ambiente") or "produccion" for e in es} or {"produccion"}, key=lambda a: (AMB_ORDEN.get(a, 9), a))
        cajas = []
        for a in ambs:
            ea = [e for e in es if (e.get("ambiente") or "produccion") == a]
            rs = Counter((_red_de(e, redes) or {}).get("detalle", "").split(" ·")[0] or "sin red declarada" for e in ea)
            nodo = f"ee-{slug(x['nombre'])}-{AMB_CORTO.get(a, slug(a))}"
            inst = nombres_inst.get(nodo)
            cajas.append({"ambiente": a, "nombre_amb": AMB_NOMBRE.get(a, a.title()), "equipos": len(ea), "redes": [r for r, _ in rs.most_common(3)],
                          "clases": Counter(clase_eq(e) for e in ea), "nodo": nodo, "existe": bool(inst),
                          "estado": inst["node_state"] if inst else "por habilitar"})
        hop = f"hop-{slug(x['nombre'])}" if x["ambito"] == "nube" else None
        tr = None
        if trans:
            cnt = Counter(grupo_periferico(e) for e in trans)
            nodo_t = f"ee-{slug(x['nombre'])}-trans"
            inst = nombres_inst.get(nodo_t)
            tr = {"nodo": nodo_t, "existe": bool(inst), "estado": inst["node_state"] if inst else "por habilitar", "equipos": len(trans),
                  "tipos": [(n, cnt[k]) for k, n in TRANSVERSALES if cnt.get(k)],
                  "redes": [r for r, _ in Counter((_red_de(e, redes) or {}).get("detalle", "").split(" ·")[0] or "gestión" for e in trans).most_common(3)]}
        cols.append({"infra": x, "cajas": cajas, "hop": hop, "hop_existe": bool(hop and hop in nombres_inst), "trans": tr})
    # --- diagramación (coordenadas del SVG)
    W_CAJA, GAP, PAD = 196, 14, 16
    x0, y_inf = 20, 190
    for c in cols:
        n = len(c["cajas"])
        c["x"], c["w"] = x0, max(PAD * 2 + n * W_CAJA + (n - 1) * GAP, 300)
        c["titulo"] = c["infra"]["nombre"] if len(c["infra"]["nombre"]) <= (c["w"] - 66) // 8 else c["infra"]["nombre"][:(c["w"] - 66) // 8 - 1] + "…"
        c["y"], c["h"] = y_inf, (330 if c["hop"] else 290) + (130 if c["trans"] else 0)
        yc = c["y"] + (110 if c["hop"] else 62)
        for i, k in enumerate(c["cajas"]):
            off = (c["w"] - (PAD * 2 + n * W_CAJA + (n - 1) * GAP)) / 2
            k["x"], k["y"], k["w"], k["h"] = c["x"] + PAD + off + i * (W_CAJA + GAP), yc, W_CAJA, 200
            k["nx"], k["ny"] = k["x"] + W_CAJA / 2, yc + 52   # centro del nodo de ejecución
        if c["hop"]:
            c["hx"], c["hy"] = c["x"] + c["w"] / 2, c["y"] + 62
        if c["trans"]:
            t = c["trans"]
            t["x"], t["y"], t["w"], t["h"] = c["x"] + PAD, yc + 214, c["w"] - 2 * PAD, 112
            t["nx"], t["ny"] = t["x"] + 60, t["y"] + 50
            t["cw"] = min(104, (t["w"] - 150) / max(len(t["tipos"]), 1) - 8)
        x0 += c["w"] + 28
    ancho = max(x0 - 8, 760)
    centro = ancho / 2
    return {"cols": cols, "ancho": ancho, "alto": y_inf + max([c["h"] for c in cols] or [290]) + 20,
            "aap": {"x": centro - 150, "y": 20, "w": 300, "h": 96, "cx": centro},
            "faceops": {"x": 20, "y": 34, "w": 210, "h": 68},
            "instancias": instancias,
            "faltan": sum(1 for c in cols for k in c["cajas"] if not k["existe"]) + sum(1 for c in cols if c["hop"] and not c["hop_existe"])
            + sum(1 for c in cols if c["trans"] and not c["trans"]["existe"])}
