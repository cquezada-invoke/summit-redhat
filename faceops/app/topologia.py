"""Mapa vivo de la infraestructura: redes, equipos y flujos de red, con el estado actual (hallazgos) de cada equipo.
Niveles: global (infraestructura → redes, o segmentos de negocio) → una red o un segmento (sus equipos y flujos) → equipo.
La topología (redes, enlaces y flujos) vive en la clasificación del cliente («_topologia»); los equipos y su estado, en el
inventario de FaceOps."""
import os
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
    if cat == "clúster openshift" or "nodo_ocp" in str(e.get("datos") or ""):
        return "openshift", ("Clúster OpenShift" if cat == "clúster openshift" else "Nodo OpenShift")
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
                     "info": {"Rol": rol_nombre, "Categoría": f"{e.get('categoria') or '—'} {e.get('forma') or ''}".strip(),
                              **({"Hardware": " ".join(x for x in ((j(e.get("datos"), {}).get("hardware") or {}).get("fabricante"), (j(e.get("datos"), {}).get("hardware") or {}).get("modelo")) if x)}
                                 if (j(e.get("datos"), {}).get("hardware") or {}).get("fabricante") else {}),
                              "Sistema": e.get("so") or "—", "IP": e.get("ip") or "—",
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


def latencia_prom(cliente_id, f, dias=7):
    """Latencia promedio del flujo en los últimos días (la peor de sus puertos) y cuántas mediciones: («0.6 ms», n)."""
    from .bd import q1
    peor, n = None, 0
    for p in re.findall(r"\d{2,5}", str(f.get("puerto", ""))):
        r = q1("SELECT AVG(ms) m, COUNT(*) n FROM flujos_latencia WHERE cliente_id=? AND clave=? AND en >= datetime('now', ?)",
               cliente_id, clave_flujo(f["desde"], f["hacia"], p), f"-{dias} days")
        if r and r["n"]:
            n += r["n"]
            peor = r["m"] if peor is None else max(peor, r["m"])
    if peor is None:
        return "", 0
    return (f"{peor:.2f} ms" if peor < 1 else f"{peor:.1f} ms"), n


def _candado_entrada(cliente, eqs, arq, f):
    """La entrada desde los usuarios: candado solo si es HTTPS y la máquina de entrada presenta un certificado de la CA interna."""
    try:
        url = ((arq or {}).get("m", {}).get("entrada") or {}).get("url") or ""
        if not url.startswith("https") and "443" not in str(f.get("puerto", "")):
            return ""
        from .certificados_panel import de_equipo, leer_sin_esperar
        r = leer_sin_esperar(cliente, eqs)   # AAP lenta (26 llamadas al vencer la caché) no frena el dibujo del mapa
        return " 🔒CA interna" if r and de_equipo(r, f["hacia"]) else " 🔒"
    except Exception:  # noqa: BLE001 - la CA puede no responder: sin candado
        return ""


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
                # las máquinas que la arquitectura asigna a un componente se dibujan aunque aún no tengan su plataforma
                # instalada (p. ej. el servidor de midPoint recién creado): sin ellas el componente y sus flujos desaparecían
                dentro = [e for e in eqs if e["nombre"] in comp_de]
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
                els.append({"data": {"id": f"f{i}", "source": fuente, "target": "e:" + f["hacia"], "label": "", "puertos": f"{f['puerto']}{_candado_entrada(cliente, eqs, _a, f)}",
                                     "latencia": "", "tipo": "entrada", "texto": f.get("texto", "")}})
            continue
        if f["desde"] in por_nombre and f["hacia"] in por_nombre:
            est, cuando = _estado_flujo(estados, f)
            a, b = por_nombre[f["desde"]], por_nombre[f["hacia"]]
            tk = en_curso.get((a.get("ip"), b.get("ip")))
            lat, lat_en = latencia(estados, f)
            lat_p, lat_n = latencia_prom(cliente["id"], f)
            els.append({"data": {"id": f"f{i}", "source": camino(f, "e:" + f["desde"]), "target": "e:" + f["hacia"],
                                 "label": (f"⏳ {tk['numero']}" if tk else ""),
                                 "fdesde": f["desde"], "fhacia": f["hacia"], "fpuertos": re.findall(r"\d{2,5}", str(f.get("puerto", ""))),
                                 "puertos": etiqueta_puertos(estados, f), "latencia": lat, "latencia_en": lat_en,
                                 "latencia_prom": lat_p, "latencia_n": lat_n, "etq_lat": ("⌀ " + lat_p) if lat_p else (lat or ""),
                                 "multi": len(re.findall(r"\d{2,5}", str(f.get("puerto", "")))) > 1, "tls": tls_de(estados, f), "tls_tipo": tls_de(estados, f).get("tipo", ""),
                                 "ticket": tk,
                                 "texto": f.get("texto", "") + ((" · pasa por " + ", ".join(v["nombre"] for v in f["via"])) if f.get("via") else ""),
                                 "tipo": "flujo", "estado_flujo": est, "verificado": cuando,
                                 "origen_ip": a.get("ip"), "destino_ip": b.get("ip"), "puerto": str(f.get("puerto", "")),
                                 "desde": f["desde"], "hacia": f["hacia"]}})
    if _a and nivel == "segmento":
        _componentes_ocp(els, _a["m"], _asg, cliente)
    if nivel == "segmento":
        _fuentes_storage(els, dentro)
        _grupos_replicas(els, comp_de)
    if _a and ambiente:
        _planificados(els, _a["m"], _asg, ambiente, pasos_vistos)
    if nivel == "segmento":
        _bifurcacion_lake(els, cliente, valor, _a["m"] if _a else None, _asg)
    return els, [{"texto": cliente["nombre"], "nivel": "global"}, {"texto": titulo}]


def _fuentes_storage(els, dentro):
    """Fuentes externas del segmento: discos de datos dedicados (no el del sistema) y montajes de red (NFS, CIFS) que usan
    sus máquinas: una caja de storage por punto de montaje, unida a la máquina que lo usa."""
    import json as _json
    ids = {e["data"]["id"] for e in els}
    sistema = {"/", "/boot", "/boot/efi", "/var", "/home", "/opt", "/tmp", "/usr", "[SWAP]", ""}
    for e in dentro:
        nid = "e:" + e["nombre"]
        if nid not in ids:
            continue
        discos = e.get("discos")
        discos = _json.loads(discos) if isinstance(discos, str) else (discos or [])
        datos = e.get("datos")
        datos = _json.loads(datos) if isinstance(datos, str) else (datos or {})
        fuentes = []
        for i, d in enumerate(discos if isinstance(discos, list) else []):
            if i == 0 or not isinstance(d, dict):
                continue   # el primer disco es el del sistema
            for pt in d.get("particiones") or []:
                for mo in pt.get("montajes") or []:
                    if mo.get("punto") not in sistema:
                        fuentes.append({"punto": mo["punto"], "detalle": f"disco de datos /dev/{d.get('dev', '')} · {mo.get('gb') or d.get('gb')} GB · {mo.get('fs', '')}",
                                        "uso": mo.get("uso"), "tipo": "disco"})
        for mo in datos.get("montajes_red") or []:
            fuentes.append({"punto": mo.get("punto"), "detalle": f"{mo.get('fs', '')} {mo.get('origen', '')} · {mo.get('gb')} GB", "uso": mo.get("uso"), "tipo": "red"})
        for j, fu in enumerate(fuentes[:4]):
            sid = f"st:{e['nombre']}:{j}"
            els.append({"data": {"id": sid, "tipo": "almacen", "label": f"Storage · {fu['punto']}\n{fu['detalle']}" + (f"\nuso {fu['uso']} %" if fu.get("uso") is not None else ""),
                                 "info": {"Máquina": e["nombre"], "Punto de montaje": fu["punto"], "Origen": fu["detalle"],
                                          "Uso": f"{fu['uso']} %" if fu.get("uso") is not None else "—", "Tipo": "montaje de red" if fu["tipo"] == "red" else "disco dedicado"}}})
            els.append({"data": {"id": f"stl:{e['nombre']}:{j}", "source": nid, "target": sid, "tipo": "montaje", "label": fu["punto"]}})


def _grupos_replicas(els, comp_de):
    """Máquinas que cumplen el mismo componente (p. ej. dos JBoss): quedan marcadas como un grupo de réplicas para poder
    comprimirlas en una sola caja (aplicaciones grandes) o verlas una a una."""
    from collections import defaultdict as _dd
    grupos = _dd(list)
    for e in els:
        d = e["data"]
        if d.get("tipo") != "equipo" or d.get("source") or not d.get("id", "").startswith("e:"):
            continue
        c = comp_de.get(d.get("nombre"))
        clave = ("c:" + c["id"], c["nombre"]) if c else (("r:" + d.get("icono", "")), d.get("rol") or "") if d.get("icono") in ("servicios", "web", "base_datos", "mensajeria") else None
        if clave:
            grupos[clave].append(d)
    for (gid, gnom), ds in grupos.items():
        if len(ds) < 2:
            continue
        for d in ds:
            d["grupo_rep"], d["grupo_nombre"], d["grupo_n"] = gid, gnom, len(ds)


def _url_app(cliente_id, segmento, m):
    """URL de la aplicación web del segmento: la de su arquitectura (entrada) o la primera route de OpenShift."""
    from urllib.parse import urlsplit
    en = ((m or {}).get("entrada") or {}).get("url") or ""
    if en.startswith("http"):
        p_ = urlsplit(en)
        return f"{p_.scheme}://{p_.netloc}/"
    try:
        from .segmentos_ocp import de_segmento
        for o in de_segmento(cliente_id, segmento):
            for u in (o["rutas_d"] or {}).values():
                return u
    except Exception:  # noqa: BLE001
        pass
    return ""


def bifurcacion_de(cliente_id, segmento):
    from .bd import q1
    try:
        return q1("SELECT * FROM bifurcaciones WHERE cliente_id=? AND segmento=?", cliente_id, segmento)
    except Exception:  # noqa: BLE001
        return None


def _componentes_ocp(els, m, asg, cliente=None):
    """Componentes en OpenShift (no son máquinas): un marco «OpenShift» con la route (el balanceador de OpenShift) como
    caja de entrada y una caja por componente; lo que corre fuera del clúster (p. ej. una base de datos en VM) queda
    fuera del marco. «estilo: bifurcacion» en un flujo es la derivación al data lake: solo se dibuja si está activada."""
    ocp = [c for c in m["componentes"] if c.get("plataforma") == "openshift" and not any(asg.get(c["id"], {}).values())]
    if not ocp:
        return
    if not any(e["data"].get("id") == "p:seg" for e in els):
        els.append({"data": {"id": "p:seg", "label": m["segmento"], "tipo": "grupo"}})
    cid = cliente["id"] if cliente else 0
    b = bifurcacion_de(cid, m["segmento"]) if cliente else None
    activa = bool(b and b["activa"])
    solo_bif = {c["id"] for c in ocp if all(f.get("estilo") == "bifurcacion" for f in m["flujos"] if c["id"] in (f["desde"], f["hacia"]))
                and any(f.get("estilo") == "bifurcacion" for f in m["flujos"] if c["id"] in (f["desde"], f["hacia"]))}
    if not activa:
        ocp = [c for c in ocp if c["id"] not in solo_bif]
    ids = {c["id"] for c in ocp}
    nodos_cl = sorted({x for c in ocp for x in (c.get("equipos") or [])})
    ns = ""
    try:
        from .segmentos_ocp import de_segmento
        ns = ", ".join(sorted({o["namespace"] for o in de_segmento(cid, m["segmento"]) if o.get("namespace")}))
    except Exception:  # noqa: BLE001
        pass
    els.append({"data": {"id": "p:ocp", "parent": "p:seg", "tipo": "grupo_ocp",
                         "label": "OpenShift · " + (", ".join(nodos_cl) or "clúster") + (f" · namespace {ns}" if ns else "")}})
    for c in ocp:
        icono, rol = rol_de({}, c)
        donde = " · ".join(c.get("equipos") or []) or "OpenShift"
        els.append({"data": {"id": "c:" + c["id"], "parent": "p:ocp", "tipo": "equipo", "icono": icono, "rol": c.get("rol") or rol,
                             "nombre": c["nombre"], "ip": "", "ubicacion": f"OpenShift · {donde}", "ambiente": "", "estado": 0, "nube": False,
                             "label": c["nombre"], "caja": c["nombre"], "componente_id": c["id"],
                             "info": {"Componente": c["nombre"], "Plataforma": "OpenShift", "Corre en": donde, "Producto": c.get("producto") or "—"}}})
    entrada = (m.get("entrada") or {}).get("hacia")
    route = None
    c_ent = next((c for c in ocp if c["id"] == entrada), None)
    # si el componente de entrada ES el router de OpenShift (p. ej. la plataforma misma), no se agrega otra caja «Route»
    es_router = bool(c_ent and (c_ent.get("tipo") in ("balanceador", "proxy") or "ingress" in str(c_ent.get("producto") or "")))
    if entrada in ids and not es_router:   # la route de OpenShift es el balanceador de la aplicación: su propia caja
        url = _url_app(cid, m["segmento"], m)
        host = url.split("//")[-1].strip("/")
        route = "c:_route"
        els.append({"data": {"id": route, "parent": "p:ocp", "tipo": "equipo", "icono": "balanceo", "rol": "Balanceo de OpenShift (route)",
                             "nombre": "Route · " + (host.split(".")[0] if host else "aplicación"), "ip": host, "ubicacion": "OpenShift · router (HAProxy)",
                             "ambiente": "", "estado": 0, "nube": False, "label": "Route", "caja": "Route", "app_url": url, "es_route": True,
                             "info": {"Rol": "Balanceador de OpenShift (route, HAProxy)", "Host": host or "—", "Hacia": entrada}}})
    ext = {x["id"]: x.get("nombre", x["id"]) for x in m["externos"]}
    presentes = {e["data"]["id"] for e in els if not e["data"].get("source")}

    def nodos_de(comp, origen):
        """Nodos del mapa que cumplen un extremo del flujo: el componente en OpenShift, las máquinas (del ambiente dibujado)
        de un componente en VM, o el actor externo (inicio del flujo si es origen; sistema externo si es destino)."""
        if comp in ids:
            return ["c:" + comp]
        if comp in ext:
            return ["x:" + comp]
        return [n for n in ("e:" + x["nombre"] for xs in (asg.get(comp) or {}).values() for x in xs) if n in presentes]

    for i, f in enumerate(m["flujos"]):
        if f.get("estilo") == "bifurcacion" and not activa:
            continue
        if f["desde"] not in ids and f["hacia"] not in ids:
            continue   # flujo entre máquinas: lo dibuja el recorrido normal con su estado verificado
        for a in nodos_de(f["desde"], True):
            for k, b_ in enumerate(nodos_de(f["hacia"], False)):
                if a.startswith("x:") and b_.startswith("x:"):
                    continue
                if a.startswith("x:") and not any(e["data"]["id"] == a for e in els):
                    els.append({"data": {"id": a, "label": "▶ " + ext[f["desde"]] + "\ninicio del flujo", "tipo": "entrada", "info": {"Rol": "Punto de entrada"}}})
                if b_.startswith("x:") and not any(e["data"]["id"] == b_ for e in els):
                    els.append({"data": {"id": b_, "label": ext[f["hacia"]] + "\nsistema externo", "tipo": "entrada",
                                         "info": {"Rol": "Sistema externo (destino del flujo)"}}})
                comun = {"estilo": f.get("estilo") or "negocio", "estado_flujo": "no aplica", "puertos": " · ".join(f["puertos"]), "label": "",
                         "texto": f.get("motivo", ""), "desde": f["desde"], "hacia": f["hacia"], "puerto": str(f.get("puerto", ""))}
                sfx = f"-{a}-{k}" if not a.startswith(("c:", "x:")) or k else ""
                if route and a.startswith("x:") and f["hacia"] == entrada:   # entrada → route → aplicación
                    els.append({"data": dict(comun, id=f"oc{i}r", source=a, target=route, tipo="entrada", hacia="route")})
                    els.append({"data": dict(comun, id=f"oc{i}", source=route, target=b_, tipo="flujo", desde="route", puertos="8080")})
                    continue
                if route and f.get("estilo") == "bifurcacion" and f["desde"] == entrada:   # la copia sale del balanceador (route)
                    a = route
                els.append({"data": dict(comun, id=f"oc{i}{sfx}", source=a, target=b_, tipo="entrada" if a.startswith("x:") else "flujo")})
    # el nodo del clúster donde corren ya está dicho en el marco: no se dibuja suelto
    nodos_ocp = {"e:" + n for n in nodos_cl}
    usados = {e["data"].get(k) for e in els for k in ("source", "target") if e["data"].get("source")}
    els[:] = [e for e in els if not (e["data"].get("id") in nodos_ocp and e["data"]["id"] not in usados)]


# ------------------------------------------------------------------ bifurcación hacia el data lake corporativo
LAKE = {"nombre": "Data lake corporativo", "detalle": "S3 + Trino (demo-datalake)", "web": "https://lake.sno.34.176.42.56.sslip.io/buckets/lake/",
        "explorador": "https://sql-lake.sno.34.176.42.56.sslip.io/lake",
        "colector": os.environ.get("FACEOPS_LAKE_COLECTOR", "http://192.168.122.50:30480"),   # interno: lo usa el espejo de nginx
        "datos": os.environ.get("FACEOPS_LAKE_DATOS", "https://colector-lake.sno.34.176.42.56.sslip.io/datos/"),
        # máquina de control para las derivaciones declaradas: el ticket no la toca (todo corre en AAP), solo la usa de límite
        "control": os.environ.get("FACEOPS_LAKE_CONTROL", "nodo-nginx")}
PRIORIDAD_PUNTO = ["balanceo", "web", "servicios", "base_datos"]   # dónde es más factible copiar cada request: el que la recibe primero


_USO_LAKE = {}


def _uso_lake(pref):
    """MB enviados al lake por el segmento (acumulado) y MB que ocupa hoy en S3; lo cuenta el colector. Caché de 60 s."""
    import time
    import requests
    c = _USO_LAKE.get(pref)
    if c and time.time() - c[0] < 60:
        return c[1]
    try:
        j = requests.get(f"{LAKE['colector']}/estado/{pref}", timeout=3).json()
        u = {"enviado_mb": round(j["enviado_bytes"] / 1048576, 1), "s3_mb": round(j["en_s3_bytes"] / 1048576, 1),
             "desde": j.get("enviado_desde", "")} if "enviado_bytes" in j else {}
    except Exception:  # noqa: BLE001 - informativo
        u = {}
    _USO_LAKE[pref] = (time.time(), u)
    return u


def prefijo_lake(segmento):
    import unicodedata
    t = unicodedata.normalize("NFKD", segmento).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40]


