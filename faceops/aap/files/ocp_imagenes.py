#!/usr/bin/env python3
"""Relevamiento de imágenes de un clúster OpenShift (lo llama openshift-imagenes.yml). Solo lectura, por la API.

Qué mide:
  en uso        imágenes (por digest) que corren en pods
  sin uso       imágenes del registro interno (ImageStreams) que ningún pod ni workload usa
  caché         imágenes guardadas en los nodos y cuántas no usa ningún pod (espacio recuperable)
  proporción    usadas / total, en cantidad y en espacio
  antiguas      imágenes en uso más viejas que el umbral → procedimiento de remediación
  replicadas    mismo digest en varios ImageStreams o etiquetas, y repositorios con muchas revisiones → estrategia de limpieza

Uso: ocp_imagenes.py <api> <dias_antigua> <max_revisiones>   (token en K8S_AUTH_API_KEY). Imprime un JSON.
"""
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone


def pedir(api, ruta, ok404=False):
    req = urllib.request.Request(api + ruta)
    req.add_header("Authorization", "Bearer " + os.environ.get("K8S_AUTH_API_KEY", ""))
    req.add_header("Accept", "application/json")
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if ok404 and e.code in (403, 404):
            return {}
        raise RuntimeError(f"GET {ruta}: HTTP {e.code}") from None


def digest(ref):
    m = re.search(r"(sha256:[0-9a-f]{64})", ref or "")
    return m.group(1) if m else ""


def repo(ref):
    r = (ref or "").split("@")[0]
    return re.sub(r":[^/:]+$", "", r)


def sistema(ns):
    return ns.startswith(("openshift", "kube")) or ns in ("default",)


