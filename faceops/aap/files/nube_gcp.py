#!/usr/bin/env python3
"""Máquinas y productos de una nube GCP desde FaceOps (corre en el execution environment de AAP).
Uso: nube_gcp.py <opciones|operar|producto> <proyecto> <entrada.json> <salida.json>
La credencial llega por la variable que inyecta la credencial de AAP de la conexión (GCP_SERVICE_ACCOUNT_FILE o
GCE_CREDENTIALS_FILE_PATH). Nunca escribe ni imprime la cuenta de servicio ni tokens: la salida es un JSON con el
resultado y, si algo falla, el motivo sin secretos.
  opciones  solo lectura: regiones, zonas, tipos de máquina, imágenes, redes, subredes, cuentas de servicio, reglas de
            firewall, máquinas con sus discos, precios de lista de la región (Cloud Billing Catalog), permisos que le
            faltan a la cuenta de servicio de la conexión y salud de los productos creados por FaceOps
  operar    una acción sobre una máquina: encender, apagar, reiniciar, redimensionar, disco_agregar, disco_ampliar,
            etiquetas, snapshot, firewall, eliminar (espera cada operación y verifica el resultado)
  producto  crear, eliminar o leer la salud de un producto (pasos REST que arma FaceOps desde datos/nube-productos.yml)"""
import json
import os
import re
import sys
import time

C = "https://compute.googleapis.com/compute/v1/projects/"
FAMILIAS = ("e2", "n2", "n2d", "n4", "t2d", "c3")


def _limpio(txt):
    return re.sub(r"(?i)(private_key|client_secret|access_token|bearer)\S*", "[oculto]", str(txt))[:600]


