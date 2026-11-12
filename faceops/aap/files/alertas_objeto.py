"""Monitoreo · Configurar alertas del equipo: arma lo que Prometheus necesita para vigilar un equipo con las alertas elegidas.

Uso: alertas_objeto.py <catalogo.yml> <prometheus.yml actual> <pedido.json>
     alertas_objeto.py --tablero <catalogo.yml> <pedido.json>   → JSON del tablero de Grafana del equipo (solo sus alertas activas)
pedido: {"equipo", "ip", "tipo": linux|windows|storage|red, "ambito": maquina | plataforma:<familia>, "alertas": [{"id", "aviso"?, "critico"?, "severidad"?, "para"?}], "retirar": bool,
         "inventario"?, "credencial"? (de AAP, para las acciones que entran a la máquina)}
Salida (JSON): {"prometheus": yaml o "" si no cambia, "reglas": yaml o "", "objetivo": yaml o "", "objetivo_archivo", "resumen": [...]}"""
import json
import re
import sys

import yaml

RUTA = "/etc/prometheus/faceops"
SEV = {"aviso": "warning", "critico": "critical"}


_CMP = re.compile(r"^(.*?)\s*(>=|<=|==|!=|>|<)\s*(__UMBRAL__|-?[0-9.]+)\s*$", re.S)


def serie(expr):
    """Expresión de la regla → (lo que se evalúa, umbral fijo o None). «… > __UMBRAL__» → (…, None) · «… == 0» → (…, 0)."""
    m = _CMP.match(expr.strip())
    if not m:
        return expr, None
    return m.group(1), (None if m.group(3) == "__UMBRAL__" else _num(m.group(3)))


def panel_id(aid):
    """Id estable del panel de cada alerta en el tablero (C01 → 6701): FaceOps arma el enlace con viewPanel."""
    return ord(aid[0].upper()) * 100 + int(re.sub(r"\D", "", aid) or 0)


def _num(v):
    f = float(v)
    return int(f) if f == int(f) else f


EXPORTADORES = {"redis": 9121, "postgresql": 9187, "nginx": 9113, "jboss": 9990}   # puerto del exportador de cada plataforma (/metrics)


def preparar_prometheus(cfg, faceops=""):
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
    if "faceops-segmentos" not in nombres:   # segmentos de negocio: sondas del blackbox, el módulo viaja en cada objetivo
        sc.append({"job_name": "faceops-segmentos", "metrics_path": "/probe", "scrape_interval": "30s",
                   "file_sd_configs": [{"files": [f"{RUTA}/objetivos/seg-*.yml"]}],
                   "relabel_configs": [{"source_labels": ["__address__"], "target_label": "__param_target"},
                                       {"source_labels": ["__param_target"], "target_label": "instance"},
                                       {"target_label": "__address__", "replacement": "127.0.0.1:9115"}]})
        cambio = True
    if "faceops-exportadores" not in nombres:   # exportadores de plataforma (redis_exporter, postgres_exporter, nginx, métricas de JBoss)
        sc.append({"job_name": "faceops-exportadores", "file_sd_configs": [{"files": [f"{RUTA}/objetivos/plt-*.yml"]}]})
        cambio = True
    if faceops and "faceops-plataformas" not in nombres:   # plataformas: lo que FaceOps midió sin agente (AAP por SSH/API), publicado en el anfitrión
        sc.append({"job_name": "faceops-plataformas", "metrics_path": "/metricas/plataformas", "scrape_interval": "60s", "honor_labels": True,
                   "static_configs": [{"targets": [faceops]}]})
        cambio = True
    cfg["scrape_configs"] = sc
    return cambio


