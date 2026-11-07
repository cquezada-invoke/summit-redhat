"""Monitoreo · Configurar alertas del equipo: arma lo que Prometheus necesita para vigilar un equipo con las alertas elegidas.

Uso: alertas_objeto.py <catalogo.yml> <prometheus.yml actual> <pedido.json>
pedido: {"equipo", "ip", "tipo": linux|windows|storage|red, "alertas": [{"id", "aviso"?, "critico"?, "severidad"?}], "retirar": bool}
Salida (JSON): {"prometheus": yaml o "" si no cambia, "reglas": yaml o "", "objetivo": yaml o "", "objetivo_archivo", "resumen": [...]}"""
import json
import re
import sys

import yaml

RUTA = "/etc/prometheus/faceops"
SEV = {"aviso": "warning", "critico": "critical"}


def _num(v):
    f = float(v)
    return int(f) if f == int(f) else f


def preparar_prometheus(cfg):
    """Asegura en prometheus.yml las reglas y los dos trabajos de FaceOps (equipos con exportador y sondeo TCP vía blackbox)."""
    cambio = False
    rf = cfg.setdefault("rule_files", []) or []
    if f"{RUTA}/reglas/*.yml" not in rf:
        rf.append(f"{RUTA}/reglas/*.yml")
        cfg["rule_files"] = rf
        cambio = True
    sc = cfg.setdefault("scrape_configs", []) or []
    nombres = {s.get("job_name") for s in sc}
    if "faceops-equipos" not in nombres:
        sc.append({"job_name": "faceops-equipos", "file_sd_configs": [{"files": [f"{RUTA}/objetivos/exp-*.yml"]}]})
        cambio = True
    if "faceops-sondeo" not in nombres:
        sc.append({"job_name": "faceops-sondeo", "metrics_path": "/probe", "params": {"module": ["tcp_connect"]},
                   "file_sd_configs": [{"files": [f"{RUTA}/objetivos/tcp-*.yml"]}],
                   "relabel_configs": [{"source_labels": ["__address__"], "target_label": "__param_target"},
                                       {"source_labels": ["__param_target"], "target_label": "instance"},
                                       {"target_label": "__address__", "replacement": "127.0.0.1:9115"}]})
        cambio = True
    cfg["scrape_configs"] = sc
    return cambio


def reglas(cat, ped):
    por_id = {a["id"]: a for a in cat["alertas"]}
    eq, tipo = ped["equipo"], ped["tipo"]
    out, resumen = [], []
    for x in ped.get("alertas") or []:
        a = por_id.get(x.get("id"))
        if not a or tipo not in a["tipos"]:
            raise SystemExit(f"La alerta {x.get('id')} no existe o no aplica a un equipo «{tipo}»")
        base = a["expr"].replace("__EQ__", eq)
        nombre = "FaceOps" + re.sub(r"[^A-Za-z0-9]", "", a["nombre"].title()) + a["id"]
        acc = x.get("accion") or "avisar"
        if acc not in (a.get("acciones") or ["avisar"]):
            raise SystemExit(f"{a['id']}: la acción «{acc}» no corresponde a esta alerta")
        etiq = {"faceops_equipo": eq, "faceops_alerta": a["id"], "faceops_pilar": a.get("grupo", ""), "faceops_accion": acc,
                "faceops_automatizacion": (cat.get("acciones") or {}).get(acc, {}).get("auto", "")}
        ann = {"summary": f"{eq} · {a['nombre']}", "description": a.get("descripcion", "") + (f" (valor: {{{{ $value | humanize }}}} {a.get('unidad', '')})" if a.get("unidad") else "")}
        if a.get("umbral", True) is False:
            sev = x.get("severidad") or a.get("severidad", "aviso")
            out.append({"alert": nombre, "expr": base, "for": x.get("para") or a.get("para", "5m"), "labels": dict(etiq, severity=SEV[sev]), "annotations": dict(ann)})
            resumen.append(f"{a['id']} {a['nombre']} ({sev}) → {acc}")
            continue
        av, cr = (None if x.get(k, a.get(k)) in (None, "") else x.get(k, a.get(k)) for k in ("aviso", "critico"))
        if av is None and cr is None:
            raise SystemExit(f"{a['id']}: indica al menos un umbral")
        if av is not None and cr is not None and (float(av) <= float(cr) if a.get("menor") else float(av) >= float(cr)):
            raise SystemExit(f"{a['id']}: el umbral de aviso ({av}) debe ser {'mayor' if a.get('menor') else 'menor'} que el crítico ({cr})")
        for sev, u in (("aviso", av), ("critico", cr)):
            if u is None:
                continue
            out.append({"alert": nombre + ("Critico" if sev == "critico" else "Aviso"), "expr": base.replace("__UMBRAL__", str(_num(u))),
                        "for": x.get("para") or a.get("para", "5m"), "labels": dict(etiq, severity=SEV[sev]), "annotations": dict(ann)})
        resumen.append(f"{a['id']} {a['nombre']} → {acc} (aviso {'—' if av is None else av} · crítico {'—' if cr is None else cr} {a.get('unidad', '')})".strip())
    return out, resumen


def main():
    cat = yaml.safe_load(open(sys.argv[1]))
    cfg = yaml.safe_load(open(sys.argv[2])) or {}
    ped = json.load(open(sys.argv[3]))
    if ped["tipo"] not in cat["tipos"]:
        raise SystemExit(f"Tipo de equipo sin alertas: {ped['tipo']}")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,63}", ped["equipo"]):
        raise SystemExit("Nombre de equipo inválido")
    t = cat["tipos"][ped["tipo"]]
    cambio = preparar_prometheus(cfg)
    retirar = ped.get("retirar") or not ped.get("alertas")
    rs, resumen = ([], []) if retirar else reglas(cat, ped)
    pref = "tcp" if t["job"] == "faceops-sondeo" else "exp"
    obj = [] if retirar else [{"targets": [f"{ped['ip']}:{t['puerto']}"], "labels": {"faceops_equipo": ped["equipo"], "faceops_tipo": ped["tipo"]}}]
    print(json.dumps({
        "prometheus": yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True) if cambio else "",
        "reglas": "" if retirar else yaml.safe_dump({"groups": [{"name": f"faceops-{ped['equipo']}", "rules": rs}]}, sort_keys=False, allow_unicode=True, width=1000),
        "objetivo": "" if retirar else yaml.safe_dump(obj, sort_keys=False, allow_unicode=True),
        "objetivo_archivo": f"{pref}-{ped['equipo']}.yml", "resumen": resumen, "n_reglas": len(rs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
