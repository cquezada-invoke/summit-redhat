#!/usr/bin/python3
"""Claro PR · Grafana → Event-Driven Ansible: crea en el Grafana del laboratorio el punto de contacto «EDA · Claro PR»
(webhook a la activación de EDA en :5013), la política que le envía las alertas de la carpeta «Claro PR» y la regla
«DiscoCasiLleno» (uso de / sobre el umbral en cualquier servidor, desde Prometheus/node_exporter). Idempotente.
La clave de administrador de Grafana se lee de ~/demo-drift/cicd-secretos.txt y nunca se imprime."""
import base64, json, os, sys, urllib.error, urllib.request

SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
G = "http://127.0.0.1:3001/grafana/api"
CAB = {"Authorization": "Basic " + base64.b64encode(f"admin:{SEC['grafana_admin_clave']}".encode()).decode(),
       "Content-Type": "application/json", "X-Disable-Provenance": "true"}
UMBRAL = int(os.environ.get("UMBRAL_DISCO", "85"))


def g(m, p, d=None, tolerar=False):
    r = urllib.request.Request(G + p, method=m, data=json.dumps(d).encode() if d is not None else None, headers=CAB)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            b = x.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


# 1. punto de contacto
cps = g("GET", "/v1/provisioning/contact-points")
cp = {"name": "EDA · Claro PR", "type": "webhook", "settings": {"url": "http://127.0.0.1:5013/endpoint", "httpMethod": "POST"}, "disableResolveMessage": True}
x = next((c for c in cps if c["name"] == cp["name"]), None)
g("PUT", f"/v1/provisioning/contact-points/{x['uid']}", dict(cp, uid=x["uid"])) if x else g("POST", "/v1/provisioning/contact-points", cp)
print("   · punto de contacto «EDA · Claro PR» → :5013")

# 2. carpeta y política: las alertas con etiqueta destino=eda-claro van a EDA
fol = next((f for f in g("GET", "/folders") if f["title"] == "Claro PR"), None) or g("POST", "/folders", {"title": "Claro PR"})
pol = g("GET", "/v1/provisioning/policies")
rutas = [r for r in pol.get("routes") or [] if (r.get("receiver") != cp["name"])]
rutas.insert(0, {"receiver": cp["name"], "object_matchers": [["destino", "=", "eda-claro"]], "group_by": ["alertname", "servidor"],
                 "group_wait": "10s", "group_interval": "1m", "repeat_interval": "30m", "continue": False})
pol["routes"] = rutas
g("PUT", "/v1/provisioning/policies", pol)
print("   · política: etiqueta destino=eda-claro → EDA")

# 3. regla de disco casi lleno
regla = {
    "title": "DiscoCasiLleno", "ruleGroup": "claro-pr", "folderUID": fol["uid"], "condition": "C", "for": "1m", "orgID": 1,
    "noDataState": "OK", "execErrState": "OK",
    "labels": {"destino": "eda-claro", "severidad": "alta"},
    "annotations": {"summary": "El disco / de {{ $labels.servidor }} está sobre " + str(UMBRAL) + "%"},
    "data": [
        {"refId": "A", "datasourceUid": "prometheus", "relativeTimeRange": {"from": 300, "to": 0},
         "model": {"refId": "A", "instant": True,
                   "expr": 'label_replace(100 * (1 - node_filesystem_avail_bytes{job="servidores",mountpoint="/",fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{job="servidores",mountpoint="/",fstype!~"tmpfs|overlay"}), "servidor", "$1", "nombre", "(.*)")'}},
        {"refId": "C", "datasourceUid": "__expr__", "model": {"refId": "C", "type": "threshold", "expression": "A",
                                                              "conditions": [{"evaluator": {"type": "gt", "params": [UMBRAL]}}]}}]}
ex = next((r for r in g("GET", "/v1/provisioning/alert-rules") if r["title"] == regla["title"]), None)
g("PUT", f"/v1/provisioning/alert-rules/{ex['uid']}", dict(regla, uid=ex["uid"])) if ex else g("POST", "/v1/provisioning/alert-rules", regla)
print(f"   · regla «DiscoCasiLleno» (uso de / sobre {UMBRAL}%) en la carpeta Claro PR")