def reglas(cat, ped):
    por_id = {a["id"]: a for a in cat["alertas"]}
    eq, tipo = ped["equipo"], ped["tipo"]
    out, resumen, umb = [], [], []
    for x in ped.get("alertas") or []:
        a = por_id.get(x.get("id"))
        if not a or tipo not in a["tipos"]:
            raise SystemExit(f"La alerta {x.get('id')} no existe o no aplica a un equipo «{tipo}»")
        if a.get("pendiente"):
            raise SystemExit(f"La alerta {a['id']} aún no se puede medir: {a['pendiente']}")
        if a.get("ambito", "maquina") != ambito(ped):
            raise SystemExit(f"La alerta {a['id']} es del ámbito «{a.get('ambito', 'maquina')}», no de «{ambito(ped)}»")
        base = a["expr"].replace("__EQ__", eq)
        nombre = "FaceOps" + re.sub(r"[^A-Za-z0-9]", "", a["nombre"].title()) + a["id"]
        acc = x.get("accion") or "avisar"
        if acc not in (a.get("acciones") or ["avisar"]):
            raise SystemExit(f"{a['id']}: la acción «{acc}» no corresponde a esta alerta")
        etiq = {"faceops_equipo": eq, "faceops_ambito": ambito(ped), "faceops_alerta": a["id"], "faceops_pilar": a.get("grupo", ""), "faceops_accion": acc,
                "faceops_automatizacion": (cat.get("acciones") or {}).get(acc, {}).get("auto", "")}
        if (cat.get("acciones") or {}).get(acc, {}).get("acceso") == "equipo":   # la respuesta entra a la máquina
            if not ped.get("inventario") or not ped.get("credencial"):
                raise SystemExit(f"{a['id']}: la acción «{acc}» necesita el inventario y la credencial de AAP del equipo")
            etiq.update(faceops_inventario=ped["inventario"], faceops_credencial=ped["credencial"])
        ann = {"summary": f"{eq} · {a['nombre']}", "description": a.get("descripcion", "") + (f" (valor: {{{{ $value | humanize }}}} {a.get('unidad', '')})" if a.get("unidad") else "")}
        if a.get("umbral", True) is False:
            sev = x.get("severidad") or a.get("severidad", "aviso")
            out.append({"alert": nombre, "expr": base, "for": x.get("para") or a.get("para", "5m"), "labels": dict(etiq, severity=SEV[sev]), "annotations": dict(ann)})
            if serie(base)[1] is not None:   # el umbral fijo de la regla, como serie para el tablero de Grafana
                umb.append(_umbral(eq, a["id"], SEV[sev], serie(base)[1]))
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
            umb.append(_umbral(eq, a["id"], SEV[sev], u))
        resumen.append(f"{a['id']} {a['nombre']} → {acc} (aviso {'—' if av is None else av} · crítico {'—' if cr is None else cr} {a.get('unidad', '')})".strip())
    return out + umb, resumen


def _umbral(eq, aid, sev, u):
    """Regla de registro con el umbral vigente (faceops_alerta_umbral): Grafana dibuja la línea de aviso/crítico y su historia."""
    return {"record": "faceops_alerta_umbral", "expr": f"vector({_num(u)})", "labels": {"faceops_equipo": eq, "faceops_alerta": aid, "severity": sev}}


UNIDAD = {"%": "percent", "s": "s", "ms": "ms", "por s": "pps", "% de éxito": "percent"}
DS = {"type": "prometheus", "uid": "prometheus"}


def ambito(ped):
    a = ped.get("ambito") or "maquina"
    if a != "maquina" and not re.fullmatch(r"plataforma:[a-z0-9-]{1,30}", a):
        raise SystemExit(f"Ámbito inválido: {a}")
    return a


def sufijo(ped):
    """'' para la máquina; '--jboss' para la plataforma JBoss (grupo, archivo de reglas y tablero propios)."""
    a = ambito(ped)
    return "" if a == "maquina" else "--" + a.split(":", 1)[1]


def uid_tablero(eq):
    """uid del tablero de un equipo en Grafana (máx. 40): faceops-al-<equipo>, o recortado con huella si es largo."""
    import hashlib
    u = f"faceops-al-{eq}"
    return u if len(u) <= 40 else f"faceops-al-{eq[:20]}-{hashlib.sha1(eq.encode()).hexdigest()[:8]}"