def declaracion_lake(m):
    """La derivación que declara la arquitectura (flujo «estilo: bifurcacion»): la aplicación escribe directo en su zona del
    lake, sin espejo. Devuelve su destino, cómo medirla (la propia aplicación informa lo que escribe) y la máquina donde corre."""
    if not m:
        return None
    f = next((x for x in m.get("flujos") or [] if x.get("estilo") == "bifurcacion"), None)
    if not f:
        return None
    comps = {c["id"]: c for c in m["componentes"]}
    eq = next((e for cid in (f["desde"], f["hacia"]) for e in (comps.get(cid) or {}).get("equipos") or []), "")
    med = f.get("medicion") or {}
    return {"destino": f.get("destino") or "", "medicion_url": med.get("url") or "", "medicion_clave": str(med.get("clave") or ""), "equipo": eq}


def params_derivacion(cliente_id, segmento, punto, accion):
    """Variables del ticket «Data lake · Derivar segmento»: espejo en el punto de entrada y flujos funcionales de su arquitectura.
    Si la arquitectura declara la derivación (la aplicación ya escribe en el lake), el ticket no pone espejo: verifica que la
    aplicación informe lo que escribe y deja la derivación registrada (modo «declarada»)."""
    from .arquitectura import vigente
    arq = vigente(cliente_id, segmento)
    ff = ((arq or {}).get("m") or {}).get("flujos_funcionales") or {}
    p = {"dl_accion": accion, "dl_prefijo": prefijo_lake(segmento), "dl_segmento": segmento, "dl_punto": punto, "dl_colector": LAKE["colector"],
         "dl_flujos": "si" if ff.get("base") else "no", "dl_app_url": ff.get("base", ""), "dl_escenario": ff.get("escenario") or [["GET", "/"]],
         "dl_minutos": int(ff.get("minutos", 30)), "dl_modo": "espejo"}
    d = declaracion_lake((arq or {}).get("m"))
    if d:
        p.update(dl_modo="declarada", dl_flujos="no", dl_punto="Route", dl_destino=d["destino"], dl_medicion_url=d["medicion_url"], dl_medicion_clave=d["medicion_clave"])
    return p


