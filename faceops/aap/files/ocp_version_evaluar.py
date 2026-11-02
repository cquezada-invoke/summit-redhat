#!/usr/bin/env python3
"""Evalúa un clúster OpenShift antes de actualizarlo (T-06). Entrada: JSON en stdin con las respuestas de la API
(cv, co, mcp, arc, csv, acks, gates, jobs_etcd). Salida: JSON con el diagnóstico."""
import json
import re
import sys
from datetime import datetime, timezone

d = json.load(sys.stdin)
cv = d.get("cv") or {}
st, sp = cv.get("status") or {}, cv.get("spec") or {}
version = (st.get("desired") or {}).get("version", "")
m = re.match(r"(\d+)\.(\d+)", version)
mayor, menor = (int(m.group(1)), int(m.group(2))) if m else (4, 0)
siguiente = f"{mayor}.{menor + 1}"

cond = {c.get("type"): c for c in st.get("conditions") or []}
upg = cond.get("Upgradeable") or {}
actualizable = upg.get("status", "True") == "True"

# APIs que se retiran en la versión de Kubernetes de la siguiente menor, y quién las usa
en_uso = []
for it in (d.get("arc") or {}).get("items") or []:
    s = it.get("status") or {}
    if not s.get("removedInRelease"):
        continue
    usuarios = set()
    for h in s.get("last24h") or []:
        for n in h.get("byNode") or []:
            for u in n.get("byUser") or []:
                usuarios.add(u.get("username", ""))
    usuarios = {u for u in usuarios if u and not u.startswith("system:serviceaccount:openshift-") and u != "system:kube-controller-manager"}
    if s.get("requestCount", 0) or usuarios:
        if usuarios:
            en_uso.append([it["metadata"]["name"], ", ".join(sorted(usuarios))[:200]])

gates = ((d.get("gates") or {}).get("data") or {})
acks = ((d.get("acks") or {}).get("data") or {})
acks_pendientes = [k for k in gates if acks.get(k) != "true"]   # admin-gates trae solo los avisos de la versión actual

no_sanos = []
for co in (d.get("co") or {}).get("items") or []:
    c = {x.get("type"): x.get("status") for x in (co.get("status") or {}).get("conditions") or []}
    if c.get("Available") != "True" or c.get("Degraded") == "True" or c.get("Progressing") == "True":
        no_sanos.append(co["metadata"]["name"])

mcp = []
for p in (d.get("mcp") or {}).get("items") or []:
    s = p.get("status") or {}
    mcp.append(f"{p['metadata']['name']} {s.get('updatedMachineCount', 0)}/{s.get('machineCount', 0)}{' degradado' if s.get('degradedMachineCount') else ''}")

olm_bloquean = []
for c in (d.get("csv") or {}).get("items") or []:
    mx = ((c.get("metadata") or {}).get("annotations") or {}).get("olm.maxOpenShiftVersion")
    if not mx:
        try:
            props = json.loads(((c.get("metadata") or {}).get("annotations") or {}).get("olm.properties", "[]"))
            mx = next((p.get("value") for p in props if p.get("type") == "olm.maxOpenShiftVersion"), None)
        except ValueError:
            mx = None
    if mx:
        mm = re.match(r"(\d+)\.(\d+)", str(mx))
        if mm and (int(mm.group(1)), int(mm.group(2))) < (mayor, menor + 1):
            olm_bloquean.append(f"{c['metadata']['name']} (máx {mx})")
olm_bloquean = sorted(set(olm_bloquean))

etcd_horas = None
ok = [j for j in (d.get("jobs_etcd") or {}).get("items") or [] if (j.get("status") or {}).get("succeeded")]
if ok:
    t = max((j.get("status") or {}).get("completionTime") or j["metadata"]["creationTimestamp"] for j in ok)
    etcd_horas = round((datetime.now(timezone.utc) - datetime.fromisoformat(t.replace("Z", "+00:00"))).total_seconds() / 3600, 1)

# ciclo de vida aproximado: las tres menores más recientes que se conocen por el canal
disponibles = sorted({u.get("version") for u in st.get("availableUpdates") or [] if u.get("version")},
                     key=lambda v: [int(x) for x in v.split(".")])
print(json.dumps({
    "version": version, "canal": sp.get("channel", ""), "disponibles": disponibles, "siguiente_menor": siguiente,
    "actualizable": actualizable, "motivo_no_actualizable": (upg.get("message") or "")[:300],
    "apis_retiradas_en_uso": en_uso[:10], "acks_pendientes": acks_pendientes,
    "operadores_no_sanos": no_sanos, "mcp": mcp, "olm_bloquean": olm_bloquean[:20], "etcd_horas": etcd_horas,
    "ciclo": "actualización en curso" if (cond.get("Progressing") or {}).get("status") == "True" else "estable",
}, ensure_ascii=False))
