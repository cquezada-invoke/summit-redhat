#!/usr/bin/python3
"""FaceOps · normaliza la lectura de un equipo de red (salida de red-lectura.sh y, si hay, facts de la colección del
fabricante) en un solo formato para la ficha del equipo y el diagrama de topología: plataforma y versión, interfaces
(estado, IP, VLAN, modo), VLAN, vecinos LLDP, rutas, BGP, balanceo y subredes conectadas.
Entrada (stdin, JSON): {"salida": "...", "hv": {variables del host}, "facts": {ansible_net_* opcionales}}.
Salida (stdout, JSON): el diccionario faceops_red."""
import ipaddress
import json
import re
import sys
from datetime import datetime, timezone


def secciones(txt):
    out, cur = {}, None
    for ln in (txt or "").splitlines():
        if ln.startswith("@@@"):
            cur = ln[3:].strip()
            out[cur] = []
        elif cur:
            out[cur].append(ln)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def js(s, defecto):
    try:
        return json.loads(s) if s else defecto
    except ValueError:
        # vtysh puede anteponer avisos («% Can't open configuration file…») a su JSON
        i = min([x for x in (s.find("{"), s.find("[")) if x >= 0] or [-1])
        try:
            return json.loads(s[i:]) if i >= 0 else defecto
        except ValueError:
            return defecto


def v0(x, k="value"):
    """lldpcli -f json0: casi todo viene como [{"value": …}]."""
    if isinstance(x, list) and x:
        x = x[0]
    return x.get(k) if isinstance(x, dict) else x