def _bifurcacion_lake(els, cliente, segmento, m, asg):
    """Todo segmento aplicativo tiene un punto factible para copiar cada request hacia el data lake: el que la recibe
    primero (la route en OpenShift, el balanceador o proxy; si no, la capa web o de servicios; en una base de datos, captura
    de cambios). Ese punto lleva en su flotante la opción «Data lake»: la bifurcación solo se dibuja cuando está activada."""
    from .bd import q1
    seg = q1("SELECT grupo FROM segmentos WHERE cliente_id=? AND nombre=?", cliente["id"], segmento)
    if not seg or (seg["grupo"] or "") != "aplicaciones":
        return
    nodos = {e["data"]["id"]: e["data"] for e in els if not e["data"].get("source")}
    declarada = bool(m and any(f.get("estilo") == "bifurcacion" for f in m.get("flujos") or []))
    punto, como = [], ""
    if "c:_route" in nodos:
        punto, como = ["c:_route"], "espejo de tráfico en la route de OpenShift (router HAProxy)"
    elif m and (m.get("entrada") or {}).get("hacia"):
        c = next((x for x in m["componentes"] if x["id"] == m["entrada"]["hacia"]), None)
        if c:
            cand = ["c:" + c["id"]] + ["e:" + x["nombre"] for xs in (asg.get(c["id"]) or {}).values() for x in xs]
            punto = list(dict.fromkeys(n for n in cand if n in nodos))[:3]
            como = ("espejo de tráfico (mirror) en " + c["nombre"]) if c.get("tipo") in ("balanceador", "proxy", "waf") else ("copia de cada request en " + c["nombre"])
    if not punto:
        eqs = [d for d in nodos.values() if d.get("tipo") in ("equipo", "por_crear")]
        for rol in PRIORIDAD_PUNTO:
            punto = [d["id"] for d in eqs if d.get("icono") == rol][:3]
            if punto:
                break
        if not punto:   # sin rol claro: las que no reciben flujos de otras (por donde entra el tráfico)
            destinos = {e["data"]["target"] for e in els if e["data"].get("source")}
            punto = [d["id"] for d in eqs if d["id"] not in destinos][:2] or [d["id"] for d in eqs][:1]
        if punto:
            d0 = nodos[punto[0]]
            como = {"balanceo": "espejo de tráfico (mirror) en el balanceador ", "base_datos": "captura de cambios (CDC) en la base de datos "}.get(
                d0.get("icono"), "copia de cada request en ") + (d0.get("nombre") or "")
    if not punto:
        return
    pref = prefijo_lake(segmento)
    b = bifurcacion_de(cliente["id"], segmento)
    activa = bool(b and b["activa"])
    url = _url_app(cliente["id"], segmento, m)
    uso = _uso_lake(pref) if activa else {}
    for n in punto:
        nodos[n]["bif"] = {"enviado_mb": uso.get("enviado_mb"), "s3_mb": uso.get("s3_mb"), "desde": uso.get("desde", ""),"segmento": segmento, "destino": ((b or {}).get("destino") if declarada and (b or {}).get("destino") else f"s3://lake/{pref}/"), "activa": activa,
                           "medicion": bool(declarada and (declaracion_lake(m) or {}).get("medicion_url")), "como": como, "declarada": declarada,
                           "por": (b or {}).get("por") or "", "en": (b or {}).get("en") or "", "web": LAKE["web"] + pref + "/", "explorador": LAKE["explorador"],
                           "datos": LAKE["datos"] + pref}
        if url and not nodos[n].get("app_url"):
            nodos[n]["app_url"] = url
    if not activa or declarada:
        return   # sin activar no se dibuja; si la arquitectura la declara, la dibuja ella (con su lake como componente)
    punto = [n for n in punto if nodos[n].get("tipo") != "por_crear"]
    if not punto:
        return   # en este ambiente el punto de derivación aún no existe (está por crear): no hay copia que dibujar
    els.append({"data": {"id": "dl:lake", "tipo": "datalake", "label": f"{LAKE['nombre']}\n{LAKE['detalle']}\nlake/{pref}/",
                         "info": {"Destino": f"s3://lake/{pref}/", "Archivos": LAKE["web"] + pref + "/", "Explorador": LAKE["explorador"]}}})
    for i, n in enumerate(punto):
        els.append({"data": {"id": f"bl{i}", "source": n, "target": "dl:lake", "tipo": "bifurcacion", "estilo": "bifurcacion", "label": "",
                             "puertos": "9000", "estado_bif": "activa", "punto": nodos[n].get("nombre") or nodos[n].get("label", ""),
                             "como": como, "destino": f"s3://lake/{pref}/", "web": LAKE["web"] + pref + "/", "explorador": LAKE["explorador"],
                           "datos": LAKE["datos"] + pref}})


