"""Diagrama de centros de datos (Infraestructura › Centros de datos): una caja grande por centro o nube con su salud y lo que
contiene, y una línea entre dos centros SOLO cuando algo de adentro se conecta de verdad. Todo sale del inventario y la
configuración de FaceOps; si un dato no existe, no hay línea (nada inventado).

Fuentes de las conexiones:
  · réplica   → «Replicar máquina» (tabla replicas_maquina) con origen y réplica en centros distintos
  · balanceo  → balanceadores (componente balanceador/Nginx/HAProxy de la arquitectura vigente, equipos de categoría
                balanceador o flujos «balanceo» de la topología) con backends en otro centro; el balanceador es un nodo propio
  · flujo     → flujos reales entre máquinas (arquitecturas vigentes y topología del cliente) que cruzan de centro
  · enlace    → enlaces de red de la topología entre redes de centros distintos (Internet, MPLS, túnel…)
  · VPN       → servidor VPN (datos.vpn) cuyos clientes son máquinas de otro centro
  · respaldo  → respaldo de datos (restic → S3) cuyo destino está en otro centro, y la copia de origen que deja «Mover máquina»
  · malla     → execution node y hop node de Edge en centros distintos
  · segmento  → máquinas del mismo segmento de negocio repartidas en centros distintos
El SVG se arma aquí (sin JS de terceros); la plantilla solo lo pinta."""
import json
import re
from collections import defaultdict

from .bd import j, q

PUERTOS = {"22": "SSH", "80": "HTTP", "443": "HTTPS", "8080": "HTTP", "8443": "HTTPS", "5432": "PostgreSQL", "3306": "MySQL",
           "1433": "SQL Server", "1521": "Oracle", "27017": "MongoDB", "6379": "Redis/Valkey", "61616": "AMQP", "5986": "WinRM",
           "5985": "WinRM", "636": "LDAPS", "389": "LDAP", "88": "Kerberos", "9000": "S3", "51820": "WireGuard", "1194": "OpenVPN",
           "500": "IPsec", "4500": "IPsec", "27199": "malla AAP", "5022": "SQL AG", "9092": "Kafka", "6443": "API OpenShift"}
# tipo → (etiqueta, clase CSS, orden de dibujo)
TIPOS = {"balanceo": ("balanceo", "cdd-balanceo", 0), "replica": ("réplica", "cdd-replica", 1), "respaldo": ("respaldo", "cdd-respaldo", 2),
         "vpn": ("VPN", "cdd-vpn", 3), "malla": ("malla AAP", "cdd-vpn", 4), "enlace": ("enlace de red", "cdd-enlace", 5),
         "flujo": ("flujo", "cdd-flujo", 6), "segmento": ("segmento", "cdd-segmento", 7)}
HIPERVISORES = ("kvm", "vmware", "vsphere", "hyperv", "proxmox", "nutanix", "openshift-virt")
PROD_BALANCEO = {"nginx", "haproxy", "f5", "citrix", "netscaler"}


def proto(puerto):
    """«5432» → «PostgreSQL 5432»; «61616 · 6379» → «AMQP 61616 · Redis/Valkey 6379»; texto libre (BGP, LAN) tal cual."""
    if puerto in (None, ""):
        return ""
    ps = re.findall(r"\d{1,5}", str(puerto))
    if not ps:
        return str(puerto)
    return " · ".join(f"{PUERTOS[p]} {p}" if p in PUERTOS else f"{p}/tcp" for p in dict.fromkeys(ps))


def _mapa_centros(cds):
    """{nombre de máquina: centro} y {ip: centro} (solo IP inequívocas)."""
    por_nombre, por_ip, ip_n = {}, {}, defaultdict(set)
    for c in cds:
        for e in c["equipos"]:
            por_nombre.setdefault(e["nombre"], c["nombre"])
            if e.get("ip") and re.match(r"\d+\.\d+\.\d+\.\d+$", e["ip"]):
                ip_n[e["ip"]].add(c["nombre"])
    for ip, cs in ip_n.items():
        if len(cs) == 1:
            por_ip[ip] = next(iter(cs))
    return por_nombre, por_ip


