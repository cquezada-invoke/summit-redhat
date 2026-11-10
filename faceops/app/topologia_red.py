"""Topología de red de FaceOps: equipos de red, lo que cuelga de ellos y (en Redes › Arquitectura) TODAS las máquinas
con la dependencia real que tienen de esos equipos.

Fuentes (no se inventa ninguna conexión; lo que no se sabe queda sin enlace o como «desconocido»):
1. Cableado de los laboratorios containerlab (datos/red-laboratorio.json, lo genera en cada despliegue
   demo4-redes/herramientas/exportar-topologia-faceops.py desde todos los *.clab.yml): enlaces interfaz a interfaz,
   direcciones por interfaz, VLAN, rutas por defecto y vecinos BGP de la configuración. Un laboratorio entra si alguno
   de sus nodos está en el inventario del cliente (se cruza por nombre).
2. Lo que cada equipo trae en su ficha (equipos.datos): interfaces con prefijo, puerta de enlace y vecinos LLDP, con
   claves tolerantes («interfaces», «red.interfaces», «gateway», «lldp», «vecinos»…). Así un equipo de red nuevo con
   interfaces/LLDP aparece solo, sin listas fijas.
3. Dependencia de cada máquina: vecino LLDP → puerta de enlace (dueño único de esa IP) → subred servida por la
   interfaz de un equipo de red. Si no hay ninguna: la red declarada del cliente en FaceOps («_topologia», por prefijo
   de IP), dibujada como contenedor y marcada «declarada»; si tampoco, «Sin dependencia de red conocida».
Las posiciones se calculan en el servidor (capas: perímetro arriba, servidores abajo) y el resultado se cachea.
"""
import hashlib
import ipaddress
import json
import os
import time
from collections import defaultdict

from flask import url_for

from .bd import j

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "red-laboratorio.json")
CATS_RED = ("router", "switch", "firewall", "balanceador")
TIPOS_RED = set(CATS_RED) | {"externo"}
_cache, _labs = {}, {"mtime": None, "datos": None}
TTL, MAX_NODOS = 120, 600
ANCHO, ALTO, SEP_GRUPO = 150, 112, 90


def _cargar():
    try:
        mt = os.path.getmtime(RUTA)
    except OSError:
        return []
    if _labs["mtime"] != mt:
        try:
            with open(RUTA, encoding="utf-8") as f:
                _labs["datos"] = json.load(f).get("labs", [])
        except (OSError, ValueError):
            _labs["datos"] = []
        _labs["mtime"] = mt
    return _labs["datos"] or []


def es_red(e):
    c = (e.get("categoria") or "").lower()
    return c in CATS_RED or (c in ("", "servidor") and bool({"nginx", "haproxy", "f5-bigip"} & set(e.get("skills") or [])))


def _tipo_inv(e):
    c = (e.get("categoria") or "").lower()
    if es_red(e):
        return c if c in CATS_RED else "balanceador"
    if c == "estación de trabajo":
        return "estacion"
    if c == "almacenamiento":
        return "almacenamiento"
    if c.startswith("clúster") or c in ("kubernetes", "openshift"):
        return "cluster"
    return "servidor"


def _tipo_lab(n):
    """Tipo de un nodo que no está en el inventario: solo por lo que declara el laboratorio (clase, imagen, BGP, VLAN)."""
    kind, img = n.get("kind", ""), (n.get("imagen") or "").lower()
    if kind in ("bridge", "ovs-bridge") or "srlinux" in kind or "ceos" in kind:
        return "switch"
    if "vyos" in img or "fortinet" in kind:
        return "firewall"
    if n.get("bgp"):
        return "externo" if n["nombre"].startswith("isp") else "router"
    if any("acceso" in v for v in (n.get("vlans") or {}).values()):
        return "switch"
    return "servidor"


# ------------------------------------------------------------------ lectura tolerante de equipos.datos
def _datos(e):
    d = e.get("datos")
    return (j(d, {}) if isinstance(d, str) else d) or {}


def _primero(d, claves):
    for k in claves:
        if isinstance(d, dict) and d.get(k) not in (None, "", [], {}):
            return d[k]
    return None


