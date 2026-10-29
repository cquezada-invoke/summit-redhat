"""Mapa vivo de la infraestructura: redes, equipos y flujos de red, con el estado actual (hallazgos) de cada equipo.
Niveles: global (infraestructura → redes, o segmentos de negocio) → una red o un segmento (sus equipos y flujos) → equipo.
La topología (redes, enlaces y flujos) vive en la clasificación del cliente («_topologia»); los equipos y su estado, en el
inventario de FaceOps."""
from collections import Counter, defaultdict

from .bd import j

FORMA = {"router": "diamond", "firewall": "octagon", "switch": "rhomboid", "balanceador": "hexagon",
         "estación de trabajo": "ellipse", "almacenamiento": "barrel", "servidor": "round-rectangle"}


def _red_de(e, redes):
    for r in redes:
        if e.get("categoria") in r.get("categorias", []):
            return r["id"]
    for r in redes:
        if r.get("prefijo") and (e.get("ip") or "").startswith(r["prefijo"]):
            return r["id"]
    return "otros"


def _nodo_equipo(e, red, peor, nhal, fantasma=False):
    rol = ", ".join(e["skills"][:2]) or (e.get("categoria") if e.get("categoria") not in (None, "", "servidor") else "")
    return {"data": {"id": "e:" + e["nombre"], "label": e["nombre"] + (("\n" + rol) if rol else ""), "parent": red, "tipo": "equipo",
                     "estado": peor.get(e["id"], 0), "forma": FORMA.get(e.get("categoria") or "servidor", "round-rectangle"),
                     "info": {"Categoría": e.get("categoria") or "—", "Sistema": e.get("so") or "—", "IP": e.get("ip") or "—",
                              "Ambiente": e.get("ambiente") or "—", "Skills": ", ".join(e["skills"]) or "ninguna (vacío)",
                              "Segmentos": ", ".join(e["segmentos"]) or "—", "Hallazgos": nhal.get(e["id"], 0)},
                     "eid": e["id"], "fantasma": fantasma}}


def _estados(cliente):
    from .bd import q
    return {r["clave"]: r for r in q("SELECT * FROM flujos WHERE cliente_id=?", cliente["id"])}


def _estado_flujo(estados, f):
    """abierto | bloqueado | verificando | sin verificar (si un puerto de varios está bloqueado, el flujo está bloqueado)."""
    import re
    ps = re.findall(r"\d{2,5}", str(f.get("puerto", "")))
    if not ps:
        return "no aplica", None
    rs = [estados.get(clave_flujo(f["desde"], f["hacia"], p)) for p in ps]
    if any(r and r["estado"] == "bloqueado" for r in rs):
        return "bloqueado", max((r["verificado"] or "") for r in rs if r)
    if all(r and r["estado"] == "abierto" for r in rs):
        return "abierto", max((r["verificado"] or "") for r in rs)
    if any(r and r["estado"] == "verificando" for r in rs):
        return "verificando", None
    return "sin verificar", None


def latencia(estados, f):
    """Latencia medida del flujo (la peor de sus puertos), con la hora de la medición: («0.42 ms», cuando) o ("", None)."""
    import re
    rs = [estados.get(clave_flujo(f["desde"], f["hacia"], p)) for p in re.findall(r"\d{2,5}", str(f.get("puerto", "")))]
    ms = [(r["latencia_ms"], r.get("latencia_en")) for r in rs if r and r.get("latencia_ms") is not None]
    if not ms:
        return "", None
    v, cuando = max(ms)
    return (f"{v:.2f} ms" if v < 1 else f"{v:.1f} ms"), cuando


def etiqueta_puertos(estados, f):
    """«5432 ✓» abierto · «5432 ✗» cerrado · «5432 ?» sin verificar; un símbolo por puerto."""
    import re
    ps = re.findall(r"\d{2,5}", str(f.get("puerto", "")))
    if not ps:
        return str(f.get("puerto", ""))
    marca = {"abierto": "✓", "bloqueado": "✗", "verificando": "…"}
    return "  ".join(f"{p} {marca.get((estados.get(clave_flujo(f['desde'], f['hacia'], p)) or {}).get('estado'), '?')}" for p in ps)


