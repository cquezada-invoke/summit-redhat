"""Mapa vivo de la infraestructura: redes, equipos y flujos de red, con el estado actual (hallazgos) de cada equipo.
Niveles: global (infraestructura → redes, o segmentos de negocio) → una red o un segmento (sus equipos y flujos) → equipo.
La topología (redes, enlaces y flujos) vive en la clasificación del cliente («_topologia»); los equipos y su estado, en el
inventario de FaceOps."""
import re
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


# Rol de la máquina dentro de la aplicación (para el icono de su caja): por el tipo del componente en la arquitectura y,
# si no hay, por sus productos (skills) o su categoría.
ROLES = [("base_datos", "Base de datos", {"postgres", "mysql", "mariadb", "oracle", "mongodb"}),
         ("mensajeria", "Mensajería y caché", {"activemq", "rabbitmq", "redis", "kafka"}),
         ("identidad", "Identidad", {"keycloak", "red-hat-idm", "389-directory-server", "active-directory", "midpoint"}),
         ("servicios", "Capa de servicios", {"jboss", "tomcat", "wildfly", "openshift", "kubernetes"}),
         ("web", "Web", {"nginx", "httpd", "iis"})]
ROL_TIPO = {"balanceador": ("balanceo", "Balanceo"), "firewall": ("firewall", "Firewall"), "waf": ("firewall", "WAF"),
            "router": ("red", "Router"), "proxy": ("web", "Proxy")}


def rol_de(e, comp=None):
    if comp and comp.get("tipo") in ROL_TIPO:
        return ROL_TIPO[comp["tipo"]]
    sk = set(e.get("skills") or []) | ({comp["producto"]} if comp and comp.get("producto") else set())
    for clave, nombre, prods in ROLES:
        if sk & prods:
            return clave, nombre
    cat = e.get("categoria") or "servidor"
    return {"firewall": ("firewall", "Firewall"), "router": ("red", "Router"), "switch": ("red", "Switch"),
            "estación de trabajo": ("usuario", "Estación de trabajo")}.get(cat, ("servidor", "Servidor"))


