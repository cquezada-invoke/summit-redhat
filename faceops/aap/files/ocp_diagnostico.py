#!/usr/bin/env python3
"""Anexo A de la guía de regularización (versión del 30-10-2026): estado de un clúster frente a cada tarea, solo lectura.
Entrada (stdin): {api: {consulta: respuesta JSON}, estados: {consulta: código HTTP}}. Salida: JSON {tareas: [...], datos: {...}}.
Reglas de la guía que este diagnóstico respeta:
  · T-01/T-14: un CronJob cuenta como respaldo de etcd solo si su NOMBRE contiene «etcd» (el sync de API Connect no cuenta),
    y se informa cuántas de las últimas cinco ejecuciones terminaron completas, no solo que el CronJob exista
  · T-02: se mira la condición Available del SecuredCluster, no que el objeto exista
  · T-05: una suscripción sin installPlanApproval (o vacío) cuenta como Automatic no declarado"""
import json
import re
import sys

d = json.load(sys.stdin)
a, st = d.get("api") or {}, d.get("estados") or {}
it = lambda k: (a.get(k) or {}).get("items") or []  # noqa: E731
SISTEMA = re.compile(r"^(openshift|kube-|default$|stackrox$|rhacs-operator$|etcd-backup$|openshift-backup$)")


def etcd():
    cjs = [c for c in it("cronjobs") if "etcd" in c["metadata"]["name"]]
    jobs = [j for j in it("jobs") if "etcd" in j["metadata"]["name"] or j["metadata"]["namespace"] in ("openshift-backup", "etcd-backup")]
    jobs.sort(key=lambda j: j["metadata"]["creationTimestamp"])
    ult = jobs[-5:]
    ok = sum(1 for j in ult if (j.get("status") or {}).get("succeeded"))
    mal = sum(1 for j in ult if (j.get("status") or {}).get("failed") and not (j.get("status") or {}).get("succeeded"))
    falsos = [f"{c['metadata']['namespace']}/{c['metadata']['name']}" for c in it("cronjobs")
              if "backup" in c["metadata"]["name"].lower() and "etcd" not in c["metadata"]["name"]]
    return cjs, ult, ok, mal, falsos


cjs, ult, ok_n, mal_n, falsos = etcd()
v = (a.get("version") or {})
ver = ((v.get("status") or {}).get("desired") or {}).get("version", "?")
apps = [p["metadata"]["name"] for p in it("proyectos") if not SISTEMA.match(p["metadata"]["name"])]
scs = it("securedclusters")
sc_estado = [(s["metadata"]["name"], next((c.get("status") for c in (s.get("status") or {}).get("conditions") or [] if c.get("type") == "Available"), "?"),
              (s.get("status") or {}).get("sensorVersion") or (s.get("status") or {}).get("deployedVersion") or "") for s in scs]
csv_comp = sorted({c["metadata"]["name"] for c in it("csv") if "compliance" in c["metadata"]["name"]})
ip_esp = [p for p in it("installplans") if (p.get("status") or {}).get("phase") == "RequiresApproval"]
subs = it("subs")
sub_sin = [f"{s['metadata']['namespace']}/{s['metadata']['name']}" for s in subs if not (s.get("spec") or {}).get("installPlanApproval")]
sub_auto = [f"{s['metadata']['namespace']}/{s['metadata']['name']}" for s in subs if (s.get("spec") or {}).get("installPlanApproval") == "Automatic"]
upg = next((c for c in (v.get("status") or {}).get("conditions") or [] if c.get("type") == "Upgradeable"), None)
upd = (v.get("status") or {}).get("availableUpdates") or []
retiradas = sorted({x["metadata"]["name"] for x in it("apirequestcounts")
                    if re.search(r"v1beta3\.flowcontrol", x["metadata"]["name"]) and ((x.get("status") or {}).get("requestCount") or 0) > 0})
ns_lr = {x["metadata"]["namespace"] for x in it("limitranges")}
ns_q = {x["metadata"]["namespace"] for x in it("quotas")}
pvcs = [p for p in it("pvcs") if not SISTEMA.match(p["metadata"]["namespace"])]
oadp = st.get("dpa") == 200 and bool(it("dpa"))
ns_nombres = {n["metadata"]["name"] for n in it("namespaces")}
fuera_std = sorted(ns_nombres & {"ingress-node-firewall-operators"})
fw_auto = [s for s in sub_auto if "firewall" in s]
rutas = it("rutas")
deps = [x for x in it("deployments") if not SISTEMA.match(x["metadata"]["namespace"])]
ult_dep = max((x["metadata"]["creationTimestamp"] for x in deps), default="")
pods_app = [p for p in it("pods") if not SISTEMA.match(p["metadata"]["namespace"])]
nodos = it("nodos")
cpu = sum(int(re.sub(r"\D", "", str((n["status"].get("capacity") or {}).get("cpu", "0"))) or 0) for n in nodos)
mem_gib = sum(int(re.sub(r"\D", "", (n["status"].get("capacity") or {}).get("memory", "0")) or 0) for n in nodos) / 1048576