def conexiones(cliente, cds, eqs):
    """(lineas, balanceadores): líneas entre pares de centros por tipo, cada una con sus componentes; y los balanceadores
    con backends conocidos y los centros donde los tiene."""
    cen, cen_ip = _mapa_centros(cds)
    nombres_cd = {c["nombre"] for c in cds}
    por_nombre = {e["nombre"]: e for e in eqs}
    lineas = {}

    def agregar(a, b, tipo, det, pr=""):
        if not a or not b or a == b or a not in nombres_cd or b not in nombres_cd:
            return
        k = (tipo,) + tuple(sorted((a, b)))
        ln = lineas.setdefault(k, {"tipo": tipo, "a": k[1], "b": k[2], "det": [], "proto": []})
        if det not in ln["det"]:
            ln["det"].append(det)
        for p in [x.strip() for x in pr.split(" · ") if x.strip()] if pr else []:
            if p not in ln["proto"]:
                ln["proto"].append(p)

    # 1. Réplicas (Replicar máquina)
    try:
        from .replicar_maquina import replicas
        for rep, ori in replicas(cliente["id"]).items():
            agregar(cen.get(ori), cen.get(rep), "replica", f"{ori} → {rep} (réplica de la máquina)")
    except Exception:  # noqa: BLE001 - sin tabla aún
        pass

    # 2. Flujos reales (arquitecturas vigentes + topología) y balanceadores
    balanc = {}
    flujos = []
    try:
        from .arquitectura import flujos_de, vigentes
        flujos = [f for f in flujos_de(cliente, eqs) if f.get("hacia") != "*" and not str(f.get("desde", "")).startswith("x:")]
        lb_comp = set()
        for r in vigentes(cliente["id"]).values():
            for c in r["m"]["componentes"]:
                if c.get("tipo") == "balanceador" or (c.get("producto") or "") in PROD_BALANCEO:
                    lb_comp.add((r["m"]["segmento"], c["id"]))
    except Exception:  # noqa: BLE001
        lb_comp = set()
    es_lb = {e["nombre"] for e in eqs if (e.get("categoria") or j(e.get("datos"), {}).get("categoria")) == "balanceador"}
    for f in flujos:
        d, h = f.get("desde"), f.get("hacia")
        lb = (d in es_lb or (f.get("segmento"), f.get("desde_c")) in lb_comp or "balance" in (f.get("texto") or "").lower())
        if lb and d in cen and h in cen:
            b = balanc.setdefault(d, {"nombre": d, "centro": cen[d], "id": (por_nombre.get(d) or {}).get("id"), "sitios": defaultdict(list), "proto": []})
            det = f"{d} → {h}" + (f" · {f['segmento']}" if f.get("segmento") else "")
            if det not in b["sitios"][cen[h]]:
                b["sitios"][cen[h]].append(det)
            for p in [x.strip() for x in proto(f.get("puerto")).split(" · ") if x.strip()]:
                if p not in b["proto"]:
                    b["proto"].append(p)
            continue
        for v in f.get("via") or []:   # balanceador en el camino (via) de un flujo de arquitectura
            if v.get("tipo") == "balanceador" and v.get("equipo") in cen and h in cen:
                b = balanc.setdefault(v["equipo"], {"nombre": v["equipo"], "centro": cen[v["equipo"]], "id": (por_nombre.get(v["equipo"]) or {}).get("id"),
                                                    "sitios": defaultdict(list), "proto": []})
                det = f"{v['equipo']} → {h} · {f.get('segmento', '')}"
                if det not in b["sitios"][cen[h]]:
                    b["sitios"][cen[h]].append(det)
        if d in cen and h in cen and cen[d] != cen[h]:
            agregar(cen[d], cen[h], "flujo", f"{d} → {h}" + (f" · {f['texto']}" if f.get("texto") else "") + (f" · {f['segmento']}" if f.get("segmento") else ""),
                    proto(f.get("puerto")))
    for b in balanc.values():   # balanceo entre sitios: también como conexión entre el centro del balanceador y los de sus backends
        for s, dets in b["sitios"].items():
            for det in dets:
                agregar(b["centro"], s, "balanceo", det, " · ".join(b["proto"]))

    # 3. Enlaces de red de la topología (redes con su centro)
    topo = j(cliente.get("clasificacion"), {}).get("_topologia", {}) or {}
    red_cd = {r["id"]: r.get("infra") for r in topo.get("redes", []) if r.get("id")}
    for en in topo.get("enlaces", []):
        a, b = red_cd.get(en.get("desde")), red_cd.get(en.get("hacia"))
        agregar(a, b, "enlace", f"red {en.get('desde')} ↔ red {en.get('hacia')}" + (f" · {en['via']}" if en.get("via") else ""), en.get("via", ""))

    # 4. VPN, respaldos y copias de origen de «Mover máquina» (datos de cada máquina)
    s3 = ""
    try:
        from .respaldo_datos import destino_s3
        s3 = destino_s3(cliente["id"])
    except Exception:  # noqa: BLE001
        pass
    for e in eqs:
        d = j(e.get("datos"), {})
        ce = cen.get(e["nombre"])
        vpn = d.get("vpn") or {}
        for cl in vpn.get("clientes") or []:
            n = cl.get("nombre") if isinstance(cl, dict) else str(cl)
            ip = (cl.get("ip") or "").split("/")[0] if isinstance(cl, dict) else ""
            otro = cen.get(n) or cen_ip.get(ip)
            agregar(ce, otro, "vpn", f"{e['nombre']} (servidor VPN) ↔ {n or ip}", f"WireGuard {vpn.get('puerto') or 51820}")
        rd = d.get("respaldo_datos") or {}
        dest = rd.get("destino") or rd.get("s3") or rd.get("repositorio") or (s3 if rd else "")
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)(?::(\d+))?", str(dest or ""))
        if rd and m:
            agregar(ce, cen_ip.get(m.group(1)), "respaldo", f"{e['nombre']} → {m.group(1)} ({rd.get('herramienta') or 'restic'} → S3)", proto(m.group(2) or "9000"))
    try:   # «Mover máquina»: la de origen queda de respaldo en el centro de donde salió
        for t in q("SELECT t.numero, t.parametros, t.limite FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? "
                   "AND a.nombre_aap='Infra · Mover máquina entre centros de datos' AND t.estado='exitoso' ORDER BY t.id", cliente["id"]):
            p = j(t["parametros"], {})
            maq = p.get("maquina") or p.get("mv_maquina") or (t.get("limite") or "").split(":")[0]
            ori = p.get("centro_origen") or p.get("mv_origen") or p.get("origen")
            if maq in cen and ori and p.get("modo", p.get("mv_modo", "mover")) == "mover":
                ori_cd = next((c["nombre"] for c in cds if ori == c["nombre"] or any(ori in (x["nombre"], x["ubicacion"]) for x in c["infras"])), None)
                agregar(ori_cd, cen[maq], "respaldo", f"{maq}: copia de origen tras moverla ({t['numero']})")
    except Exception:  # noqa: BLE001
        pass

    # 5. Malla de Edge: execution node y hop node en centros distintos
    try:
        for s in q("SELECT codigo, nodo, hop FROM edge_sitios WHERE cliente_id=?", cliente["id"]):
            if s["nodo"] and s["hop"]:
                agregar(cen.get(s["nodo"]), cen.get(s["hop"]), "malla", f"sitio {s['codigo']}: {s['nodo']} → hop {s['hop']}", "malla AAP 27199")
    except Exception:  # noqa: BLE001
        pass

    # 6. Segmentos de negocio repartidos en centros distintos
    seg_cd = defaultdict(lambda: defaultdict(list))
    for e in eqs:
        if e["nombre"] in cen:
            for s in e.get("segmentos") or []:
                seg_cd[s][cen[e["nombre"]]].append(e["nombre"])
    for s, por in seg_cd.items():
        cs = sorted(por)
        for i in range(len(cs)):
            for k in range(i + 1, len(cs)):
                agregar(cs[i], cs[k], "segmento", f"{s}: {', '.join(por[cs[i]][:4])} ({cs[i]}) · {', '.join(por[cs[k]][:4])} ({cs[k]})")

    lns = sorted(lineas.values(), key=lambda x: (TIPOS[x["tipo"]][2], x["a"], x["b"]))
    bals = [dict(b, sitios=dict(b["sitios"])) for b in balanc.values() if b["sitios"]]
    return lns, sorted(bals, key=lambda b: (-len(b["sitios"]), b["nombre"]))