def tickets_de_flujos(cliente):
    """Tickets de «Acceso de red» en curso por (origen, destino): el mapa los muestra sobre el flujo."""
    from .bd import q
    out = {}
    for t in q("SELECT t.id, t.numero, t.estado, t.parametros FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
               "WHERE t.cliente_id=? AND a.nombre_aap='FaceOps · Acceso de red' AND t.estado IN ('en_cola','en_ejecucion','pendiente_aprobacion')",
               cliente["id"]):
        p = j(t["parametros"], {})
        out[(p.get("origen"), p.get("destino"))] = {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "accion": p.get("red_accion")}
    return out


def construir(cliente, eqs, hallazgos, nivel="global", valor="", vista="infra"):
    topo = j(cliente.get("clasificacion"), {}).get("_topologia", {})
    redes = topo.get("redes", []) + [{"id": "otros", "nombre": "Otras redes", "infra": "", "detalle": "sin red declarada"}]
    peor, nhal = {}, Counter(h["equipo_id"] for h in hallazgos)
    sev = {"alta": 3, "media": 2, "baja": 1}
    for h in hallazgos:
        peor[h["equipo_id"]] = max(peor.get(h["equipo_id"], 0), sev.get(h["severidad"], 0))
    por_red = defaultdict(list)
    for e in eqs:
        e["red"] = _red_de(e, redes)
        por_red[e["red"]].append(e)
    por_nombre = {e["nombre"]: e for e in eqs}
    flujos = [f for f in topo.get("flujos", []) if f.get("hacia") != "*"]
    centrales = topo.get("centrales", [])
    els = []

    def caja(es):
        return {"n": len(es), "alta": sum(1 for e in es if peor.get(e["id"]) == 3),
                "media": sum(1 for e in es if peor.get(e["id"]) == 2), "ok": sum(1 for e in es if not peor.get(e["id"]))}

    if nivel == "global" and vista == "infra":
        for infra in sorted({r["infra"] for r in redes if r["infra"] and por_red.get(r["id"])}):
            els.append({"data": {"id": "i:" + infra, "label": infra, "tipo": "infra"}})
        for r in redes:
            es = por_red.get(r["id"], [])
            if not es:
                continue
            c = caja(es)
            els.append({"data": {"id": "r:" + r["id"], "label": f"{r['nombre']}\n{c['n']} equipos", "tipo": "red",
                                 "parent": ("i:" + r["infra"]) if r.get("infra") else None, "drill": {"nivel": "red", "valor": r["id"]},
                                 "estado": 3 if c["alta"] else (2 if c["media"] else 0), "caja": c,
                                 "info": {"Red": r.get("detalle", ""), "Equipos": c["n"], "Con hallazgos altos": c["alta"],
                                          "Con hallazgos medios": c["media"], "Sin hallazgos": c["ok"]}}})
        for ce in centrales:
            r = next((x for x in redes if x["id"] == ce.get("red")), {})
            els.append({"data": {"id": "c:" + ce["id"], "label": ce["nombre"], "tipo": "central",
                                 "parent": ("i:" + r["infra"]) if r.get("infra") else None, "info": {"Rol": ce.get("detalle", "")}}})
        con_equipos = {r["id"] for r in redes if por_red.get(r["id"])}
        for en in topo.get("enlaces", []):
            if en["desde"] in con_equipos and en["hacia"] in con_equipos:
                els.append({"data": {"id": f"l:{en['desde']}-{en['hacia']}", "source": "r:" + en["desde"],
                                     "target": "r:" + en["hacia"], "label": en.get("via", ""), "tipo": "enlace"}})
        for f in topo.get("flujos", []):
            if f.get("hacia") == "*" and any(c["id"] == f["desde"] for c in centrales):
                for rid in con_equipos:
                    els.append({"data": {"id": f"g:{f['desde']}-{rid}", "source": "c:" + f["desde"], "target": "r:" + rid,
                                         "label": f.get("puerto", ""), "tipo": "gestion"}})
        # flujos entre redes distintas (resumen)
        cruces, bloq = Counter(), Counter()
        est = _estados(cliente)
        for f in flujos:
            a, b = por_nombre.get(f["desde"]), por_nombre.get(f["hacia"])
            if a and b and a["red"] != b["red"]:
                cruces[(a["red"], b["red"])] += 1
                bloq[(a["red"], b["red"])] += _estado_flujo(est, f)[0] == "bloqueado"
        for (x, y), n in cruces.items():
            els.append({"data": {"id": f"x:{x}-{y}", "source": "r:" + x, "target": "r:" + y, "tipo": "flujo",
                                 "label": f"{n} flujos" + (f" · {bloq[(x, y)]} bloqueados" if bloq[(x, y)] else ""),
                                 "estado_flujo": "bloqueado" if bloq[(x, y)] else ""}})
        # bloqueos dentro de cada red: se marcan en la propia red
        for f in flujos:
            a, b = por_nombre.get(f["desde"]), por_nombre.get(f["hacia"])
            if a and b and a["red"] == b["red"] and _estado_flujo(est, f)[0] == "bloqueado":
                for el in els:
                    if el["data"]["id"] == "r:" + a["red"]:
                        el["data"]["info"]["Flujos bloqueados"] = el["data"]["info"].get("Flujos bloqueados", 0) + 1
                        el["data"]["bloqueos"] = el["data"].get("bloqueos", 0) + 1
        return els, [{"texto": cliente["nombre"]}]

    if nivel == "global" and vista == "negocio":
        from .bd import q
        segs = q("SELECT * FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", cliente["id"])
        for s in segs:
            es = [e for e in eqs if s["nombre"] in e["segmentos"]]
            if not es:
                continue
            c = caja(es)
            els.append({"data": {"id": "s:" + s["nombre"], "label": f"{s['nombre']}\n{c['n']} equipos · {s.get('sistema') or ''}",
                                 "tipo": "red", "drill": {"nivel": "segmento", "valor": s["nombre"]},
                                 "estado": 3 if c["alta"] else (2 if c["media"] else 0), "caja": c,
                                 "info": {"Descripción": s.get("descripcion", ""), "Sistema": s.get("sistema") or "—", "Equipos": c["n"],
                                          "Con hallazgos altos": c["alta"], "Con hallazgos medios": c["media"]}}})
        cruces = Counter()
        for f in flujos:
            a, b = por_nombre.get(f["desde"]), por_nombre.get(f["hacia"])
            for x in (a or {}).get("segmentos", []):
                for y in (b or {}).get("segmentos", []):
                    if x != y:
                        cruces[(x, y)] += 1
        for (x, y), n in cruces.items():
            els.append({"data": {"id": f"x:{x}-{y}", "source": "s:" + x, "target": "s:" + y, "label": f"{n} flujos", "tipo": "flujo"}})
        return els, [{"texto": cliente["nombre"]}]

    # nivel red o segmento: equipos + flujos; los equipos de otras redes que participan aparecen tenues en su propia red
    if nivel == "red":
        r = next((x for x in redes if x["id"] == valor), {"id": valor, "nombre": valor})
        dentro = por_red.get(valor, [])
        titulo = r["nombre"]
    else:
        dentro = [e for e in eqs if valor in e["segmentos"]]
        titulo = valor
    nombres = {e["nombre"] for e in dentro}
    rel = [f for f in flujos if f["desde"] in nombres or f["hacia"] in nombres]
    externos = {n for f in rel for n in (f["desde"], f["hacia"]) if n not in nombres and n in por_nombre}
    padres = set()
    for e in dentro + [por_nombre[n] for n in externos]:
        pid = ("p:" + e["red"]) if nivel == "red" or e["nombre"] in externos else "p:seg"
        if pid not in padres:
            padres.add(pid)
            rr = next((x for x in redes if x["id"] == e["red"]), {"nombre": e["red"]})
            lab = titulo if pid == "p:seg" else (rr["nombre"] + (" · " + rr.get("detalle", "") if pid == "p:" + valor else ""))
            els.append({"data": {"id": pid, "label": lab, "tipo": "grupo", "fantasma": e["nombre"] in externos and pid != "p:" + valor}})
        els.append(_nodo_equipo(e, pid, peor, nhal, fantasma=e["nombre"] in externos))
    estados = _estados(cliente)
    en_curso = tickets_de_flujos(cliente)
    for i, f in enumerate(rel):
        if f["desde"] in por_nombre and f["hacia"] in por_nombre:
            est, cuando = _estado_flujo(estados, f)
            a, b = por_nombre[f["desde"]], por_nombre[f["hacia"]]
            tk = en_curso.get((a.get("ip"), b.get("ip")))
            lat, lat_en = latencia(estados, f)
            els.append({"data": {"id": f"f{i}", "source": "e:" + f["desde"], "target": "e:" + f["hacia"],
                                 "label": ("⏱ " + lat if lat else "") + (f"  ⏳ {tk['numero']}" if tk else ""),
                                 "puertos": etiqueta_puertos(estados, f), "latencia": lat, "latencia_en": lat_en,
                                 "ticket": tk,
                                 "texto": f.get("texto", ""), "tipo": "flujo", "estado_flujo": est, "verificado": cuando,
                                 "origen_ip": a.get("ip"), "destino_ip": b.get("ip"), "puerto": str(f.get("puerto", "")),
                                 "desde": f["desde"], "hacia": f["hacia"]}})
    return els, [{"texto": cliente["nombre"], "nivel": "global"}, {"texto": titulo}]


