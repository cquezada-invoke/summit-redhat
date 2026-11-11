"""Monitoreo · Grafana de OpenShift: tablero «Alertas de OpenShift · <clúster>» con las alertas del catálogo de tipo openshift
(aap/files/alertas-objetos.yml) como VISUALIZACIÓN: por alerta, lo que evalúa su expresión, sus umbrales de aviso y crítico como
líneas y, como anotación, los tramos en que la condición se cumplió. Consulta el monitoreo del clúster (Thanos Querier de
openshift-monitoring) por el datasource <ds_uid>. Sin acciones de EDA.
Uso: tablero_ocp.py <catalogo.yml> <clúster> <ds_uid>
     tablero_ocp.py --reglas <catalogo.yml> <clúster>   → PrometheusRule «faceops-alertas-clúster» (JSON) para openshift-monitoring:
     las mismas alertas como reglas del monitoreo del clúster (visibles en su Prometheus y Alertmanager), sin acciones."""
import json
import re
import sys

import yaml

_CMP = re.compile(r"^(.*?)\s*(>=|<=|==|!=|>|<)\s*(__UMBRAL__|-?[0-9.]+)\s*$", re.S)
UNIDAD = {"%": "percent", "ms": "ms", "días": "d", "pods": "short"}


def panel_id(aid):
    return ord(aid[0].upper()) * 100 + int(re.sub(r"\D", "", aid) or 0)


def uid_tablero(cl):
    return f"faceops-ocp-{cl}"[:40]


def tablero(cat, cl, ds):
    DS = {"type": "prometheus", "uid": ds}
    xs = [a for a in cat["alertas"] if "openshift" in a.get("tipos", []) and a.get("expr")]
    paneles, anot = [], [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
                          "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"}]
    for k, a in enumerate(xs):
        m = _CMP.match(a["expr"].strip())
        base = m.group(1) if m else a["expr"]
        pid = panel_id(a["id"])
        tg = [{"refId": "A", "datasource": DS, "expr": base, "legendFormat": "__auto"}]
        if a.get("umbral", True) is not False:
            for ref, sev in (("B", "aviso"), ("C", "critico")):
                if a.get(sev) is not None:
                    tg.append({"refId": ref, "datasource": DS, "expr": f"vector({a[sev]})", "legendFormat": f"Umbral {sev}"})
            cond = a["expr"].replace("__UMBRAL__", str(a.get("aviso", a.get("critico"))))
        else:
            cond = a["expr"]
        paneles.append({
            "type": "timeseries", "id": pid, "title": f"{a['nombre']} · {a['id']}", "datasource": DS,
            "description": f"{a.get('descripcion', '')}. Expresión: {a['expr']}. Acción: por definir (por ahora solo visualización).",
            "gridPos": {"h": 9, "w": 12, "x": 12 * (k % 2), "y": 9 * (k // 2)}, "targets": tg,
            "fieldConfig": {"defaults": {"unit": UNIDAD.get(a.get("unidad"), "short"), "custom": {"lineWidth": 2, "fillOpacity": 8, "spanNulls": True}},
                            "overrides": [{"matcher": {"id": "byName", "options": f"Umbral {s_}"},
                                           "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c_}},
                                                          {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 6]}},
                                                          {"id": "custom.fillOpacity", "value": 0}]}
                                          for s_, c_ in (("aviso", "orange"), ("critico", "red"))]},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "none"}}})
        anot.append({"name": f"{a['id']} en condición", "datasource": DS, "enable": True, "hide": True, "iconColor": "red",
                     "expr": cond, "step": "60s", "titleFormat": f"{a['nombre']} ({a['id']})", "useValueForTime": False,
                     "filter": {"exclude": False, "ids": [pid]}})
    return {"uid": uid_tablero(cl), "title": f"Alertas de OpenShift · {cl}", "tags": ["faceops", "alertas", "openshift", cl], "editable": False,
            "timezone": "browser", "refresh": "1m", "schemaVersion": 39, "time": {"from": "now-24h", "to": "now"},
            "description": "Generado por Ansible («Monitoreo · Grafana de OpenShift») desde el catálogo de alertas de FaceOps: no editar a mano",
            "templating": {"list": []}, "annotations": {"list": anot}, "panels": paneles}


def reglas(cat, cl):
    """Una regla por umbral (aviso y crítico) etiquetada con faceops_alerta/faceops_equipo para la trazabilidad con FaceOps."""
    rs = []
    for a in cat["alertas"]:
        if "openshift" not in a.get("tipos", []) or not a.get("expr"):
            continue
        nombre = "FaceOps" + re.sub(r"[^A-Za-z0-9]", "", a["nombre"].title()) + a["id"]
        etq = {"faceops_equipo": cl, "faceops_alerta": a["id"], "faceops_origen": "faceops-tentativa"}
        ann = {"summary": f"{cl} · {a['nombre']}", "description": a.get("descripcion", "")}
        if a.get("umbral", True) is False:
            rs.append({"alert": nombre, "expr": a["expr"], "for": a.get("para", "5m"),
                       "labels": dict(etq, severity="critical" if a.get("severidad") == "critico" else "warning"), "annotations": ann})
            continue
        for sev, s_ in (("aviso", "warning"), ("critico", "critical")):
            if a.get(sev) is not None:
                rs.append({"alert": nombre + ("Critico" if sev == "critico" else "Aviso"), "expr": a["expr"].replace("__UMBRAL__", str(a[sev])),
                           "for": a.get("para", "5m"), "labels": dict(etq, severity=s_), "annotations": ann})
    return {"apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule",
            "metadata": {"name": "faceops-alertas-clúster".replace("ú", "u"), "namespace": "openshift-monitoring",
                         "labels": {"faceops.invoke.cl/gestionado": "true"}},
            "spec": {"groups": [{"name": f"faceops-{cl}", "rules": rs}]}}


if __name__ == "__main__":
    if sys.argv[1] == "--reglas":
        print(json.dumps(reglas(yaml.safe_load(open(sys.argv[2])), sys.argv[3]), ensure_ascii=False))
        sys.exit(0)
    print(json.dumps(tablero(yaml.safe_load(open(sys.argv[1])), sys.argv[2], sys.argv[3]), ensure_ascii=False, indent=1))