def tablero(cat, ped):
    """Tablero «Alertas de <equipo>»: SOLO las alertas activas de ese equipo. Por cada una, lo que evalúa su regla (la misma
    expresión, sin la comparación), las líneas de umbral vigentes (faceops_alerta_umbral) y, como anotación, cuándo se disparó
    (ALERTS). Todas las consultas van fijas al equipo: no hay variable ni otras máquinas."""
    eq, suf = ped["equipo"], sufijo(ped)
    por_id = {a["id"]: a for a in cat["alertas"]}
    xs = [por_id[x["id"]] for x in ped.get("alertas") or [] if x.get("id") in por_id]
    q_ = lambda t: t.replace("__EQ__", eq).replace("$equipo", eq)   # noqa: E731
    paneles, anot = [], [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
                          "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"}]
    for k, a in enumerate(xs):
        pid, (base, _) = panel_id(a["id"]), serie(a["expr"])
        por = re.search(r"by \(faceops_equipo, (\w+)\)", base)
        paneles.append({
            "type": "timeseries", "id": pid, "title": f"{a['nombre']} · {a['id']}", "datasource": DS,
            "description": f"{a.get('descripcion', '')}. Regla en Prometheus (grupo faceops-{eq}{suf}): {q_(a['expr'])}",
            "gridPos": {"h": 9, "w": 12, "x": 12 * (k % 2), "y": 9 * (k // 2)},
            "targets": [{"refId": "A", "datasource": DS, "expr": q_(base), "legendFormat": "{{" + por.group(1) + "}}" if por else a["nombre"]},
                        {"refId": "B", "datasource": DS, "expr": f'faceops_alerta_umbral{{faceops_equipo="{eq}",faceops_alerta="{a["id"]}"}}',
                         "legendFormat": "Umbral {{severity}}"}],
            "fieldConfig": {"defaults": {"unit": UNIDAD.get(a.get("unidad"), "short"), "custom": {"lineWidth": 2, "fillOpacity": 8, "spanNulls": True}},
                            "overrides": [{"matcher": {"id": "byName", "options": f"Umbral {s_}"},
                                           "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c_}},
                                                          {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 6]}},
                                                          {"id": "custom.fillOpacity", "value": 0}]}
                                          for s_, c_ in (("warning", "orange"), ("critical", "red"))]},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "none"}}})
        anot.append({"name": f"{a['id']} disparada", "datasource": DS, "enable": True, "hide": True, "iconColor": "red",
                     "expr": f'ALERTS{{faceops_equipo="{eq}",faceops_alerta="{a["id"]}",alertstate="firing"}}', "step": "60s",
                     "titleFormat": "{{alertname}}", "textFormat": "{{severity}}", "tagKeys": "faceops_alerta,severity",
                     "useValueForTime": False, "filter": {"exclude": False, "ids": [pid]}})
    return {"uid": uid_tablero(eq + suf), "title": f"Alertas de {eq}" + (f" · {suf[2:]}" if suf else ""), "tags": ["faceops", "alertas", eq], "editable": False,
            "timezone": "browser", "refresh": "1m", "schemaVersion": 39, "time": {"from": "now-24h", "to": "now"},
            "description": "Generado por Ansible («Monitoreo · Configurar alertas del equipo») con las alertas activas del equipo: no editar a mano",
            "templating": {"list": []}, "annotations": {"list": anot}, "panels": paneles}


MODULOS = {"http_ok", "tcp_connect"}


def objetivos_segmento(ped):
    """Sondas de un segmento de negocio (blackbox): el VIP por HTTP (por el balanceador), los nodos del balanceador y los
    componentes por TCP. Cada objetivo lleva su módulo (__param_module) y su rol (vip | backend | componente)."""
    out = []
    for p in ped.get("objetivos") or []:
        tg, mod, rol = str(p.get("target") or ""), p.get("modulo"), p.get("rol")
        if mod not in MODULOS or rol not in ("vip", "backend", "componente"):
            raise SystemExit(f"Sonda inválida: {mod} / {rol}")
        if not re.fullmatch(r"(https?://[A-Za-z0-9._:/%~+-]{3,200}|[A-Za-z0-9._-]{1,120}:[0-9]{1,5})", tg):
            raise SystemExit(f"Objetivo inválido: {tg}")
        out.append({"targets": [tg], "labels": {"__param_module": mod, "faceops_equipo": ped["equipo"], "faceops_tipo": "segmento",
                                                "faceops_rol": rol, "faceops_componente": re.sub(r"[^A-Za-z0-9._ -]", "", str(p.get("componente") or ""))[:60]}})
    if not out:
        raise SystemExit("El segmento no tiene nada que medir (sin URL de entrada ni componentes con IP)")
    return out


