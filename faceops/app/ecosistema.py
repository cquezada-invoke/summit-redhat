"""Arquitectura del ecosistema del cliente, de alto nivel, armada desde el inventario: por centro de datos (y la nube
pública), sus capas de red (firewalls, routers y switches, balanceadores), el cómputo de cada infraestructura (con sus
segmentos de negocio), los clústeres de contenedores y el almacenamiento; a un costado, la plataforma de automatización
(FaceOps, AAP, bóveda y Git) que llega a todos. No dibuja cada máquina: agrupa y cuenta."""
from collections import Counter

CAPAS = [("firewalls", "Perímetro", "Firewalls"), ("red", "Red", "Routers y switches"), ("balanceadores", "Balanceo", "Balanceadores"),
         ("computo", "Cómputo", None), ("clusters", "Contenedores", "Clústeres de contenedores"), ("storage", "Almacenamiento", "Storage")]
ANCHO_COL, GAP, X0, Y0 = 300, 30, 20, 120


def armar(eqs, infras, grupo, clase, nube_publica):
    """Columnas por centro de datos con sus capas; cada capa: [{titulo, sub, n, nombres}]."""
    centros = {}
    por_ubic = {x["ubicacion"]: x for x in infras}
    for x in infras:
        centros.setdefault(x["centro"], {"nombre": x["centro"], "nube": x["centro"] == nube_publica, "infras": [], "eqs": []})["infras"].append(x)
    for e in eqs:
        inf = por_ubic.get(e.get("ubicacion") or "")
        cen = inf["centro"] if inf else (e.get("ubicacion") or "Sin centro de datos")
        centros.setdefault(cen, {"nombre": cen, "nube": cen == nube_publica, "infras": [], "eqs": []})["eqs"].append(e)
    cols = []
    for cen in sorted(centros.values(), key=lambda c: (c["nube"], c["nombre"])):
        capas = {}
        g_ = {}
        for e in cen["eqs"]:
            gp = grupo(e)
            k = "red" if gp in ("routers", "switches") else gp
            g_.setdefault(k, []).append(e)
        for k, _t, titulo in CAPAS:
            if k == "computo":
                cajas = []
                for x in cen["infras"]:
                    es = [e for e in cen["eqs"] if (e.get("ubicacion") or "") == x["ubicacion"] and grupo(e) in ("servidores", "otros", "estaciones")]
                    if not es and x["tipo"] in ("openshift",):
                        continue
                    segs = Counter(s for e in es for s in (e.get("segmentos") or []))
                    amb = Counter(e.get("ambiente") or "produccion" for e in es)
                    cajas.append({"titulo": x["nombre"], "tipo": x["tipo"], "n": len(es), "unidad": "máquina",
                                  "sub": " · ".join(f"{n} {a}" for a, n in amb.most_common()) or "sin máquinas aún",
                                  "nombres": [s for s, _ in segs.most_common(4)], "nombres_tit": "segmentos"})
                sueltos = [e for e in cen["eqs"] if not por_ubic.get(e.get("ubicacion") or "") and grupo(e) in ("servidores", "otros", "estaciones")]
                if sueltos:
                    cajas.append({"titulo": "Otras máquinas", "tipo": "", "n": len(sueltos), "unidad": "máquina", "sub": "sin infraestructura declarada", "nombres": []})
                if cajas:
                    capas[k] = cajas
            elif g_.get(k):
                es = g_[k]
                tipos = Counter((e.get("so") or e.get("categoria") or "").split(" ")[0] for e in es)
                capas[k] = [{"titulo": titulo, "tipo": k, "n": len(es), "unidad": "equipo",
                             "sub": " · ".join(f"{n} {t}" for t, n in tipos.most_common(3) if t),
                             "nombres": [e.get("nombre") for e in es][:4], "nombres_tit": "equipos"}]
        cols.append({"nombre": cen["nombre"], "nube": cen["nube"], "capas": [(k, t, capas[k]) for k, t, _ in CAPAS if k in capas],
                     "total": len(cen["eqs"])})
    # coordenadas
    x = X0
    alto = 0
    for c in cols:
        c["x"], c["w"], c["y"] = x, ANCHO_COL, Y0
        y = Y0 + 44
        for _k, _t, cajas in c["capas"]:
            for cj in cajas:
                cj["y"] = y + 18
                cj["h"] = 58 + (14 if cj.get("nombres") else 0)
                y += cj["h"] + 22
            y += 6
        c["h"] = max(y - Y0 + 6, 200)
        alto = max(alto, Y0 + c["h"])
        x += ANCHO_COL + GAP
    ancho_c = x - GAP
    plat_x = ancho_c + 50
    return {"cols": cols, "ancho": plat_x + 230, "alto": alto + 30, "plat": {"x": plat_x, "y": Y0, "w": 210},
            "inet": {"x": X0, "w": ancho_c - X0, "y": 20}}