def _nodo_equipo(e, red, peor, nhal, fantasma=False, comp=None):
    rol = ", ".join(e["skills"][:2]) or (e.get("categoria") if e.get("categoria") not in (None, "", "servidor") else "")
    icono, rol_nombre = rol_de(e, comp)
    # caja con lo principal de la máquina: nombre, IP y rol · ambiente
    caja = "\n".join(x for x in [e["nombre"], e.get("ip") or "", " · ".join(y for y in [rol_nombre, e.get("ambiente") or ""] if y)] if x)
    return {"data": {"id": "e:" + e["nombre"], "label": e["nombre"] + (("\n" + rol) if rol else ""), "caja": caja, "parent": red, "tipo": "equipo",
                     "energia": e.get("energia") or "", "icono": icono, "rol": rol_nombre, "nombre": e["nombre"], "ip": e.get("ip") or "",
                     "ubicacion": e.get("ubicacion") or "", "ambiente": e.get("ambiente") or "",
                     "nube": "gcp" in (e.get("proveedor") or "").lower() or "cloud" in (e.get("ubicacion") or "").lower(),
                     "estado": peor.get(e["id"], 0), "forma": FORMA.get(e.get("categoria") or "servidor", "round-rectangle"),
                     "info": {"Rol": rol_nombre, "Categoría": e.get("categoria") or "—", "Sistema": e.get("so") or "—", "IP": e.get("ip") or "—",
                              "Ambiente": e.get("ambiente") or "—", "Skills": ", ".join(e["skills"]) or "ninguna (vacío)",
                              "Segmentos": ", ".join(e["segmentos"]) or "—", "Hallazgos": nhal.get(e["id"], 0),
                              "Código (Git)": " · ".join(f"<a href='{c['ver']}' target='_blank'>{c['repo'].split('/')[-1]}@{c['sha'][:7]}</a>"
                                                         for c in j(e.get("codigo"), [])) or "<b style='color:var(--bad)'>sin estructura como código</b>"},
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


def tls_de(estados, f):
    """TLS detectado en los puertos del flujo: {tipo: interna|autofirmado|publica|otra, emisor, sujeto, vence} o {}."""
    for p in re.findall(r"\d{2,5}", str(f.get("puerto", ""))):
        r = estados.get(clave_flujo(f["desde"], f["hacia"], p)) or {}
        if r.get("tls"):
            t = j(r["tls"], {})
            if t.get("tipo"):
                return dict(t, puerto=p)
    return {}


def etiqueta_puertos(estados, f):
    """«5432 ✓» abierto · «5432 ✗» cerrado · «5432 ?» sin verificar; un símbolo por puerto."""
    import re
    ps = re.findall(r"\d{2,5}", str(f.get("puerto", "")))
    if not ps:
        return str(f.get("puerto", ""))
    marca = {"abierto": "✓", "bloqueado": "✗", "verificando": "…"}
    partes = []
    for p in ps:
        r = estados.get(clave_flujo(f["desde"], f["hacia"], p)) or {}
        tls = j(r.get("tls"), {}) if r.get("tls") else {}
        candado = {"interna": " 🔒CA interna", "autofirmado": " 🔓autofirmado", "publica": " 🔒CA pública", "otra": " 🔒"}.get(tls.get("tipo"), "")
        partes.append(f"{p} {marca.get(r.get('estado'), '?')}{candado}")
    return ("⇉ " if len(ps) > 1 else "") + "   ".join(partes)


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


def construir(cliente, eqs, hallazgos, nivel="global", valor="", vista="infra", ambiente=""):
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
    from .arquitectura import flujos_de
    flujos = [f for f in flujos_de(cliente, eqs) if f.get("hacia") != "*"]
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
    comp_de, _a, _asg = {}, None, {}
    if nivel == "segmento":  # qué componente de la arquitectura cumple cada máquina (para su rol)
        from .arquitectura import asignacion as _asig, vigente as _vig
        _a = _vig(cliente["id"], valor)
        if _a:
            _asg = _asig(_a["m"], eqs)
            for c in _a["m"]["componentes"]:
                for amb_, es_ in _asg.get(c["id"], {}).items():
                    if not ambiente or amb_ == ambiente:
                        for x in es_:
                            comp_de.setdefault(x["nombre"], c)
        if ambiente:  # un ambiente a la vez: producción, QA y desarrollo no se mezclan
            if _a:
                dentro = [e for e in dentro if e["nombre"] in comp_de]
            else:
                dentro = [e for e in dentro if (e.get("ambiente") or "") == ambiente]
            titulo = f"{valor} · {ambiente}"
    nombres = {e["nombre"] for e in dentro}
    if nivel == "segmento":
        # El segmento es la aplicación y sus relaciones internas: lo que interactúa desde otro segmento se ve en ese
        # segmento, no aquí. Los actores externos de la arquitectura (p. ej. usuarios) marcan por dónde arranca el flujo.
        from .arquitectura import vigentes
        con_arq = valor in vigentes(cliente["id"])  # con arquitectura: solo sus flujos declarados
        rel = [f for f in flujos if (f["desde"] in nombres or f["desde"].startswith("x:")) and f["hacia"] in nombres
               and (f.get("segmento") == valor if con_arq else not f.get("segmento"))]
        externos = set()
    else:
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
        els.append(_nodo_equipo(e, pid, peor, nhal, fantasma=e["nombre"] in externos, comp=comp_de.get(e["nombre"])))
    estados = _estados(cliente)
    en_curso = tickets_de_flujos(cliente)
    entradas, pasos_vistos, virtuales = {}, set(), set()

    def camino(f, origen):
        """Servicios de red por los que pasa el flujo (firewall, WAF, balanceador): nodos intermedios. Devuelve el
        último nodo del camino, que es el que se une al destino con la arista del flujo (puertos y estado)."""
        actual = origen
        for v in f.get("via") or []:
            nid = ("e:" + v["equipo"]) if v.get("equipo") in por_nombre else "v:" + v["id"]
            if nid.startswith("v:") and nid not in virtuales:
                virtuales.add(nid)
                els.append({"data": {"id": nid, "label": f"{v['nombre']}\n(por asignar)", "tipo": "red_virtual", "parent": "p:seg",
                                     "info": {"Rol": v["tipo"], "Estado": "sin equipo asignado"}}})
            if nid == actual:  # el mismo equipo cumple otro rol del camino (p. ej. firewall y WAF en el FortiGate)
                for el in reversed(els):
                    if el["data"].get("tipo") == "paso" and el["data"].get("target") == nid:
                        if v["tipo"].upper() not in el["data"]["label"]:
                            el["data"]["label"] += " + " + v["tipo"].upper()
                        break
                continue
            if (actual, nid) not in pasos_vistos:
                pasos_vistos.add((actual, nid))
                els.append({"data": {"id": f"p{len(pasos_vistos)}", "source": actual, "target": nid, "label": v["tipo"].upper(),
                                     "tipo": "paso", "texto": f"pasa por {v['nombre']}"}})
            actual = nid
        return actual

    for i, f in enumerate(rel):
        if f["desde"].startswith("x:") and f["hacia"] in por_nombre:  # entrada: actor externo → primer componente
            if f["desde"] not in entradas:
                entradas[f["desde"]] = True
                els.append({"data": {"id": f["desde"], "label": "▶ " + f.get("externo", f["desde"][2:]) + "\ninicio del flujo",
                                     "tipo": "entrada", "info": {"Rol": "Punto de entrada", "Hacia": f["hacia"], "Puerto": f["puerto"]}}})
            fuente = camino(f, f["desde"])
            if (fuente, "e:" + f["hacia"]) not in pasos_vistos:
                pasos_vistos.add((fuente, "e:" + f["hacia"]))
                els.append({"data": {"id": f"f{i}", "source": fuente, "target": "e:" + f["hacia"], "label": "", "puertos": f["puerto"],
                                     "latencia": "", "tipo": "entrada", "texto": f.get("texto", "")}})
            continue
        if f["desde"] in por_nombre and f["hacia"] in por_nombre:
            est, cuando = _estado_flujo(estados, f)
            a, b = por_nombre[f["desde"]], por_nombre[f["hacia"]]
            tk = en_curso.get((a.get("ip"), b.get("ip")))
            lat, lat_en = latencia(estados, f)
            els.append({"data": {"id": f"f{i}", "source": camino(f, "e:" + f["desde"]), "target": "e:" + f["hacia"],
                                 "label": (f"⏳ {tk['numero']}" if tk else ""),
                                 "puertos": etiqueta_puertos(estados, f), "latencia": lat, "latencia_en": lat_en,
                                 "multi": len(re.findall(r"\d{2,5}", str(f.get("puerto", "")))) > 1, "tls": tls_de(estados, f), "tls_tipo": tls_de(estados, f).get("tipo", ""),
                                 "ticket": tk,
                                 "texto": f.get("texto", "") + ((" · pasa por " + ", ".join(v["nombre"] for v in f["via"])) if f.get("via") else ""),
                                 "tipo": "flujo", "estado_flujo": est, "verificado": cuando,
                                 "origen_ip": a.get("ip"), "destino_ip": b.get("ip"), "puerto": str(f.get("puerto", "")),
                                 "desde": f["desde"], "hacia": f["hacia"]}})
    if _a and ambiente:
        _planificados(els, _a["m"], _asg, ambiente, pasos_vistos)
    return els, [{"texto": cliente["nombre"], "nivel": "global"}, {"texto": titulo}]


def m_nombre(m, cid):
    return next((c["nombre"] for c in m["componentes"] if c["id"] == cid), None) or \
        next((x.get("nombre", cid) for x in m["externos"] if x["id"] == cid), cid)


def _planificados(els, m, asg, amb, vistos):
    """Componentes que la arquitectura declara en este ambiente pero que aún no existen: cajas «por crear» y sus
    uniones planificadas, para ver la aplicación completa del ambiente."""
    nodos = {}
    if not any(e["data"].get("id") == "p:seg" for e in els):
        els.append({"data": {"id": "p:seg", "label": f"{m['segmento']} · {amb}", "tipo": "grupo"}})
    for c in m["componentes"]:
        if c["replicas"].get(amb) and not asg.get(c["id"], {}).get(amb):
            icono, rol = rol_de({}, c)
            nid = f"v:{c['id']}:{amb}"
            nodos[c["id"]] = [nid]
            els.append({"data": {"id": nid, "parent": "p:seg", "tipo": "por_crear", "icono": icono, "estado": 0,
                                 "componente": c["nombre"], "ambiente": amb, "clave_paso": f"hab:{c['id']}:{amb}", "nombre": c["nombre"],
                                 "rol": rol, "ubicacion": m.get("centro_de_datos") or "", "nube": "cloud" in (m.get("centro_de_datos") or "").lower(),
                                 "red": c["tipo"] != "servidor" and not c.get("sistema"),
                                 "caja": f"{c['nombre']}\npor crear ({c['replicas'][amb]})\n{rol} · {amb}",
                                 "info": {"Componente": c["nombre"], "Estado": f"declarado en la arquitectura, por crear en {amb}",
                                          "Réplicas": c["replicas"][amb], "Producto": c.get("producto") or c["tipo"]}}})
        else:
            nodos[c["id"]] = ["e:" + e["nombre"] for e in asg.get(c["id"], {}).get(amb, [])]
    ext = {x["id"] for x in m["externos"]}
    for i, f in enumerate(m["flujos"]):
        origen = ["x:" + f["desde"]] if f["desde"] in ext else nodos.get(f["desde"], [])
        destino = nodos.get(f["hacia"], [])
        for a in origen:
            for b in destino:
                if not (a.startswith("v:") or b.startswith("v:")):
                    continue  # las uniones entre máquinas reales ya están dibujadas con su estado
                if a.startswith("x:") and not any(e["data"]["id"] == a for e in els):
                    els.append({"data": {"id": a, "label": "▶ " + next((x.get("nombre", a[2:]) for x in m["externos"] if x["id"] == a[2:]), a[2:])
                                         + "\ninicio del flujo", "tipo": "entrada", "info": {"Rol": "Punto de entrada"}}})
                if (a, b) in vistos:
                    continue
                vistos.add((a, b))
                faltan = [n for n, x in ((m_nombre(m, f["desde"]), a), (m_nombre(m, f["hacia"]), b)) if x.startswith("v:")]
                els.append({"data": {"id": f"pl{i}-{len(vistos)}", "source": a, "target": b, "tipo": "planificado", "ambiente": amb,
                                     "puertos": " · ".join(f["puertos"]), "label": "", "texto": f.get("motivo", ""),
                                     "desde_c": m_nombre(m, f["desde"]), "hacia_c": m_nombre(m, f["hacia"]), "faltan": faltan}})


# ------------------------------------------------------------------ verificación de flujos (en vivo, vía AAP)
def clave_flujo(desde, hacia, puerto):
    return f"{desde}>{hacia}:{puerto}"


def flujos_verificables(cliente, eqs):
    """Flujos con puerto TCP numérico y equipo de origen con sistema (los equipos de red se verifican aparte)."""
    import re
    from .arquitectura import flujos_de
    por_nombre = {e["nombre"]: e for e in eqs}
    out, vistos = [], set()
    for f in flujos_de(cliente, eqs):
        a, b = por_nombre.get(f.get("desde")), por_nombre.get(f.get("hacia"))
        if not a or not b or not b.get("ip") or (a.get("categoria") or "servidor") != "servidor" or a.get("estado") != "con datos":
            continue
        for p in re.findall(r"\d{2,5}", str(f.get("puerto", ""))):
            k = clave_flujo(a["nombre"], b["nombre"], p)
            if k not in vistos:
                vistos.add(k)
                out.append({"clave": k, "desde": a["nombre"], "ip": b["ip"], "puerto": int(p)})
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
               "ON CONFLICT(cliente_id, clave) DO UPDATE SET aap_job_id=excluded.aap_job_id, estado=excluded.estado", cliente["id"], f["clave"],
               "verificando", None, job)
    auditar(usuario, cliente["id"], "verificar flujos de red", "mapa", {"flujos": len(flujos), "trabajos": jobs})
    return len(flujos), jobs


def clasificar_tls(crudo):
    """Certificado leído por AAP → JSON {tipo: interna|autofirmado|publica|otra, emisor, sujeto, vence} ('' si no hay TLS)."""
    import json as _json
    import os as _os
    import yaml as _yaml
    try:
        t = _json.loads(crudo) if isinstance(crudo, str) else crudo
    except ValueError:
        t = None
    if not t:
        return ""
    try:
        conf = _yaml.safe_load(open(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "datos", "pki.yml"))) or {}
    except OSError:
        conf = {}
    em = (t.get("emisor") or "").lower()
    if t.get("autofirmado"):
        tipo = "autofirmado"
    elif any(x.lower() in em for x in conf.get("ca_interna", [])):
        tipo = "interna"
    elif any(x.lower() in em for x in conf.get("ca_publicas", [])):
        tipo = "publica"
    else:
        tipo = "otra"
    return _json.dumps(dict(t, tipo=tipo), ensure_ascii=False)


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
        tls_r = art.get("faceops_flujos_tls") or {}
        for f in q("SELECT * FROM flujos WHERE cliente_id=? AND aap_job_id=?", p["cliente_id"], p["aap_job_id"]):
            try:
                ms = float(lat.get(f["clave"]))
            except (TypeError, ValueError):
                ms = None
            ej("UPDATE flujos SET estado=?, verificado=?, latencia_ms=?, latencia_en=?, tls=? WHERE id=?",
               res.get(f["clave"], "sin respuesta"), ahora(), ms, ahora() if ms is not None else (f.get("latencia_en") or ""),
               clasificar_tls(tls_r.get(f["clave"])) if f["clave"] in tls_r else (f.get("tls") or ""), f["id"])


