#!/usr/bin/env python3
"""Informe diario (T-09 + T-12) y ficha operativa (T-13) de un clúster OpenShift, en Markdown.
Entrada (stdin): {tipo: informe|ficha, cluster, fecha, api: {...respuestas...}, metricas: {nodo: texto}, ficha_previa: texto}.
Salida: JSON {md, resumen: [líneas], datos: {...}}."""
import json
import re
import sys
from datetime import datetime, timezone

d = json.load(sys.stdin)
a, cl, fecha = d.get("api") or {}, d["cluster"], d["fecha"]
it = lambda k: (a.get(k) or {}).get("items") or []  # noqa: E731


def gb(b):
    return f"{b / 1073741824:.1f} GB"


def etcd():
    # T-14: cuenta solo lo que es respaldo de etcd (nombre con «etcd» o namespaces de respaldo), no el sync de API Connect
    js = sorted([j for j in it("jobs_etcd") if "etcd" in j["metadata"]["name"] or j["metadata"]["namespace"] in ("etcd-backup", "openshift-backup")],
                key=lambda j: j["metadata"]["creationTimestamp"])
    if not js:
        return None, "sin ejecuciones: el clúster no tiene respaldo de etcd (T-01)"
    u = js[-1]
    st = u.get("status") or {}
    ok = bool(st.get("succeeded"))
    t = st.get("completionTime") or u["metadata"]["creationTimestamp"]
    h = (datetime.now(timezone.utc) - datetime.fromisoformat(t.replace("Z", "+00:00"))).total_seconds() / 3600
    bien = sum(1 for j in js[-5:] if (j.get("status") or {}).get("succeeded"))
    return ok and h < 26 and bien == len(js[-5:]), (f"{'correcto' if ok else 'FALLIDO'} · {u['metadata']['namespace']}/{u['metadata']['name']} hace {h:.0f} h"
                                                     f" · últimas ejecuciones OK: {bien} de {len(js[-5:])}")


def volumenes():
    usados, cap = {}, {}
    for txt in (d.get("metricas") or {}).values():
        for l in (txt or "").splitlines():
            m = re.match(r'kubelet_volume_stats_(used|capacity)_bytes\{.*namespace="([^"]+)".*persistentvolumeclaim="([^"]+)".*\} ([0-9.e+]+)', l)
            if m:
                (usados if m.group(1) == "used" else cap)[(m.group(2), m.group(3))] = float(m.group(4))
    filas = []
    for p in it("pvcs"):
        k = (p["metadata"]["namespace"], p["metadata"]["name"])
        pct = round(100 * usados[k] / cap[k]) if k in usados and cap.get(k) else None
        filas.append({"ns": k[0], "pvc": k[1], "fase": (p.get("status") or {}).get("phase", ""), "cap": ((p.get("spec") or {}).get("resources") or {}).get("requests", {}).get("storage", ""),
                      "sc": (p.get("spec") or {}).get("storageClassName") or "—", "uso": pct})
    pv_sueltos = [v["metadata"]["name"] for v in it("pvs") if (v.get("status") or {}).get("phase") not in ("Bound",)]
    return filas, pv_sueltos


def planes():
    return [p for p in it("planes") if not (p.get("spec") or {}).get("approved") and (p.get("status") or {}).get("phase") == "RequiresApproval"]