# ------------------------------------------------------------------ verificación de flujos (en vivo, vía AAP)
def clave_flujo(desde, hacia, puerto):
    return f"{desde}>{hacia}:{puerto}"


def flujos_verificables(cliente, eqs):
    """Flujos con puerto TCP numérico y equipo de origen con sistema (los equipos de red se verifican aparte)."""
    import re
    topo = j(cliente.get("clasificacion"), {}).get("_topologia", {})
    por_nombre = {e["nombre"]: e for e in eqs}
    out = []
    for f in topo.get("flujos", []):
        a, b = por_nombre.get(f.get("desde")), por_nombre.get(f.get("hacia"))
        if not a or not b or not b.get("ip") or (a.get("categoria") or "servidor") != "servidor" or a.get("estado") != "con datos":
            continue
        for p in re.findall(r"\d{2,5}", str(f.get("puerto", ""))):
            out.append({"clave": clave_flujo(a["nombre"], b["nombre"], p), "desde": a["nombre"], "ip": b["ip"], "puerto": int(p)})
    return out


def verificar_flujos(cliente, eqs, usuario="sistema", solo=None):
    """Lanza «FaceOps · Verificar flujos de red» en AAP, un trabajo por inventario/credencial de los equipos de origen."""
    from .aap import AAP, ErrorAAP
    from .bd import ahora, auditar, ej, q1
    from .operacion import acceso_equipo
    aap = AAP.de_cliente(cliente)
    r = aap.get("job_templates/", name="FaceOps · Verificar flujos de red").get("results", [])
    if not r:
        raise ErrorAAP("Falta la plantilla «FaceOps · Verificar flujos de red» en AAP")
    flujos = flujos_verificables(cliente, eqs)
    if solo:  # prueba puntual de una unión del mapa (botón «Probar latencia»)
        flujos = [f for f in flujos if any(f["clave"].startswith(clave_flujo(a, b, "")) for a, b in solo)]
        if not flujos:
            raise ErrorAAP("Esa unión no se puede probar desde su origen (el origen debe ser un servidor con datos del recolector)")
    por_nombre = {e["nombre"]: e for e in eqs}
    grupos = defaultdict(list)
    for f in flujos:
        try:
            inv, _, creds = acceso_equipo(aap, cliente, por_nombre[f["desde"]])
        except ErrorAAP:
            continue
        grupos[(inv, tuple(creds))].append(f)
    jobs = []
    for (inv, creds), fl in grupos.items():
        job = aap.lanzar("job", r[0]["id"], extra_vars={"faceops_flujos": fl}, limite=":".join(sorted({f["desde"] for f in fl})),
                         inventario=inv, credenciales=list(creds))
        jobs.append(job)
        for f in fl:
            ej("INSERT INTO flujos(cliente_id, clave, estado, verificado, aap_job_id) VALUES (?,?,?,?,?) "
               "ON CONFLICT(cliente_id, clave) DO UPDATE SET aap_job_id=excluded.aap_job_id", cliente["id"], f["clave"],
               "verificando", None, job)
    auditar(usuario, cliente["id"], "verificar flujos de red", "mapa", {"flujos": len(flujos), "trabajos": jobs})
    return len(flujos), jobs


