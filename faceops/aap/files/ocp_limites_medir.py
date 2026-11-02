#!/usr/bin/env python3
"""Reserva declarada frente a consumo real (T-07) y propuesta de LimitRange / ResourceQuota por namespace de aplicación.
Entrada (stdin): {api: {pods, nodos, mp, mn, lr, rq, proy}, previas: [mediciones anteriores], ahora}. Salida: JSON."""
import json
import re
import statistics
import sys

SISTEMA = re.compile(r"^(openshift|kube-|default$|hostpath|stackrox|rhacs|open-cluster|multicluster|local-cluster|assisted-installer)")


def cpu(v):
    v = str(v or "0")
    if v.endswith("n"):
        return float(v[:-1]) / 1e9
    if v.endswith("u"):
        return float(v[:-1]) / 1e6
    if v.endswith("m"):
        return float(v[:-1]) / 1000
    return float(v or 0)


def mem(v):   # en MiB
    v = str(v or "0")
    for s, f in (("Ki", 1 / 1024), ("Mi", 1), ("Gi", 1024), ("Ti", 1048576), ("k", 1 / 1048.576), ("M", 1e6 / 1048576), ("G", 1e9 / 1048576)):
        if v.endswith(s):
            return float(v[:-len(s)]) * f
    return float(v or 0) / 1048576


def red(x, paso):
    return round(max(paso, round(x / paso + 0.4999) * paso), 3)


d = json.load(sys.stdin)
a = d["api"]
pods = [p for p in (a.get("pods") or {}).get("items") or [] if (p.get("status") or {}).get("phase") == "Running"]
uso_pod = {}
for m in (a.get("mp") or {}).get("items") or []:
    uso_pod[(m["metadata"]["namespace"], m["metadata"]["name"])] = (
        sum(cpu(c["usage"]["cpu"]) for c in m.get("containers") or []), sum(mem(c["usage"]["memory"]) for c in m.get("containers") or []))
nodos = []
uso_nodo = {m["metadata"]["name"]: (cpu(m["usage"]["cpu"]), mem(m["usage"]["memory"])) for m in (a.get("mn") or {}).get("items") or []}
for n in (a.get("nodos") or {}).get("items") or []:
    nom = n["metadata"]["name"]
    al = n["status"].get("allocatable") or {}
    rc = sum(cpu(((c.get("resources") or {}).get("requests") or {}).get("cpu")) for p in pods if p["spec"].get("nodeName") == nom for c in p["spec"]["containers"])
    rm = sum(mem(((c.get("resources") or {}).get("requests") or {}).get("memory")) for p in pods if p["spec"].get("nodeName") == nom for c in p["spec"]["containers"])
    uc, um = uso_nodo.get(nom, (0, 0))
    ac, am = cpu(al.get("cpu")), mem(al.get("memory"))
    nodos.append({"nodo": nom, "cpu_reservada_pct": round(100 * rc / ac) if ac else 0, "cpu_usada_pct": round(100 * uc / ac) if ac else 0,
                  "mem_reservada_pct": round(100 * rm / am) if am else 0, "mem_usada_pct": round(100 * um / am) if am else 0,
                  "resumen": f"nodo {nom}: CPU reservada {round(100 * rc / ac) if ac else 0} % · usada {round(100 * uc / ac) if ac else 0} % · "
                             f"memoria reservada {round(100 * rm / am) if am else 0} % · usada {round(100 * um / am) if am else 0} %"})
