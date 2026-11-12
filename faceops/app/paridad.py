"""Réplicas y paridad de ambientes de un segmento de negocio (regla de Cristian, pilar Disponibilidad).

1. Producción: cada componente del segmento (cada rol de su arquitectura: servidor de aplicaciones, base de datos, caché,
   balanceador…) necesita AL MENOS 2 réplicas. Con 1 resta salud y aparece «Agregar réplica de <componente>», remediable
   con la skill «Replicar máquina» (ticket con aprobación) desde la ficha de la máquina.
2. Ambientes previos (QA, desarrollo): basta 1 réplica, pero con la MISMA arquitectura (los mismos componentes) y las
   MISMAS versiones de sistema operativo y plataformas que producción. Cada diferencia resta salud y aparece como
   «Alinear <componente> con producción (QA tiene X, prod tiene Y)», con su acción si existe o «sin acción».
3. Vínculo entre ambientes: el modelo existente de FaceOps: los ambientes de un segmento son los de su arquitectura
   (ambientes:) y sus máquinas llevan su ambiente; QA y desarrollo de un segmento corresponden a SU producción. Si el
   segmento no tiene producción (sin máquinas productivas), queda «sin productivo de referencia» con el enlace para
   definirlo en su arquitectura; no se infiere de otros segmentos.
Se calcula solo con el inventario y la arquitectura aprobada (sin consultas en vivo). Lo que corre en OpenShift tiene sus
réplicas dentro del clúster y no se cuenta aquí."""
import re
from collections import defaultdict

from .bd import j, q1

MIN_PROD = 2
NOMBRE_AMB = {"produccion": "producción", "qa": "QA", "desarrollo": "desarrollo", "calidad": "calidad", "pruebas": "pruebas"}
REPLICAR = "Infra · Replicar máquina"


def _so(e):
    return (e.get("so") or "").strip()