def gb(s):
    m = re.match(r"([\d.]+)\s*([KMGTP]i?)?", str(s or "0"))
    if not m:
        return 0.0
    f = {"Ki": 1 / 1048576, "Mi": 1 / 1024, "Gi": 1, "Ti": 1024, "Pi": 1048576, "K": 1e3 / 2**30, "M": 1e6 / 2**30, "G": 1e9 / 2**30, "T": 1e12 / 2**30}
    return float(m.group(1)) * f.get(m.group(2) or "", 1 / 2**30)


cap_pvc = sum(gb(((p.get("spec") or {}).get("resources") or {}).get("requests", {}).get("storage")) for p in pvcs)
sc_mal = [n for n, av, _ in sc_estado if av != "True"]
tareas = [
    {"t": "T-01", "n": "Respaldo de etcd", "ok": bool(cjs) and len(ult) > 0 and mal_n == 0 and ok_n == len(ult),
     "d": (f"CronJob {', '.join(c['metadata']['namespace'] + '/' + c['metadata']['name'] for c in cjs)} ({cjs[0]['spec'].get('schedule', '')})"
           f" · últimas ejecuciones OK: {ok_n} de {len(ult)}" if cjs else "SIN RESPALDO: no hay un CronJob con «etcd» en el nombre")
          + (f" · no cuentan como respaldo de etcd: {', '.join(falsos[:3])}" if falsos else "")},
    {"t": "T-02", "n": "Agente de seguridad (ACS)", "ok": bool(scs) and not sc_mal,
     "d": (" · ".join(f"{n} {ver_ or ''} Available={av}" for n, av, ver_ in sc_estado) if scs else
           ("ACS no instalado (sin el recurso SecuredCluster)" if st.get("securedclusters") != 200 else "sin SecuredCluster"))
          + (" · instalado pero NO disponible: no está reportando a Central" if sc_mal else "")},
    {"t": "T-03", "n": "Compliance Operator", "ok": bool(csv_comp), "d": ", ".join(csv_comp) or "NO INSTALADO"},
    {"t": "T-04", "n": "Planes de instalación en espera", "ok": not ip_esp,
     "d": (f"{len(ip_esp)} en espera de aprobación: " + ", ".join(sorted({p['metadata']['namespace'] for p in ip_esp}))) if ip_esp else "ninguno"},
    {"t": "T-05", "n": "Suscripciones sin estrategia declarada", "ok": not sub_sin,
     "d": (", ".join(sub_sin) if sub_sin else "todas declaradas") + (f" · Automatic declaradas: {len(sub_auto)}" if sub_auto else "")},
    {"t": "T-06", "n": "Versión de OpenShift", "ok": not upg or upg.get("status") == "True",
     "d": f"{ver} · canal {(v.get('spec') or {}).get('channel', 'sin canal')} · {len(upd)} actualización(es) disponible(s)"
          + (f" · no actualizable: {upg.get('message', '')[:160]}" if upg and upg.get("status") != "True" else "")
          + (f" · APIs retiradas en uso: {', '.join(retiradas)}" if retiradas else "")},
    {"t": "T-07", "n": "LimitRange y ResourceQuota", "ok": all(x in ns_lr for x in apps) and all(x in ns_q for x in apps),
     "d": f"{len(apps)} namespaces de aplicaciones · sin LimitRange: {sum(1 for x in apps if x not in ns_lr)} · sin ResourceQuota: {sum(1 for x in apps if x not in ns_q)}"},
    {"t": "T-08", "n": "Namespaces fuera de estándar", "ok": not fuera_std and not fw_auto,
     "d": (f"fuera de estándar: {', '.join(fuera_std)}" if fuera_std else "namespaces estándar")
          + (f" · firewall de nodo con aprobación Automatic: {', '.join(fw_auto)}" if fw_auto else "")},
    {"t": "T-11", "n": "Uso del clúster", "ok": True,
     "d": f"{len(apps)} proyectos · {len(pods_app)} pods fuera de sistema · {len(rutas)} rutas · último despliegue {ult_dep[:10] or '—'}"},
    {"t": "T-12", "n": "Respaldo de datos de aplicación", "ok": not pvcs or oadp,
     "d": f"{len(pvcs)} volúmenes de aplicaciones ({cap_pvc:.0f} GiB) · " + ("OADP configurado" if oadp else "sin OADP (los volúmenes no se respaldan)")},
    {"t": "Capacidad", "n": "Capacidad instalada", "ok": True, "d": f"{len(nodos)} nodos · {cpu} vCPU · {mem_gib:.0f} GiB"},
]
print(json.dumps({"tareas": tareas, "datos": {"version": ver, "etcd_ok": ok_n, "etcd_ultimas": len(ult), "planes": len(ip_esp),
                                              "sin_declarar": len(sub_sin), "acs_no_disponible": sc_mal, "pvc": len(pvcs)}}, ensure_ascii=False))