def m_nombre(m, cid):
    return next((c["nombre"] for c in m["componentes"] if c["id"] == cid), None) or \
        next((x.get("nombre", cid) for x in m["externos"] if x["id"] == cid), cid)


def _planificados(els, m, asg, amb, vistos):
    """Componentes que la arquitectura declara en este ambiente pero que aún no existen: cajas «por crear» y sus
    uniones planificadas, para ver la aplicación completa del ambiente. Lo que corre en OpenShift no es «por crear» (lo
    dibuja su marco), la derivación al data lake solo existe si está activada, y un servicio de red del camino ya dibujado
    «por asignar» no se repite."""
    nodos = {}
    if not any(e["data"].get("id") == "p:seg" for e in els):
        els.append({"data": {"id": "p:seg", "label": f"{m['segmento']} · {amb}", "tipo": "grupo"}})
    existentes = {e["data"]["id"] for e in els if not e["data"].get("source")}
    por_crear = set()
    for c in m["componentes"]:
        if c.get("plataforma") == "openshift":
            nodos[c["id"]] = []
            continue
        if "v:" + c["id"] in existentes:   # servicio de red del camino ya dibujado (por asignar)
            nodos[c["id"]] = ["v:" + c["id"]]
            continue
        if c["replicas"].get(amb) and not asg.get(c["id"], {}).get(amb):
            icono, rol = rol_de({}, c)
            nid = f"v:{c['id']}:{amb}"
            nodos[c["id"]] = [nid]
            por_crear.add(nid)
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
        if f.get("estilo") == "bifurcacion":
            continue   # la derivación al data lake se dibuja aparte y solo si está activada
        origen = ["x:" + f["desde"]] if f["desde"] in ext else nodos.get(f["desde"], [])
        destino = nodos.get(f["hacia"], [])
        via = [n for v in f.get("via") or [] for n in nodos.get(v, [])[:1]]
        for a in origen:
            for b in destino:
                camino_ = [a] + [v for v in via if v not in (a, b)] + [b]
                if not any(n in por_crear for n in camino_):
                    continue  # las uniones entre máquinas reales ya están dibujadas con su estado
                if a.startswith("x:") and not any(e["data"]["id"] == a for e in els):
                    els.append({"data": {"id": a, "label": "▶ " + next((x.get("nombre", a[2:]) for x in m["externos"] if x["id"] == a[2:]), a[2:])
                                         + "\ninicio del flujo", "tipo": "entrada", "info": {"Rol": "Punto de entrada"}}})
                faltan = [n for n, x in ((m_nombre(m, f["desde"]), a), (m_nombre(m, f["hacia"]), b)) if x in por_crear]
                for x, y in zip(camino_, camino_[1:]):
                    if (x, y) in vistos:
                        continue
                    vistos.add((x, y))
                    els.append({"data": {"id": f"pl{i}-{len(vistos)}", "source": x, "target": y, "tipo": "planificado", "ambiente": amb,
                                         "puertos": " · ".join(f["puertos"]), "label": "",
                                         "texto": f.get("motivo", "") + (" · pasa por " + ", ".join(m_nombre(m, v) for v in f["via"]) if f.get("via") else ""),
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
            if ms is not None:
                ej("INSERT INTO flujos_latencia(cliente_id, clave, ms, en) VALUES (?,?,?,?)", p["cliente_id"], f["clave"], ms, ahora())
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
        if not es and s["nombre"] not in arqs:
            continue  # sin máquinas ni arquitectura: no hay nada que mostrar
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
                    "costo": costo(arqs[s["nombre"]]["m"], eqs) if s["nombre"] in arqs else None,
                    "pedidas": sum(n for c in arqs[s["nombre"]]["m"]["componentes"] if c.get("plataforma") != "openshift"
                                   for n in c["replicas"].values()) if s["nombre"] in arqs else 0,
                    "ocp": _ocp_de(cliente["id"], s["nombre"]),
                    "ocp_comps": [c["nombre"] for c in arqs[s["nombre"]]["m"]["componentes"] if c.get("plataforma") == "openshift"] if s["nombre"] in arqs else []})
    return out


def _ocp_de(cliente_id, segmento):
    try:
        from .segmentos_ocp import de_segmento
        return de_segmento(cliente_id, segmento)
    except Exception:  # noqa: BLE001
        return []


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
    es_d = es if ambiente else ([e for e in es if (e.get("ambiente") or "") == "produccion"] or es)   # disponibilidad: la de producción
    disp = disponibilidad_segmento(es_d, j(seg.get("ficha"), {}), exige=ambiente in ("", "produccion"))
    paridad = None
    try:   # réplicas y paridad de ambientes (app/paridad.py): solo inventario y arquitectura, sin consultas en vivo
        from .paridad import evaluar as _paridad
        try:
            from flask import url_for as _uf
            urls_ = {"equipo": lambda i: _uf("consola.equipo", eid=i), "arquitectura": _uf("consola.arquitectura")}
        except Exception:  # noqa: BLE001  (fuera de una petición: sin enlaces)
            urls_ = {}
        paridad = _paridad(cliente["id"], segmento, eqs, arq, urls_) if seg else None
    except Exception:  # noqa: BLE001
        paridad = None
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
            "costo_mensual": k["mensual"] if k else None, "incremental": k["incremental"] if k else None, "moneda": k["moneda"] if k else "USD",
            "ocp": [{"ambiente": o["ambiente"], "namespace": o["namespace"], "rutas": o["rutas_d"], "gitops": o["gitops"], "version": o["version"]}
                    for o in _ocp_de(cliente["id"], segmento)],
            "ocp_comps": [c["nombre"] for c in arq["m"]["componentes"] if c.get("plataforma") == "openshift"] if arq else [],
            **_puntaje_salud(salud_segmento(cliente, eqs, es, hs, segmento, arq, disp,
                                            ((paridad or {}).get("items") or {}).get(ambiente or "produccion", []) if paridad else None)),
            "paridad": {k_: v for k_, v in (paridad or {}).items() if k_ not in ("items", "por_maquina")} if paridad else None,
            "disponibilidad": {k_: v for k_, v in disp.items() if k_ != "funciones"} | {"funciones": [{x_: f[x_] for x_ in ("funcion", "n", "on", "estado")} for f in disp["funciones"]]},
            "salud_meta": metas_salud(j(seg.get("ficha"), {})),
            "metricas_app": _indicadores_app(arq)}