cv = a.get("cv") or {}
version = ((cv.get("status") or {}).get("desired") or {}).get("version", "")
if d["tipo"] == "informe":
    ok_e, txt_e = etcd()
    vols, sueltos = volumenes()
    pend = planes()
    llenos = [v for v in vols if v["uso"] is not None and v["uso"] >= 80]
    no_bound = [v for v in vols if v["fase"] != "Bound"]
    oadp = bool(it("dpa"))
    acs_mal = [x["metadata"]["name"] for x in it("securedclusters")
               if next((c.get("status") for c in (x.get("status") or {}).get("conditions") or [] if c.get("type") == "Available"), "") != "True"]
    alertas = ([] if ok_e else [f"Respaldo de etcd: {txt_e}"]) + [f"Agente de ACS instalado pero NO disponible: {n}" for n in acs_mal] + [f"Volumen {v['ns']}/{v['pvc']} al {v['uso']} %" for v in llenos] \
        + [f"Volumen {v['ns']}/{v['pvc']} en {v['fase']}" for v in no_bound] + ([f"{len(sueltos)} PV sin reclamar"] if sueltos else [])
    L = [f"# Informe diario · {cl} · {fecha[:10]}", "", f"OpenShift {version} · generado {fecha}", "",
         "## Atención" if alertas else "## Sin alertas", ""] + [f"- ⚠ {x}" for x in alertas] + ["",
         "## Respaldo de etcd", "", f"- {txt_e}", "",
         f"## Volúmenes persistentes ({len(vols)})", "", "| Namespace | PVC | Estado | Capacidad | Uso | Clase |", "|---|---|---|---|---|---|"]
    L += [f"| {v['ns']} | {v['pvc']} | {v['fase']} | {v['cap']} | {str(v['uso']) + ' %' if v['uso'] is not None else '—'} | {v['sc']} |" for v in vols] or ["| — | sin volúmenes | | | | |"]
    L += ["", f"PV sin reclamar: {', '.join(sueltos) or 'ninguno'}", "", f"## Planes de instalación en espera ({len(pend)})", ""]
    L += [f"- {p['metadata']['namespace']} · {(p['spec'].get('clusterServiceVersionNames') or [''])[0]}" for p in pend] or ["- ninguno"]
    porsc = {}
    for v in vols:
        porsc[v["sc"]] = porsc.get(v["sc"], 0) + 1
    L += ["", "## Respaldo de datos de aplicación (T-12)", "",
          f"- OADP: {'configurado (' + str(len(it('dpa'))) + ' DataProtectionApplication)' if oadp else 'no instalado: los datos de los volúmenes no se respaldan'}",
          f"- Volúmenes por clase de almacenamiento: {', '.join(f'{k}: {n}' for k, n in sorted(porsc.items())) or '—'}"]
    resumen = [f"{cl} · {fecha[:16]} · {'sin alertas' if not alertas else str(len(alertas)) + ' alerta(s)'}", f"etcd: {txt_e}",
               f"volúmenes: {len(vols)} ({len(llenos)} sobre 80 %, {len(no_bound)} sin Bound, {len(sueltos)} PV sueltos)",
               f"planes en espera: {len(pend)}", f"OADP: {'sí' if oadp else 'no'}"] + [f"⚠ {x}" for x in alertas[:8]]
    print(json.dumps({"md": "\n".join(L) + "\n", "resumen": resumen, "datos": {"alertas": alertas, "etcd_ok": ok_e, "volumenes": len(vols),
                                                                              "llenos": len(llenos), "planes": len(pend), "oadp": oadp}}, ensure_ascii=False))
elif d["tipo"] == "uso":   # T-11: ¿el ambiente se usa?
    SIS = re.compile(r"^(openshift|kube-|default$|stackrox$|rhacs-operator$|etcd-backup$|openshift-backup$)")
    proys = [p["metadata"]["name"] for p in it("proyectos") if not SIS.match(p["metadata"]["name"])]
    pods = [p for p in it("pods") if not SIS.match(p["metadata"]["namespace"])]
    rutas = [r for r in it("rutas") if not SIS.match(r["metadata"]["namespace"])]
    deps = sorted([x for x in it("deployments") if not SIS.match(x["metadata"]["namespace"])], key=lambda x: x["metadata"]["creationTimestamp"])
    ult = deps[-1]["metadata"]["creationTimestamp"][:10] if deps else "—"
    L = [f"# Uso del clúster · {cl} · {fecha[:10]}", "", f"OpenShift {version} · leído {fecha}", "",
         "| Dato | Valor |", "|---|---|", f"| Proyectos de aplicaciones | {len(proys)} |", f"| Pods fuera de namespaces de sistema | {len(pods)} |",
         f"| Rutas publicadas | {len(rutas)} |", f"| Último despliegue de aplicación | {ult} |", "",
         "## Últimos despliegues", "", "| Proyecto | Despliegue | Creado |", "|---|---|---|"]
    L += [f"| {x['metadata']['namespace']} | {x['metadata']['name']} | {x['metadata']['creationTimestamp'][:10]} |" for x in deps[-20:][::-1]] or ["| — | — | — |"]
    L += ["", "## Rutas que todavía se publican", ""] + [f"- {r['metadata']['namespace']} · {(r.get('spec') or {}).get('host', '')}" for r in rutas[:60]]
    L += ["", "Antes de liberar: avisar con este listado, esperar el plazo acordado con el cliente y tomar un respaldo final de etcd fuera del clúster."]
    print(json.dumps({"md": "\n".join(L) + "\n", "resumen": [f"{cl} · {len(proys)} proyectos · {len(pods)} pods de aplicaciones · {len(rutas)} rutas · último despliegue {ult}"],
                      "datos": {"proyectos": len(proys), "pods": len(pods), "rutas": len(rutas), "ultimo_despliegue": ult}}, ensure_ascii=False))