def _plats(e):
    """Plataformas de la máquina con su versión cuando el inventario la conoce (JBoss); el resto, por su skill."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    out = {}
    for x in d.get("jboss") or []:
        if isinstance(x, dict) and x.get("familia"):
            out[str(x["familia"])] = str(x.get("version") or "")
    for s in e.get("skills") or []:
        if s not in ("respaldo-git", "monitoreo", "node-exporter") and not any(s.lower().startswith(k.lower()) for k in out):
            out.setdefault(s, "")
    return out


def _txt(vals):
    v = sorted({x for x in vals if x})
    return " / ".join(v) if v else "—"


def _accion(cliente_id, nombres):
    """Primera automatización publicada de la lista, con su estado de prueba (sin automatización: «sin acción»)."""
    from .pruebas import estado, exceptuada
    for n in nombres:
        a = q1("SELECT id, nombre, nombre_aap FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, n)
        if a:
            ep = estado(dict(a, cliente_id=cliente_id))["estado"]
            ok = ep == "certificada" or bool(exceptuada(n))
            return {"nombre": a["nombre"], "aid": a["id"], "lista": ok, "motivo": "" if ok else "sin plan de prueba aprobado"}
    return None


def _componentes(arq, es, eqs):
    """{componente: {ambiente: [máquinas]}} desde la arquitectura aprobada; sin arquitectura, por el rol de cada máquina."""
    from .topologia import rol_de
    comps, ambs, ocp = {}, [], []
    if arq:
        from .arquitectura import asignacion
        m = arq["m"]
        ambs = list(m.get("ambientes") or ["produccion"])
        asig = asignacion(m, eqs)
        for c in m.get("componentes") or []:
            if c.get("plataforma") == "openshift":
                ocp.append(c.get("nombre") or c["id"])
                continue
            req = c.get("replicas") or {}
            comps[c.get("nombre") or c["id"]] = {"por_amb": {a: list((asig.get(c["id"]) or {}).get(a) or []) for a in ambs},
                                                 "declarado": {a: int(req.get(a) or 0) for a in ambs} if isinstance(req, dict) else {}}
    else:
        for e in es:
            _, rol = rol_de(e)
            a = e.get("ambiente") or "produccion"
            comps.setdefault(rol, {"por_amb": defaultdict(list), "declarado": {}})["por_amb"][a].append(e)
            if a not in ambs:
                ambs.append(a)
    orden = {"produccion": 0, "qa": 1, "desarrollo": 2}
    return comps, sorted(ambs, key=lambda a: (orden.get(a, 9), a)), ocp


def evaluar(cliente_id, segmento, eqs, arq=None, urls=None):
    """Regla de réplicas y paridad del segmento. urls: {'equipo': fn(eid) → url, 'arquitectura': url} para los enlaces.
    → {produccion: [...], previos: {amb: [...]}, items: {amb: [ítems de salud]}, cumple, sin_productivo, por_maquina}"""
    urls = urls or {}
    es = [e for e in eqs if segmento in (e.get("segmentos") or [])]
    comps, ambs, ocp = _componentes(arq, es, eqs)
    items = defaultdict(list)
    prod_f, previos, por_maquina = [], defaultdict(list), {}
    hay_prod = any(c["por_amb"].get("produccion") for c in comps.values())
    acc_rep = _accion(cliente_id, [REPLICAR])
    if not comps and ocp:   # todo corre en OpenShift: sus réplicas viven dentro del clúster, no hay máquinas que contar
        return {"produccion": [], "previos": {}, "items": {}, "cumple": True, "sin_productivo": False, "por_maquina": {}, "ambientes": ambs,
                "ocp": ocp, "componentes": 0}
    if not hay_prod:
        if comps or es:
            it = {"nivel": "media", "area": "Disponibilidad", "titulo": "Sin productivo de referencia",
                  "detalle": "El segmento no tiene máquinas en producción: no hay contra qué comparar sus ambientes",
                  "remedio": "Definir el ambiente productivo en la arquitectura del segmento", "url": urls.get("arquitectura", ""), "accion": None}
            for a in ambs or ["produccion"]:
                items[a].append(it)
        return {"produccion": [], "previos": {}, "items": dict(items), "cumple": not (comps or es), "sin_productivo": bool(comps or es),
                "por_maquina": {}, "ambientes": ambs, "ocp": ocp, "componentes": len(comps)}
    for nom, c in comps.items():
        prod = c["por_amb"].get("produccion") or []
        if not prod and "produccion" in c.get("declarado", {}) and not c["declarado"].get("produccion"):
            continue   # el componente no existe en producción (solo en un ambiente previo, por diseño)
        ok = len(prod) >= MIN_PROD
        f = {"componente": nom, "n": len(prod), "ok": ok, "maquinas": [e["nombre"] for e in prod]}
        prod_f.append(f)
        for e in prod:
            por_maquina[e["nombre"]] = (ok, "" if ok else f"{nom}: {len(prod)} réplica en producción (mínimo {MIN_PROD})")
        if not ok:
            e0 = prod[0] if prod else None
            items["produccion"].append({
                "nivel": "media", "area": "Disponibilidad", "titulo": f"Agregar réplica de {nom}",
                "detalle": f"Producción tiene {len(prod)} réplica{'s' if len(prod) != 1 else ''} (mínimo {MIN_PROD})" + (f": {', '.join(f['maquinas'])}" if prod else ""),
                "remedio": "Replicar máquina (skill de la máquina, ticket con aprobación)" if acc_rep else "sin acción: «Replicar máquina» no está publicada en AAP",
                "url": (urls["equipo"](e0["id"]) + "#skills") if e0 and urls.get("equipo") and acc_rep else "", "accion": acc_rep})
    for amb in ambs:
        if amb == "produccion":
            continue
        an = NOMBRE_AMB.get(amb, amb)
        for nom, c in comps.items():
            prod, xs = c["por_amb"].get("produccion") or [], c["por_amb"].get(amb) or []
            if not prod:
                continue
            difs = []
            if not xs:
                difs.append(("falta", f"Alinear {nom} con producción ({an} no tiene el componente, prod tiene {len(prod)})",
                             ["FaceOps · Habilitar servidor"], "Crear el servidor del componente con «Habilitar servidor» (ticket con aprobación)"))
            else:
                so_p, so_x = {_so(e) for e in prod}, {_so(e) for e in xs}
                if so_p != so_x:
                    mayor = lambda s: re.findall(r"\b(\d+)(?:\.\d+)?", " ".join(s))   # noqa: E731
                    nombres = ["Migración · RHEL a versión soportada"] if mayor(so_p)[:1] != mayor(so_x)[:1] else ["FaceOps · Aplicar parches de seguridad", "Sistema · Aplicar parches de seguridad"]
                    difs.append(("so", f"Alinear {nom} con producción ({an} tiene {_txt(so_x)}, prod tiene {_txt(so_p)})", nombres,
                                 "Llevar el sistema operativo a la versión de producción"))
                pl_p, pl_x = defaultdict(set), defaultdict(set)
                for e in prod:
                    for k, v in _plats(e).items():
                        pl_p[k].add(v)
                for e in xs:
                    for k, v in _plats(e).items():
                        pl_x[k].add(v)
                for k in sorted(pl_p):
                    if k not in pl_x:
                        difs.append(("plat", f"Alinear {nom} con producción ({an} no tiene {k}, prod tiene {k} {_txt(pl_p[k])})".replace(" —)", ")"),
                                     ["FaceOps · Habilitar servidor"], f"Instalar {k} como en producción"))
                    elif {v for v in pl_p[k] if v} and {v for v in pl_p[k] if v} != {v for v in pl_x[k] if v}:
                        difs.append(("ver", f"Alinear {nom} con producción ({an} tiene {k} {_txt(pl_x[k])}, prod tiene {k} {_txt(pl_p[k])})",
                                     ["Migración · JBoss a EAP 8"] if "eap" in k.lower() or "jboss" in k.lower() or "wildfly" in k.lower() else [],
                                     f"Llevar {k} a la versión de producción"))
            for e in xs:
                por_maquina[e["nombre"]] = (not difs, "; ".join(d[1] for d in difs[:2]))
            for tipo, tit, nombres, rem in difs:
                acc = _accion(cliente_id, nombres) if nombres else None
                e0 = xs[0] if xs else None
                it = {"nivel": "media", "area": "Disponibilidad", "titulo": tit, "detalle": f"{an}: {', '.join(e['nombre'] for e in xs) or 'sin máquinas'}",
                      "remedio": (f"{rem} · «{acc['nombre']}»" + ("" if acc["lista"] else f" ({acc['motivo']})")) if acc else f"{rem} · sin acción",
                      "url": (urls["equipo"](e0["id"]) + "#skills") if acc and e0 and urls.get("equipo") else "", "accion": acc}
                items[amb].append(it)
                previos[amb].append({"componente": nom, "titulo": tit, "accion": (acc or {}).get("nombre") or "sin acción"})
    cumple = all(f["ok"] for f in prod_f) and not any(previos.values())
    return {"produccion": prod_f, "previos": dict(previos), "items": dict(items), "cumple": cumple, "sin_productivo": False,
            "por_maquina": por_maquina, "ambientes": ambs, "ocp": ocp, "componentes": len(comps)}


_memo = {}


def por_maquina(cliente_id):
    """{máquina: (cumple, motivo)} de todos los segmentos del cliente (criterio D3 del radar y del reporte por área).
    Inventario y arquitectura de la base, sin consultas en vivo; memo de 60 s."""
    import time
    if cliente_id in _memo and time.time() - _memo[cliente_id][0] < 60:
        return _memo[cliente_id][1]
    from .arquitectura import vigentes
    from .bd import q
    from .inventario import segmentos_de
    segs = q("SELECT * FROM segmentos WHERE cliente_id=?", cliente_id)
    from .inventario import skills_de
    eqs = [dict(e) for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cliente_id)]
    for e in eqs:   # igual que equipos_visibles: segmentos por regla y skills del inventario
        try:
            e["segmentos"] = segmentos_de(e, segs)
            e["skills"] = skills_de(e)
        except Exception:  # noqa: BLE001
            e.setdefault("segmentos", [])
            e["skills"] = []
    arqs = vigentes(cliente_id)
    out = {}
    for s in segs:
        try:
            r = evaluar(cliente_id, s["nombre"], eqs, arqs.get(s["nombre"]))
        except Exception:  # noqa: BLE001
            continue
        for n, v in r["por_maquina"].items():
            if n not in out or not v[0]:
                out[n] = v
    _memo[cliente_id] = (time.time(), out)
    return out
