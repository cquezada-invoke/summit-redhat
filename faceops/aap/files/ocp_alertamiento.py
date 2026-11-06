#!/usr/bin/env python3
"""Modelo de alertamiento de OpenShift (lo llama openshift-alertamiento.yml). Se hace en Python porque las alertas llevan
plantillas de Prometheus ({{ $labels.x }}) que Ansible intentaría interpretar, y porque la configuración de Alertmanager se
debe editar como YAML sin riesgo de dejarla convertida en texto.

Uso: ocp_alertamiento.py <modo evaluar|aplicar|retirar> <api> <modelo.yml> <url EDA> <pilares separados por coma>
Token: variable de entorno K8S_AUTH_API_KEY. Imprime un JSON con el resultado; sale con código 1 si algo falla.
"""
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

import yaml

PILARES = ("disponibilidad", "seguridad", "rendimiento")
NS = "openshift-monitoring"
SEL = "faceops.invoke.cl%2Fmodelo%3Dalertamiento"


def pedir(api, metodo, ruta, cuerpo=None, tipo="application/json", ok=(200, 201)):
    req = urllib.request.Request(api + ruta, method=metodo, data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    req.add_header("Authorization", "Bearer " + os.environ.get("K8S_AUTH_API_KEY", ""))
    req.add_header("Accept", "application/json")
    if cuerpo is not None:
        req.add_header("Content-Type", tipo)
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return e.code, {}
        raise RuntimeError(f"{metodo} {ruta}: HTTP {e.code} {e.read()[:300].decode(errors='replace')}") from None


def reglas(modelo, pilares):
    out = {p: [] for p in pilares}
    for r in modelo["reglas"]:
        if r["pilar"] not in pilares:
            continue
        for sev, val in (("warning", r.get("aviso")), ("critical", r.get("critico"))):
            if val is None:
                continue
            crit = sev == "critical"
            out[r["pilar"]].append({
                "alert": "FaceOps" + r["nombre"] + ("Critico" if crit else "Aviso"),
                "expr": r["expr"].replace("__UMBRAL__", str(val)), "for": r["para"],
                "labels": {"severity": sev, "faceops_pilar": r["pilar"], "faceops_ambito": r["ambito"], "faceops_regla": r["id"],
                           "faceops_umbral": str(val), "faceops_accion": r["accion"] if crit else "diagnosticar"},
                "annotations": {"summary": r["texto"],
                                "description": f"{r['texto']} · umbral {'crítico' if crit else 'de aviso'}: {val}",
                                "faceops_remediacion": f"{r['accion']} (con aprobación en AAP)" if crit else "diagnóstico automático (solo lectura)"}})
    return out


def alertmanager(api, modo, eda):
    _, sec = pedir(api, "GET", f"/api/v1/namespaces/{NS}/secrets/alertmanager-main")
    conf = yaml.safe_load(base64.b64decode(sec["data"]["alertmanager.yaml"])) or {}
    conf["receivers"] = [x for x in conf.get("receivers") or [] if x.get("name") != "faceops-eda"]
    ruta = conf.setdefault("route", {})
    ruta["routes"] = [x for x in ruta.get("routes") or [] if x.get("receiver") != "faceops-eda"]
    if modo == "aplicar":
        conf["receivers"].append({"name": "faceops-eda", "webhook_configs": [{"url": eda, "send_resolved": True}]})
        ruta["routes"].insert(0, {"receiver": "faceops-eda", "matchers": ['faceops_pilar=~"disponibilidad|seguridad|rendimiento"'],
                                  "continue": True, "group_wait": "30s", "repeat_interval": "1h"})
    if not ruta.get("receiver"):
        raise RuntimeError("La configuración de Alertmanager no tiene receptor por defecto: no se toca")
    nuevo = yaml.safe_dump(conf, sort_keys=False, allow_unicode=True)
    pedir(api, "PATCH", f"/api/v1/namespaces/{NS}/secrets/alertmanager-main",
          {"data": {"alertmanager.yaml": base64.b64encode(nuevo.encode()).decode()}}, "application/merge-patch+json")
    return any(x.get("name") == "faceops-eda" for x in conf["receivers"])


def main():
    modo, api, archivo, eda, pil = sys.argv[1:6]
    api = api.rstrip("/")
    pilares = [p for p in pil.split(",") if p] or list(PILARES)
    if modo not in ("evaluar", "aplicar", "retirar") or set(pilares) - set(PILARES) or not api.startswith("https://"):
        raise RuntimeError("Modo o pilares no válidos, o falta el API del clúster")
    modelo = yaml.safe_load(open(archivo, encoding="utf-8"))
    _, mon = pedir(api, "GET", "/apis/config.openshift.io/v1/clusteroperators/monitoring")
    disp = any(c.get("type") == "Available" and c.get("status") == "True" for c in mon.get("status", {}).get("conditions", []))
    if not disp:
        raise RuntimeError("El operador de monitoreo del clúster no está disponible: el modelo no se puede aplicar")
    st, cmc = pedir(api, "GET", f"/api/v1/namespaces/{NS}/configmaps/cluster-monitoring-config", ok=(200, 404))
    uwm = "enableUserWorkload: true" in ((cmc or {}).get("data") or {}).get("config.yaml", "")
    rp = reglas(modelo, pilares)
    url = f"/apis/monitoring.coreos.com/v1/namespaces/{NS}/prometheusrules"
    receptor = None
    if modo == "aplicar":
        for p in pilares:
            cuerpo = {"apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule",
                      "metadata": {"name": f"faceops-{p}", "namespace": NS,
                                   "labels": {"faceops.invoke.cl/modelo": "alertamiento", "faceops.invoke.cl/pilar": p, "role": "alert-rules"}},
                      "spec": {"groups": [{"name": f"faceops-{p}", "rules": rp[p]}]}}
            pedir(api, "PATCH", f"{url}/faceops-{p}?fieldManager=faceops&force=true", cuerpo, "application/apply-patch+yaml")
        receptor = alertmanager(api, modo, eda)
    elif modo == "retirar":
        receptor = alertmanager(api, modo, eda)
        for p in PILARES:
            pedir(api, "DELETE", f"{url}/faceops-{p}", ok=(200, 202, 404))
    _, fin = pedir(api, "GET", f"{url}?labelSelector={SEL}")
    print(json.dumps({"modo": modo, "monitoreo": "disponible", "monitoreo_usuario": uwm, "reglas_modelo": len(modelo["reglas"]),
                      "alertas_por_pilar": {p: len(v) for p, v in rp.items()},
                      "aplicadas": [x["metadata"]["name"] for x in fin.get("items", [])],
                      "receptor_eda": receptor, "eda": eda if modo == "aplicar" else ""}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e)[:500]}, ensure_ascii=False))
        sys.exit(1)