def _indicadores_app(arq):
    """Indicadores que publica la propia aplicación (p. ej. tiempos de respuesta del modelo de IA): informativos, fuera de la salud."""
    url = ((arq or {}).get("m") or {}).get("metricas")
    if not url:
        return []
    mt = _metricas_app(url)
    return (mt or {}).get("indicadores") or [{"area": "Aplicación", "nivel": "media", "titulo": "No responde el endpoint de métricas de la aplicación", "detalle": url}]


PRINCIPIOS_SALUD = ["Disponibilidad", "Rendimiento", "Seguridad", "Recuperación", "Costo", "Excelencia operativa"]   # pilares (AWS Well-Architected)


def metas_salud(ficha=None):
    """Meta alcanzable de cada principio: la de la ficha del segmento o, si no tiene, la de la organización."""
    import os
    import yaml
    try:
        base = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "metas-salud.yml"), encoding="utf-8")) or {}
    except OSError:
        base = {}
    propias = (ficha or {}).get("metas_salud") or {}
    return {p: float(propias.get(p, base.get(p, 100))) for p in PRINCIPIOS_SALUD}


def estrella_svg(actual, meta, tam=320, etiquetas=True):
    """Radar de salud: un eje por principio; contorno punteado = meta, área azul = situación actual, ámbar = brecha."""
    import math
    from markupsafe import Markup, escape
    W, H = 520, 340
    cx, cy, R = W / 2, 178, 118
    def pt(i, v):
        a = math.radians(-90 + 360 / len(PRINCIPIOS_SALUD) * i)
        r = R * max(0, min(100, v)) / 100
        return cx + r * math.cos(a), cy + r * math.sin(a)
    pol = lambda vals: " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, v) for i, v in enumerate(vals)))
    act = [actual.get(p, 0) for p in PRINCIPIOS_SALUD]
    met = [meta.get(p, 100) for p in PRINCIPIOS_SALUD]
    out = [f'<svg class="estrella" viewBox="0 0 {W} {H}" width="{tam}" role="img" aria-label="Radar de salud">']
    for f in (1, .5):
        out.append(f'<polygon points="{pol([100 * f] * len(PRINCIPIOS_SALUD))}" fill="none" stroke="var(--line)" stroke-width="1"/>')
    for i in range(len(PRINCIPIOS_SALUD)):
        x, y = pt(i, 100)
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="var(--line)" stroke-width="1"/>')
    out.append(f'<polygon points="{pol(met)}" fill="#fac775" fill-opacity=".55" stroke="#ba7517" stroke-width="1.6" stroke-dasharray="6 4"/>')
    out.append(f'<polygon points="{pol(act)}" fill="#e6f1fb" stroke="#185fa5" stroke-width="2.2"/>')
    for i in range(len(PRINCIPIOS_SALUD)):
        for vals, col in ((met, "#ba7517"), (act, "#185fa5")):
            x, y = pt(i, vals[i])
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="{col}"/>')
        if etiquetas:
            a = math.radians(-90 + 360 / len(PRINCIPIOS_SALUD) * i)
            x, y = cx + (R + 22) * math.cos(a), cy + (R + 22) * math.sin(a)
            anc = "middle" if abs(math.cos(a)) < .3 else ("start" if math.cos(a) > 0 else "end")
            dy = -14 if i == 0 else (6 if math.sin(a) > .5 else 0)
            out.append(f'<text x="{x:.1f}" y="{y + dy:.1f}" text-anchor="{anc}" font-size="13" font-weight="700" fill="currentColor">{escape(PRINCIPIOS_SALUD[i])}</text>'
                       f'<text x="{x:.1f}" y="{y + dy + 15:.1f}" text-anchor="{anc}" font-size="11.5" fill="var(--muted)">{act[i]:.0f} % · meta {met[i]:.0f} %</text>')
    out.append("</svg>")
    return Markup("".join(out))