def _itfs_eq(e):
    """[(interfaz, «ip/prefijo» o «ip», vlan)] de la ficha del equipo."""
    d = _datos(e)
    src = _primero(d, ("interfaces", "interfaces_red")) or _primero(d.get("red") or {}, ("interfaces",)) or []
    out = []
    items = src.items() if isinstance(src, dict) else enumerate(src if isinstance(src, list) else [])
    for k, v in items:
        if isinstance(v, (str, list)):
            v = {"ip": v}
        if not isinstance(v, dict):
            continue
        nom = _primero(v, ("nombre", "name", "interfaz", "interface")) or (k if isinstance(k, str) else "")
        ips = _primero(v, ("ip", "ipv4", "direccion", "address", "ips", "ip_address")) or []
        pref = _primero(v, ("prefijo", "prefix", "mascara", "netmask", "mask"))
        for ip in ips if isinstance(ips, list) else [ips]:
            ip = str(ip.get("address") or ip.get("ip") or "") if isinstance(ip, dict) else str(ip)
            if ip and "/" not in ip and pref:
                ip = f"{ip}/{pref}"
            try:
                ipaddress.ip_interface(ip)
            except ValueError:
                continue
            out.append((str(nom), ip, _primero(v, ("vlan", "vlan_id"))))
    return out


def _gw_eq(e):
    d = _datos(e)
    g = _primero(d, ("gateway", "puerta_enlace", "puerta_de_enlace")) or _primero(d.get("red") or {}, ("gateway", "puerta_enlace"))
    return str(g) if g else ""


def _lldp_eq(e):
    """[{local, vecino, puerto, ip}] de los vecinos LLDP/CDP de la ficha."""
    d = _datos(e)
    src = _primero(d, ("lldp", "vecinos_lldp", "lldp_vecinos", "vecinos", "cdp")) or _primero(d.get("red") or {}, ("lldp", "vecinos")) or []
    out = []
    items = src.items() if isinstance(src, dict) else enumerate(src if isinstance(src, list) else [])
    for k, v in items:
        if isinstance(v, str):
            v = {"vecino": v}
        if isinstance(v, list):   # {interfaz_local: [vecinos]}
            for x in v:
                if isinstance(x, dict):
                    out.append(_lldp_norm(dict(x, local=x.get("local") or k)))
            continue
        if isinstance(v, dict):
            out.append(_lldp_norm(dict(v, local=v.get("local") or (k if isinstance(k, str) else ""))))
    return [x for x in out if x["vecino"] or x["ip"]]


def _lldp_norm(v):
    return {"local": str(_primero(v, ("local", "interfaz", "interfaz_local", "puerto_local", "local_port", "local_interface")) or ""),
            "vecino": str(_primero(v, ("vecino", "equipo", "remoto", "sistema", "system_name", "sysname", "host", "nombre", "neighbor", "chassis")) or ""),
            "puerto": str(_primero(v, ("puerto", "puerto_remoto", "remote_port", "port_id", "port", "interfaz_remota")) or ""),
            "ip": str(_primero(v, ("ip", "mgmt_ip", "ip_gestion", "management_address", "mgmt")) or "")}


# ------------------------------------------------------------------ laboratorios containerlab
def _ips(n):
    for itf, ips in (n.get("interfaces") or {}).items():
        for ip in ips:
            yield itf, ip
    for ip in n.get("loopback") or []:
        yield "lo", ip


def _redes_itf(n, itf):
    """Redes IP de una interfaz y de sus subinterfaces 802.1Q (eth2 → eth2.20, eth2.30…)."""
    out = []
    for i, ips in (n.get("interfaces") or {}).items():
        if i == itf or i.startswith(itf + "."):
            for ip in ips:
                try:
                    out.append(ipaddress.ip_interface(ip).network)
                except ValueError:
                    pass
    return out


def _etiqueta_enlace(na, ia, nb, ib):
    ra, rb = _redes_itf(na, ia), _redes_itf(nb, ib)
    comunes = [x for x in ra if x in rb]
    vl = (na.get("vlans") or {}).get(ia) or (nb.get("vlans") or {}).get(ib) or {}
    partes = []
    if vl.get("acceso"):
        partes.append(f"VLAN {vl['acceso']}")
    elif vl.get("troncal"):
        partes.append("802.1Q " + "·".join(str(x) for x in sorted(vl["troncal"])))
    redes = comunes or (ra if not rb else (rb if not ra else []))
    if len(redes) == 1:
        partes.append(str(redes[0]))
    elif not partes and len(redes) > 1:
        partes.append(f"{len(redes)} redes")
    return " · ".join(partes)