def resumen_segmentos(cliente, eqs, hallazgos, segmentos):
    """Tarjetas de la lista de aplicaciones / segmentos de negocio: componentes, estado y flujos."""
    from .arquitectura import costo, flujos_de, vigentes
    flujos = flujos_de(cliente, eqs)
    arqs = vigentes(cliente["id"])
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
        fl = [f for f in flujos if f.get("desde") in nombres and f.get("hacia") in nombres
              and (f.get("segmento") == s["nombre"] if s["nombre"] in arqs else not f.get("segmento"))]
        est = [_estado_flujo(estados, f)[0] for f in fl]
        out.append({"nombre": s["nombre"], "descripcion": s.get("descripcion", ""), "sistema": s.get("sistema", ""),
                    "componentes": sorted(es, key=lambda e: e["nombre"]), "peor": peor,
                    "roles": Counter(k for e in es for k in (e["skills"] or [e.get("categoria") or "servidor"])).most_common(6),
                    "ambientes": sorted({e["ambiente"] for e in es if e["ambiente"]}), "flujos": len(fl),
                    "bloqueados": est.count("bloqueado"), "abiertos": est.count("abierto"),
                    "alta": sum(1 for e in es if peor.get(e["id"]) == 3), "media": sum(1 for e in es if peor.get(e["id"]) == 2),
                    "arquitectura": arqs.get(s["nombre"]), "ficha": j(s.get("ficha"), {}),
                    "costo": costo(arqs[s["nombre"]]["m"], eqs) if s["nombre"] in arqs else None})
    return out


