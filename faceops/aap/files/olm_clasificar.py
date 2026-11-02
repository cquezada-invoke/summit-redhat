#!/usr/bin/env python3
"""Clasifica los planes de instalación en espera (T-04): parche de Red Hat, versión menor de Red Hat, IBM/Cloud Pak u otro.
Entrada: JSON en stdin {subs: [...], planes: [...]}. Salida: JSON [{n, ns, actual, destino, fuente, categoria}]."""
import json
import re
import sys

d = json.load(sys.stdin)
subs = d.get("subs") or []


def ver(csv):
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", csv or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


out = []
for ip in d.get("planes") or []:
    if (ip.get("spec") or {}).get("approved") or (ip.get("status") or {}).get("phase") != "RequiresApproval":
        continue
    ns, n = ip["metadata"]["namespace"], ip["metadata"]["name"]
    destino = ((ip.get("spec") or {}).get("clusterServiceVersionNames") or [""])[0]
    del_ns = [s for s in subs if s["metadata"]["namespace"] == ns]
    sub = next((s for s in del_ns if ((s.get("status") or {}).get("installPlanRef") or {}).get("name") == n), None) or \
        next((s for s in del_ns if (s.get("spec") or {}).get("name", "") and destino.startswith(s["spec"]["name"])), None) or (del_ns[0] if del_ns else {})
    actual = (sub.get("status") or {}).get("installedCSV") or ""
    fuente = (sub.get("spec") or {}).get("source") or ""
    if "ibm" in fuente.lower() or "ibm" in destino.lower() or "cp4" in ns:
        cat = "ibm"
    elif fuente == "redhat-operators":
        cat = "parche" if actual and ver(actual) and ver(actual) == ver(destino) else ("instalacion" if not actual else "menor")
    else:
        cat = "otro"
    out.append({"n": n, "ns": ns, "actual": actual, "destino": destino, "fuente": fuente, "categoria": cat})
print(json.dumps(out))