class G:
    def __init__(self, proyecto):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        f = os.environ.get("GCP_SERVICE_ACCOUNT_FILE") or os.environ.get("GCE_CREDENTIALS_FILE_PATH") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not f or not os.path.exists(f):
            raise RuntimeError("La credencial de AAP de la nube no entregó la cuenta de servicio")
        cr = service_account.Credentials.from_service_account_file(f, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.s = AuthorizedSession(cr)
        self.p = proyecto or cr.project_id
        self.cuenta = cr.service_account_email

    def r(self, metodo, url, cuerpo=None, **params):
        r = self.s.request(metodo, url, json=cuerpo, params=params or None, timeout=120)
        try:
            d = r.json() if r.content else {}
        except ValueError:
            d = {"texto": r.text[:300]}
        return r.status_code, d

    def todos(self, url, clave="items", **params):
        out, tok = [], None
        while True:
            cod, d = self.r("GET", url, **dict(params, **({"pageToken": tok} if tok else {})))
            if cod != 200:
                return None, f"{cod} {_msg(d)}"
            it = d.get(clave)
            if isinstance(it, dict):   # aggregatedList
                for v in it.values():
                    for lista in v.values():
                        if isinstance(lista, list):
                            out += lista
            elif it:
                out += it
            tok = d.get("nextPageToken")
            if not tok:
                return out, ""

    def esperar(self, cod, d, minutos=15):
        """Espera una operación larga (Compute, SQL, o google.longrunning) hasta que termine. Devuelve (ok, motivo)."""
        if cod not in (200, 201, 202, 204):
            return False, f"{cod} {_msg(d)}"
        fin = time.time() + minutos * 60
        op = d
        while True:
            if op.get("kind", "").endswith("#operation") or ("status" in op and "selfLink" in op and "operationType" in op):
                if op.get("status") == "DONE":
                    err = (op.get("error") or {}).get("errors") or []
                    return (not err), "; ".join(e.get("message", "") for e in err)[:400]
                url = op.get("selfLink")
            elif "/operations/" in str(op.get("name", "")):   # google.longrunning (sin «done» mientras sigue en curso)
                if op.get("done"):
                    return ("error" not in op), _msg(op.get("error") or {})
                url = op.get("_base", "") + op["name"]
            else:
                return True, ""   # respuesta síncrona
            if time.time() > fin:
                return True, "sigue en curso en la nube (se verá en su salud)"
            time.sleep(5)
            base = op.get("_base", "")
            cod, op = self.r("GET", url)
            if cod != 200:
                return False, f"{cod} {_msg(op)}"
            op["_base"] = base


def _msg(d):
    e = (d or {}).get("error") if isinstance(d, dict) else None
    if isinstance(e, dict):
        return _limpio(e.get("message") or e.get("status") or json.dumps(e)[:200])
    return _limpio(json.dumps(d)[:200]) if d else ""


def _base_lro(url):
    m = re.match(r"(https://[^/]+/v[0-9a-z]+/)", url)
    return m.group(1) if m else ""


def _campo(d, ruta):
    for k in ruta.split("."):
        d = d.get(k) if isinstance(d, dict) else None
    return d


def _nombre(u):
    return (u or "").rsplit("/", 1)[-1]


# ------------------------------------------------------------------ opciones (solo lectura)
def _precios(g, regiones):
    """Precios de lista por hora de la región (USD): {region: {familia: [vcpu, gb], disco: {tipo: gb-mes}, ip: hora}}."""
    skus, e = g.todos("https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus", clave="skus", pageSize=5000)
    if skus is None:
        return {}, f"no se leyeron los precios: {e}"
    out = {r: {"disco": {}} for r in regiones}

    def precio(s):
        try:
            tr = s["pricingInfo"][0]["pricingExpression"]["tieredRates"]
            return max(float(t["unitPrice"].get("units") or 0) + float(t["unitPrice"].get("nanos") or 0) / 1e9 for t in tr)
        except (KeyError, IndexError, ValueError):
            return None
    for s in skus:
        d = s.get("description", "")
        if any(x in d for x in ("Commit", "Spot", "Preemptible", "Sole Tenancy", "Custom", "Extended")):
            continue
        for r in set(s.get("serviceRegions") or []) & set(regiones):
            for fam in FAMILIAS:
                for i, parte in enumerate(("Core", "Ram")):
                    if re.match(rf"^{fam.upper()}( AMD| Intel)? (Predefined )?Instance {parte} running in", d):
                        out[r].setdefault(fam, [None, None])[i] = precio(s)
            for tipo, pat in (("pd-balanced", r"^Balanced PD Capacity"), ("pd-ssd", r"^SSD backed PD Capacity"), ("pd-standard", r"^Storage PD Capacity"),
                              ("hyperdisk-balanced", r"^Hyperdisk Balanced Capacity")):
                if re.match(pat, d):
                    out[r]["disco"][tipo] = precio(s)
            if re.match(r"^External IP Charge on a Standard VM", d):
                out[r]["ip"] = precio(s)
    for r in out:
        out[r] = {k: v for k, v in out[r].items() if k == "disco" or k == "ip" or (isinstance(v, list) and None not in v)}
    return out, ""


def opciones(g, ent):
    res = {"ok": True, "proyecto": g.p, "cuenta_servicio": g.cuenta, "errores": {}}
    regs, e = g.todos(f"{C}{g.p}/regions")
    if regs is None:
        return {"ok": False, "error": f"no se pudo leer la nube: {e}"}
    res["regiones"] = sorted(r["name"] for r in regs if r.get("status") == "UP")
    zon, e = g.todos(f"{C}{g.p}/zones")
    res["zonas"] = {}
    for z in zon or []:
        if z.get("status") == "UP":
            res["zonas"].setdefault(_nombre(z.get("region")), []).append(z["name"])
    for v in res["zonas"].values():
        v.sort()
    pedidas = [r for r in dict.fromkeys(ent.get("regiones") or []) if r in res["zonas"]][:4]
    res["tipos"] = {}
    for r in pedidas:
        for z in res["zonas"][r]:
            ts, e = g.todos(f"{C}{g.p}/zones/{z}/machineTypes", filter='name eq "(e2|n2|n2d|n4|t2d|c3)-.*"', maxResults=500)
            if ts is None:
                res["errores"]["tipos"] = e
                continue
            for t in ts:
                if t.get("deprecated") or t["guestCpus"] > 32 or re.search(r"-(lssd|metal)$", t["name"]):
                    continue
                x = res["tipos"].setdefault(t["name"], {"cpu": t["guestCpus"], "ram": round(t["memoryMb"] / 1024, 2), "zonas": []})
                x["zonas"].append(z)
                if t.get("isSharedCpu"):
                    x["compartido"] = True
    res["imagenes"] = {}
    for im in ent.get("imagenes") or []:
        m = re.match(r"projects/([^/]+)/global/images/family/(.+)$", im.get("imagen", ""))
        if not m:
            continue
        cod, d = g.r("GET", f"{C}{m.group(1)}/global/images/family/{m.group(2)}")
        res["imagenes"][im["id"]] = {"ok": cod == 200, "nombre": d.get("name", ""), "disco_gb": int(d.get("diskSizeGb") or 0)} if cod == 200 else {"ok": False}
    redes, e = g.todos(f"{C}{g.p}/global/networks")
    res["redes"] = [{"nombre": n["name"], "auto": bool(n.get("autoCreateSubnetworks"))} for n in redes or []]
    if redes is None:
        res["errores"]["redes"] = e
    sub, e = g.todos(f"{C}{g.p}/aggregated/subnetworks")
    res["subredes"] = [{"nombre": s["name"], "red": _nombre(s.get("network")), "region": _nombre(s.get("region")), "rango": s.get("ipCidrRange", "")}
                       for s in sub or [] if _nombre(s.get("region")) in (pedidas or res["regiones"])]
    cods, d = g.r("GET", f"https://iam.googleapis.com/v1/projects/{g.p}/serviceAccounts", pageSize=100)
    cuentas = [a["email"] for a in d.get("accounts", []) if not a.get("disabled")] if cods == 200 else []
    if cods != 200:
        res["errores"]["cuentas"] = f"{cods} {_msg(d)}"
        codp, dp = g.r("GET", f"https://cloudresourcemanager.googleapis.com/v1/projects/{g.p}")
        if codp == 200 and dp.get("projectNumber"):
            cuentas = [f"{dp['projectNumber']}-compute@developer.gserviceaccount.com"]
    res["cuentas"] = cuentas
    fw, e = g.todos(f"{C}{g.p}/global/firewalls")
    res["firewall"] = [{"nombre": f["name"], "red": _nombre(f.get("network")), "direccion": f.get("direction"), "etiquetas": f.get("targetTags") or [],
                        "origenes": f.get("sourceRanges") or [], "permite": [f"{a.get('IPProtocol')}:{','.join(a.get('ports') or ['todos'])}" for a in f.get("allowed") or []],
                        "deshabilitada": bool(f.get("disabled"))} for f in fw or []]
    vms, e = g.todos(f"{C}{g.p}/aggregated/instances")
    dis, _e = g.todos(f"{C}{g.p}/aggregated/disks")
    dmap = {d["name"]: d for d in dis or []}
    res["maquinas"] = []
    for v in vms or []:
        ds = []
        for d in v.get("disks", []):
            n = _nombre(d.get("source"))
            ds.append({"nombre": n, "gb": int(d.get("diskSizeGb") or (dmap.get(n) or {}).get("sizeGb") or 0), "arranque": bool(d.get("boot")),
                       "tipo": _nombre((dmap.get(n) or {}).get("type"))})
        ni = (v.get("networkInterfaces") or [{}])[0]
        res["maquinas"].append({"nombre": v["name"], "zona": _nombre(v.get("zone")), "estado": v.get("status"), "tipo": _nombre(v.get("machineType")),
                                "etiquetas": v.get("labels") or {}, "etiquetas_red": (v.get("tags") or {}).get("items") or [], "discos": ds,
                                "cuenta": ((v.get("serviceAccounts") or [{}])[0]).get("email", ""), "red": _nombre(ni.get("network")),
                                "subred": _nombre(ni.get("subnetwork")), "ip": ni.get("networkIP", ""),
                                "publica": next((a.get("natIP") for a in ni.get("accessConfigs") or [] if a.get("natIP")), ""),
                                "creada": v.get("creationTimestamp", "")})
    if vms is None:
        res["errores"]["maquinas"] = e
    sn, e = g.todos(f"{C}{g.p}/global/snapshots")
    res["snapshots"] = [{"nombre": s["name"], "disco": _nombre(s.get("sourceDisk")), "gb": int(s.get("diskSizeGb") or 0), "maquina": (s.get("labels") or {}).get("faceops-vm", ""),
                         "creado": s.get("creationTimestamp", "")} for s in sn or []]
    precios, e = _precios(g, pedidas)
    res["precios"] = precios
    if e:
        res["errores"]["precios"] = e
    perms = sorted(set(ent.get("permisos") or []))
    tiene = set()
    for i in range(0, len(perms), 90):
        cod, d = g.r("POST", f"https://cloudresourcemanager.googleapis.com/v1/projects/{g.p}:testIamPermissions", {"permissions": perms[i:i + 90]})
        if cod != 200:
            res["errores"]["permisos"] = f"{cod} {_msg(d)}"
            break
        tiene |= set(d.get("permissions") or [])
    res["permisos_faltan"] = [p for p in perms if p not in tiene] if "permisos" not in res["errores"] else None
    cod, d = g.r("POST", f"https://cloudresourcemanager.googleapis.com/v1/projects/{g.p}:getIamPolicy", {})
    res["roles"] = sorted({b["role"] for b in d.get("bindings", []) if f"serviceAccount:{g.cuenta}" in b.get("members", [])}) if cod == 200 else None
    servicios, e = g.todos(f"https://serviceusage.googleapis.com/v1/projects/{g.p}/services", clave="services", filter="state:ENABLED", pageSize=200)
    res["apis"] = sorted(_nombre(s.get("name")) for s in servicios or []) if servicios is not None else None
    res["productos"] = {}
    for p in ent.get("productos") or []:
        res["productos"][p["clave"]] = _salud(g, p)
    return res


def _salud(g, p):
    cod, d = g.r("GET", p["url"])
    if cod == 404:
        return {"salud": "no existe"}
    if cod != 200:
        return {"salud": "sin lectura", "motivo": f"{cod} {_msg(d)}"}
    if "?filter=" in p["url"]:   # listado: existe si trae algo
        d = (d.get("endpoints") or [{}])[0]
    if not p.get("campo"):
        return {"salud": "operativo"}
    v = _campo(d, p["campo"])
    return {"salud": "operativo" if v in (p.get("ok") or []) else ("en creación" if v in ("CREATING", "PENDING_CREATE", "PROVISIONING", "STAGING", "RECONCILING", None) else "con problemas"),
            "estado": v}


# ------------------------------------------------------------------ operar una máquina
def operar(g, e):
    z, m, ac = e["zona"], e["maquina"], e["accion"]
    base = f"{C}{g.p}/zones/{z}"
    cod, vm = g.r("GET", f"{base}/instances/{m}")
    if cod != 200 and not (ac == "eliminar" and cod == 404):
        return {"ok": False, "error": f"no existe la máquina {m} en {z} ({cod})"}
    hecho = []

    def paso(metodo, url, cuerpo=None, que=""):
        ok, mot = g.esperar(*g.r(metodo, url, cuerpo))
        if not ok:
            raise RuntimeError(f"{que or url.rsplit('/', 1)[-1]}: {mot}")
        hecho.append(que + (f" ({mot})" if mot else ""))

    def estado():
        return g.r("GET", f"{base}/instances/{m}")[1].get("status")
    try:
        if ac in ("encender", "apagar", "reiniciar"):
            st = estado()
            if ac == "encender" and st == "RUNNING" or ac == "apagar" and st == "TERMINATED":
                hecho.append(f"ya estaba {'encendida' if ac == 'encender' else 'apagada'}: sin cambios")
            else:
                paso("POST", f"{base}/instances/{m}/" + {"encender": "start", "apagar": "stop", "reiniciar": "reset"}[ac], que=ac)
        elif ac == "redimensionar":
            nuevo = e["tipo"]
            if _nombre(vm.get("machineType")) == nuevo:
                hecho.append(f"ya es {nuevo}: sin cambios")
            else:
                estaba = vm.get("status")
                if estaba != "TERMINATED":
                    if not e.get("detener"):
                        return {"ok": False, "error": "redimensionar requiere apagar la máquina: confirma el apagado"}
                    paso("POST", f"{base}/instances/{m}/stop", que="apagar")
                paso("POST", f"{base}/instances/{m}/setMachineType", {"machineType": f"zones/{z}/machineTypes/{nuevo}"}, que=f"tipo {nuevo}")
                if estaba == "RUNNING":
                    paso("POST", f"{base}/instances/{m}/start", que="encender")
        elif ac == "disco_agregar":
            d = e["disco"]
            if d["nombre"] in [_nombre(x.get("source")) for x in vm.get("disks", [])]:
                hecho.append(f"el disco {d['nombre']} ya está conectado: sin cambios")
            else:
                cod, _x = g.r("GET", f"{base}/disks/{d['nombre']}")
                if cod == 404:
                    paso("POST", f"{base}/disks", {"name": d["nombre"], "sizeGb": str(d["gb"]), "type": f"zones/{z}/diskTypes/{d['tipo']}",
                                                   "labels": {"origen": "faceops", "faceops-vm": m}}, que=f"crear disco {d['nombre']} de {d['gb']} GB")
                paso("POST", f"{base}/instances/{m}/attachDisk", {"source": f"zones/{z}/disks/{d['nombre']}", "autoDelete": True}, que="conectar disco")
        elif ac == "disco_ampliar":
            d = e["disco"]
            cod, x = g.r("GET", f"{base}/disks/{d['nombre']}")
            if cod != 200:
                return {"ok": False, "error": f"no existe el disco {d['nombre']}"}
            if int(x.get("sizeGb") or 0) >= int(d["gb"]):
                if int(x.get("sizeGb") or 0) > int(d["gb"]):
                    return {"ok": False, "error": f"un disco solo se amplía: {d['nombre']} ya tiene {x.get('sizeGb')} GB"}
                hecho.append(f"ya tiene {d['gb']} GB: sin cambios")
            else:
                paso("POST", f"{base}/disks/{d['nombre']}/resize", {"sizeGb": str(d["gb"])}, que=f"{d['nombre']} a {d['gb']} GB")
        elif ac == "etiquetas":
            if e.get("etiquetas") is not None:
                paso("POST", f"{base}/instances/{m}/setLabels", {"labels": e["etiquetas"], "labelFingerprint": vm.get("labelFingerprint")}, que="etiquetas")
            if e.get("etiquetas_red") is not None:
                cod, vm = g.r("GET", f"{base}/instances/{m}")
                paso("POST", f"{base}/instances/{m}/setTags", {"items": e["etiquetas_red"], "fingerprint": (vm.get("tags") or {}).get("fingerprint")}, que="etiquetas de red")
        elif ac == "snapshot":
            d = e["snapshot"]
            cod, _x = g.r("GET", f"{C}{g.p}/global/snapshots/{d['nombre']}")
            if cod == 200:
                hecho.append(f"el snapshot {d['nombre']} ya existe: sin cambios")
            else:
                paso("POST", f"{base}/disks/{d['disco']}/createSnapshot", {"name": d["nombre"], "labels": {"origen": "faceops", "faceops-vm": m}},
                     que=f"snapshot {d['nombre']} de {d['disco']}")
        elif ac == "firewall":
            f = e["firewall"]
            regla, tag = f["nombre"], f["etiqueta"]
            if f.get("modo") == "quitar":
                tags = [t for t in (vm.get("tags") or {}).get("items") or [] if t != tag]
                paso("POST", f"{base}/instances/{m}/setTags", {"items": tags, "fingerprint": (vm.get("tags") or {}).get("fingerprint")}, que=f"quitar la etiqueta {tag}")
            else:
                red = (vm.get("networkInterfaces") or [{}])[0].get("network")
                cuerpo = {"name": regla, "network": red, "direction": "INGRESS", "targetTags": [tag], "sourceRanges": f["origenes"],
                          "allowed": [{"IPProtocol": f["protocolo"], **({"ports": f["puertos"]} if f.get("puertos") else {})}],
                          "description": f"FaceOps · {m}"}
                cod, x = g.r("GET", f"{C}{g.p}/global/firewalls/{regla}")
                if cod == 200:
                    paso("PATCH", f"{C}{g.p}/global/firewalls/{regla}", {k: cuerpo[k] for k in ("sourceRanges", "allowed", "targetTags")}, que=f"regla {regla}")
                else:
                    paso("POST", f"{C}{g.p}/global/firewalls", cuerpo, que=f"regla {regla}")
                tags = (vm.get("tags") or {}).get("items") or []
                if tag not in tags:
                    paso("POST", f"{base}/instances/{m}/setTags", {"items": tags + [tag], "fingerprint": (vm.get("tags") or {}).get("fingerprint")}, que=f"etiqueta {tag}")
        elif ac == "eliminar":
            if cod == 200:
                paso("DELETE", f"{base}/instances/{m}", que=f"eliminar {m}")
            else:
                hecho.append(f"{m} ya no existía")
            sn, _x = g.todos(f"{C}{g.p}/global/snapshots", filter=f'labels.faceops-vm eq "{m}"')
            for s in sn or []:
                paso("DELETE", f"{C}{g.p}/global/snapshots/{s['name']}", que=f"snapshot {s['name']}")
            fw, _x = g.todos(f"{C}{g.p}/global/firewalls")
            for f in fw or []:
                if f["name"].startswith(f"fo-{m}-"):
                    paso("DELETE", f"{C}{g.p}/global/firewalls/{f['name']}", que=f"regla {f['name']}")
            ds, _x = g.todos(f"{base}/disks", filter=f'labels.faceops-vm eq "{m}"')
            for d in ds or []:
                if not d.get("users"):
                    paso("DELETE", f"{base}/disks/{d['name']}", que=f"disco {d['name']}")
            if g.r("GET", f"{base}/instances/{m}")[0] != 404:
                return {"ok": False, "error": f"{m} sigue existiendo en la nube", "hecho": hecho}
        else:
            return {"ok": False, "error": f"acción no soportada: {ac}"}
    except RuntimeError as x:
        return {"ok": False, "error": _limpio(x), "hecho": hecho}
    cod, vm = g.r("GET", f"{base}/instances/{m}")
    out = {"ok": True, "hecho": hecho, "maquina": m, "zona": z, "accion": ac}
    if cod == 200:
        ni = (vm.get("networkInterfaces") or [{}])[0]
        out.update({"estado": vm.get("status"), "tipo": _nombre(vm.get("machineType")), "etiquetas": vm.get("labels") or {},
                    "etiquetas_red": (vm.get("tags") or {}).get("items") or [], "ip": ni.get("networkIP", ""),
                    "publica": next((a.get("natIP") for a in ni.get("accessConfigs") or [] if a.get("natIP")), ""),
                    "discos": [{"nombre": _nombre(d.get("source")), "gb": int(d.get("diskSizeGb") or 0), "arranque": bool(d.get("boot"))} for d in vm.get("disks", [])]})
    else:
        out["estado"] = "ELIMINADA" if cod == 404 else "?"
    return out


# ------------------------------------------------------------------ productos
def producto(g, e):
    ac, est = e["accion"], e.get("estado") or {}
    hecho = []
    try:
        if ac == "crear":
            ya = _salud(g, est) if est.get("url") else {"salud": "no existe"}
            if ya["salud"] not in ("no existe", "sin lectura"):
                hecho.append(f"{e['nombre']} ya existe: sin cambios")
            else:
                for p in e["pasos"]:
                    cod, d = g.r(p["metodo"], p["url"], p.get("cuerpo"))
                    if cod == 409:
                        hecho.append(f"{p['metodo']} {_nombre(p['url'].split('?')[0])}: ya existía")
                        continue
                    if isinstance(d, dict):
                        d["_base"] = _base_lro(p["url"])
                    ok, mot = g.esperar(cod, d, int(e.get("espera_min") or 15))
                    if not ok:
                        raise RuntimeError(mot)
                    hecho.append(f"{p['metodo']} {_nombre(p['url'].split('?')[0])}" + (f" ({mot})" if mot else ""))
        elif ac == "eliminar":
            for p in e["pasos"]:
                cod, d = g.r(p["metodo"], p["url"], p.get("cuerpo"))
                if cod == 404:
                    hecho.append(f"{_nombre(p['url'].split('?')[0])}: ya no existía")
                    continue
                if isinstance(d, dict):
                    d["_base"] = _base_lro(p["url"])
                ok, mot = g.esperar(cod, d, int(e.get("espera_min") or 15))
                if not ok:
                    raise RuntimeError(mot)
                hecho.append(f"eliminado {_nombre(p['url'].split('?')[0])}")
        elif ac != "estado":
            return {"ok": False, "error": f"acción no soportada: {ac}"}
    except RuntimeError as x:
        return {"ok": False, "error": _limpio(x), "hecho": hecho}
    salud = _salud(g, est) if est.get("url") else {"salud": "sin lectura"}
    if ac == "eliminar" and salud["salud"] != "no existe":
        return {"ok": False, "error": f"{e['nombre']} sigue existiendo en la nube", "hecho": hecho, "salud": salud}
    if ac == "crear" and salud["salud"] == "no existe":
        return {"ok": False, "error": f"{e['nombre']} no quedó creado", "hecho": hecho, "salud": salud}
    return {"ok": True, "hecho": hecho, "salud": salud, "producto": e.get("producto"), "nombre": e["nombre"]}


def main():
    modo, proyecto, entrada, salida = sys.argv[1:5]
    ent = json.load(open(entrada))
    try:
        g = G(proyecto)
        res = {"opciones": opciones, "operar": operar, "producto": producto}[modo](g, ent)
    except Exception as x:  # noqa: BLE001 - el motivo vuelve a FaceOps, sin secretos
        res = {"ok": False, "error": _limpio(f"{type(x).__name__}: {x}")}
    with open(salida, "w") as f:
        json.dump(res, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