def estrella_parque(cliente, eqs, hallazgos):
    """Radar de salud del parque: promedio de los segmentos de aplicaciones en producción, con su meta."""
    from .bd import q
    segs = [s["nombre"] for s in q("SELECT nombre FROM segmentos WHERE cliente_id=? AND grupo='aplicaciones'", cliente["id"])]
    filas = []
    for sg in segs:
        try:
            r = resumen_segmento(cliente, eqs, hallazgos, sg, "produccion")
        except Exception:  # noqa: BLE001
            continue
        if not r["equipos"] and not r.get("ocp"):
            continue
        filas.append({"nombre": sg, "pct": r["salud_pct"], "areas": {a["area"]: a["pct"] for a in r["salud_areas"]}})
    if not filas:
        return None
    actual = {p: round(sum(f["areas"].get(p, 100) for f in filas) / len(filas)) for p in PRINCIPIOS_SALUD}
    meta = metas_salud()
    return {"actual": actual, "meta": meta, "svg": estrella_svg(actual, meta, tam=430), "segmentos": sorted(filas, key=lambda f: f["pct"]),
            "pct": round(sum(f["pct"] for f in filas) / len(filas))}


def _puntaje_salud(salud):
    """% de salud por área (parte en 100: −15 por situación grave, −6 por advertencia) y del segmento (promedio de sus áreas)."""
    pen = {"alta": 15, "media": 6}
    areas = {a: 100 for a in PRINCIPIOS_SALUD}   # los pilares siempre se muestran (100 % si no hay nada que atender)
    for x in salud:
        if x["area"] not in areas:
            continue
        areas[x["area"]] = 0 if x.get("cero") else max(0, areas[x["area"]] - pen.get(x["nivel"], 0))
    total = round(sum(areas.values()) / len(areas)) if areas else 100   # promedio de las áreas: una sola área mala no lleva todo a 0
    return {"salud": salud, "salud_pct": total, "salud_areas": [{"area": k, "pct": areas[k]} for k in PRINCIPIOS_SALUD]}


