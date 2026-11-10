#!/usr/bin/env python3
"""Exporta el cableado real de los laboratorios containerlab de la demo 4 a faceops/datos/red-laboratorio.json.

FaceOps no ve el repositorio del laboratorio (su imagen solo lleva faceops/), así que el diagrama de topología de
«Redes» lee este resumen: nodos (con su IP de gestión, direcciones por interfaz, VLAN, rutas y vecinos BGP) y enlaces
(los «links» de cada .clab.yml). Todo sale de los archivos del laboratorio: nada se infiere ni se inventa.
Lo corre faceops/version.sh en cada despliegue (antes de construir la imagen), así que las topologías nuevas entran
solas; el JSON no se versiona (.gitignore). A mano: python3 demo4-redes/herramientas/exportar-topologia-faceops.py
"""
import glob
import ipaddress
import json
import os
import re
import sys

import yaml

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LAB = os.path.join(RAIZ, "demo4-redes", "lab")
SALIDA = os.path.join(RAIZ, "faceops", "datos", "red-laboratorio.json")


def archivos():
    """Todas las topologías del laboratorio (cualquier *.clab.yml bajo demo4-redes/lab): un laboratorio nuevo entra solo."""
    return sorted(os.path.relpath(f, LAB) for f in glob.glob(os.path.join(LAB, "**", "*.clab.y*ml"), recursive=True))


def _comentarios(texto):
    """Comentario de cada nodo («    rtr:   # Router de borde…») y la primera línea del archivo, como descripción."""
    por_nodo = {m.group(1): m.group(2).strip() for m in re.finditer(r"^ {4}([\w.-]+):[^#\n]*#\s*(.+)$", texto, re.M)}
    cab = next((l.lstrip("# ").strip() for l in texto.splitlines() if l.startswith("#")), "")
    return por_nodo, cab


def _frr(ruta):
    asn, vecinos = None, []
    for l in open(ruta, encoding="utf-8"):
        m = re.match(r"\s*router bgp (\d+)", l)
        if m:
            asn = int(m.group(1))
        m = re.match(r"\s*neighbor (\d+\.\d+\.\d+\.\d+) remote-as (\d+)", l)
        if m:
            vecinos.append({"ip": m.group(1), "asn": int(m.group(2))})
    return {"asn": asn, "vecinos": vecinos} if asn else None


def _vbash(ruta, nodo):
    for l in open(ruta, encoding="utf-8"):
        m = re.match(r"set interfaces ethernet (eth\d+) (?:vif (\d+) )?address (\S+)", l.strip())
        if m:
            itf = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
            if m.group(1) != "eth0":   # eth0 es la gestión (ya va como mgmt_ip)
                nodo["interfaces"].setdefault(itf, []).append(m.group(3))
            if m.group(2):
                nodo["vlans"].setdefault(m.group(1), {"troncal": []})["troncal"].append(int(m.group(2)))


def _exec(lineas, nodo):
    for l in lineas or []:
        for m in re.finditer(r"ip addr add (\S+) dev ([\w.-]+)", l):
            if m.group(2) != "lo":
                nodo["interfaces"].setdefault(m.group(2), []).append(m.group(1))
            else:
                nodo.setdefault("loopback", []).append(m.group(1))
        for m in re.finditer(r"ip route replace (\S+) via (\d+\.\d+\.\d+\.\d+)", l):
            nodo["rutas"].append({"destino": m.group(1), "via": m.group(2)})
        for m in re.finditer(r"for v in ([\d ]+); do bridge vlan add dev (eth\d+) vid", l):
            nodo["vlans"].setdefault(m.group(2), {})["troncal"] = [int(x) for x in m.group(1).split()]
        for m in re.finditer(r"bridge vlan add dev (eth\d+) vid (\d+) pvid", l):
            nodo["vlans"].setdefault(m.group(1), {})["acceso"] = int(m.group(2))
        m = re.search(r"ip link add link (eth\d+) name \S+ type vlan id (\d+)", l)
        if m:
            nodo["vlans"].setdefault(m.group(1), {"troncal": []})["troncal"].append(int(m.group(2)))


def lab(rel):
    ruta = os.path.join(LAB, rel)
    texto = open(ruta, encoding="utf-8").read()
    y = yaml.safe_load(texto)
    com, cab = _comentarios(texto)
    topo = y.get("topology") or {}
    defe = topo.get("defaults") or {}
    base = os.path.dirname(ruta)
    nodos = []
    for nombre, n in (topo.get("nodes") or {}).items():
        n = dict(defe, **(n or {}))
        nodo = {"nombre": nombre, "kind": n.get("kind", ""), "imagen": n.get("image", ""), "mgmt_ip": n.get("mgmt-ipv4", ""),
                "descripcion": com.get(nombre, ""), "interfaces": {}, "rutas": [], "vlans": {}}
        _exec(n.get("exec"), nodo)
        for b in n.get("binds") or []:
            orig, dest = b.split(":")[0], b.split(":")[1]
            f = os.path.normpath(os.path.join(base, orig))
            if not os.path.isfile(f):   # las sucursales se despliegan con la configuración común de lab/config
                f = os.path.join(LAB, "config", os.path.basename(orig))
            if not os.path.isfile(f):
                continue
            if dest.endswith("frr.conf"):
                bgp = _frr(f)
                if bgp:
                    nodo["bgp"] = bgp
            elif f.endswith(".vbash") and "limpiar" not in f:
                _vbash(f, nodo)
        nodos.append({k: v for k, v in nodo.items() if v not in ({}, [], "")} | {"nombre": nombre})
    enlaces = []
    for ln in topo.get("links") or []:
        (a, ia), (b, ib) = (x.split(":", 1) for x in ln["endpoints"])
        enlaces.append({"a": a, "ia": ia, "b": b, "ib": ib})
    return {"lab": y.get("name", rel), "archivo": "demo4-redes/lab/" + rel, "descripcion": cab,
            "gestion": (y.get("mgmt") or {}).get("ipv4-subnet", ""), "nodos": nodos, "enlaces": enlaces}


def _valida(ip):
    try:
        ipaddress.ip_interface(ip)
        return True
    except ValueError:
        return False


def main():
    labs = []
    for r in archivos():
        try:
            labs.append(lab(r))
        except Exception as ex:  # noqa: BLE001  (una topología mal formada no deja sin diagrama a las demás)
            print(f"omitida {r}: {ex}", file=sys.stderr)
    for l in labs:   # validación mínima: cada extremo de un enlace es un nodo del laboratorio y cada IP es válida
        nombres = {n["nombre"] for n in l["nodos"]}
        l["enlaces"] = [e for e in l["enlaces"] if e["a"] in nombres and e["b"] in nombres]   # extremos «host:»/«macvlan:» no son equipos
        for n in l["nodos"]:
            for itf, ips in list(n.get("interfaces", {}).items()):
                n["interfaces"][itf] = [ip for ip in ips if _valida(ip)]
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump({"fuente": "containerlab (demo4-redes/lab) · generado por demo4-redes/herramientas/exportar-topologia-faceops.py",
                   "labs": labs}, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{SALIDA}: {len(labs)} laboratorios · {sum(len(l['nodos']) for l in labs)} nodos · {sum(len(l['enlaces']) for l in labs)} enlaces",
          file=sys.stderr)


if __name__ == "__main__":
    main()
