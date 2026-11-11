#!/usr/bin/env python3
"""Imágenes de OpenShift por uso y antigüedad, y su movilidad a Quay (lo llaman openshift-imagenes-evaluar.yml y
openshift-imagenes-quay.yml). Token del clúster en K8S_AUTH_API_KEY; nunca se imprime ningún secreto.

  evaluar <api> <salida> : clasifica cada revisión de los ImageStreams (en uso, referenciada, sin uso por rangos de meses,
                           huérfanas) con su último uso conocido y tamaño, arma el plan de movilidad a Quay y deja en <salida>
                           plan.md, plan.csv y plan.json (si hay G y T en el entorno, también en Git). Solo lectura.
  mover <api> <salida>   : vuelve a evaluar, toma de las candidatas las seleccionadas (MQ_SELECCION) y las copia a Quay con
                           un Job de skopeo dentro del clúster (digest preservado y verificado). Solo si la copia se verificó
                           y MQ_RETIRAR=1 quita la etiqueta del registro interno. Credenciales de Quay en QUAY_USUARIO/QUAY_CLAVE.
Parámetros por entorno: IM_MESES (6), IM_RANGOS ("3,6,12"), IM_INCLUIR / IM_EXCLUIR (namespaces, coma, comodines),
IM_QUAY (host de Quay), IM_QUAY_ORG (organización; vacía = una por namespace), IM_CLUSTER (nombre para el informe).
"""
import base64
import csv
import fnmatch
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

REG = "image-registry.openshift-image-registry.svc:5000"
NS_MOV = "faceops-movilidad"
AHORA = datetime.now(timezone.utc)
CTX = ssl.create_default_context()
CTX.check_hostname, CTX.verify_mode = False, ssl.CERT_NONE


def pedir(api, ruta, metodo="GET", cuerpo=None, ok=(), tipo="application/json", crudo=False):
    req = urllib.request.Request(api + ruta, method=metodo, data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    req.add_header("Authorization", "Bearer " + os.environ.get("K8S_AUTH_API_KEY", ""))
    req.add_header("Accept", "application/json")
    if cuerpo is not None:
        req.add_header("Content-Type", tipo)
    for intento in range(4):   # la API agregada (image.openshift.io) puede responder 503 un momento: reintenta
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=120) as r:
                b = r.read()
                return b.decode(errors="replace") if crudo else (json.loads(b) if b else {})
        except urllib.error.HTTPError as e:
            if e.code in ok:
                return {}
            if e.code < 500 or intento == 3:
                raise RuntimeError(f"{metodo} {ruta.split('?')[0]}: HTTP {e.code}") from None
        except urllib.error.URLError as e:
            if intento == 3:
                raise RuntimeError(f"{metodo} {ruta.split('?')[0]}: {e.reason}") from None
        time.sleep(10 * (intento + 1))


def listar(api, ruta, opcional=True):
    out, cont = [], ""
    while True:
        sep = "&" if "?" in ruta else "?"
        r = pedir(api, f"{ruta}{sep}limit=500" + (f"&continue={cont}" if cont else ""), ok=(403, 404) if opcional else ())
        out += r.get("items") or []
        cont = (r.get("metadata") or {}).get("continue") or ""
        if not cont:
            return out


