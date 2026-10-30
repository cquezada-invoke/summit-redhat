"""Completitud de seguridad para el panel: políticas al día, CVE cubiertos vs pendientes y cobertura de controles
(respaldo en Git, credencial en la bóveda, monitoreo, revisión de CVE) por equipo."""
from collections import Counter

ESTADOS_POL = [("al_dia", "al día", "s-exi"), ("desactualizada", "desactualizada", "s-apr"), ("no_cumple", "no cumple", "s-fal"),
               ("fallida", "falló", "s-fal"), ("pendiente", "sin aplicar", "s-otr")]
SEVS = [("crítica", "s-fal"), ("importante", "s-apr"), ("moderada", "s-man"), ("baja", "s-otr")]


def _pct(a, b):
    return round(100 * a / b) if b else 0


def completitud(cliente_id, eqs):
    from .boveda import maquinas_con_credencial
    from .cves import por_equipo
    from .politicas_seg import tablero
    pols, por_eq = tablero(cliente_id, eqs)
    total_pol = Counter()
    por_cat = {}
    for p in pols:
        for k, n in p["cuenta"].items():
            total_pol[k] += n
            c = por_cat.setdefault(p["categoria"], Counter())
            c[k] += n
    n_pol = sum(total_pol.values())
    cats = sorted(({"categoria": c, "total": sum(v.values()), "al_dia": v["al_dia"], "pct": _pct(v["al_dia"], sum(v.values()))}
                   for c, v in por_cat.items() if sum(v.values())), key=lambda x: x["pct"])
    cv = por_equipo(cliente_id, eqs)
    revisados = [e for e in eqs if cv[e["id"]]["revisado"]]
    pend = sum(cv[e["id"]]["pendientes"] for e in eqs)
    cub = sum(cv[e["id"]]["cubiertos"] or 0 for e in eqs)
    from .bd import q
    sev = Counter(r["severidad"] for r in q("SELECT DISTINCT equipo_id, cve, severidad FROM cves_equipo WHERE cliente_id=? AND estado='pendiente'",
                                            cliente_id) if r["equipo_id"] in {e["id"] for e in eqs})
    # cobertura de controles: solo servidores Linux (donde aplican el respaldo, la bóveda y el agente de monitoreo)
    linux = [e for e in eqs if (e.get("categoria") or "servidor") == "servidor" and "windows" not in (e.get("so") or "").lower()
             and "kvm" not in e["skills"]]
    con_vault = maquinas_con_credencial([e["nombre"] for e in linux])
    pol_ok = {eid for eid, ps in por_eq.items() if ps and all(x["estado"] == "al_dia" for x in ps)}
    controles = [
        ("Respaldo continuo en Git", sum(1 for e in linux if "respaldo-git" in e["skills"])),
        ("Credencial en la bóveda", sum(1 for e in linux if e["nombre"] in con_vault)),
        ("Monitoreo (node_exporter)", sum(1 for e in linux if "node-exporter" in e["skills"])),
        ("CVE revisados", sum(1 for e in linux if cv[e["id"]]["revisado"])),
        ("Sin CVE críticos pendientes", sum(1 for e in linux if cv[e["id"]]["revisado"] and not cv[e["id"]]["criticos"])),
        ("Todas sus políticas al día", sum(1 for e in linux if e["id"] in pol_ok)),
    ]
    controles = [{"nombre": n, "n": c, "total": len(linux), "pct": _pct(c, len(linux))} for n, c in controles]
    indice = round(sum(x["pct"] for x in controles) / len(controles)) if controles else 0
    peores = sorted(({"e": e, "faltan": [n for n, ok in (
        ("respaldo", "respaldo-git" in e["skills"]), ("bóveda", e["nombre"] in con_vault), ("monitoreo", "node-exporter" in e["skills"]),
        ("CVE", cv[e["id"]]["revisado"]), ("políticas", e["id"] in pol_ok)) if not ok], "criticos": cv[e["id"]]["criticos"]} for e in linux),
        key=lambda x: (-len(x["faltan"]), -x["criticos"], x["e"]["nombre"]))[:5]
    return {"indice": indice, "pol": {"total": n_pol, "cuenta": dict(total_pol), "pct": _pct(total_pol["al_dia"], n_pol), "categorias": cats},
            "cve": {"pendientes": pend, "cubiertos": cub, "pct": _pct(cub, cub + pend), "revisados": len(revisados), "equipos": len(eqs),
                    "sev": dict(sev)},
            "controles": controles, "linux": len(linux), "peores": [p for p in peores if p["faltan"]]}