def resumen_segmento(cliente, eqs, hallazgos, segmento, ambiente=""):
    """Información general del segmento para el panel del mapa: ficha, recursos totales, flujos, costo."""
    from .arquitectura import costo, vigente
    from .bd import q1
    seg = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", cliente["id"], segmento) or {}
    es = [e for e in eqs if segmento in e["segmentos"]]
    arq = vigente(cliente["id"], segmento)
    if ambiente:
        if arq:
            from .arquitectura import asignacion
            mios = {x["nombre"] for v in asignacion(arq["m"], eqs).values() for x in v.get(ambiente, [])}
            es = [e for e in es if e["nombre"] in mios]
        else:
            es = [e for e in es if (e.get("ambiente") or "") == ambiente]
    ids = {e["id"] for e in es}
    hs = [h for h in hallazgos if h["equipo_id"] in ids]
    disco = sum(sum(d.get("gb", 0) for d in j(e.get("discos"), [])) for e in es)
    k = costo(arq["m"], eqs) if arq else None
    tarjeta = next((t for t in resumen_segmentos(cliente, es, hallazgos, [seg]) if t["nombre"] == segmento), {}) if seg else {}
    if k and ambiente:  # costo solo de este ambiente
        filas = [f for f in k["filas"] if f["ambiente"] == ambiente]
        k = dict(k, mensual=round(sum(f["mensual"] for f in filas), 2), incremental=round(sum(f["incremental"] for f in filas), 2))
    return {"nombre": segmento, "ambiente": ambiente, "descripcion": seg.get("descripcion", ""), "ficha": j(seg.get("ficha"), {}),
            "equipos": len(es), "encendidos": sum(1 for e in es if e.get("energia") == "encendido"),
            "apagados": sum(1 for e in es if e.get("energia") == "apagado"),
            "vcpu": sum(int(e.get("cpu") or 0) for e in es), "ram_gb": round(sum((e.get("ram_mb") or 0) for e in es) / 1024, 1),
            "disco_gb": round(disco), "ambientes": sorted({e["ambiente"] for e in es if e.get("ambiente")}),
            "sistemas": sorted({e.get("so") for e in es if e.get("so")}), "productos": sorted({k_ for e in es for k_ in e["skills"]}),
            "hallazgos": len(hs), "altos": sum(1 for h in hs if h["severidad"] == "alta"),
            "flujos": tarjeta.get("flujos", 0), "abiertos": tarjeta.get("abiertos", 0), "bloqueados": tarjeta.get("bloqueados", 0),
            "arquitectura": arq["version"] if arq else None, "entrada": (arq["m"].get("entrada") or {}) if arq else {},
            "costo_mensual": k["mensual"] if k else None, "incremental": k["incremental"] if k else None, "moneda": k["moneda"] if k else "USD"}