nss = {}
lr_ns = {x["metadata"]["namespace"] for x in (a.get("lr") or {}).get("items") or []}
for p in pods:
    ns = p["metadata"]["namespace"]
    if SISTEMA.match(ns):
        continue
    x = nss.setdefault(ns, {"namespace": ns, "pods": 0, "contenedores": 0, "req_cpu": 0.0, "req_mem": 0.0, "lim_cpu": 0.0, "lim_mem": 0.0, "sin_requests": 0,
                            "uso_cpu": 0.0, "uso_mem": 0.0, "uso_contenedor": []})
    x["pods"] += 1
    uc, um = uso_pod.get((ns, p["metadata"]["name"]), (0, 0))
    x["uso_cpu"] += uc
    x["uso_mem"] += um
    nc = len(p["spec"]["containers"])
    for c in p["spec"]["containers"]:
        rq = (c.get("resources") or {}).get("requests") or {}
        x["contenedores"] += 1
        x["req_cpu"] += cpu(rq.get("cpu"))
        x["req_mem"] += mem(rq.get("memory"))
        lm = (c.get("resources") or {}).get("limits") or {}
        x["lim_cpu"] += cpu(lm.get("cpu")) if lm.get("cpu") else 0
        x["lim_mem"] += mem(lm.get("memory")) if lm.get("memory") else 0
        x["sin_requests"] += 0 if rq.get("cpu") else 1
        x["uso_contenedor"].append([uc / nc, um / nc])
for x in nss.values():
    x["limitrange"] = x["namespace"] in lr_ns
    x["resumen"] = (f"{x['namespace']}: {x['pods']} pods · reserva {x['req_cpu']:.2f} cores / {x['req_mem'] / 1024:.2f} GB · "
                    f"uso {x['uso_cpu']:.2f} cores / {x['uso_mem'] / 1024:.2f} GB · {x['sin_requests']} contenedor(es) sin requests"
                    f"{' · con LimitRange' if x['limitrange'] else ''}")
medicion = {"fecha": d["ahora"], "nodos": nodos, "namespaces": sorted(nss.values(), key=lambda x: x["namespace"])}

# propuesta: con todas las mediciones (anteriores + esta), el percentil alto del consumo por contenedor
todas = (d.get("previas") or []) + [medicion]
propuesta = {}
for ns in nss:
    cont = [u for m in todas for x in m.get("namespaces") or [] if x["namespace"] == ns for u in x.get("uso_contenedor") or []]
    picos_ns = [(x["uso_cpu"], x["uso_mem"]) for m in todas for x in m.get("namespaces") or [] if x["namespace"] == ns]
    if not cont:
        continue
    c90 = statistics.quantiles([c for c, _ in cont], n=10)[-1] if len(cont) > 1 else cont[0][0]
    m90 = statistics.quantiles([m for _, m in cont], n=10)[-1] if len(cont) > 1 else cont[0][1]
    cr, mr = red(c90 * 1.2, 0.05), red(m90 * 1.2, 64)
    pc, pm = max(c for c, _ in picos_ns), max(m for _, m in picos_ns)
    propuesta[ns] = {"cpu_req": f"{int(cr * 1000)}m", "mem_req": f"{int(mr)}Mi", "cpu_lim": f"{int(cr * 4000)}m", "mem_lim": f"{int(mr * 2)}Mi",
                     "cuota_cpu": f"{red(max(pc * 2, nss[ns]['req_cpu'] * 1.2, cr * nss[ns]['contenedores'] * 1.3), 0.5):g}",
                     "cuota_mem": f"{int(red(max(pm * 2, nss[ns]['req_mem'] * 1.2, mr * nss[ns]['contenedores'] * 1.3), 512))}Mi",
                     # la cuota de límites nunca queda bajo lo que ya declaran las cargas (si no, un reinicio las rechazaría)
                     "cuota_cpu_lim": f"{red(max(cr * 4 * nss[ns]['contenedores'] * 1.3, nss[ns]['lim_cpu'] * 1.2), 1):g}",
                     "cuota_mem_lim": f"{int(red(max(mr * 2 * nss[ns]['contenedores'] * 1.3, nss[ns]['lim_mem'] * 1.2), 1024))}Mi",
                     "mediciones": len(picos_ns)}
    p = propuesta[ns]
    p["resumen"] = (f"por contenedor {p['cpu_req']}/{p['mem_req']} (límite {p['cpu_lim']}/{p['mem_lim']}) · cuota {p['cuota_cpu']} cores / {p['cuota_mem']}"
                    f" · base: {p['mediciones']} medición(es)")
print(json.dumps({"medicion": medicion, "propuesta": propuesta, "n_mediciones": len(todas)}, ensure_ascii=False))
