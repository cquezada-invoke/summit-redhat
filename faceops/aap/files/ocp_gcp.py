#!/usr/bin/env python3
"""OpenShift en GCP · apoyo de las acciones «Crear» y «Eliminar» (corre en el execution environment de AAP).
Subcomandos (todos responden un JSON por stdout, nunca secretos):
  version <pedido>                  «ultima-4» | «ultima-5» | «4.22» | «4.22.3» → versión exacta GA del canal stable
  prerrequisitos <proyecto> <region> <dominio> <tipo> <nodos_cpu>   zona DNS pública, permisos, APIs y cuota
  instancias <proyecto>             instancias con etiqueta faceops-ocp (para el ciclo de vencimiento y apagado)
  energia <proyecto> <zona> <instancia> stop|start
La credencial de GCP llega por la variable GCP_SERVICE_ACCOUNT_FILE (credencial de AAP)."""
import json
import os
import re
import sys
import urllib.error
import urllib.request

GRAFO = "https://api.openshift.com/api/upgrades_info/v1/graph?channel={}"
ESPEJO = "https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/{}/"
MINOR_DESDE = {4: 14, 5: 0}
PERMISOS = [
    "compute.instances.create", "compute.networks.create", "compute.subnetworks.create", "compute.firewalls.create",
    "compute.routers.create", "compute.addresses.create", "compute.forwardingRules.create", "compute.targetPools.create",
    "compute.backendServices.create", "compute.healthChecks.create", "compute.instanceGroups.create", "compute.images.create",
    "dns.managedZones.create", "dns.changes.create", "dns.resourceRecordSets.list",
    "iam.serviceAccounts.create", "iam.serviceAccounts.actAs", "iam.serviceAccountKeys.create", "iam.roles.get",
    "resourcemanager.projects.getIamPolicy", "resourcemanager.projects.setIamPolicy",
    "storage.buckets.create", "storage.objects.create", "serviceusage.services.list"]
APIS = ["compute.googleapis.com", "dns.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com",
        "cloudresourcemanager.googleapis.com", "serviceusage.googleapis.com", "storage-api.googleapis.com"]


def _get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as r:
        return r.read().decode()


def _clave(v):
    return [int(x) for x in re.findall(r"\d+", v.split("-")[0])]


def _canal(canal, mm):
    try:
        nodos = json.loads(_get(GRAFO.format(canal))).get("nodes") or []
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    vs = [n["version"] for n in nodos if n.get("version", "").startswith(mm + ".") and "-" not in n["version"]]
    return max(vs, key=_clave) if vs else ""


def rama(mayor):
    """Última GA de la rama mayor: recorre stable-M.m desde un menor conocido hasta el primer canal sin versiones."""
    desde = MINOR_DESDE.get(mayor, 0)
    mejor, vacios = "", 0
    for m in range(desde, desde + 40):
        v = _canal(f"stable-{mayor}.{m}", f"{mayor}.{m}")
        if v is None:
            return {"error": "No se pudo consultar el grafo de versiones de OpenShift (api.openshift.com)"}
        mejor, vacios = (v, 0) if v else (mejor, vacios + 1)
        if vacios >= 2 and (mejor or m - desde >= 2):
            break
    if mejor:
        return {"version": mejor, "canal": "stable-" + ".".join(mejor.split(".")[:2])}
    otros = []
    for c in ("candidate", "fast"):
        for mn in range(MINOR_DESDE.get(mayor, 0), MINOR_DESDE.get(mayor, 0) + 3):
            n = json.loads(_get(GRAFO.format(f"{c}-{mayor}.{mn}"))).get("nodes") or []
            vs = [x["version"] for x in n if x.get("version", "").startswith(f"{mayor}.{mn}")]
            if vs:
                otros.append({"canal": f"{c}-{mayor}.{mn}", "version": max(vs, key=_clave)})
    return {"error": "aún sin versión estable publicada", "otros_canales": otros}


def version(pedido):
    p = (pedido or "").strip().lower()
    if p in ("", "ultima", "ultima-estable"):
        p = "ultima-4"
    if re.fullmatch(r"ultima-\d+", p):
        r = rama(int(p.split("-")[1]))
    elif re.fullmatch(r"\d+\.\d+", p):
        v = _canal(f"stable-{p}", p)
        r = {"version": v, "canal": f"stable-{p}"} if v else {"error": f"OpenShift {p} no tiene versiones en el canal stable-{p}"}
    elif re.fullmatch(r"\d+\.\d+\.\d+", p):
        mm = ".".join(p.split(".")[:2])
        v = _canal(f"stable-{mm}", mm)
        nodos = json.loads(_get(GRAFO.format(f"stable-{mm}"))).get("nodes") or [] if v else []
        r = {"version": p, "canal": f"stable-{mm}"} if any(n.get("version") == p for n in nodos) else \
            {"error": f"La versión {p} no existe en el canal stable-{mm}"}
    else:
        r = {"error": f"Versión no reconocida: «{pedido}» (usa ultima-4, ultima-5, 4.22 o 4.22.3)"}
    if r.get("version"):
        try:   # el instalador y el cliente publicados en el espejo, con su huella
            suma = _get(ESPEJO.format(r["version"]) + "sha256sum.txt")
        except (urllib.error.URLError, TimeoutError):
            return {"error": f"OpenShift {r['version']} no tiene instalador publicado en mirror.openshift.com"}
        h = dict(reversed(x.split()) for x in suma.splitlines() if len(x.split()) == 2)
        if "openshift-install-linux.tar.gz" not in h or "openshift-client-linux.tar.gz" not in h:
            return {"error": f"OpenShift {r['version']} no tiene instalador para linux/amd64 en el espejo"}
        r.update({"url": ESPEJO.format(r["version"]), "sha_instalador": h["openshift-install-linux.tar.gz"],
                  "sha_cliente": h["openshift-client-linux.tar.gz"], "mayor": int(r["version"].split(".")[0])})
    return r