def actualizar_flujos():
    """La llama el vigía: lee el resultado de los trabajos de verificación terminados."""
    from .aap import AAP, ErrorAAP
    from .bd import ahora, ej, q, q1
    pend = q("SELECT DISTINCT cliente_id, aap_job_id FROM flujos WHERE estado='verificando' AND aap_job_id IS NOT NULL")
    for p in pend:
        c = q1("SELECT * FROM clientes WHERE id=?", p["cliente_id"])
        try:
            r = AAP.de_cliente(c).get(f"jobs/{p['aap_job_id']}/")
        except ErrorAAP:
            continue
        if r.get("status") not in ("successful", "failed", "error", "canceled"):
            continue
        art = r.get("artifacts") or {}
        res, lat = art.get("faceops_flujos_resultado") or {}, art.get("faceops_flujos_latencia") or {}
        for f in q("SELECT * FROM flujos WHERE cliente_id=? AND aap_job_id=?", p["cliente_id"], p["aap_job_id"]):
            try:
                ms = float(lat.get(f["clave"]))
            except (TypeError, ValueError):
                ms = None
            ej("UPDATE flujos SET estado=?, verificado=?, latencia_ms=?, latencia_en=? WHERE id=?",
               res.get(f["clave"], "sin respuesta"), ahora(), ms, ahora() if ms is not None else (f.get("latencia_en") or ""), f["id"])