else:   # ficha operativa
    sp = cv.get("spec") or {}
    infra = ((a.get("infra") or {}).get("status") or {})
    hist = ((cv.get("status") or {}).get("history") or [])
    instalado = hist[-1].get("startedTime", "")[:10] if hist else ""
    nodos = []
    for n in it("nodos"):
        roles = [k.split("/")[-1] for k in n["metadata"].get("labels", {}) if k.startswith("node-role.kubernetes.io/")]
        c = n["status"].get("capacity") or {}
        mem_gb = round(int(re.sub(r"\D", "", c.get("memory", "0")) or 0) / 1048576)
        nodos.append(f"| {n['metadata']['name']} | {', '.join(roles)} | {c.get('cpu', '')} | {mem_gb} GB | {n['status'].get('nodeInfo', {}).get('osImage', '')} |")
    subs = it("subs")
    ops = [f"| {s['spec'].get('name', s['metadata']['name'])} | {s['metadata']['namespace']} | {s['spec'].get('channel', '—')} | {s['spec'].get('installPlanApproval', 'SIN DECLARAR')} | {(s.get('status') or {}).get('installedCSV', '')} |" for s in subs]
    nombres = {s["spec"].get("name", "") for s in subs}
    integ = [f"- Registro de imágenes interno: {((a.get('registro') or {}).get('spec') or {}).get('managementState', '—')}",
             f"- GitOps (Argo CD): {'sí' if any('gitops' in n for n in nombres) else 'no'} · aplicaciones: {len(it('argo'))}",
             f"- Seguridad (ACS): {'sí' if it('securedclusters') else 'no'}",
             f"- Gestión multiclúster (ACM): {'sí' if any('advanced-cluster-management' in n or n == 'multicluster-engine' for n in nombres) else 'no'}",
             f"- Respaldo de etcd: {etcd()[1]}", f"- OADP: {'sí' if it('dpa') else 'no'}",
             f"- Clases de almacenamiento: {', '.join(x['metadata']['name'] for x in it('sc')) or '—'}"]
    auto = ["## Identidad", "", f"- Cluster ID: `{sp.get('clusterID', '')}`", f"- Infraestructura: `{infra.get('infrastructureName', '')}` · plataforma {infra.get('platform', (infra.get('platformStatus') or {}).get('type', ''))}",
            f"- API: {infra.get('apiServerURL', '')}", f"- Instalado: {instalado or '—'}", f"- Versión: {version} · canal {sp.get('channel', '—')}", "",
            f"## Topología ({len(nodos)} nodos)", "", "| Nodo | Roles | CPU | Memoria | Sistema |", "|---|---|---|---|---|"] + nodos + ["",
            f"## Operadores ({len(ops)})", "", "| Operador | Namespace | Canal | Aprobación | Versión |", "|---|---|---|---|---|"] + ops + ["",
            "## Integraciones", ""] + integ
    # Las secciones humanas (procedimientos y contactos) se conservan de la ficha anterior
    previa = d.get("ficha_previa") or ""
    m = re.search(r"(<!-- faceops:manual -->.*)", previa, re.S)
    manual = m.group(1) if m else ("<!-- faceops:manual -->\n## Procedimientos\n\n- Cómo se actualiza: _(completar)_\n- Cómo se respalda: «OpenShift · Respaldo de etcd» (T-01)"
                                    "\n- Cómo se restaura: _(completar: procedimiento de Red Hat probado y tiempo de recuperación medido)_\n- A quién se escala: _(completar)_\n\n"
                                    "## Contactos\n\n- Negocio: _(nombre)_\n- Plataforma: _(nombre)_\n")
    pend_manual = manual.count("_(completar") + manual.count("_(nombre)_")
    md = "\n".join([f"# Ficha operativa · {cl}", "", f"_Las secciones de identidad, topología, operadores e integraciones se generan desde el clúster ({fecha[:16]}). "
                    "Procedimientos y contactos se completan a mano y FaceOps los conserva._", "", "<!-- faceops:auto -->"] + auto + ["", manual.strip(), ""])
    print(json.dumps({"md": md, "resumen": [f"{cl} · OpenShift {version} · {len(nodos)} nodo(s) · {len(ops)} operador(es)",
                                            f"secciones manuales pendientes: {pend_manual}"], "datos": {"pendientes_manual": pend_manual}}, ensure_ascii=False))