def _info_nodo(n, dueno):
    info = {}
    if n.get("descripcion"):
        info["Rol"] = n["descripcion"]
    if n.get("mgmt_ip"):
        info["Gestión"] = n["mgmt_ip"]
    itfs = [f"{i} {ip}" for i, ip in _ips(n)]
    if itfs:
        info["Interfaces"] = " · ".join(itfs)
    vls = [f"{i}: {'VLAN ' + str(v['acceso']) if v.get('acceso') else '802.1Q ' + ','.join(map(str, v.get('troncal') or []))}"
           for i, v in (n.get("vlans") or {}).items()]
    if vls:
        info["VLAN"] = " · ".join(vls)
    for r in n.get("rutas") or []:
        if r["destino"] == "default":
            d = dueno.get(r["via"])
            info["Puerta de enlace"] = f"{r['via']} ({', '.join(sorted(d))})" if d else f"{r['via']} (equipo desconocido)"
    if n.get("bgp"):
        info["BGP"] = f"AS {n['bgp']['asn']} · {len(n['bgp']['vecinos'])} vecinos"
    return info


# ------------------------------------------------------------------ construcción
def _clave(cliente, eqs, red_inv, completo):
    h = hashlib.sha1()
    h.update(f"{cliente['id']}|{completo}|{_labs['mtime']}|{len(red_inv)}".encode())
    h.update(json.dumps((j(cliente.get("clasificacion"), {}) or {}).get("_topologia", {}), sort_keys=True, default=str).encode())
    for e in eqs:
        d = e.get("datos")
        h.update(f"{e['id']}|{e['nombre']}|{e.get('ip')}|{e.get('categoria')}|{e.get('estado')}|{e.get('ultima_recoleccion')}|{len(d) if isinstance(d, str) else 0};".encode())
    return h.hexdigest()


def diagrama(cliente, eqs, red_inv, completo=False):
    """{elementos, nodos, aristas, resumen, omitidos, labs} para Cytoscape (layout «preset»).
    completo=False: equipos de red y lo que cuelga de ellos en los laboratorios (bajo la lista de Redes).
    completo=True: además todas las máquinas visibles con su dependencia de red (Redes › Arquitectura)."""
    labs = _cargar()
    k = _clave(cliente, eqs, red_inv, completo)
    hit = _cache.get(k)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    r = _construir(cliente, eqs, red_inv, labs, completo)
    if len(_cache) > 32:
        _cache.clear()
    _cache[k] = (time.time(), r)
    return r