def resumen_segmentos(cliente, eqs, hallazgos, segmentos):
    """Tarjetas de la lista de aplicaciones / segmentos de negocio: componentes, estado y flujos."""
    topo = j(cliente.get("clasificacion"), {}).get("_topologia", {})
    estados = _estados(cliente)
    sev = {"alta": 3, "media": 2, "baja": 1}
    peor = {}
    for h in hallazgos:
        peor[h["equipo_id"]] = max(peor.get(h["equipo_id"], 0), sev.get(h["severidad"], 0))
    out = []
    for s in segmentos:
        es = [e for e in eqs if s["nombre"] in e["segmentos"]]
        if not es:
            continue
        nombres = {e["nombre"] for e in es}
        fl = [f for f in topo.get("flujos", []) if f.get("desde") in nombres or f.get("hacia") in nombres]
        est = [_estado_flujo(estados, f)[0] for f in fl]
        out.append({"nombre": s["nombre"], "descripcion": s.get("descripcion", ""), "sistema": s.get("sistema", ""),
                    "componentes": sorted(es, key=lambda e: e["nombre"]), "peor": peor,
                    "roles": Counter(k for e in es for k in (e["skills"] or [e.get("categoria") or "servidor"])).most_common(6),
                    "ambientes": sorted({e["ambiente"] for e in es if e["ambiente"]}), "flujos": len(fl),
                    "bloqueados": est.count("bloqueado"), "abiertos": est.count("abierto"),
                    "alta": sum(1 for e in es if peor.get(e["id"]) == 3), "media": sum(1 for e in es if peor.get(e["id"]) == 2)})
    return out