def fecha(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def iso(d):
    return d.isoformat(timespec="seconds") if d else ""


def digest(ref):
    m = re.search(r"(sha256:[0-9a-f]{64})", ref or "")
    return m.group(1) if m else ""


def lista(txt):
    return [x.strip() for x in (txt or "").split(",") if x.strip()]


def cfg():
    rangos = sorted({int(x) for x in lista(os.environ.get("IM_RANGOS") or "3,6,12")})
    return {"meses": int(os.environ.get("IM_MESES") or 6), "rangos": rangos,
            "incluir": lista(os.environ.get("IM_INCLUIR")),
            "excluir": lista(os.environ.get("IM_EXCLUIR")) + ["openshift", "openshift-*", "kube-*", "default", NS_MOV],
            "quay": (os.environ.get("IM_QUAY") or "").strip().rstrip("/"), "org": (os.environ.get("IM_QUAY_ORG") or "").strip(),
            "cluster": os.environ.get("IM_CLUSTER") or ""}


def ns_ok(c, ns):
    if any(fnmatch.fnmatch(ns, p) for p in c["excluir"]):
        return False
    return not c["incluir"] or any(fnmatch.fnmatch(ns, p) for p in c["incluir"])


def rango(c, meses):
    ant = 0
    for r in c["rangos"]:
        if meses < r:
            return f"sin uso {ant}–{r} meses"
        ant = r
    return f"sin uso más de {ant} meses"


def plantilla(w):
    s = w.get("spec") or {}
    return ((s.get("template") or {}).get("spec") or ((s.get("jobTemplate") or {}).get("spec") or {}).get("template", {}).get("spec") or {})


def imagenes_de(spec):
    return [c.get("image", "") for c in (spec.get("containers") or []) + (spec.get("initContainers") or [])]


def evaluar(api, c):
    # 1. Registro interno: ImageStreams de los namespaces evaluados, cada etiqueta y sus revisiones
    iss = listar(api, "/apis/image.openshift.io/v1/imagestreams", opcional=False)
    revs, por_tag, todos_dig = [], {}, set()
    for s in iss:
        ns, nom = s["metadata"]["namespace"], s["metadata"]["name"]
        for t in s.get("status", {}).get("tags") or []:
            for i, it in enumerate(t.get("items") or []):
                todos_dig.add(it.get("image", ""))
        if not ns_ok(c, ns):
            continue
        for t in s.get("status", {}).get("tags") or []:
            items = t.get("items") or []
            por_tag[(ns, nom, t["tag"])] = items
            for i, it in enumerate(items):
                revs.append({"ns": ns, "is": nom, "tag": t["tag"], "rev": i, "digest": it.get("image", ""), "push": fecha(it.get("created")),
                             "uso": None, "fuente": "", "estado": "sin uso", "historial": []})

    def resolver(ref, cuando=None):
        """Referencia de un contenedor → digests del registro interno (por digest o por ns/is:tag, a la fecha de uso)."""
        d = digest(ref)
        if d:
            return {d}
        m = re.match(r"^(?:[^/]+/)?([^/]+)/([^/:@]+):([^/:@]+)$", ref or "")
        if not m:
            return set()
        items = por_tag.get((m.group(1), m.group(2), m.group(3))) or []
        if cuando:   # la revisión vigente cuando se usó: la más nueva creada antes de esa fecha
            antes = [x for x in items if (fecha(x.get("created")) or AHORA) <= cuando]
            items = antes or items[-1:]
        return {items[0]["image"]} if items else set()

    usos = {}   # digest → (fecha, fuente)
    corriendo = set()   # digests con pods en ejecución
    activos = {}   # digest → fuente (referencia vigente: no es candidata aunque no tenga pods)
    hist = {}   # digest → referencias en historial (rollback)

    def uso(ds, f, fuente):
        for d in ds:
            if f and (d not in usos or f > usos[d][0]):
                usos[d] = (f, fuente)

    # 2. Pods: en ejecución = en uso ahora; terminados = último uso al terminar
    for p in listar(api, "/api/v1/pods", opcional=False):
        ns, st = p["metadata"]["namespace"], p.get("status") or {}
        ref_spec = imagenes_de(p.get("spec") or {})
        cs = (st.get("containerStatuses") or []) + (st.get("initContainerStatuses") or [])
        ds = {digest(x.get("imageID")) for x in cs if digest(x.get("imageID"))} | set().union(*[resolver(r) for r in ref_spec] or [set()])
        if st.get("phase") in ("Running", "Pending"):
            uso(ds, AHORA, f"pod {ns}/{p['metadata']['name']} en ejecución")
            corriendo |= ds
            for d in ds:
                activos[d] = f"pod {ns}/{p['metadata']['name']}"
        else:
            fin = max([fecha(((x.get("state") or {}).get("terminated") or {}).get("finishedAt")) or fecha(p["metadata"].get("creationTimestamp")) for x in cs]
                      or [fecha(p["metadata"].get("creationTimestamp"))])
            uso(ds, fin, f"pod terminado {ns}/{p['metadata']['name']}")
    # 3. Cargas con referencia vigente (aunque estén escaladas a cero o sean CronJobs)
    for kind, ruta in (("Deployment", "/apis/apps/v1/deployments"), ("StatefulSet", "/apis/apps/v1/statefulsets"),
                       ("DaemonSet", "/apis/apps/v1/daemonsets"), ("CronJob", "/apis/batch/v1/cronjobs"),
                       ("DeploymentConfig", "/apis/apps.openshift.io/v1/deploymentconfigs")):
        for w in listar(api, ruta):
            for r in imagenes_de(plantilla(w)):
                for d in resolver(r):
                    activos.setdefault(d, f"{kind} {w['metadata']['namespace']}/{w['metadata']['name']}")
                    if kind == "CronJob":
                        uso({d}, fecha((w.get("status") or {}).get("lastScheduleTime")), f"CronJob {w['metadata']['namespace']}/{w['metadata']['name']}")
    # 4. ReplicaSets y ReplicationControllers: activos si tienen réplicas; históricos = último uso cuando los reemplazó el siguiente
    for kind, ruta, anot in (("ReplicaSet", "/apis/apps/v1/replicasets", "deployment.kubernetes.io/revision"),
                             ("ReplicationController", "/api/v1/replicationcontrollers", "openshift.io/deployment-config.latest-version")):
        grupos = {}
        for w in listar(api, ruta):
            dueño = next((o["name"] for o in w["metadata"].get("ownerReferences") or []), w["metadata"]["name"])
            grupos.setdefault((w["metadata"]["namespace"], dueño), []).append(w)
        for (ns, dueño), ws in grupos.items():
            ws.sort(key=lambda x: int((x["metadata"].get("annotations") or {}).get(anot) or 0) or fecha(x["metadata"]["creationTimestamp"]).timestamp())
            for i, w in enumerate(ws):
                creado = fecha(w["metadata"]["creationTimestamp"])
                fin = fecha(ws[i + 1]["metadata"]["creationTimestamp"]) if i + 1 < len(ws) else None
                for r in imagenes_de(plantilla(w)):
                    ds = resolver(r, creado)
                    if (w.get("spec") or {}).get("replicas"):
                        for d in ds:
                            activos.setdefault(d, f"{kind} {ns}/{w['metadata']['name']}")
                    else:
                        uso(ds, fin or creado, f"{kind} histórico {ns}/{w['metadata']['name']}")
                        for d in ds:
                            hist.setdefault(d, []).append(f"{ns}/{dueño} ({kind} {w['metadata']['name']})")
    # 5. Jobs terminados y eventos recientes de descarga o arranque
    for w in listar(api, "/apis/batch/v1/jobs"):
        st = w.get("status") or {}
        f = fecha(st.get("completionTime")) or max([x for x in (fecha(y.get("lastTransitionTime")) for y in st.get("conditions") or []) if x] or [None])
        for r in imagenes_de(plantilla(w)):
            uso(resolver(r, fecha(w["metadata"]["creationTimestamp"])), f or fecha(st.get("startTime")), f"Job {w['metadata']['namespace']}/{w['metadata']['name']}")
            if st.get("active"):
                for d in resolver(r):
                    activos.setdefault(d, f"Job {w['metadata']['namespace']}/{w['metadata']['name']}")
    for ev in listar(api, "/api/v1/events"):
        if ev.get("reason") not in ("Pulled", "Pulling", "Created", "Started"):
            continue
        m = re.search(r'image "([^"]+)"', ev.get("message") or "")
        if m:
            uso(resolver(m.group(1)), fecha(ev.get("lastTimestamp") or ev.get("eventTime")), f"evento {ev.get('reason')} {ev['metadata']['namespace']}")

    # 6. Huérfanas: imágenes del registro interno que ya ningún ImageStream referencia
    huerf = []
    for im in listar(api, "/apis/image.openshift.io/v1/images"):
        a = (im["metadata"].get("annotations") or {})
        if a.get("openshift.io/image.managed") == "true" and im["metadata"]["name"] not in todos_dig:
            m = re.match(r"^[^/]+/([^/]+)/[^/@]+@", im.get("dockerImageReference") or "")
            if (m and ns_ok(c, m.group(1))) or (not m and not c["incluir"]):
                huerf.append(im)

    # 7. Tamaño y fecha de construcción de cada revisión (metadatos de la imagen en OpenShift)
    meta = {im["metadata"]["name"]: im for im in huerf}
    for r in revs:
        if r["digest"] not in meta:
            meta[r["digest"]] = pedir(api, f"/apis/image.openshift.io/v1/images/{r['digest']}", ok=(403, 404))

    def tam(im):
        capas = im.get("dockerImageLayers") or []
        return sum(int(x.get("size") or 0) for x in capas) or int((im.get("dockerImageMetadata") or {}).get("Size") or 0), {x.get("name") for x in capas}

    filas = []
    for r in revs:
        im = meta.get(r["digest"]) or {}
        r["bytes"], r["capas"] = tam(im)
        r["construida"] = fecha((im.get("dockerImageMetadata") or {}).get("Created"))
        if r["digest"] in activos:
            r["estado"], r["fuente"] = ("en uso" if r["digest"] in corriendo else "referenciada"), activos[r["digest"]]
            r["uso"] = usos.get(r["digest"], (None,))[0]
        else:
            u = usos.get(r["digest"])
            r["uso"], r["fuente"] = (u[0], u[1]) if u else (None, "")
            base = max([x for x in (r["uso"], r["push"]) if x] or [AHORA])
            r["meses"] = round((AHORA - base).days / 30.44, 1)
            r["base"] = "último uso" if r["uso"] else "push al registro (sin uso registrado)"
            r["estado"] = rango(c, r["meses"])
            r["historial"] = sorted(set(hist.get(r["digest"], [])))
            r["candidata"] = r["meses"] >= c["meses"]
        filas.append(r)
    for im in huerf:
        b, capas = tam(im)
        f = fecha(im["metadata"].get("creationTimestamp"))
        filas.append({"ns": "", "is": "", "tag": "", "rev": 0, "digest": im["metadata"]["name"], "push": f, "uso": None, "fuente": "",
                      "estado": "huérfana", "historial": [], "bytes": b, "capas": capas, "construida": fecha((im.get("dockerImageMetadata") or {}).get("Created")),
                      "meses": round((AHORA - (f or AHORA)).days / 30.44, 1), "base": "creación en el registro", "candidata": True,
                      "ref": im.get("dockerImageReference") or ""})
    return plan(c, filas)


def destino(c, r):
    host = c["quay"] or "<quay>"
    if r["estado"] == "huérfana":
        return "—"
    org, repo = (c["org"], f"{r['ns']}-{r['is']}") if c["org"] else (r["ns"], r["is"])
    tag = r["tag"] if r["rev"] == 0 else f"{r['tag']}-{r['digest'][7:19]}"
    return f"{host}/{org}/{repo}:{tag}"


def origen(r):
    return f"{REG}/{r['ns']}/{r['is']}@{r['digest']}" if r["ns"] else f"{REG}/<repositorio>@{r['digest']}"


def plan(c, filas):
    cand = [r for r in filas if r.get("candidata")]
    tope = c["rangos"][-1] if c["rangos"] else 12
    for r in cand:
        r["destino"] = destino(c, r)
        r["ola"] = 1 if r["estado"] == "huérfana" or r["meses"] >= tope else 2
        r["riesgo"] = "medio" if r["historial"] else "bajo"
        r["referencias"] = ("rollback de " + ", ".join(r["historial"]) + " apuntaría a la imagen retirada: actualizar a Quay o aceptar") if r["historial"] else "ninguna (no está en uso)"
        r["accion"] = "copiar a Quay" if r["ns"] else "podar (sin ImageStream: el registro ya no la sirve)"
        r["comando"] = f"skopeo copy --all --preserve-digests docker://{origen(r)} docker://{r['destino']}" if r["ns"] else f"# {r.get('ref') or r['digest']}: la quita oc adm prune images"
        r["id"] = f"{r['ns']}/{r['is']}:{r['tag']}" if r["ns"] else f"huérfana@{r['digest'][7:19]}"
    cand.sort(key=lambda r: (r["ola"], r["riesgo"] != "bajo", -r["bytes"]))
    # poda posterior: etiqueta completa candidata → se quita la etiqueta; revisiones viejas → pruner con historial acotado
    tags = {}
    for r in filas:
        if r["ns"]:
            tags.setdefault((r["ns"], r["is"], r["tag"]), []).append(r)
    poda = [f"oc tag -d {ns}/{is_}:{t}" for (ns, is_, t), rs in sorted(tags.items()) if all(x.get("candidata") for x in rs)]
    horas = int(c["meses"] * 30.44 * 24)
    poda += [f"oc adm prune images --keep-tag-revisions=1 --keep-younger-than={horas}h --confirm   # revisiones antiguas y huérfanas, después de verificar la copia en Quay"]
    capas_cand = set().union(*[r["capas"] for r in cand] or [set()])
    capas_resto = set().union(*[r["capas"] for r in filas if not r.get("candidata")] or [set()])
    resumen = {}
    for r in filas:
        x = resumen.setdefault(r["estado"], {"rango": r["estado"], "revisiones": 0, "bytes": 0})
        x["revisiones"] += 1
        x["bytes"] += r["bytes"]
    por_ns = {}
    for r in cand:
        x = por_ns.setdefault(r["ns"] or "(huérfanas)", {"namespace": r["ns"] or "(huérfanas)", "candidatas": 0, "bytes": 0})
        x["candidatas"] += 1
        x["bytes"] += r["bytes"]
    orden = ["en uso", "referenciada"] + [rango(c, m) for m in [0] + c["rangos"]] + ["huérfana"]
    return {
        "cluster": c["cluster"], "medido": iso(AHORA), "meses_umbral": c["meses"], "rangos_meses": c["rangos"],
        "namespaces": {"incluir": c["incluir"], "excluir": c["excluir"]}, "quay": c["quay"] or "", "quay_org": c["org"] or "(una por namespace)",
        "revisiones": len(filas), "candidatas": len(cand), "bytes_candidatas": sum(r["bytes"] for r in cand),
        "capas_exclusivas": len(capas_cand - capas_resto),
        "rangos": sorted(resumen.values(), key=lambda x: orden.index(x["rango"]) if x["rango"] in orden else 99),
        "por_namespace": sorted(por_ns.values(), key=lambda x: -x["bytes"]),
        "filas": filas, "plan": cand, "poda": poda,
    }


def mb(b):
    return f"{b / 2**20:,.0f} MB".replace(",", ".")


def escribir(res, salida):
    os.makedirs(salida, exist_ok=True)
    cols = ["ns", "is", "tag", "rev", "digest", "estado", "meses", "base", "uso", "fuente", "push", "construida", "bytes", "candidata", "ola", "riesgo", "accion", "destino", "referencias", "comando"]
    with open(f"{salida}/plan.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in res["filas"]:
            w.writerow([iso(r.get(k)) if isinstance(r.get(k), datetime) else r.get(k, "") for k in cols])
    lim = {k: v for k, v in res.items() if k != "filas"}
    with open(f"{salida}/plan.json", "w") as f:
        json.dump(lim, f, ensure_ascii=False, indent=1, default=lambda x: iso(x) if isinstance(x, datetime) else sorted(x))
    q = res["quay"] or "<quay>"
    L = [f"# Imágenes de OpenShift: uso, antigüedad y movilidad a Quay · {res['cluster']}", "",
         f"- Medido: {res['medido']} · umbral: sin uso hace {res['meses_umbral']} meses o más · rangos: {', '.join(map(str, res['rangos_meses']))} meses",
         f"- Namespaces: {', '.join(res['namespaces']['incluir']) or 'todos los de aplicaciones'} (excluidos: {', '.join(res['namespaces']['excluir'])})",
         f"- Revisiones evaluadas: {res['revisiones']} · candidatas a Quay: {res['candidatas']} ({mb(res['bytes_candidatas'])}; capas que se liberan solo con ellas: {res['capas_exclusivas']})",
         f"- Destino: {q} · organización: {res['quay_org']}", "",
         "## Por rango", "", "| Rango | Revisiones | Tamaño |", "|---|---:|---:|"]
    L += [f"| {x['rango']} | {x['revisiones']} | {mb(x['bytes'])} |" for x in res["rangos"]]
    if res["por_namespace"]:
        L += ["", "## Candidatas por namespace", "", "| Namespace | Candidatas | Tamaño |", "|---|---:|---:|"]
        L += [f"| {x['namespace']} | {x['candidatas']} | {mb(x['bytes'])} |" for x in res["por_namespace"]]
    L += ["", "## Plan de movilidad", ""]
    if not res["plan"]:
        L += ["No hay imágenes candidatas con este umbral."]
    else:
        L += ["Orden: ola 1 (huérfanas y las de más tiempo sin uso) y luego ola 2; dentro de cada ola primero las de riesgo bajo y las más grandes.",
              "Ninguna candidata está en uso ni referenciada por una carga vigente. Se ejecuta con «OpenShift · Mover imágenes a Quay» (con aprobación).", "",
              "| Ola | Imagen | Rev. | Sin uso | Tamaño | Riesgo | Acción | Destino en Quay | Referencias a actualizar |", "|---:|---|---:|---|---:|---|---|---|---|"]
        L += [f"| {r['ola']} | {r['id']} | {r['rev']} | {r['meses']} meses ({r['base']}) | {mb(r['bytes'])} | {r['riesgo']} | {r['accion']} | `{r['destino']}` | {r['referencias']} |" for r in res["plan"]]
        L += ["", "### Comandos (equivalentes manuales)", "", "```"] + [r["comando"] for r in res["plan"]] + ["```"]
    L += ["", "## Poda posterior en el registro interno", "", "Solo después de verificar el digest en Quay:", "", "```"] + res["poda"] + ["```", ""]
    with open(f"{salida}/plan.md", "w") as f:
        f.write("\n".join(L))


def publicar(res, salida):
    g, t = os.environ.get("G", "").rstrip("/"), os.environ.get("T", "")
    if not (g and t):
        return "sin token"
    api = f"{g}/api/v1/repos/repositorio/openshift-manifiestos/contents/imagenes-movilidad/{res['cluster']}/"
    hd = {"Authorization": "token " + t, "Content-Type": "application/json"}
    ok = 0
    for n in ("plan.md", "plan.csv", "plan.json"):
        sha = None
        try:
            sha = json.load(urllib.request.urlopen(urllib.request.Request(api + n, headers=hd), timeout=30)).get("sha")
        except Exception:  # noqa: BLE001 - todavía no existe
            pass
        body = {"content": base64.b64encode(open(f"{salida}/{n}", "rb").read()).decode(), "message": os.environ.get("MSG", "FaceOps · imágenes")}
        if sha:
            body["sha"] = sha
        for intento in range(3):
            try:
                urllib.request.urlopen(urllib.request.Request(api + n, data=json.dumps(body).encode(), headers=hd, method="PUT" if sha else "POST"), timeout=60)
                ok += 1
                break
            except Exception:  # noqa: BLE001
                time.sleep(5)
    return f"git ok {ok}"


def resumen_stats(res, git):
    return {"cluster": res["cluster"], "medido": res["medido"], "meses_umbral": res["meses_umbral"], "rangos_meses": res["rangos_meses"],
            "revisiones": res["revisiones"], "candidatas": res["candidatas"], "mb_candidatas": round(res["bytes_candidatas"] / 2**20),
            "capas_exclusivas": res["capas_exclusivas"], "quay": res["quay"], "quay_org": res["quay_org"],
            "namespaces": res["namespaces"],
            "rangos": [{"rango": x["rango"], "revisiones": x["revisiones"], "mb": round(x["bytes"] / 2**20)} for x in res["rangos"]],
            "por_namespace": [{"namespace": x["namespace"], "candidatas": x["candidatas"], "mb": round(x["bytes"] / 2**20)} for x in res["por_namespace"]][:20],
            "plan": [{"id": r["id"], "rev": r["rev"], "digest": r["digest"][:19], "ola": r["ola"], "riesgo": r["riesgo"], "meses": r["meses"], "base": r["base"],
                      "mb": round(r["bytes"] / 2**20), "accion": r["accion"], "destino": r["destino"], "referencias": r["referencias"], "comando": r["comando"]} for r in res["plan"]][:60],
            "detalle": [{"id": f"{r['ns']}/{r['is']}:{r['tag']}" if r["ns"] else f"huérfana@{r['digest'][7:19]}", "rev": r["rev"], "estado": r["estado"],
                         "fuente": r["fuente"], "candidata": bool(r.get("candidata"))} for r in sorted(res["filas"], key=lambda x: (x["ns"], x["is"], x["tag"], x["rev"]))][:150],
            "poda": res["poda"][:30], "informe_git": git.startswith("git ok") and git != "git ok 0",
            "git": f"repositorio/openshift-manifiestos/imagenes-movilidad/{res['cluster']}/plan.md" if git.startswith("git ok") and git != "git ok 0" else git}


# ── mover: Job de skopeo dentro del clúster (lee el registro interno con una cuenta de servicio de solo lectura)
SCRIPT = r'''set -u
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
mkdir -p /tmp/certs && cp /ca/service-ca.crt /tmp/certs/ca.crt
while read -r ID ORIGEN DESTINO DIG; do
  [ -z "$ID" ] && continue
  if skopeo copy -q --all --preserve-digests --retry-times 2 --src-creds "sa:$TOKEN" --src-cert-dir /tmp/certs \
       --dest-authfile /auth/auth.json --dest-tls-verify="$TLS" "docker://$ORIGEN" "docker://$DESTINO" >/tmp/log 2>&1; then
    D2=$(skopeo inspect --raw --authfile /auth/auth.json --tls-verify="$TLS" "docker://$DESTINO" 2>/dev/null | sha256sum | cut -d' ' -f1)
    if [ "sha256:$D2" = "$DIG" ]; then echo "MQ OK $ID $DIG"; else echo "MQ DIGEST $ID esperado $DIG obtenido sha256:$D2"; fi
  else
    echo "MQ FALLA $ID $(tail -c 300 /tmp/log | tr '\n' ' ' | sed 's/sa:[^ ]*/sa:***/g')"
  fi
done < /plan/lista
echo "MQ FIN"
'''


def aplicar(api, ruta, obj):
    nombre = obj["metadata"]["name"]
    if pedir(api, f"{ruta}/{nombre}", ok=(404,)):
        pedir(api, f"{ruta}/{nombre}", "DELETE", ok=(404,))
        for _ in range(30):
            if not pedir(api, f"{ruta}/{nombre}", ok=(404,)):
                break
            time.sleep(2)
    return pedir(api, ruta, "POST", obj)


def mover(api, c, res):
    sel = lista(os.environ.get("MQ_SELECCION"))
    if not c["quay"]:
        raise RuntimeError("falta el host de Quay (mq_quay)")
    todas = "todas" in sel
    elegir = [r for r in res["plan"] if r["ns"] and (todas or r["id"] in sel or f"{r['ns']}/{r['is']}" in sel)]
    vistos = {r["id"] for r in elegir} | {f"{r['ns']}/{r['is']}" for r in elegir}
    omitidas = [s for s in sel if s != "todas" and s not in vistos]
    out = {"cluster": c["cluster"], "quay": c["quay"], "seleccion": sel, "omitidas_no_candidatas": omitidas, "copiadas": [], "fallidas": [], "retiradas": [], "retiro_pendiente": []}
    if not elegir:
        return out
    # Credenciales de Quay → auth.json en un Secret efímero del namespace de movilidad (se borra al terminar)
    auth = {"auths": {c["quay"]: {"auth": base64.b64encode(f"{os.environ['QUAY_USUARIO']}:{os.environ['QUAY_CLAVE']}".encode()).decode()}}}
    ns = NS_MOV
    if not pedir(api, f"/api/v1/namespaces/{ns}", ok=(404,)):
        pedir(api, "/api/v1/namespaces", "POST", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns, "labels": {"faceops.invoke.cl/uso": "movilidad-imagenes"}}})
    pedir(api, f"/api/v1/namespaces/{ns}/serviceaccounts", "POST", {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "mover"}}, ok=(409,))
    pedir(api, "/apis/rbac.authorization.k8s.io/v1/clusterrolebindings", "POST", {
        "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "faceops-movilidad-lectura"},
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "registry-viewer"},
        "subjects": [{"kind": "ServiceAccount", "name": "mover", "namespace": ns}]}, ok=(409,))
    aplicar(api, f"/api/v1/namespaces/{ns}/secrets", {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "quay-auth"}, "type": "Opaque",
                                                       "data": {"auth.json": base64.b64encode(json.dumps(auth).encode()).decode()}})
    aplicar(api, f"/api/v1/namespaces/{ns}/configmaps", {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "service-ca",
                                                          "annotations": {"service.beta.openshift.io/inject-cabundle": "true"}}, "data": {}})
    lista_ = "".join(f"{r['id']}#{r['rev']} {origen(r)} {r['destino']} {r['digest']}\n" for r in elegir)
    aplicar(api, f"/api/v1/namespaces/{ns}/configmaps", {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "plan"}, "data": {"lista": lista_}})
    pedir(api, f"/apis/batch/v1/namespaces/{ns}/jobs/mover?propagationPolicy=Background", "DELETE", ok=(404,))
    time.sleep(5)
    pedir(api, f"/apis/batch/v1/namespaces/{ns}/jobs", "POST", {
        "apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "mover"},
        "spec": {"backoffLimit": 0, "ttlSecondsAfterFinished": 3600, "activeDeadlineSeconds": int(os.environ.get("MQ_TIMEOUT") or 3000),
                 "template": {"spec": {"serviceAccountName": "mover", "restartPolicy": "Never",
                                       "containers": [{"name": "skopeo", "image": os.environ.get("MQ_IMAGEN") or "registry.redhat.io/rhel9/skopeo:latest",
                                                       "command": ["/bin/bash", "-c", SCRIPT], "env": [{"name": "TLS", "value": "true" if os.environ.get("MQ_TLS", "1") == "1" else "false"}],
                                                       "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"memory": "1Gi"}},
                                                       "volumeMounts": [{"name": "auth", "mountPath": "/auth", "readOnly": True}, {"name": "ca", "mountPath": "/ca"},
                                                                        {"name": "plan", "mountPath": "/plan"}]}],
                                       "volumes": [{"name": "auth", "secret": {"secretName": "quay-auth"}}, {"name": "ca", "configMap": {"name": "service-ca"}},
                                                   {"name": "plan", "configMap": {"name": "plan"}}]}}}})
    log, fin = "", time.time() + int(os.environ.get("MQ_TIMEOUT") or 3000)
    try:
        while time.time() < fin:
            time.sleep(10)
            j = pedir(api, f"/apis/batch/v1/namespaces/{ns}/jobs/mover")
            st = j.get("status") or {}
            if st.get("succeeded") or st.get("failed"):
                pods = pedir(api, f"/api/v1/namespaces/{ns}/pods?labelSelector=job-name%3Dmover").get("items") or []
                if pods:
                    log = pedir(api, f"/api/v1/namespaces/{ns}/pods/{pods[0]['metadata']['name']}/log", crudo=True)
                break
        else:
            out["fallidas"].append({"id": "*", "motivo": "el Job de copia no terminó a tiempo"})
    finally:   # las credenciales de Quay no quedan en el clúster
        pedir(api, f"/api/v1/namespaces/{ns}/secrets/quay-auth", "DELETE", ok=(404,))
    ok = set()
    for linea in log.splitlines():
        p = linea.split(" ", 3)
        if len(p) >= 3 and p[0] == "MQ" and p[1] == "OK":
            ok.add(p[2])
        elif len(p) >= 3 and p[0] == "MQ" and p[1] in ("FALLA", "DIGEST"):
            out["fallidas"].append({"id": p[2], "motivo": (p[3] if len(p) > 3 else p[1])[:300]})
    if "MQ FIN" not in log and not out["fallidas"]:
        out["fallidas"].append({"id": "*", "motivo": "el Job de copia terminó sin informar (revisar el pod en faceops-movilidad)"})
    for r in elegir:
        if f"{r['id']}#{r['rev']}" in ok:
            out["copiadas"].append({"id": r["id"], "rev": r["rev"], "digest": r["digest"][:19], "destino": r["destino"], "mb": round(r["bytes"] / 2**20)})
    # Retiro del registro interno: solo etiquetas cuyas revisiones TODAS se copiaron y verificaron; lo demás queda para el pruner
    if os.environ.get("MQ_RETIRAR") == "1":
        copiadas = {(r["ns"], r["is"], r["tag"], r["digest"]) for r in elegir if f"{r['id']}#{r['rev']}" in ok}
        por_tag = {}
        for r in res["filas"]:
            if r["ns"]:
                por_tag.setdefault((r["ns"], r["is"], r["tag"]), []).append(r)
        for (ns_, is_, t), rs in sorted(por_tag.items()):
            if not any((ns_, is_, t, x["digest"]) in copiadas for x in rs):
                continue
            if all((ns_, is_, t, x["digest"]) in copiadas for x in rs):
                pedir(api, f"/apis/image.openshift.io/v1/namespaces/{ns_}/imagestreamtags/{is_}:{t}", "DELETE", ok=(404,))
                out["retiradas"].append(f"{ns_}/{is_}:{t}")
            else:
                out["retiro_pendiente"].append(f"{ns_}/{is_}:{t} (revisiones antiguas: las quita el pruner de imágenes)")
    return out


def main():
    modo, api, salida = sys.argv[1], sys.argv[2].rstrip("/"), sys.argv[3]
    c = cfg()
    res = evaluar(api, c)
    if modo == "evaluar":
        escribir(res, salida)
        print(json.dumps(resumen_stats(res, publicar(res, salida)), ensure_ascii=False, default=str))
    elif modo == "mover":
        out = mover(api, c, res)
        out["plan_vigente"] = res["candidatas"]
        print(json.dumps(out, ensure_ascii=False, default=str))
        if out["fallidas"]:
            sys.exit(2)
    else:
        raise RuntimeError("modo: evaluar o mover")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e)[:500]}, ensure_ascii=False))
        sys.exit(1)