def _construir(cliente, eqs, red_inv, labs, completo):
    por_nombre = {}
    for e in eqs:   # si hay un retirado y uno vigente con el mismo nombre, manda el vigente
        if e["nombre"] not in por_nombre or por_nombre[e["nombre"]].get("estado") == "retirado":
            por_nombre[e["nombre"]] = e
    incluidos = [l for l in labs if any(n["nombre"] in por_nombre for n in l["nodos"])]
    omitidos = [{"lab": l["lab"], "nodos": len(l["nodos"]), "archivo": l.get("archivo", "")} for l in labs if l not in incluidos]
    nodos, aristas, grupos = {}, [], []
    dueno = defaultdict(set)        # IP → nombres de nodo (para BGP y puertas de enlace)
    duenos_id = defaultdict(set)    # IP → ids de nodo
    redes_dev = []                  # (red, id del equipo de red, interfaz, vlan): subredes que sirve cada equipo de red
    alias = {}                      # nombre / hostname (minúsculas, sin dominio) → id de nodo
    pares = {}

    def arista(a, b, tipo, label, detalle, **extra):
        par = frozenset((a, b))
        if a == b:
            return None
        if par in pares:
            ar = pares[par]["data"]
            if tipo in ("bgp", "lldp") and not ar.get(tipo):
                ar[tipo] = True
                ar["label"] = tipo.upper() + (" · " + ar["label"] if ar["label"] else "")
                ar["detalle"] = (ar.get("detalle") or "") + " · " + detalle
            return ar
        x = {"data": dict({"id": f"{tipo[0]}:{a}|{b}", "source": a, "target": b, "tipo": tipo, "label": label, "detalle": detalle}, **extra)}
        pares[par] = x
        aristas.append(x)
        return x["data"]

    def nodo_eq(i, e, parent, **extra):
        d = {"id": i, "nombre": e["nombre"], "ip": e.get("ip") or "", "parent": parent, "tipo": _tipo_inv(e), "inv": True,
             "url": url_for("consola.equipo", eid=e["id"]), "retirado": e.get("estado") == "retirado", "info": {}}
        d.update(extra)
        nodos[i] = d
        alias.setdefault(e["nombre"].lower().split(".")[0], i)
        hn = (_datos(e).get("hostname") or "").lower().split(".")[0]
        if hn:
            alias.setdefault(hn, i)
        if e.get("ip"):
            duenos_id[e["ip"]].add(i)
        for itf, ip, vl in _itfs_eq(e):
            duenos_id[ip.split("/")[0]].add(i)
            if "/" in ip and d["tipo"] in TIPOS_RED:
                redes_dev.append((ipaddress.ip_interface(ip).network, i, itf, vl))
        return d

    # 1 · laboratorios containerlab
    for l in incluidos:
        for n in l["nodos"]:
            for _i, ip in _ips(n):
                dueno[ip.split("/")[0]].add(n["nombre"])
            if n.get("mgmt_ip"):
                dueno[n["mgmt_ip"]].add(n["nombre"])
    nid = {}
    for l in incluidos:
        gid = "g:" + l["lab"]
        corto = (l.get("descripcion") or "").replace("Demo 4 · ", "")
        for sep in (" — ", " (", ":", ". "):
            corto = corto.split(sep)[0]
        grupos.append({"data": {"id": gid, "label": f"{corto.strip() or l['lab']} · {l['lab']}", "tipo": "grupo",
                                "info": {"Fuente": l.get("archivo", ""), "Red de gestión": l.get("gestion", "")}}})
        for n in l["nodos"]:
            i = "n:" + n["nombre"] if "n:" + n["nombre"] not in nodos else f"n:{l['lab']}:{n['nombre']}"
            nid[(l["lab"], n["nombre"])] = i
            e = por_nombre.get(n["nombre"])
            if e:
                d = nodo_eq(i, e, gid)
                d["ip"] = e.get("ip") or n.get("mgmt_ip") or ""
                if e.get("ip") and n.get("mgmt_ip") and e["ip"] != n["mgmt_ip"]:
                    d["info"]["IP en el inventario"] = e["ip"]
            else:
                d = nodos[i] = {"id": i, "nombre": n["nombre"], "ip": n.get("mgmt_ip") or "", "parent": gid, "tipo": _tipo_lab(n),
                                "inv": False, "info": {}}
                alias.setdefault(n["nombre"].lower(), i)
            d["lab"] = l["lab"]
            d["info"] = dict(_info_nodo(n, dueno), **d["info"])
            for itf, ip in _ips(n):
                duenos_id[ip.split("/")[0]].add(i)
                if d["tipo"] in TIPOS_RED and itf != "lo":
                    vl = (n.get("vlans") or {}).get(itf.split(".")[0], {})
                    redes_dev.append((ipaddress.ip_interface(ip).network, i, itf, itf.split(".")[1] if "." in itf else vl.get("acceso")))
            if n.get("mgmt_ip"):
                duenos_id[n["mgmt_ip"]].add(i)
        for en in l["enlaces"]:
            a, b = nid.get((l["lab"], en["a"])), nid.get((l["lab"], en["b"]))
            if a and b:
                na = next(x for x in l["nodos"] if x["nombre"] == en["a"])
                nb = next(x for x in l["nodos"] if x["nombre"] == en["b"])
                arista(a, b, "fisico", _etiqueta_enlace(na, en["ia"], nb, en["ib"]), f"{en['a']} {en['ia']} ↔ {en['b']} {en['ib']}")
    # 2 · equipos de red del inventario fuera de los laboratorios (p. ej. emulados con interfaces/LLDP en su ficha)
    en_lab = {d["nombre"] for d in nodos.values() if d.get("inv")}
    sueltos = []
    for e in red_inv:
        if e["nombre"] not in en_lab:
            i = "n:" + e["nombre"] if "n:" + e["nombre"] not in nodos else f"n:inv:{e['id']}"
            d = nodo_eq(i, e, "g:sin")
            itfs = _itfs_eq(e)
            if itfs:
                d["info"]["Interfaces"] = " · ".join(f"{a} {b}" + (f" (VLAN {c})" if c else "") for a, b, c in itfs[:12])
            d["info"]["Centro de datos"] = e.get("ubicacion") or "—"
            sueltos.append((i, e))
    # 3 · máquinas (solo en la arquitectura completa)
    maquinas = []
    if completo:
        ya = {d["nombre"] for d in nodos.values() if d.get("inv")}
        for e in sorted(eqs, key=lambda x: x["nombre"]):
            if e["nombre"] in ya or es_red(e) or e.get("estado") == "retirado":
                continue
            ya.add(e["nombre"])
            maquinas.append(("m:" + str(e["id"]), e))
            nodo_eq("m:" + str(e["id"]), e, "g:nodep")
    # 4 · vecinos BGP de la configuración FRR de los laboratorios
    gestion = {n["mgmt_ip"] for l in incluidos for n in l["nodos"] if n.get("mgmt_ip")}
    for l in incluidos:
        for n in l["nodos"]:
            for v in (n.get("bgp") or {}).get("vecinos", []):
                a = nid[(l["lab"], n["nombre"])]
                cands = [x for x in duenos_id.get(v["ip"], ()) if nodos[x].get("lab")]
                if len(cands) != 1:   # vecino que no está en los laboratorios incluidos (o ambiguo): desconocido
                    b = "u:" + v["ip"]
                    nodos.setdefault(b, {"id": b, "nombre": "vecino BGP", "ip": v["ip"], "tipo": "desconocido", "inv": False,
                                         "parent": "g:" + l["lab"], "info": {"BGP": f"AS {v['asn']}", "Equipo": "desconocido"}})
                else:
                    b = cands[0]
                arista(a, b, "bgp", f"BGP AS {n['bgp']['asn']}–{v['asn']}",
                       f"sesión BGP {n['nombre']} → {v['ip']}" + (" (por la red de gestión)" if v["ip"] in gestion else ""), bgp=True)
    # 5 · vecinos LLDP de la ficha de cualquier equipo (de red o máquina)
    for i, d in list(nodos.items()):
        e = por_nombre.get(d["nombre"]) if d.get("inv") else None
        if not e:
            continue
        for v in _lldp_eq(e):
            b = alias.get(v["vecino"].lower().split(".")[0]) if v["vecino"] else None
            if not b and v["ip"] and len(duenos_id.get(v["ip"], ())) == 1:
                b = next(iter(duenos_id[v["ip"]]))
            if not b:
                b = "u:lldp:" + (v["vecino"] or v["ip"]).lower()
                nodos.setdefault(b, {"id": b, "nombre": v["vecino"] or "vecino LLDP", "ip": v["ip"], "tipo": "desconocido", "inv": False,
                                     "parent": d["parent"], "info": {"Visto por LLDP desde": d["nombre"], "Equipo": "no está en el inventario"}})
            puertos = " ↔ ".join(x for x in (v["local"], v["puerto"]) if x)
            arista(i, b, "lldp", "LLDP" + (f" · {puertos}" if puertos else ""), f"LLDP {d['nombre']} {v['local']} ↔ {v['vecino'] or v['ip']} {v['puerto']}".strip(), lldp=True)
    # 6 · dependencia de cada máquina: LLDP (ya), puerta de enlace, subred servida por un equipo de red
    dep = set()
    for i, e in maquinas:
        con_red = {x for a in aristas for x in (a["data"]["source"], a["data"]["target"])
                   if i in (a["data"]["source"], a["data"]["target"]) and x != i and nodos.get(x, {}).get("tipo") in TIPOS_RED | {"desconocido"}}
        gw = _gw_eq(e)
        if gw:
            due = [x for x in duenos_id.get(gw, ()) if nodos[x]["tipo"] in TIPOS_RED]
            nodos[i]["info"]["Puerta de enlace"] = gw + (f" ({nodos[due[0]]['nombre']})" if len(due) == 1 else
                                                         (" (varios equipos con esa IP)" if due else " (equipo desconocido)"))
            if len(due) == 1:
                arista(i, due[0], "gateway", f"gateway {gw}", f"{e['nombre']} sale por {gw}")
                con_red.add(due[0])
        ips = {e.get("ip")} | {ip.split("/")[0] for _, ip, _ in _itfs_eq(e)}
        for ip in [x for x in ips if x]:
            try:
                a_ = ipaddress.ip_address(ip)
            except ValueError:
                continue
            sirven = [(rd, did, itf, vl) for rd, did, itf, vl in redes_dev if a_ in rd and did != i]
            if 0 < len({s[1] for s in sirven}) <= 3:   # más de tres equipos con la misma red: ambiguo, no se dibuja
                for rd, did, itf, vl in sirven:
                    if did not in con_red:
                        arista(i, did, "subred", (f"VLAN {vl} · " if vl else "") + str(rd), f"{ip} está en {rd} ({nodos[did]['nombre']} {itf})")
                        con_red.add(did)
        if con_red:
            dep.add(i)
            nodos[i]["parent"] = "g:dep"
    # 7 · redes declaradas en FaceOps (por prefijo) para lo que no tiene dependencia real
    topo = (j(cliente.get("clasificacion"), {}) or {}).get("_topologia", {}) or {}
    redes = topo.get("redes", [])
    contenedor = next((rd for rd in redes if rd.get("categorias") and set(rd["categorias"]) & set(CATS_RED)), None)
    activos = set()
    if contenedor and incluidos:
        activos.add(contenedor["id"])
        grupos.insert(0, {"data": {"id": "g:r:" + contenedor["id"], "label": contenedor["nombre"], "tipo": "contenedor",
                                   "info": {"Red": contenedor.get("detalle", ""), "Fuente": "topología declarada en FaceOps"}}})
        for gr in grupos:
            if gr["data"]["id"].startswith("g:") and gr["data"]["id"] != "g:r:" + contenedor["id"]:
                gr["data"]["parent"] = "g:r:" + contenedor["id"]
    if completo:
        for rd in redes:
            if not rd.get("prefijo"):
                continue
            hijos_ = [i for i, e in maquinas if i not in dep and (e.get("ip") or "").startswith(rd["prefijo"])]
            if hijos_:
                activos.add(rd["id"])
                grupos.append({"data": {"id": "g:dr:" + rd["id"], "label": f"{rd['nombre']} · {rd.get('detalle', '')} (declarada)", "tipo": "declarada",
                                        "info": {"Red": rd.get("detalle", ""), "Infraestructura": rd.get("infra", ""), "Equipos": len(hijos_),
                                                 "Fuente": "topología declarada en FaceOps (prefijo de IP): sin interfaz ni gateway conocidos"}}})
                for i in hijos_:
                    nodos[i]["parent"] = "g:dr:" + rd["id"]
                    nodos[i]["info"]["Red"] = f"{rd['nombre']} (declarada en FaceOps)"
    else:
        otros = [e for e in eqs if not es_red(e) and e.get("estado") != "retirado"]
        hay = [(rd, sum(1 for e in otros if (e.get("ip") or "").startswith(rd["prefijo"]))) for rd in redes if rd.get("prefijo")]
        hay = [(rd, n) for rd, n in hay if n]
        if hay:
            grupos.append({"data": {"id": "g:srv", "label": "Redes de servidores · declaradas en FaceOps", "tipo": "grupo",
                                    "info": {"Fuente": "topología declarada del cliente (redes por prefijo de IP)"}}})
            for rd, n in hay:
                activos.add(rd["id"])
                nodos["r:" + rd["id"]] = {"id": "r:" + rd["id"], "nombre": rd["nombre"], "ip": rd.get("detalle", ""), "parent": "g:srv",
                                          "tipo": "red", "inv": True, "n": n, "url": url_for("consola.redes_arquitectura"),
                                          "info": {"Red": rd.get("detalle", ""), "Equipos": n, "Infraestructura": rd.get("infra", "")}}
    for en in topo.get("enlaces", []):
        if en.get("desde") in activos and en.get("hacia") in activos:
            def ref(x):
                if contenedor and x == contenedor["id"]:
                    return "g:r:" + x
                return ("g:dr:" if completo else "r:") + x
            arista(ref(en["desde"]), ref(en["hacia"]), "declarado", en.get("via", ""), "enlace declarado en FaceOps")
    # grupos de lo que quedó fuera de los laboratorios
    for i, e in sueltos:
        if any(i in (a["data"]["source"], a["data"]["target"]) for a in aristas):
            nodos[i]["parent"] = "g:inv"
        else:
            nodos[i]["info"]["Conexiones"] = "desconocidas"
    grupos.append({"data": {"id": "g:inv", "label": "Equipos de red del inventario (interfaces y LLDP de su ficha)", "tipo": "grupo", "info": {}}})
    if completo:
        grupos.append({"data": {"id": "g:dep", "label": "Máquinas con dependencia de red conocida", "tipo": "grupo",
                                "info": {"Criterio": "vecino LLDP, puerta de enlace o subred de una interfaz de un equipo de red"}}})
        grupos.append({"data": {"id": "g:nodep", "label": "Máquinas sin dependencia de red conocida", "tipo": "sinred",
                                "info": {"Por qué": "sin LLDP, sin puerta de enlace conocida y sin subred servida por un equipo de red ni red declarada"}}})
    grupos.append({"data": {"id": "g:sin", "label": "Equipos de red sin datos de interconexión", "tipo": "sinred",
                            "info": {"Por qué": "no aparecen en ningún laboratorio y su ficha no trae interfaces ni LLDP: conexiones desconocidas"}}})
    recortado = len(nodos) > MAX_NODOS
    if recortado:   # tope de seguridad: primero los equipos de red, luego las máquinas con dependencia
        quedan = sorted(nodos, key=lambda i: (nodos[i]["tipo"] not in TIPOS_RED, nodos[i].get("parent") == "g:nodep", not nodos[i].get("inv")))[:MAX_NODOS]
        nodos = {i: nodos[i] for i in quedan}
    usados = {d.get("parent") for d in nodos.values()}
    for gr in grupos:
        if gr["data"]["id"] in usados and gr["data"].get("parent"):
            usados.add(gr["data"]["parent"])
    grupos = [gr for gr in grupos if gr["data"]["id"] in usados]
    validos = set(nodos) | {gr["data"]["id"] for gr in grupos}
    aristas = [a for a in aristas if a["data"]["source"] in validos and a["data"]["target"] in validos]
    _posiciones(nodos, aristas, grupos)
    for d in nodos.values():
        d["label"] = d["nombre"] + (f"\n{d['ip']}" if d.get("ip") else "") + (f"\n{d['n']} equipos" if d.get("n") else "")
        if d.get("retirado"):
            d["label"] += "\n(retirado)"
    els = grupos + [{"data": {k: v for k, v in d.items() if k != "pos"}, "position": d["pos"]} for d in nodos.values()] + aristas
    por_tipo = defaultdict(int)
    for a in aristas:
        por_tipo[a["data"]["tipo"]] += 1
    resumen = {"equipos_red": sum(1 for d in nodos.values() if d["tipo"] in TIPOS_RED),
               "equipos_red_inv": sum(1 for d in nodos.values() if d["tipo"] in TIPOS_RED and d.get("inv")),
               "maquinas": len(maquinas), "con_dependencia": len(dep),
               "declaradas": sum(1 for d in nodos.values() if str(d.get("parent", "")).startswith("g:dr:")),
               "sin_dependencia": sum(1 for d in nodos.values() if d.get("parent") == "g:nodep"),
               "red_sin_datos": sum(1 for d in nodos.values() if d.get("parent") == "g:sin"), "aristas": dict(por_tipo)}
    return {"elementos": els, "nodos": len(nodos), "aristas": len(aristas), "omitidos": omitidos, "recortado": recortado,
            "labs": [l["lab"] for l in incluidos], "resumen": resumen}