def _metricas_app(url):
    """Métricas que publica la propia aplicación (tiempos del modelo de IA, estado del lake…) en su «metricas» de la
    arquitectura. Se consulta por la red interna del clúster con el nombre de la route (sin depender del DNS público)."""
    import requests
    from urllib.parse import urlsplit
    p_ = urlsplit(url)
    for destino in (url, f"{p_.scheme}://192.168.122.50{p_.path}"):
        try:
            r = requests.get(destino, headers={"Host": p_.netloc} if "192.168.122.50" in destino else {}, timeout=4, verify=False)
            if r.ok:
                return r.json()
        except Exception:  # noqa: BLE001
            continue
    return None


def q1_seg(cliente, segmento):
    from .bd import q1
    return q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", cliente["id"], segmento)


def disponibilidad_segmento(es, ficha=None, riesgos=None, exige=True):
    """Disponibilidad del segmento (antes Gobierno › Disponibilidad): cada función (sistema o plataforma) con sus instancias
    encendidas. Con dos o más es alta disponibilidad; con una sola, punto único de falla; sin ninguna, caída. Un clúster
    OpenShift es redundante por sí mismo (sus réplicas viven dentro). `exige`: en producción la redundancia se exige
    (alimenta el pilar Disponibilidad de la salud); en QA o desarrollo solo se informa."""
    from .asistente import PLATAFORMAS
    from .vistas import _familia_so, _plataformas_de
    nombre = {c: n for c, n, _ in PLATAFORMAS}
    funciones = defaultdict(list)
    for e in es:
        ps_ = [c for c in _plataformas_de(e, (riesgos or {}).get(e["id"]) or {}) if not c.startswith("so:") and c not in ("rhel", "windows-server")]
        for c in ps_ or ["so:" + _familia_so(e.get("so"))]:
            funciones[c].append(e)
    fx = []
    for c, lst in sorted(funciones.items()):
        on = [e for e in lst if e.get("energia") == "encendido"]
        ha = len(on) >= 2 or (c == "openshift" and on)
        estado = "alta disponibilidad" if ha else ("punto único de falla" if len(on) == 1 else "caída")
        fx.append({"codigo": c, "funcion": nombre.get(c, c[3:] if c.startswith("so:") else c), "n": len(lst), "on": len(on), "estado": estado,
                   "equipos": [(e["id"], e["nombre"], e.get("energia")) for e in lst],
                   "uptime": max((j(e.get("datos"), {}).get("uptime_dias") or 0) for e in on) if on else None})
    balanceo = any(c in ("nginx", "haproxy", "f5-bigip") for c in funciones) \
        or any((e.get("categoria") or "") in ("", "servidor") and {"nginx", "haproxy", "f5-bigip"} & set(e.get("skills") or []) for e in es)
    ha_ = sum(1 for x in fx if x["estado"] == "alta disponibilidad")
    fi_ = ficha or {}
    return {"funciones": fx, "balanceo": balanceo, "total": len(fx), "ha": ha_, "pct": round(100 * ha_ / len(fx)) if fx else None,
            "spof": [x for x in fx if x["estado"] == "punto único de falla"], "caidas": [x for x in fx if x["estado"] == "caída"],
            "rto": fi_.get("rto") or "", "rpo": fi_.get("rpo") or "", "exige": exige}