def main():
    if sys.argv[1] == "--tablero":
        print(json.dumps(tablero(yaml.safe_load(open(sys.argv[2])), json.load(open(sys.argv[3]))), ensure_ascii=False, indent=1))
        return
    cat = yaml.safe_load(open(sys.argv[1]))
    cfg = yaml.safe_load(open(sys.argv[2])) or {}
    ped = json.load(open(sys.argv[3]))
    if ped["tipo"] not in cat["tipos"]:
        raise SystemExit(f"Tipo de equipo sin alertas: {ped['tipo']}")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,63}", ped["equipo"]):
        raise SystemExit("Nombre de equipo inválido")
    t = cat["tipos"][ped["tipo"]]
    faceops = ped.get("faceops_metricas") or (cat.get("monitoreo") or {}).get("faceops_metricas") or ""   # configuración, no código
    if faceops and not re.fullmatch(r"[A-Za-z0-9.-]+:\d{2,5}", faceops):
        raise SystemExit(f"faceops_metricas inválido: {faceops}")
    cambio = preparar_prometheus(cfg, faceops)
    retirar = ped.get("retirar") or not ped.get("alertas")
    rs, resumen = ([], []) if retirar else reglas(cat, ped)
    pref = "tcp" if t["job"] == "faceops-sondeo" else ("seg" if t["job"] == "faceops-segmentos" else "exp")
    por_id = {a["id"]: a for a in cat["alertas"]}
    sin_agente = not retirar and all(por_id.get(x.get("id"), {}).get("sin_agente") for x in ped.get("alertas") or [])
    obj = [] if retirar or sin_agente else ([{"targets": [f"{ped['ip']}:{t['puerto']}"], "labels": {"faceops_equipo": ped["equipo"], "faceops_tipo": ped["tipo"]}}]
                                            if pref != "seg" else objetivos_segmento(ped))   # sin agente: las métricas las publica FaceOps
    # exportador de la plataforma: si alguna alerta del pedido lo necesita, su objetivo va aparte (job faceops-exportadores)
    fam = ambito(ped).split(":", 1)[1] if ambito(ped) != "maquina" else ""
    necesita = (not retirar) and fam in EXPORTADORES and any(por_id.get(x.get("id"), {}).get("exportador") for x in ped.get("alertas") or [])
    obj_x = [{"targets": [f"{ped['ip']}:{EXPORTADORES[fam]}"], "labels": {"faceops_equipo": ped["equipo"], "faceops_plataforma": fam}}] if necesita else []
    print(json.dumps({
        "objetivo_exp": yaml.safe_dump(obj_x, sort_keys=False, allow_unicode=True) if necesita else "",
        "objetivo_exp_archivo": f"plt-{ped['equipo']}--{fam}.yml" if fam else "",
        "prometheus": yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True) if cambio else "",
        "reglas": "" if retirar else yaml.safe_dump({"groups": [{"name": f"faceops-{ped['equipo']}{sufijo(ped)}", "rules": rs}]}, sort_keys=False, allow_unicode=True, width=1000),
        "grupo": f"faceops-{ped['equipo']}{sufijo(ped)}", "reglas_archivo": f"{ped['equipo']}{sufijo(ped)}.yml", "tablero_uid": uid_tablero(ped["equipo"] + sufijo(ped)),
        "objetivo": "" if retirar or sin_agente else yaml.safe_dump(obj, sort_keys=False, allow_unicode=True), "sin_agente": sin_agente,
        "objetivo_archivo": f"{pref}-{ped['equipo']}.yml", "resumen": resumen, "n_reglas": sum(1 for r in rs if "alert" in r)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