def main():
    ent = json.load(sys.stdin)
    s = secciones(ent.get("salida", ""))
    hv, facts = ent.get("hv") or {}, ent.get("facts") or {}
    host = s.get("HOST") or facts.get("ansible_net_hostname") or ""

    # plataforma y versión
    vy = js(s.get("VYOS"), {})
    frr = s.get("FRR", "")
    m_frr = re.search(r"FRRouting\s+(\S+)", frr)
    ha = re.search(r"HAProxy version\s+(\S+)", s.get("HAPROXY", ""))
    plat_hv = (hv.get("faceops_plataforma") or "").lower()
    if vy.get("version") or plat_hv == "vyos" or facts.get("ansible_net_version"):
        so, version = "VyOS", re.sub(r"^VyOS\s+", "", vy.get("version") or facts.get("ansible_net_version") or "")
        detalle = f"compilado {vy.get('built_on', '')}".strip() if vy.get("built_on") else ""
    elif plat_hv == "haproxy" and ha:
        so, version, detalle = "HAProxy", ha.group(1), (f"enrutamiento FRRouting {m_frr.group(1)}" if m_frr else "")
    else:
        so, version = "FRRouting", (m_frr.group(1) if m_frr else "")
        detalle = ""
    try:
        uptime_s = int(float((s.get("UPTIME") or "0").split()[0]))
    except (ValueError, IndexError):
        uptime_s = None

    # interfaces
    links = js(s.get("LINK"), [])
    addrs = {a.get("ifname"): a for a in js(s.get("ADDR"), [])}
    vlans_br = {v.get("ifname"): v.get("vlans") or [] for v in js(s.get("VLAN"), [])}
    gestion_ip = hv.get("ansible_host") or ""
    lldp_vec = []
    ll = js(s.get("LLDP"), {})
    for blk in (ll.get("lldp") or [{}]) if isinstance(ll, dict) else []:
        for it in (blk.get("interface") or []) if isinstance(blk, dict) else []:
            ch = (it.get("chassis") or [{}])[0] if isinstance(it.get("chassis"), list) else (it.get("chassis") or {})
            pt = (it.get("port") or [{}])[0] if isinstance(it.get("port"), list) else (it.get("port") or {})
            vec = {"interfaz": it.get("name"), "equipo": v0(ch.get("name")) or v0(ch.get("id")) or "",
                   "puerto": v0(pt.get("id")) or "", "puerto_desc": v0(pt.get("descr")) or "",
                   "ip_gestion": v0(ch.get("mgmt-ip")) or "", "descripcion": (v0(ch.get("descr")) or "")[:120]}
            if vec["puerto_desc"] == vec["puerto"]:
                vec["puerto_desc"] = ""
            lldp_vec.append(vec)
    vec_por_if = {v["interfaz"]: v for v in lldp_vec}
    ifs, subredes, svis = [], [], {}
    for l in links:
        n = l.get("ifname") or ""
        if n == "lo" or n.startswith(("sit", "gre", "erspan", "ip6tnl", "tunl", "ip_vti", "ip6_vti", "ip6gre", "teql", "pim", "dummy", "ifb")):
            continue
        n_base = n.split("@")[0]
        li = l.get("linkinfo") or {}
        kind = li.get("info_kind") or ""
        a = addrs.get(n_base) or {}
        ips = [f"{x['local']}/{x['prefixlen']}" for x in a.get("addr_info", []) if x.get("family") == "inet"]
        oper = (l.get("operstate") or "").lower()
        admin = "up" if "UP" in (l.get("flags") or []) else "down"
        if oper == "unknown":
            oper = "up" if "LOWER_UP" in (l.get("flags") or []) else "down"
        st = (l.get("stats64") or {})
        vl = vlans_br.get(n_base) or []
        pvid = next((x.get("vlan") for x in vl if "PVID" in (x.get("flags") or [])), None)
        ids = [x.get("vlan") for x in vl if x.get("vlan")]
        sin_et = [x.get("vlan") for x in vl if "Egress Untagged" in (x.get("flags") or [])]
        maestro = l.get("master")
        if n_base == "eth0" or any(ip.startswith(gestion_ip + "/") for ip in ips if gestion_ip):
            modo = "gestión"
        elif kind == "bridge":
            modo = "puente"
        elif kind == "vlan":
            vid = (li.get("info_data") or {}).get("id")
            padre = next((x for x in links if (l.get("link") and x.get("ifname") == l.get("link")) or (l.get("link_index") and x.get("ifindex") == l.get("link_index"))), {})
            if ((padre.get("linkinfo") or {}).get("info_kind")) == "bridge":
                modo = "SVI"   # interfaz de capa 3 de una VLAN del switch
                if vid is not None:
                    svis[vid] = {"interfaz": n_base, "ips": ips}
            else:
                modo = f"subinterfaz VLAN {vid}" if vid is not None else "subinterfaz"
        elif maestro and vl:
            modo = (f"acceso VLAN {pvid}" if len(ids) == 1 and pvid else f"troncal {','.join(str(x) for x in ids)}")
        elif ips:
            modo = "ruteada"
        else:
            modo = "sin uso" if oper != "up" else "capa 2"
        if kind == "bridge" and not ips:
            continue   # el puente interno de un switch no es un puerto: sus VLAN se muestran como tabla
        for ip in ips:
            try:
                net = str(ipaddress.ip_interface(ip).network)
                if modo != "gestión" and net not in subredes:
                    subredes.append(net)
            except ValueError:
                pass
        x = {"nombre": n_base, "descripcion": l.get("ifalias") or "", "estado": oper, "admin": admin, "mac": l.get("address") or "",
             "mtu": l.get("mtu"), "ips": ips, "modo": modo, "tipo": kind or "ethernet",
             "rx_bytes": (st.get("rx") or {}).get("bytes"), "tx_bytes": (st.get("tx") or {}).get("bytes"),
             "errores": ((st.get("rx") or {}).get("errors") or 0) + ((st.get("tx") or {}).get("errors") or 0)}
        if vl:
            x["vlans"] = {"pvid": pvid, "permitidas": ids, "sin_etiqueta": sin_et}
        if n_base in vec_por_if:
            v = vec_por_if[n_base]
            x["vecino"] = {"equipo": v["equipo"], "puerto": v["puerto"]}
        ifs.append(x)
    orden = {"gestión": 0, "ruteada": 1}
    ifs.sort(key=lambda i: (orden.get(i["modo"], 2 if not i["modo"].startswith("SVI") else 3),
                            [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", i["nombre"])]))

    # VLAN (switches)
    vl_tab = {}
    for n, vl in vlans_br.items():
        if n.startswith("br"):
            continue
        for v in vl:
            vid = v.get("vlan")
            if not vid:
                continue
            e = vl_tab.setdefault(vid, {"id": vid, "acceso": [], "troncal": [], "svi": ""})
            (e["acceso"] if len(vl) == 1 else e["troncal"]).append(n)
    for vid, sv in svis.items():
        vl_tab.setdefault(vid, {"id": vid, "acceso": [], "troncal": [], "svi": ""})["svi"] = ", ".join(sv["ips"]) or sv["interfaz"]
        desc = next((i["descripcion"] for i in ifs if i["nombre"] == sv["interfaz"]), "")
        vl_tab[vid]["nombre"] = desc
    vlans = [vl_tab[k] for k in sorted(vl_tab)]

    # rutas: FRR (zebra) o, si no hay, la tabla del kernel
    rutas, por_proto, defecto = [], {}, None
    rj = js(s.get("RUTAS"), {})
    if isinstance(rj, dict) and rj:
        for pref, lst in rj.items():
            for r in lst:
                if not r.get("selected"):
                    continue
                nh = (r.get("nexthops") or [{}])[0]
                via = nh.get("ip") or ("conectada" if nh.get("directlyConnected") else "")
                proto = r.get("protocol", "")
                if proto == "local":
                    continue   # /32 de las propias direcciones (FRR 10): no aportan a la tabla
                por_proto[proto] = por_proto.get(proto, 0) + 1
                fila = {"prefijo": pref, "protocolo": proto, "via": via, "interfaz": nh.get("interfaceName", "")}
                rutas.append(fila)
                if pref == "0.0.0.0/0":
                    defecto = fila
    else:
        for r in js(s.get("RUTASK"), []):
            pref = r.get("dst", "")
            pref = "0.0.0.0/0" if pref == "default" else pref
            proto = {"kernel": "connected", "boot": "kernel"}.get(r.get("protocol", ""), r.get("protocol", "kernel"))
            por_proto[proto] = por_proto.get(proto, 0) + 1
            fila = {"prefijo": pref, "protocolo": proto, "via": r.get("gateway") or "conectada", "interfaz": r.get("dev", "")}
            rutas.append(fila)
            if pref == "0.0.0.0/0":
                defecto = fila
    rutas.sort(key=lambda r: (r["prefijo"] != "0.0.0.0/0", r["prefijo"]))

    # BGP
    bgp, bgp_as = [], None
    bj = js(s.get("BGP"), {})
    for af in ("ipv4Unicast",):
        a = (bj.get(af) or {}) if isinstance(bj, dict) else {}
        for ip, p in (a.get("peers") or {}).items():
            bgp.append({"vecino": ip, "as_remoto": p.get("remoteAs"), "estado": p.get("state", ""), "descripcion": p.get("desc", ""),
                        "prefijos_recibidos": p.get("pfxRcd"), "prefijos_enviados": p.get("pfxSnt"), "desde": p.get("peerUptime", "")})
        if a.get("as"):
            bgp_as = a.get("as")
    # balanceo (HAProxy)
    balanceo = None
    if s.get("HASTAT"):
        filas = [l.split(",") for l in s["HASTAT"].splitlines() if l and not l.startswith("#")]
        cab = (s["HASTAT"].splitlines()[0].lstrip("# ").split(",")) if s["HASTAT"].startswith("#") else []
        ix = {k: i for i, k in enumerate(cab)}
        g = lambda f, k: f[ix[k]] if k in ix and ix[k] < len(f) else ""
        fe, be = [], {}
        for f in filas:
            px, sv = g(f, "pxname"), g(f, "svname")
            if px == "estadisticas":
                continue
            if sv == "FRONTEND":
                fe.append({"nombre": px, "estado": g(f, "status"), "sesiones": g(f, "scur")})
            elif sv == "BACKEND":
                be.setdefault(px, {"nombre": px, "servidores": []})["estado"] = g(f, "status")
            else:
                be.setdefault(px, {"nombre": px, "servidores": []})["servidores"].append(
                    {"nombre": sv, "estado": g(f, "status"), "direccion": g(f, "addr"), "chequeo": g(f, "check_status")})
        balanceo = {"version": ha.group(1) if ha else "", "frontends": fe, "backends": list(be.values()),
                    "vip": [ip.split("/")[0] for i in ifs if i["modo"] != "gestión" for ip in i["ips"]][1:] or []}

    red = {"plataforma": so, "version": version, "detalle": detalle, "hostname": host,
           "fabricante": hv.get("faceops_fabricante") or ("VyOS" if so == "VyOS" else ""),
           "modelo": hv.get("faceops_modelo") or facts.get("ansible_net_model") or "", "serie": facts.get("ansible_net_serialnum") or "",
           "descripcion": s.get("EQUIPO") or hv.get("descripcion") or "", "rol": hv.get("descripcion") or "",
           "zona": hv.get("faceops_zona") or "", "uptime_s": uptime_s,
           "gestion": {"ip": gestion_ip, "interfaz": next((i["nombre"] for i in ifs if i["modo"] == "gestión"), "eth0"),
                       "acceso": hv.get("faceops_consola") or "ssh"},
           "interfaces": ifs, "vlans": vlans, "vecinos": lldp_vec, "subredes": subredes,
           "rutas": {"total": len(rutas), "por_protocolo": por_proto, "defecto": defecto, "lista": rutas[:60]},
           "bgp": bgp, "balanceo": balanceo,
           "recolectado": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if bgp and bgp_as:
        red["bgp_as"] = bgp_as
    json.dump(red, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