def salud_segmento(cliente, eqs, es, hs, segmento, arq, disp=None, paridad=None):
    """Salud consolidada del segmento con su mecanismo de remediación: máquinas apagadas, hallazgos, flujos cerrados,
    flujos sin TLS, latencias altas, métricas de la aplicación (p. ej. tiempos de respuesta del modelo de IA) y la
    [{nivel: alta|media|ok|info, area, titulo, detalle, remedio}]"""
    from .arquitectura import flujos_de
    out = []
    apagadas = [e["nombre"] for e in es if e.get("energia") == "apagado"]
    if apagadas and len(apagadas) == len(es):   # todo el segmento apagado: no hay servicio
        out.append({"nivel": "alta", "area": "Disponibilidad", "cero": True, "titulo": "Segmento completamente apagado: sin servicio", "detalle": ", ".join(apagadas[:8]),
                    "remedio": "Encender sus máquinas desde cada caja (ticket «Encender o apagar equipo» con aprobación)"})
    elif apagadas:   # apagada no es falla: puede ser a propósito (advertencia)
        out.append({"nivel": "media", "area": "Disponibilidad", "titulo": f"{len(apagadas)} máquina(s) apagada(s)", "detalle": ", ".join(apagadas[:8]),
                    "remedio": "Encenderlas desde su caja si se necesitan (ticket «Encender o apagar equipo» con aprobación)"})
    # sin alta disponibilidad: función con una sola instancia encendida en producción (la misma cuenta que la sección
    # «Disponibilidad» del segmento en el mapa: disponibilidad_segmento)
    if disp is None:
        disp = disponibilidad_segmento(es, j((q1_seg(cliente, segmento) or {}).get("ficha"), {}))
    if paridad is not None:   # regla de réplicas y paridad (app/paridad.py): por componente de la arquitectura, reemplaza el conteo por función
        out += paridad
    elif disp.get("exige"):
        for x in disp["spof"]:
            out.append({"nivel": "media", "area": "Disponibilidad", "titulo": f"{x['funcion']} sin alta disponibilidad (una sola máquina en producción)",
                        "detalle": next((n for _, n, en in x["equipos"] if en == "encendido"), ""), "remedio": "Replicar con alta disponibilidad desde su caja (botón HA)"})
    # recuperación: cada máquina con respaldo para reconstruirla, y el segmento con su RTO/RPO definidos
    sin_resp = [e["nombre"] for e in es if (e.get("categoria") or "servidor") == "servidor" and "respaldo-git" not in (e.get("skills") or [])]
    if sin_resp:
        out.append({"nivel": "media", "area": "Recuperación", "titulo": f"{len(sin_resp)} máquina(s) sin respaldo continuo", "detalle": ", ".join(sin_resp[:8]),
                    "remedio": "Habilitar «Respaldo continuo en Git» (configuración, paquetes y servicios para recuperarlas)"})
    fi_ = j((q1_seg(cliente, segmento) or {}).get("ficha"), {})
    if not fi_.get("rto") or not fi_.get("rpo"):
        out.append({"nivel": "media", "area": "Recuperación", "titulo": "Sin RTO/RPO definidos en la ficha del segmento",
                    "remedio": "Definir en la ficha cuánto puede estar caído (RTO) y cuántos datos se pueden perder (RPO)"})
    por_t = defaultdict(list)
    for h in hs:
        por_t[(h["severidad"], h["titulo"])].append(h)
    for (sev, tit), xs in sorted(por_t.items(), key=lambda x: {"alta": 0, "media": 1}.get(x[0][0], 2))[:8]:
        out.append({"nivel": "alta" if sev == "alta" else "media", "area": "Excelencia operativa", "titulo": tit,
                    "detalle": ", ".join(sorted({x.get("equipo") or "" for x in xs}))[:160],
                    "remedio": (xs[0].get("sugerencia") or "Reparar desde la caja de cada máquina (botón Reparar)")[:200]})
    estados = _estados(cliente)
    nombres = {e["nombre"] for e in es}
    fl = [f for f in flujos_de(cliente, eqs) if f.get("desde") in nombres and f.get("hacia") in nombres and f.get("segmento") in (segmento, None, "")]
    cerrados = [f for f in fl if _estado_flujo(estados, f)[0] == "bloqueado"]
    if cerrados:
        out.append({"nivel": "alta", "area": "Rendimiento", "titulo": f"{len(cerrados)} flujo(s) cerrado(s)",
                    "detalle": ", ".join(f"{f['desde']}→{f['hacia']}:{f.get('puerto')}" for f in cerrados[:4]),
                    "remedio": "Abrirlos con ticket desde la línea del mapa (aprobación en AAP y ServiceNow)"})
    sin_tls = [f for f in fl if not tls_de(estados, f).get("tipo")]
    if sin_tls:
        out.append({"nivel": "media", "area": "Seguridad", "titulo": f"{len(sin_tls)} flujo(s) sin TLS",
                    "detalle": ", ".join(f"{f['desde']}→{f['hacia']}" for f in sin_tls[:4]),
                    "remedio": "Emitir el certificado con ticket desde la línea del flujo («Certificados · Emitir para servicio», CA interna)"})
    lentos = []
    from .rendimiento import umbral as _umbral
    um_ = _umbral(cliente["id"])
    for f in fl:
        v, n = latencia_prom(cliente["id"], f)
        try:
            ms = float(v.split()[0]) if v else 0
        except ValueError:
            ms = 0
        if ms > um_:
            lentos.append((ms, f))
    if lentos:
        lentos.sort(key=lambda x: -x[0])
        out.append({"nivel": "media", "area": "Rendimiento", "titulo": f"Latencia sobre el umbral ({um_:.0f} ms) en {len(lentos)} flujo(s): cuello de botella",
                    "detalle": ", ".join(f"{f['desde']}→{f['hacia']} ⌀ {ms:.0f} ms" for ms, f in lentos[:4]),
                    "remedio": "Revisar red y carga del destino; medir de nuevo con «Probar latencia» en la línea"})
    try:   # SLA del servicio (Gobierno › SLA): incumplimiento, «SLA en riesgo» por error budget y tiempos de atención
        from .sla import salud_items
        out += salud_items(cliente, segmento)
    except Exception:  # noqa: BLE001  sin SLA o sin tablas: nada que agregar
        pass
    orden = {"alta": 0, "media": 1, "info": 2, "ok": 3}
    return sorted(out, key=lambda x: orden.get(x["nivel"], 2))