def _corta(t, n):
    return t if len(t) <= n else t[:max(1, n - 1)] + "…"


def _url_infra(x, eqs):
    """Ficha de cada infraestructura: hipervisor, nube, clúster OpenShift (su máquina ocp-<clúster>) o, si no, su inventario."""
    from flask import url_for
    if x["tipo"] in ("openshift", "openshift-virt") and x["detalle_d"].get("cluster"):
        e = next((e for e in eqs if e["nombre"] == "ocp-" + x["detalle_d"]["cluster"]), None)
        if e:
            return url_for("consola.equipo", eid=e["id"])
    if x["tipo"] in HIPERVISORES:
        return url_for("consola.hipervisor", iid=x["id"])
    if x.get("ambito") == "nube":
        return url_for("consola.nube", iid=x["id"])
    return url_for("consola.inventario", infra=x["ubicacion"])


def diagrama(cliente, cds, eqs):
    """Geometría del SVG: cajas de centros (con sus infraestructuras clicables), nodos de balanceadores arriba y líneas
    ortogonales por carriles bajo las cajas."""
    from flask import url_for
    from .salud import tono_salud

    def url_infra(x):
        return _url_infra(x, eqs)

    def url_equipo(i):
        return url_for("consola.equipo", eid=i)

    def url_centro(n_):
        return url_for("consola.centros_datos", centro=n_)
    todas, bals = conexiones(cliente, cds, eqs)
    # el balanceo se dibuja con el nodo del balanceador (arriba), no como línea entre cajas
    lns = [x for x in todas if x["tipo"] != "balanceo" or not bals]
    n = len(cds)
    if not n:
        return None
    cols = n if n <= 3 else 3
    filas = (n + cols - 1) // cols
    W = 1000 if cols <= 3 else 1100
    M, GX = 20, 44
    BW = (W - 2 * M - (cols - 1) * GX) / cols
    TC = 2 if BW >= 400 else 1   # columnas de infraestructuras dentro de cada caja (cajas angostas: una por fila)
    TW = (BW - 24 - (TC - 1) * 12) / TC
    TH, TG = 46, 8
    # pares de centros conectados: un haz por par (una línea paralela por tipo), cada haz por su propio carril bajo la fila
    # de su caja superior
    pos = {c["nombre"]: (i // cols, i % cols) for i, c in enumerate(cds)}
    pares = {}
    for ln in lns:
        pares.setdefault((ln["a"], ln["b"]), []).append(ln)
    carriles_fila = defaultdict(int)
    for a_, b_ in pares:
        carriles_fila[min(pos[a_][0], pos[b_][0])] += 1
    altos = []
    for f in range(filas):
        fila_cds = cds[f * cols:(f + 1) * cols]
        altos.append(max(150 + ((len(c["infras"]) + TC - 1) // TC) * (TH + TG) for c in fila_cds))
    top = 24 + (104 if bals else 0)
    ys, y = [], top
    for f in range(filas):
        ys.append(y)
        y += altos[f] + 34 + carriles_fila[f] * 34 + (24 if carriles_fila[f] else 0)
    H = y + 6
    cajas, lineas, nodos_b = [], [], []
    geo = {}
    for i, c in enumerate(cds):
        f, k = pos[c["nombre"]]
        x0, y0, h = M + k * (BW + GX), ys[f], altos[f]
        geo[c["nombre"]] = (x0, y0, BW, h)
        res = c.get("res") or {}
        sal = res.get("salud")
        hip = [x for x in c["infras"] if x["tipo"] in HIPERVISORES]
        clu = [x for x in c["infras"] if x["tipo"] in ("openshift", "openshift-virt")]
        plats = sorted({s for e in c["equipos"] for s in (e.get("skills") or []) if s not in ("rhel", "windows", "linux")})
        resumen = [f"{len(hip)} hipervisor{'es' if len(hip) != 1 else ''}", f"{len(c['equipos'])} máquina{'s' if len(c['equipos']) != 1 else ''} · {res.get('on', 0)} encendidas",
                   f"{len(plats)} plataforma{'s' if len(plats) != 1 else ''}", f"{len(clu)} clúster{'es' if len(clu) != 1 else ''}"]
        tiles = []
        for t, x in enumerate(c["infras"]):
            tx, ty = x0 + 12 + (t % TC) * (TW + 12), y0 + 140 + (t // TC) * (TH + TG)
            n_eq = x.get("equipos") or 0
            sub = {"clúster": "clúster OpenShift"}.get(x.get("unidad"), f"{n_eq} máquina{'s' if n_eq != 1 else ''}")
            tiles.append({"x": tx, "y": ty, "w": TW, "h": TH, "tit": _corta(x["nombre"], int(TW / 7.2)), "full": x["nombre"],
                          "sub": _corta(f"{x['prov']['nombre']} · {sub}", int(TW / 5.9)), "url": url_infra(x), "tipo": x["tipo"]})
        cajas.append({"nombre": c["nombre"], "tit": _corta(c["nombre"], int(BW / 9.5)), "x": x0, "y": y0, "w": BW, "h": h,
                      "tono": tono_salud(sal), "salud": None if sal is None else int(sal), "etiqueta": c.get("etiqueta", ""),
                      "region": c.get("region", ""), "resumen": resumen, "plats": _corta(", ".join(plats), int((BW - 24) / 5.8)) if plats else "",
                      "tiles": tiles, "url": url_centro(c["nombre"])})
    # puntos de anclaje repartidos en el borde inferior / superior de cada caja
    anc = defaultdict(list)
    for par in pares:
        fa, fb = pos[par[0]][0], pos[par[1]][0]
        sup, inf = (par[0], par[1]) if fa <= fb else (par[1], par[0])
        anc[(sup, "b")].append(par)
        anc[(inf, "b" if fa == fb else "t")].append(par)

    def punto(cd, lado, par):
        x0, y0, w, h = geo[cd]
        lst = anc[(cd, lado)]
        i = lst.index(par)
        x = x0 + w * (0.15 + 0.7 * (i + 1) / (len(lst) + 1))
        return x, (y0 + h if lado == "b" else y0)
    usados = defaultdict(int)
    for par, grupo in pares.items():
        fa, fb = pos[par[0]][0], pos[par[1]][0]
        sup, inf = (par[0], par[1]) if fa <= fb else (par[1], par[0])
        f = pos[sup][0]
        usados[f] += 1
        yl = ys[f] + altos[f] + 22 + usados[f] * 34
        x1, y1 = punto(sup, "b", par)
        x2, y2 = punto(inf, "b" if fa == fb else "t", par)
        partes = [TIPOS[ln["tipo"]][0] + (f" {ln['proto'][0]}" if ln["proto"] and ln["tipo"] != "enlace" else "") for ln in grupo]
        txt = _corta(" · ".join(partes), 64)
        corto = abs(x2 - x1) < 7.0 * len(txt)   # tramo horizontal corto: el rótulo va a la derecha del haz
        for i, ln in enumerate(grupo):
            o = (i - (len(grupo) - 1) / 2) * 6
            d = f"M{x1 + o:.1f},{y1:.1f} V{yl + o:.1f} H{x2 + o:.1f} V{y2:.1f}"
            et = TIPOS[ln["tipo"]][0]
            lineas.append({"d": d, "cls": TIPOS[ln["tipo"]][1], "tipo": ln["tipo"],
                           "tx": (max(x1, x2) + 3 * len(grupo) + 8) if corto else (x1 + x2) / 2, "ta": "start" if corto else "middle",
                           "ty": yl - 3 * len(grupo) - 5, "txt": txt if i == 0 else "",
                           "titulo": f"{et.capitalize()} · {ln['a']} ↔ {ln['b']}", "proto": ln["proto"], "det": ln["det"][:40],
                           "mas": max(0, len(ln["det"]) - 40)})
    # balanceadores: nodos arriba, conectados a cada centro donde tienen backends
    for i, b in enumerate(bals):
        bw = 230
        sitios = [s for s in b["sitios"] if s in geo]
        if not sitios:
            continue
        cx = sum(geo[s][0] + geo[s][2] / 2 for s in sitios) / len(sitios)
        cx = min(max(cx + (i - (len(bals) - 1) / 2) * (bw + 16) * (1 if len(bals) > 1 else 0), M + bw / 2), W - M - bw / 2)
        by = 18
        nodos_b.append({"x": cx - bw / 2, "y": by, "w": bw, "h": 50, "tit": _corta(b["nombre"], 26), "full": b["nombre"],
                        "sub": _corta(f"balanceador · {len(sitios)} sitio{'s' if len(sitios) != 1 else ''} · {', '.join(b['proto'][:2])}", 38),
                        "url": url_equipo(b["id"]) if b.get("id") else None, "centro": b["centro"]})
        for j_, s in enumerate(sitios):
            x0, y0, w, h = geo[s]
            if pos[s][0] == 0:
                xt = x0 + w / 2 + (i - (len(bals) - 1) / 2) * 26
                d = f"M{cx:.1f},{by + 50} V{by + 50 + 22 + i * 6} H{xt:.1f} V{y0}"
            else:   # sitio en la segunda fila: baja por el pasillo entre columnas
                xg = x0 - GX / 2 if pos[s][1] > 0 else x0 + w + GX / 2
                yg = y0 - 14
                d = f"M{cx:.1f},{by + 50} V{by + 50 + 22 + i * 6} H{xg:.1f} V{yg} H{x0 + w / 2:.1f} V{y0}"
                xt = x0 + w / 2
            lineas.append({"d": d, "cls": "cdd-balanceo", "tipo": "balanceo", "tx": xt + 6, "ty": y0 - 8,
                           "txt": "", "titulo": f"Balanceo · {b['nombre']} → {s}", "proto": b["proto"], "det": b["sitios"][s][:40],
                           "mas": max(0, len(b["sitios"][s]) - 40)})
    tipos_presentes = sorted({x["tipo"] for x in lineas}, key=lambda t: TIPOS[t][2])
    return {"w": W, "h": H, "cajas": cajas, "lineas": lineas, "balanceadores": nodos_b,
            "leyenda": [(TIPOS[t][0], TIPOS[t][1]) for t in tipos_presentes], "n_conexiones": len(todas),
            "resumen": [{"tipo": TIPOS[x["tipo"]][0], "a": x["a"], "b": x["b"], "n": len(x["det"])} for x in todas],
            "json": json.dumps([{"t": x["titulo"], "p": x["proto"], "d": x["det"], "m": x["mas"]} for x in lineas], ensure_ascii=False)}