def _sesion():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    f = os.environ.get("GCP_SERVICE_ACCOUNT_FILE") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or ""
    if not f or not os.path.exists(f):
        raise SystemExit(json.dumps({"error": "Falta la credencial de GCP (cuenta de servicio) en la plantilla"}))
    cr = service_account.Credentials.from_service_account_file(f, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(cr), cr.service_account_email


def prerrequisitos(proyecto, region, dominio, tipo, cpus):
    s, sa = _sesion()
    out = {"cuenta": sa, "chequeos": []}

    def ch(item, ok, detalle, bloquea=True):
        out["chequeos"].append({"item": item, "ok": bool(ok), "detalle": detalle, "bloquea": bloquea})
    r = s.get(f"https://dns.googleapis.com/dns/v1/projects/{proyecto}/managedZones")
    zonas = r.json().get("managedZones", []) if r.ok else []
    z = next((x for x in zonas if x.get("dnsName") == dominio.rstrip(".") + "." and x.get("visibility", "public") == "public"), None)
    ch("Zona DNS pública", z, f"{z['name']} ({dominio})" if z else
       (f"no hay zona pública «{dominio}» en Cloud DNS de {proyecto}" if r.ok else f"Cloud DNS respondió {r.status_code}"))
    r = s.post(f"https://cloudresourcemanager.googleapis.com/v1/projects/{proyecto}:testIamPermissions", json={"permissions": PERMISOS})
    tiene = set(r.json().get("permissions", [])) if r.ok else set()
    faltan = [p for p in PERMISOS if p not in tiene]
    ch("Permisos de la cuenta de servicio", r.ok and not faltan, "suficientes para IPI" if r.ok and not faltan else
       (f"faltan {len(faltan)}: " + ", ".join(faltan[:8]) + (" …" if len(faltan) > 8 else "") if r.ok else f"IAM respondió {r.status_code}"))
    r = s.get(f"https://serviceusage.googleapis.com/v1/projects/{proyecto}/services?filter=state:ENABLED&pageSize=200")
    act = {x["config"]["name"] for x in r.json().get("services", [])} if r.ok else set()
    fa = [a for a in APIS if a not in act]
    ch("APIs habilitadas", r.ok and not fa, "todas" if r.ok and not fa else (", ".join(fa) if r.ok else f"Service Usage respondió {r.status_code}"))
    r = s.get(f"https://compute.googleapis.com/compute/v1/projects/{proyecto}/regions/{region}")
    if r.ok:
        q = {x["metric"]: x["limit"] - x["usage"] for x in r.json().get("quotas", [])}
        fam = tipo.split("-")[0].upper() + "_CPUS"
        libre = min(q.get("CPUS", 0), q.get(fam, q.get("CPUS", 0)))
        ch("Cuota de CPU en " + region, libre >= cpus, f"libres {int(libre)} · se necesitan {cpus} (con el bootstrap)")
        zs = [z_.rsplit("/", 1)[-1] for z_ in r.json().get("zones", [])]
        out["zonas"] = zs
        okt = [z_ for z_ in zs if s.get(f"https://compute.googleapis.com/compute/v1/projects/{proyecto}/zones/{z_}/machineTypes/{tipo}").ok]
        ch(f"Tipo {tipo}", okt, ", ".join(okt) if okt else f"no disponible en {region}")
        out["zonas_tipo"] = okt
    else:
        ch("Región " + region, False, f"Compute respondió {r.status_code}")
    out["listo"] = all(c["ok"] for c in out["chequeos"] if c["bloquea"])
    return out


def instancias(proyecto):
    s, _ = _sesion()
    r = s.get(f"https://compute.googleapis.com/compute/v1/projects/{proyecto}/aggregated/instances",
              params={"filter": "labels.faceops-ocp:*", "maxResults": 500})
    out = []
    for z, v in (r.json().get("items") or {}).items() if r.ok else []:
        for i in v.get("instances", []):
            out.append({"nombre": i["name"], "zona": z.split("/")[-1], "estado": i["status"], "etiquetas": i.get("labels", {}),
                        "creada": i.get("creationTimestamp", "")})
    return {"instancias": out} if r.ok else {"error": f"Compute respondió {r.status_code}"}


def energia(proyecto, zona, inst, accion):
    s, _ = _sesion()
    r = s.post(f"https://compute.googleapis.com/compute/v1/projects/{proyecto}/zones/{zona}/instances/{inst}/{accion}")
    return {"ok": r.ok, "codigo": r.status_code}


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        res = {"version": lambda: version(a[1] if len(a) > 1 else ""),
               "prerrequisitos": lambda: prerrequisitos(a[1], a[2], a[3], a[4], int(a[5])),
               "instancias": lambda: instancias(a[1]),
               "energia": lambda: energia(a[1], a[2], a[3], a[4])}[a[0]]()
    except (KeyError, IndexError):
        res = {"error": "uso: version|prerrequisitos|instancias|energia …"}
    except SystemExit as x:   # sin credencial: el motivo va como JSON
        res = json.loads(x.code) if isinstance(x.code, str) else {"error": str(x.code)}
    except Exception as x:  # noqa: BLE001 - errores de red o de la API: el motivo, sin trazas
        res = {"error": f"{type(x).__name__}: {str(x)[:300]}"}
    print(json.dumps(res, ensure_ascii=False))