NIVEL_TIPO = {"externo": 0, "router": 1, "firewall": 2, "balanceador": 3, "switch": 3, "servidor": 4, "estacion": 4,
              "almacenamiento": 4, "cluster": 4, "desconocido": 4, "red": 4}
ABAJO = ("g:dep", "g:nodep", "g:sin", "g:srv")


def _posiciones(nodos, aristas, grupos):
    """Capas por grupo: BFS desde el perímetro (ISP; si no hay, el router), perímetro arriba y servidores abajo;
    dentro de cada capa, ordenados por la posición de sus vecinos de la capa anterior. Los grupos de máquinas van en
    filas debajo; las máquinas con dependencia, ordenadas por el equipo de red del que dependen."""
    ady = defaultdict(set)
    for a in aristas:
        s, t = a["data"]["source"], a["data"]["target"]
        if s in nodos and t in nodos:
            ady[s].add(t)
            ady[t].add(s)
    misma = {i: {w for w in ady[i] if nodos[w].get("parent") == nodos[i].get("parent")} for i in nodos}
    hijos = defaultdict(list)
    for i, d in nodos.items():
        hijos[d.get("parent")].append(i)
    cruces = defaultdict(set)
    for i in nodos:
        for w in ady[i]:
            if nodos[i].get("parent") != nodos[w].get("parent"):
                cruces[nodos[i].get("parent")].add(nodos[w].get("parent"))
    gids = [gr["data"]["id"] for gr in grupos if gr["data"]["id"] in hijos]
    pend = [x for x in gids if x not in ABAJO and not x.startswith("g:dr:")]
    pend.sort(key=lambda gid: (gid == "g:inv", -sum(1 for i in hijos[gid] if nodos[i].get("inv"))))
    orden = []
    while pend:
        x = pend.pop(0)
        orden.append(x)
        for y in sorted(cruces[x], key=lambda y: gids.index(y) if y in gids else 99):
            if y in pend:
                pend.remove(y)
                orden.append(y)
    x0, y_max = 0, 0
    for gid in orden:
        ids = hijos[gid]
        nivel = {}
        for r in sorted(ids, key=lambda i: (NIVEL_TIPO.get(nodos[i]["tipo"], 4), nodos[i]["nombre"])):
            if r in nivel:
                continue
            nivel[r] = NIVEL_TIPO.get(nodos[r]["tipo"], 4)
            cola = [r]
            while cola:
                u = cola.pop(0)
                for w in sorted(misma[u]):
                    if w not in nivel:
                        nivel[w] = nivel[u] + 1
                        cola.append(w)
        capas = defaultdict(list)
        for i in ids:
            capas[nivel[i]].append(i)
        xs = {}
        ancho = max(len(v) for v in capas.values())
        for lv in sorted(capas):
            capa = capas[lv]
            capa.sort(key=lambda i: (sum(xs[w] for w in misma[i] if w in xs) / max(1, sum(1 for w in misma[i] if w in xs))
                                     if any(w in xs for w in misma[i]) else 1e9, nodos[i]["nombre"]))
            off = (ancho - len(capa)) * ANCHO / 2
            for k_, i in enumerate(capa):
                xs[i] = x0 + off + k_ * ANCHO
                nodos[i]["pos"] = {"x": xs[i], "y": lv * ALTO}
                y_max = max(y_max, lv * ALTO)
        x0 += ancho * ANCHO + SEP_GRUPO
    ya = {i: nodos[i]["pos"]["x"] for i in nodos if "pos" in nodos[i]}
    por_fila = max(8, min(14, int(max(x0, ANCHO * 8) // ANCHO)))
    y = y_max + ALTO * 1.7
    filas = ["g:dep"] + [g_ for g_ in gids if g_.startswith("g:dr:")] + ["g:srv", "g:nodep", "g:sin"]
    for gid in filas:
        ids = hijos.get(gid, [])
        if gid == "g:dep":   # bajo el equipo del que dependen
            ids = sorted(ids, key=lambda i: (min([ya[w] for w in ady[i] if w in ya] or [1e9]), nodos[i]["nombre"]))
        else:
            ids = sorted(ids, key=lambda i: (nodos[i]["tipo"], nodos[i]["nombre"]))
        x_ini = 0
        if gid == "g:dep" and ids:   # centrada bajo sus equipos
            xs_ = [ya[w] for i in ids for w in ady[i] if w in ya]
            x_ini = max(0, (sum(xs_) / len(xs_) if xs_ else 0) - min(len(ids), por_fila) * ANCHO / 2)
        for k_, i in enumerate(ids):
            nodos[i]["pos"] = {"x": x_ini + (k_ % por_fila) * ANCHO * (1.3 if gid == "g:srv" else 1), "y": y + (k_ // por_fila) * ALTO}
        if ids:
            y += ((len(ids) - 1) // por_fila + 1) * ALTO + ALTO * 0.95
    for d in nodos.values():
        d.setdefault("pos", {"x": 0, "y": y})