def main():
    api, dias, max_rev = sys.argv[1].rstrip("/"), int(sys.argv[2]), int(sys.argv[3])
    ahora = datetime.now(timezone.utc)
    pods = pedir(api, "/api/v1/pods?limit=5000").get("items", [])
    en_uso = {}   # digest → datos
    for p in pods:
        ns = p["metadata"]["namespace"]
        if p.get("status", {}).get("phase") not in ("Running", "Pending"):
            continue
        for cs in (p.get("status", {}).get("containerStatuses") or []) + (p.get("status", {}).get("initContainerStatuses") or []):
            d = digest(cs.get("imageID")) or digest(cs.get("image"))
            if not d:
                continue
            x = en_uso.setdefault(d, {"digest": d, "imagen": cs.get("image", ""), "namespaces": set(), "pods": 0, "sistema": True})
            x["namespaces"].add(ns)
            x["pods"] += 1
            x["sistema"] = x["sistema"] and sistema(ns)
    # referencias de workloads (aunque no tengan pods corriendo: escalados a cero, cronjobs)
    refs = set()
    for ruta in ("/apis/apps/v1/deployments", "/apis/apps/v1/statefulsets", "/apis/apps/v1/daemonsets", "/apis/batch/v1/cronjobs"):
        for w in pedir(api, ruta + "?limit=5000", ok404=True).get("items", []):
            spec = w.get("spec", {}).get("template", {}).get("spec") or w.get("spec", {}).get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {})
            for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
                refs.add(c.get("image", ""))
    refs_dig = {digest(r) for r in refs if digest(r)}
    # registro interno: ImageStreams y sus revisiones
    iss = pedir(api, "/apis/image.openshift.io/v1/imagestreams?limit=5000", ok404=True).get("items", [])
    registro, por_digest, por_is = [], defaultdict(list), []
    for s in iss:
        ns, nom = s["metadata"]["namespace"], s["metadata"]["name"]
        repos_ = {x for x in (s.get("status", {}).get("dockerImageRepository"), s.get("status", {}).get("publicDockerImageRepository")) if x}
        revs = 0
        for t in s.get("status", {}).get("tags") or []:
            for i, it in enumerate(t.get("items") or []):
                revs += 1
                d = it.get("image", "")
                usada = d in en_uso or d in refs_dig or (i == 0 and any(f"{r_}:{t['tag']}" in refs for r_ in repos_))
                creado = it.get("created", "")
                registro.append({"ns": ns, "is": nom, "tag": t["tag"], "rev": i, "digest": d, "creado": creado, "usada": bool(usada), "sistema": sistema(ns)})
                por_digest[d].append(f"{ns}/{nom}:{t['tag']}")
        por_is.append({"is": f"{ns}/{nom}", "revisiones": revs, "sistema": sistema(ns)})
    # caché de los nodos
    nodos = pedir(api, "/api/v1/nodes").get("items", [])
    cache, cache_sin = [], []
    for n in nodos:
        for im in n.get("status", {}).get("images") or []:
            ds = {digest(x) for x in im.get("names") or [] if digest(x)}
            usada = bool(ds & set(en_uso))
            e = {"nodo": n["metadata"]["name"], "nombre": next((x for x in im.get("names") or [] if "@" not in x), (im.get("names") or [""])[0]),
                 "mb": round((im.get("sizeBytes") or 0) / 2**20), "usada": usada}
            cache.append(e)
            if not usada:
                cache_sin.append(e)
    # antigüedad de las imágenes en uso (metadatos de la imagen en el registro de OpenShift, si los tiene)
    antiguas = []
    for d, x in en_uso.items():
        im = pedir(api, f"/apis/image.openshift.io/v1/images/{d}", ok404=True)
        c = (im.get("dockerImageMetadata") or {}).get("Created") or ""
        if not c:
            continue
        try:
            edad = (ahora - datetime.fromisoformat(c.replace("Z", "+00:00"))).days
        except ValueError:
            continue
        x["edad_dias"] = edad
        if edad > dias and not x["sistema"]:
            antiguas.append({"imagen": x["imagen"], "edad_dias": edad, "namespaces": sorted(x["namespaces"]), "pods": x["pods"]})
    replicadas = [{"digest": d[:19], "ubicaciones": u} for d, u in por_digest.items() if len(set(u)) > 1]
    muchas_rev = [x for x in por_is if x["revisiones"] > max_rev]
    reg_total, reg_usadas = len(registro), sum(1 for r in registro if r["usada"])
    mb_cache, mb_sin = sum(e["mb"] for e in cache), sum(e["mb"] for e in cache_sin)
    print(json.dumps({
        "medido": ahora.isoformat(timespec="seconds"), "umbral_dias": dias, "max_revisiones": max_rev,
        "en_uso": {"imagenes": len(en_uso), "de_aplicaciones": sum(1 for x in en_uso.values() if not x["sistema"]),
                   "detalle": sorted([{"imagen": x["imagen"], "pods": x["pods"], "namespaces": sorted(x["namespaces"]), "edad_dias": x.get("edad_dias")}
                                      for x in en_uso.values() if not x["sistema"]], key=lambda y: -y["pods"])[:50]},
        "registro": {"imagestreams": len(iss), "revisiones": reg_total, "usadas": reg_usadas, "sin_uso": reg_total - reg_usadas,
                     "pct_uso": round(100 * reg_usadas / reg_total) if reg_total else None,
                     "sin_uso_detalle": [r for r in registro if not r["usada"] and not r["sistema"]][:50]},
        "cache_nodos": {"imagenes": len(cache), "mb": mb_cache, "sin_uso": len(cache_sin), "mb_sin_uso": mb_sin,
                        "pct_uso": round(100 * (len(cache) - len(cache_sin)) / len(cache)) if cache else None,
                        "mayores_sin_uso": sorted(cache_sin, key=lambda e: -e["mb"])[:15]},
        "antiguas": sorted(antiguas, key=lambda y: -y["edad_dias"])[:30],
        "replicadas": replicadas[:30], "muchas_revisiones": sorted(muchas_rev, key=lambda y: -y["revisiones"])[:30],
        "remediacion_antiguas": [
            "Reconstruir la imagen sobre la versión vigente de su imagen base (UBI) y volver a desplegar con el pipeline",
            "Escanear la imagen nueva (ACS) antes de promoverla y comparar CVE contra la anterior",
            "Definir una vida máxima de imagen en producción y un disparador de reconstrucción cuando cambia la base",
        ],
        "estrategia_limpieza": [
            f"Limitar el historial de cada ImageStream (máximo {max_rev} revisiones por etiqueta)",
            "Ejecutar el pruner de imágenes del registro (ImagePruner) a diario: keepTagRevisions 3, keepYoungerThanDuration 60m",
            "Unificar etiquetas que apuntan al mismo digest y eliminar los ImageStreams duplicados entre namespaces",
            "Dejar que el kubelet libere la caché de nodos por umbral de disco (imageGCHighThresholdPercent 85 / Low 80)",
        ],
    }, ensure_ascii=False, default=list))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e)[:500]}, ensure_ascii=False))
        sys.exit(1)
