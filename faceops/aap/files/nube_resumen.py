#!/usr/bin/env python3
"""Nube · Recuperar consumo y estado (corre en el execution environment de AAP, solo lectura).
Uso: nube_resumen.py <proveedor gcp|aws|azure> <cuenta o proyecto> <salida.json> [tabla de facturación en BigQuery]
La credencial llega por las variables que inyecta la credencial de AAP de la conexión (GCP_SERVICE_ACCOUNT_FILE /
GCE_CREDENTIALS_FILE_PATH, AWS_ACCESS_KEY_ID…, AZURE_CLIENT_ID…). Escribe un JSON con el consumo y el estado de la nube;
nunca escribe ni imprime secretos. Lo que no se puede leer queda como {"ok": false, "falta": "..."} con el rol o la API
que falta, nunca inventado.

Consumo, en este orden:
  a) facturación real: export de Cloud Billing a BigQuery (promedio de los últimos 3 meses cerrados, filtrado por proyecto)
  b) API de facturación del proveedor (AWS Cost Explorer, Azure Cost Management). En GCP, la API de Cloud Billing no
     entrega montos: solo confirma la cuenta de facturación asociada.
  c) estimación con precios públicos (Cloud Billing Catalog API) sobre lo inventariado: VM encendidas (730 h/mes),
     discos e IPs estáticas."""
import datetime as dt
import json
import os
import re
import sys

HOY = dt.datetime.now(dt.timezone.utc)
H_MES = 730.0
DIAS_SNAP = 90
DIAS_CLAVE = 90
# GCP: rol mínimo por lectura (lo que se informa como «sin permiso: falta rol X»)
ROL = {"compute": "roles/compute.viewer", "billing": "roles/billing.viewer (en la cuenta de facturación)",
       "bigquery": "roles/bigquery.dataViewer y roles/bigquery.jobUser", "iam": "roles/iam.securityReviewer",
       "recommender": "roles/recommender.computeViewer", "catalogo": "roles/billing.viewer o API de Cloud Billing habilitada",
       "budgets": "roles/billing.viewer (en la cuenta de facturación)", "sql": "roles/cloudsql.viewer", "storage": "roles/storage.objectViewer o roles/viewer"}
API = {"compute": "compute.googleapis.com", "billing": "cloudbilling.googleapis.com", "bigquery": "bigquery.googleapis.com",
       "iam": "iam.googleapis.com", "recommender": "recommender.googleapis.com", "catalogo": "cloudbilling.googleapis.com",
       "budgets": "billingbudgets.googleapis.com", "sql": "sqladmin.googleapis.com", "storage": "storage.googleapis.com"}
COMPUTE_SVC = "6F81-5844-456A"   # Compute Engine en el catálogo de Cloud Billing


def _fecha(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _meses(n):
    """Los n últimos meses cerrados (AAAAMM), del más antiguo al más reciente, y el mes en curso."""
    y, m = HOY.year, HOY.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return list(reversed(out)), f"{HOY.year}{HOY.month:02d}"


def _servicio(nombre):
    """Agrupa un servicio del proveedor en Compute, Storage, Red u Otros."""
    n = (nombre or "").lower()
    if any(k in n for k in ("network", "networking", "egress", "ip", "dns", "load balanc", "vpc", "cdn", "bandwidth", "data transfer")):
        return "Red"
    if any(k in n for k in ("storage", "disk", "snapshot", "s3", "ebs", "bucket", "persistent")):
        return "Storage"
    if any(k in n for k in ("compute", "ec2", "virtual machine", "kubernetes", "instance", "vm")):
        return "Compute"
    return "Otros"


# ------------------------------------------------------------------ GCP
class GCP:
    def __init__(self, proyecto, tabla_bq=""):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        f = os.environ.get("GCP_SERVICE_ACCOUNT_FILE") or os.environ.get("GCE_CREDENTIALS_FILE_PATH") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not f or not os.path.exists(f):
            raise RuntimeError("La credencial de AAP de la nube no entregó la cuenta de servicio")
        cr = service_account.Credentials.from_service_account_file(f, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.s = AuthorizedSession(cr)
        self.p = proyecto or os.environ.get("GCP_PROJECT") or os.environ.get("GCE_PROJECT") or cr.project_id
        self.tabla_bq = tabla_bq
        self.cuenta_srv = cr.service_account_email

    def get(self, url, **params):
        r = self.s.get(url, params=params or None, timeout=60)
        return r.status_code, (r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else {})

    def post(self, url, cuerpo):
        r = self.s.post(url, json=cuerpo, timeout=120)
        return r.status_code, (r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else {})

    def falta(self, clave, cod, cuerpo):
        """Texto honesto de por qué no se pudo leer: API deshabilitada o rol que falta."""
        msg = json.dumps(cuerpo)[:2000]
        if "SERVICE_DISABLED" in msg or "has not been used in project" in msg or "it is disabled" in msg:
            return f"API deshabilitada: habilita {API[clave]} en el proyecto"
        if cod in (401, 403):
            return f"sin permiso: falta rol {ROL[clave]}"
        if cod == 404:
            return "no encontrado"
        return f"error {cod}"

    def todos(self, url, clave_items="items", **params):
        out, tok = [], None
        while True:
            q = dict(params, **({"pageToken": tok} if tok else {}))
            cod, d = self.get(url, **q)
            if cod != 200:
                return None, (cod, d)
            it = d.get(clave_items)
            if isinstance(it, dict):   # aggregatedList: {zona: {instances: [...]}}
                for v in it.values():
                    for k_, lista in v.items():
                        if isinstance(lista, list):
                            out += lista
            elif it:
                out += it
            tok = d.get("nextPageToken")
            if not tok:
                return out, None

    # ---- inventario y estado
    def inventario(self):
        base = f"https://compute.googleapis.com/compute/v1/projects/{self.p}"
        res = {}
        vms, e = self.todos(f"{base}/aggregated/instances", maxResults=500)
        if vms is None:
            f_ = self.falta("compute", *e)
            return {k: {"ok": False, "falta": f_} for k in ("maquinas", "discos", "ips", "snapshots", "firewall", "cuotas")}
        maqs = []
        for v in vms:
            zona = v.get("zone", "").rsplit("/", 1)[-1]
            pub = [ac.get("natIP") for ni in v.get("networkInterfaces", []) for ac in ni.get("accessConfigs", []) if ac.get("natIP")]
            maqs.append({"id": str(v.get("id", "")), "etiquetas": v.get("labels") or {}, "nombre": v["name"], "estado": v.get("status", ""), "zona": zona, "region": zona.rsplit("-", 1)[0],
                         "tipo": v.get("machineType", "").rsplit("/", 1)[-1], "publicas": pub,
                         "discos": [d.get("source", "").rsplit("/", 1)[-1] for d in v.get("disks", [])]})
        res["maquinas"] = {"ok": True, "lista": maqs}
        dis, e = self.todos(f"{base}/aggregated/disks", maxResults=500)
        res["discos"] = {"ok": True, "lista": [{"nombre": d["name"], "zona": d.get("zone", "").rsplit("/", 1)[-1], "gb": int(d.get("sizeGb") or 0),
                                                 "tipo": d.get("type", "").rsplit("/", 1)[-1], "en_uso": bool(d.get("users"))} for d in dis]} \
            if dis is not None else {"ok": False, "falta": self.falta("compute", *e)}
        ips, e = self.todos(f"{base}/aggregated/addresses", maxResults=500)
        res["ips"] = {"ok": True, "lista": [{"nombre": a["name"], "ip": a.get("address"), "region": a.get("region", "global").rsplit("/", 1)[-1],
                                              "tipo": a.get("addressType", "EXTERNAL"), "en_uso": a.get("status") == "IN_USE"} for a in ips]} \
            if ips is not None else {"ok": False, "falta": self.falta("compute", *e)}
        sn, e = self.todos(f"{base}/global/snapshots", maxResults=500)
        res["snapshots"] = {"ok": True, "lista": [{"nombre": s["name"], "gb": int(s.get("storageBytes") or 0) / 1e9,
                                                    "dias": (HOY - _fecha(s["creationTimestamp"])).days if _fecha(s.get("creationTimestamp")) else None} for s in sn]} \
            if sn is not None else {"ok": False, "falta": self.falta("compute", *e)}
        fw, e = self.todos(f"{base}/global/firewalls", maxResults=500)
        if fw is not None:
            reglas = [{"nombre": f["name"], "red": f.get("network", "").rsplit("/", 1)[-1],
                       "abierta": f.get("direction", "INGRESS") == "INGRESS" and not f.get("disabled") and "0.0.0.0/0" in (f.get("sourceRanges") or []) and bool(f.get("allowed")),
                       "puertos": ", ".join(f"{a.get('IPProtocol')}:{','.join(a.get('ports') or ['todos'])}" for a in f.get("allowed") or [])} for f in fw]
            res["firewall"] = {"ok": True, "lista": reglas}
        else:
            res["firewall"] = {"ok": False, "falta": self.falta("compute", *e)}
        # cuotas: las del proyecto y las de las regiones usadas (o la configurada)
        cuotas = []
        cod, d = self.get(base)
        if cod == 200:
            cuotas += [dict(q, ambito="global") for q in d.get("quotas", [])]
        regiones = sorted({m["region"] for m in maqs} | {x["zona"].rsplit("-", 1)[0] for x in res["discos"].get("lista", []) if x.get("zona")})
        for rg in regiones or [os.environ.get("NR_REGION", "")]:
            if not rg:
                continue
            cod2, d2 = self.get(f"{base}/regions/{rg}")
            if cod2 == 200:
                cuotas += [dict(q, ambito=rg) for q in d2.get("quotas", [])]
        if cod == 200:
            res["cuotas"] = {"ok": True, "lista": [{"metrica": q["metric"], "ambito": q["ambito"], "uso": q.get("usage", 0), "limite": q.get("limit", 0),
                                                    "pct": round(100 * q.get("usage", 0) / q["limit"]) if q.get("limit") else 0}
                                                   for q in cuotas if q.get("limit") and q.get("usage")]}
        else:
            res["cuotas"] = {"ok": False, "falta": self.falta("compute", cod, d)}
        res["regiones"] = {"ok": True, "lista": regiones}
        # productos gestionados: Cloud SQL y buckets de Cloud Storage
        prods, faltas = [], []
        cod, d = self.get(f"https://sqladmin.googleapis.com/v1/projects/{self.p}/instances")
        if cod == 200:
            prods += [{"tipo": "Cloud SQL", "nombre": i["name"], "region": i.get("region", ""), "detalle": i.get("databaseVersion", ""),
                       "estado": i.get("state", ""), "etiquetas": (i.get("settings") or {}).get("userLabels") or {}} for i in d.get("items", [])]
        else:
            faltas.append("Cloud SQL: " + self.falta("sql", cod, d))
        bk, e = self.todos("https://storage.googleapis.com/storage/v1/b", project=self.p, maxResults=500)
        if bk is not None:
            prods += [{"tipo": "Cloud Storage", "nombre": b["name"], "region": b.get("location", "").lower(), "detalle": b.get("storageClass", ""),
                       "estado": "", "etiquetas": b.get("labels") or {}} for b in bk]
        else:
            faltas.append("buckets: " + self.falta("storage", *e))
        res["productos"] = {"ok": not faltas or bool(prods), "lista": prods, "falta": " · ".join(faltas)}
        return res

    def claves(self):
        cuentas, e = self.todos(f"https://iam.googleapis.com/v1/projects/{self.p}/serviceAccounts", clave_items="accounts", pageSize=100)
        if cuentas is None:
            return {"ok": False, "falta": self.falta("iam", *e)}
        out = []
        for c in cuentas:
            cod, d = self.get(f"https://iam.googleapis.com/v1/{c['name']}/keys", keyTypes="USER_MANAGED")
            if cod != 200:
                return {"ok": False, "falta": self.falta("iam", cod, d)}
            for k in d.get("keys", []):
                f_ = _fecha(k.get("validAfterTime"))
                out.append({"cuenta": c.get("email"), "clave": k["name"].rsplit("/", 1)[-1][:8], "dias": (HOY - f_).days if f_ else None})
        return {"ok": True, "lista": out}

    def recomendaciones(self, zonas, regiones):
        recs = []
        tipos = [("google.compute.instance.MachineTypeRecommender", zonas, "Ajustar tamaño de máquina"),
                 ("google.compute.instance.IdleResourceRecommender", zonas, "Máquina ociosa"),
                 ("google.compute.disk.IdleResourceRecommender", zonas, "Disco ocioso"),
                 ("google.compute.address.IdleResourceRecommender", regiones + ["global"], "IP ociosa")]
        for rec, lugares, nom in tipos:
            for lg in lugares:
                cod, d = self.get(f"https://recommender.googleapis.com/v1/projects/{self.p}/locations/{lg}/recommenders/{rec}/recommendations",
                                  filter="stateInfo.state=ACTIVE")
                if cod != 200:
                    return {"ok": False, "falta": self.falta("recommender", cod, d)}
                for r in d.get("recommendations", []):
                    c = ((r.get("primaryImpact") or {}).get("costProjection") or {}).get("cost") or {}
                    dur = float(str(((r.get("primaryImpact") or {}).get("costProjection") or {}).get("duration", "2592000s")).rstrip("s") or 2592000)
                    monto = -(float(c.get("units") or 0) + float(c.get("nanos") or 0) / 1e9)
                    recs.append({"tipo": nom, "descripcion": r.get("description", "")[:200], "lugar": lg,
                                 "ahorro_usd_mes": round(monto * 2592000 / dur, 2) if c.get("currencyCode", "USD") == "USD" else None})
        return {"ok": True, "lista": recs}

    # ---- consumo
    def facturacion_cuenta(self):
        cod, d = self.get(f"https://cloudbilling.googleapis.com/v1/projects/{self.p}/billingInfo")
        if cod != 200:
            return {"ok": False, "falta": self.falta("billing", cod, d)}
        return {"ok": True, "cuenta": (d.get("billingAccountName") or "").rsplit("/", 1)[-1], "habilitada": bool(d.get("billingEnabled"))}

    def presupuesto(self, cuenta):
        if not cuenta:
            return {"ok": False, "falta": "sin cuenta de facturación asociada"}
        cod, d = self.get(f"https://billingbudgets.googleapis.com/v1/billingAccounts/{cuenta}/budgets")
        if cod != 200:
            return {"ok": False, "falta": self.falta("budgets", cod, d)}
        for b in d.get("budgets", []):
            proys = (b.get("budgetFilter") or {}).get("projects") or []
            if proys and not any(p.endswith(self.p) for p in proys):
                continue
            m = ((b.get("amount") or {}).get("specifiedAmount") or {})
            if m and m.get("currencyCode", "USD") == "USD":
                return {"ok": True, "nombre": b.get("displayName", ""), "usd_mes": float(m.get("units") or 0) + float(m.get("nanos") or 0) / 1e9}
        return {"ok": True, "nombre": "", "usd_mes": None}

    def export_bq(self):
        """Busca el export de facturación en BigQuery: la tabla declarada o una del proyecto (prefiere el export detallado
        gcp_billing_export_resource_v1_*, que trae el costo por recurso)."""
        tabla = self.tabla_bq
        if not tabla:
            ds, e = self.todos(f"https://bigquery.googleapis.com/bigquery/v2/projects/{self.p}/datasets", clave_items="datasets", maxResults=200)
            if ds is None:
                return None, self.falta("bigquery", *e)
            hall = []
            for d_ in ds:
                did = d_["datasetReference"]["datasetId"]
                tb, e = self.todos(f"https://bigquery.googleapis.com/bigquery/v2/projects/{self.p}/datasets/{did}/tables", clave_items="tables", maxResults=500)
                hall += [f"{self.p}.{did}.{t['tableReference']['tableId']}" for t in tb or [] if re.match(r"gcp_billing_export_(resource_)?v1_", t["tableReference"]["tableId"])]
            hall.sort(key=lambda t: "resource_v1" not in t)
            tabla = hall[0] if hall else ""
        if not tabla:
            return None, "sin export de facturación a BigQuery en el proyecto"
        detallado = "resource_v1" in tabla
        cerrados, actual = _meses(3)
        rec = "resource.name, resource.global_name" if detallado else "CAST(NULL AS STRING), CAST(NULL AS STRING)"
        sql = ("SELECT mes, servicio, r, rg, ANY_VALUE(etq) AS etiquetas, SUM(usd) AS usd FROM ("
               f"SELECT invoice.month AS mes, service.description AS servicio, {rec.replace(', ', ' AS r, ', 1)} AS rg, "
               "(SELECT STRING_AGG(CONCAT(l.key, '=', l.value), ',' ORDER BY l.key) FROM UNNEST(labels) l) AS etq, "
               "cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0) AS usd "
               f"FROM `{tabla}` WHERE project.id = @p AND invoice.month IN UNNEST(@meses) AND currency = 'USD') GROUP BY 1, 2, 3, 4")
        cod, d = self.post(f"https://bigquery.googleapis.com/bigquery/v2/projects/{self.p}/queries",
                           {"query": sql, "useLegacySql": False, "timeoutMs": 60000, "parameterMode": "NAMED",
                            "queryParameters": [{"name": "p", "parameterType": {"type": "STRING"}, "parameterValue": {"value": self.p}},
                                                {"name": "meses", "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRING"}},
                                                 "parameterValue": {"arrayValues": [{"value": m} for m in cerrados + [actual]]}}]})
        if cod != 200:
            return None, self.falta("bigquery", cod, d)
        filas = [[c.get("v") for c in r["f"]] for r in d.get("rows", [])]
        return {"tabla": tabla, "detallado": detallado,
                "filas": [{"mes": f[0], "servicio": f[1], "recurso": f[2] or "", "recurso_global": f[3] or "", "etiquetas": f[4] or "", "usd": float(f[5] or 0)} for f in filas]}, ""

    def catalogo(self, regiones):
        """SKU de Compute Engine con precio por hora o por GB-mes en las regiones usadas (precios públicos en USD)."""
        skus, e = self.todos(f"https://cloudbilling.googleapis.com/v1/services/{COMPUTE_SVC}/skus", clave_items="skus", currencyCode="USD", pageSize=5000)
        if skus is None:
            return None, self.falta("catalogo", *e)
        out = []
        for s in skus:
            if (s.get("category") or {}).get("usageType") != "OnDemand":
                continue
            rg = s.get("serviceRegions") or []
            if regiones and not (set(rg) & set(regiones)) and "global" not in rg:
                continue
            pi = ((s.get("pricingInfo") or [{}])[0].get("pricingExpression") or {})
            tiers = pi.get("tieredRates") or []
            if not tiers:
                continue
            up = tiers[-1].get("unitPrice") or {}
            out.append({"d": s.get("description", ""), "g": (s.get("category") or {}).get("resourceGroup", ""), "r": rg,
                        "u": pi.get("usageUnit", ""), "usd": float(up.get("units") or 0) + float(up.get("nanos") or 0) / 1e9})
        return out, ""

    def tipo_maquina(self, zona, tipo, cache={}):
        k = (zona, tipo)
        if k not in cache:
            cod, d = self.get(f"https://compute.googleapis.com/compute/v1/projects/{self.p}/zones/{zona}/machineTypes/{tipo}")
            cache[k] = (d.get("guestCpus", 0), d.get("memoryMb", 0) / 1024) if cod == 200 else (0, 0)
        return cache[k]


def _precio(skus, region, patron, grupo=None):
    for s in skus:
        if region in s["r"] and re.search(patron, s["d"], re.I) and (not grupo or s["g"] == grupo) and "Commit" not in s["d"] and "Spot" not in s["d"] and "Preemptible" not in s["d"]:
            return s["usd"]
    return None


def _rec(tipo, rid, nombre, servicio, grupo, usd_mes, zona="", etiquetas=None, nota=""):
    return {"tipo": tipo, "id": rid, "nombre": nombre, "servicio": servicio, "grupo": grupo, "zona": zona, "etiquetas": etiquetas or {},
            "promedio_usd": None if usd_mes is None else round(usd_mes, 2),
            "mes_actual_usd": None if usd_mes is None else round(usd_mes * HOY.day / 30.4, 2), "nota": nota}


def estimar_gcp(g, inv, skus):
    """Estimación mensual en USD con los precios públicos: VM encendidas 730 h (CPU, RAM, sus discos y su IP pública),
    discos sueltos por GB-mes, snapshots e IPs estáticas. Devuelve el desglose por grupo, el costo por recurso y lo que
    quedó sin precio."""
    det = {"Compute": 0.0, "Storage": 0.0, "Red": 0.0}
    recursos, sin_precio = [], []
    discos = {d["nombre"]: d for d in inv["discos"].get("lista", [])}
    p_ip = {}

    def precio_disco(d):
        pat = {"pd-ssd": r"^SSD backed PD Capacity", "pd-balanced": r"^Balanced PD Capacity", "pd-extreme": r"^Extreme PD Capacity",
               "hyperdisk-balanced": r"^Hyperdisk Balanced Capacity"}.get(d["tipo"], r"^Storage PD Capacity")
        p = _precio(skus, d["zona"].rsplit("-", 1)[0], pat)
        if p is None:
            sin_precio.append(d["tipo"])
        return None if p is None else d["gb"] * p

    usados = set()
    for m in inv["maquinas"].get("lista", []):
        cpu_ram = 0.0
        if m["estado"] == "RUNNING":
            fam = m["tipo"].split("-")[0].upper()
            cpu, ram = g.tipo_maquina(m["zona"], m["tipo"])
            if m["tipo"] in ("e2-micro", "e2-small", "e2-medium"):
                pc, pr = _precio(skus, m["region"], r"^E2 Instance Core running in"), _precio(skus, m["region"], r"^E2 Instance Ram running in")
                cpu = {"e2-micro": 0.25, "e2-small": 0.5, "e2-medium": 1}[m["tipo"]]
            else:
                pc = _precio(skus, m["region"], rf"^{fam} (Predefined |AMD |Intel )?Instance Core running in", "CPU")
                pr = _precio(skus, m["region"], rf"^{fam} (Predefined |AMD |Intel )?Instance Ram running in", "RAM")
            if pc is None or pr is None:
                sin_precio.append(m["tipo"])
            else:
                cpu_ram = (cpu * pc + ram * pr) * H_MES
        sto = 0.0
        for dn in m.get("discos") or []:
            if dn in discos:
                usados.add(dn)
                sto += precio_disco(discos[dn]) or 0
        red = 0.0
        if m["estado"] == "RUNNING" and m["publicas"]:
            p_ip.setdefault(m["region"], _precio(skus, m["region"], r"^External IP Charge on a Standard VM"))
            red = (p_ip[m["region"]] or 0) * H_MES * len(m["publicas"])
        det["Compute"] += cpu_ram
        det["Storage"] += sto
        det["Red"] += red
        recursos.append(_rec("vm", m.get("id") or m["nombre"], m["nombre"], "Compute Engine", "Compute", cpu_ram + sto + red, m["zona"], m.get("etiquetas"),
                             f"CPU y RAM {usd_txt(cpu_ram)} · discos {usd_txt(sto)} · IP pública {usd_txt(red)}" + ("" if m["estado"] == "RUNNING" else " · apagada: solo discos")))
    for dn, d in discos.items():
        if dn in usados:
            continue
        v = precio_disco(d)
        det["Storage"] += v or 0
        recursos.append(_rec("disco", dn, dn, "Compute Engine", "Storage", v, d["zona"], nota="disco sin máquina"))
    for sn in inv["snapshots"].get("lista", []):
        p = _precio(skus, "global", r"^Storage PD Snapshot") or _precio(skus, (inv["regiones"]["lista"] or ["us-central1"])[0], r"^Storage PD Snapshot")
        v = None if p is None else sn["gb"] * p
        det["Storage"] += v or 0
        recursos.append(_rec("snapshot", sn["nombre"], sn["nombre"], "Compute Engine", "Storage", v))
    for a in inv["ips"].get("lista", []):
        if a["tipo"] != "EXTERNAL" or a["en_uso"]:   # la IP en uso ya va en el costo de su máquina
            continue
        p = _precio(skus, a["region"], r"^Static Ip Charge")
        v = None if p is None else p * H_MES
        det["Red"] += v or 0
        recursos.append(_rec("ip", a["nombre"], a["ip"] or a["nombre"], "Compute Engine", "Red", v, a["region"], nota="IP estática sin uso"))
    for pr in (inv.get("productos") or {}).get("lista", []):   # productos gestionados: sin precio público confiable sin su uso real
        recursos.append(_rec("producto", f"{pr['tipo']}/{pr['nombre']}", pr["nombre"], pr["tipo"], "Otros", None, pr.get("region", ""), pr.get("etiquetas"),
                             "sin estimación: su costo depende del uso; se obtiene con el export de facturación a BigQuery"))
    return {k: round(v, 2) for k, v in det.items()}, recursos, sorted(set(sin_precio))


def usd_txt(v):
    return f"USD {v:,.2f}"


def _recursos_bq(filas, cerrados, actual, inv):
    """Costo por recurso desde el export detallado (resource.name), o por servicio si el export es el estándar."""
    vms = {m["nombre"]: m for m in inv.get("maquinas", {}).get("lista", [])}
    prods = {p["nombre"]: p for p in (inv.get("productos") or {}).get("lista", [])}
    acc = {}
    for f in filas:
        nombre = (f["recurso"] or "").rsplit("/", 1)[-1]
        es_vm = nombre in vms   # la VM y sus discos (el de arranque se llama igual) suman en una sola fila
        clave = ("vm", nombre) if es_vm else (f["servicio"], f["recurso_global"] or f["recurso"] or f["servicio"])
        r = acc.setdefault(clave, {"servicio": "Compute Engine" if es_vm else f["servicio"], "id": vms[nombre].get("id") or nombre if es_vm else (f["recurso_global"] or f["recurso"] or f["servicio"]),
                                   "nombre": nombre or f["servicio"], "es_vm": es_vm,
                                   "etiquetas": dict(x.split("=", 1) for x in (f["etiquetas"] or "").split(",") if "=" in x), "meses": {}})
        r["meses"][f["mes"]] = r["meses"].get(f["mes"], 0) + f["usd"]
    con = sorted({m for r in acc.values() for m in r["meses"] if m in cerrados})
    out = []
    for r in acc.values():
        if r["es_vm"] or ("Compute Engine" in r["servicio"] and "/instances/" in r["id"]):
            tipo = "vm"
        elif r["nombre"] in prods:
            tipo = "producto"
        elif not r["id"] or r["id"] == r["servicio"]:
            tipo = "servicio"
        else:
            tipo = "producto"
        prom = sum(r["meses"].get(m, 0) for m in con) / len(con) if con else None
        out.append({"tipo": tipo, "id": r["id"], "nombre": r["nombre"], "servicio": r["servicio"], "grupo": _servicio(r["servicio"]), "zona": (vms.get(r["nombre"]) or {}).get("zona", ""),
                    "etiquetas": r["etiquetas"], "promedio_usd": None if prom is None else round(prom, 2), "mes_actual_usd": round(r["meses"].get(actual, 0), 2), "nota": ""})
    return sorted(out, key=lambda x: -(x["mes_actual_usd"] or 0))


def gcp(proyecto, tabla_bq):
    g = GCP(proyecto, tabla_bq)
    out = {"proveedor": "gcp", "cuenta": g.p, "calculado": HOY.isoformat(timespec="seconds")}
    inv = g.inventario()
    out.update(inv)
    zonas = sorted({m["zona"] for m in inv["maquinas"].get("lista", [])} | {d["zona"] for d in inv["discos"].get("lista", [])} | ({os.environ["NR_ZONA"]} if os.environ.get("NR_ZONA") else set()))
    regiones = sorted(set(inv.get("regiones", {}).get("lista", [])) | ({os.environ["NR_ZONA"].rsplit("-", 1)[0]} if os.environ.get("NR_ZONA") else set()))
    out["claves"] = g.claves()
    out["recomendaciones"] = g.recomendaciones(zonas, regiones)
    fac = g.facturacion_cuenta()
    out["facturacion"] = fac
    out["presupuesto"] = g.presupuesto(fac.get("cuenta")) if fac.get("ok") else {"ok": False, "falta": fac.get("falta")}
    # consumo: a) export a BigQuery
    cerrados, actual = _meses(3)
    bq, motivo_bq = g.export_bq()
    intentos = {"bigquery": motivo_bq or "ok", "api": "la API de Cloud Billing de GCP no entrega montos de consumo"
                + ("" if fac.get("ok") else f" ({fac.get('falta')})")}
    if bq is not None and bq["filas"]:
        por_mes = {}
        for f in bq["filas"]:
            por_mes.setdefault(f["mes"], {}).setdefault(_servicio(f["servicio"]), 0.0)
            por_mes[f["mes"]][_servicio(f["servicio"])] += f["usd"]
        tot = {m: sum(v.values()) for m, v in por_mes.items()}
        con = [m for m in cerrados if m in tot]
        prom = sum(tot[m] for m in con) / len(con) if con else None
        serv = {}
        for f in bq["filas"]:
            if f["mes"] in con:
                serv[f["servicio"]] = serv.get(f["servicio"], 0) + f["usd"] / max(1, len(con))
        desg = {}
        for m in con:
            for k, v in por_mes[m].items():
                desg[k] = desg.get(k, 0) + v / len(con)
        out["consumo"] = {"ok": True, "fuente": "facturacion", "detalle_fuente": f"export de facturación en BigQuery ({bq['tabla']})"
                          + ("" if bq["detallado"] else "; export estándar: costo por servicio, no por recurso"),
                          "promedio_usd": round(prom, 2) if prom is not None else None, "meses": con,
                          "mes_actual_usd": round(tot.get(actual, 0), 2), "mes_anterior_usd": round(tot[cerrados[-1]], 2) if cerrados[-1] in tot else None,
                          "desglose": {k: round(v, 2) for k, v in desg.items()},
                          "top_servicios": [{"servicio": k, "usd": round(v, 2)} for k, v in sorted(serv.items(), key=lambda x: -x[1])[:3]],
                          "recursos": _recursos_bq(bq["filas"], cerrados, actual, inv), "intentos": intentos}
        return out
    if bq is not None:
        intentos["bigquery"] = f"el export ({bq['tabla']}) no tiene filas del proyecto en los últimos 3 meses"
    # c) estimación con precios públicos sobre lo inventariado
    if not inv["maquinas"].get("ok"):
        out["consumo"] = {"ok": False, "fuente": "sin datos", "falta": inv["maquinas"].get("falta"), "intentos": intentos}
        return out
    skus, motivo = g.catalogo(regiones)
    if skus is None:
        out["consumo"] = {"ok": False, "fuente": "sin datos", "falta": f"precios públicos: {motivo}", "intentos": dict(intentos, catalogo=motivo)}
        return out
    desg, recursos, sin_precio = estimar_gcp(g, inv, skus)
    tot = round(sum(desg.values()), 2)
    n_on = sum(1 for m in inv["maquinas"]["lista"] if m["estado"] == "RUNNING")
    out["consumo"] = {"ok": True, "fuente": "estimado", "promedio_usd": tot, "mes_actual_usd": round(tot * HOY.day / 30.4, 2), "mes_anterior_usd": None,
                      "desglose": desg, "top_servicios": [{"servicio": k, "usd": v} for k, v in sorted(desg.items(), key=lambda x: -x[1]) if v][:3],
                      "recursos": recursos,
                      "detalle_fuente": (f"Precios públicos de Cloud Billing Catalog (USD, a demanda) sobre lo inventariado: {n_on} VM encendida(s) × 730 h/mes "
                                         f"(CPU y RAM por tipo), {len(inv['discos'].get('lista', []))} disco(s) por GB-mes, snapshots, IPs estáticas y públicas. "
                                         "El mes en curso se prorratea por los días transcurridos. No incluye tráfico de salida, licencias, productos gestionados ni descuentos."
                                         + (f" Sin precio para: {', '.join(sin_precio)}." if sin_precio else "")),
                      "intentos": dict(intentos, catalogo="ok")}
    return out


# ------------------------------------------------------------------ AWS (preparado; sin probar en el laboratorio)
def aws(cuenta):
    import boto3
    out = {"proveedor": "aws", "cuenta": cuenta, "calculado": HOY.isoformat(timespec="seconds")}
    reg = os.environ.get("AWS_REGION") or os.environ.get("NR_REGION") or "us-east-1"
    ec2 = boto3.client("ec2", region_name=reg)
    try:
        vms = [i for r in ec2.describe_instances()["Reservations"] for i in r["Instances"]]
        out["maquinas"] = {"ok": True, "lista": [{"nombre": next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), i["InstanceId"]),
                                                   "estado": i["State"]["Name"].upper(), "zona": i["Placement"]["AvailabilityZone"], "region": reg,
                                                   "tipo": i["InstanceType"], "publicas": [i["PublicIpAddress"]] if i.get("PublicIpAddress") else []} for i in vms]}
        vol = ec2.describe_volumes()["Volumes"]
        out["discos"] = {"ok": True, "lista": [{"nombre": v["VolumeId"], "zona": v["AvailabilityZone"], "gb": v["Size"], "tipo": v["VolumeType"], "en_uso": v["State"] == "in-use"} for v in vol]}
        ips = ec2.describe_addresses()["Addresses"]
        out["ips"] = {"ok": True, "lista": [{"nombre": a.get("AllocationId", ""), "ip": a.get("PublicIp"), "region": reg, "tipo": "EXTERNAL", "en_uso": bool(a.get("AssociationId"))} for a in ips]}
        sn = ec2.describe_snapshots(OwnerIds=["self"])["Snapshots"]
        out["snapshots"] = {"ok": True, "lista": [{"nombre": s["SnapshotId"], "gb": s["VolumeSize"], "dias": (HOY - s["StartTime"]).days} for s in sn]}
        sg = ec2.describe_security_groups()["SecurityGroups"]
        out["firewall"] = {"ok": True, "lista": [{"nombre": s["GroupName"], "red": s.get("VpcId", ""), "puertos": str(p.get("FromPort", "todos")),
                                                   "abierta": any(r.get("CidrIp") == "0.0.0.0/0" for r in p.get("IpRanges", []))} for s in sg for p in s.get("IpPermissions", [])]}
        out["regiones"] = {"ok": True, "lista": [reg]}
    except Exception as e:  # noqa: BLE001
        for k in ("maquinas", "discos", "ips", "snapshots", "firewall"):
            out.setdefault(k, {"ok": False, "falta": f"sin permiso: falta política de lectura de EC2 ({type(e).__name__})"})
    out["cuotas"] = {"ok": False, "falta": "no implementado para AWS"}
    out["claves"] = {"ok": False, "falta": "no implementado para AWS"}
    out["recomendaciones"] = {"ok": False, "falta": "no implementado para AWS"}
    try:
        ce = boto3.client("ce", region_name="us-east-1")
        cerrados, actual = _meses(3)
        ini = f"{cerrados[0][:4]}-{cerrados[0][4:]}-01"
        fin = HOY.date().isoformat()
        r = ce.get_cost_and_usage(TimePeriod={"Start": ini, "End": fin}, Granularity="MONTHLY", Metrics=["UnblendedCost"],
                                  GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])
        por_mes, serv = {}, {}
        for p in r["ResultsByTime"]:
            mes = p["TimePeriod"]["Start"][:7].replace("-", "")
            for gr in p.get("Groups", []):
                v = float(gr["Metrics"]["UnblendedCost"]["Amount"])
                por_mes.setdefault(mes, {}).setdefault(_servicio(gr["Keys"][0]), 0.0)
                por_mes[mes][_servicio(gr["Keys"][0])] += v
                if mes in cerrados:
                    serv[gr["Keys"][0]] = serv.get(gr["Keys"][0], 0) + v / 3
        tot = {m: sum(v.values()) for m, v in por_mes.items()}
        con = [m for m in cerrados if m in tot]
        out["consumo"] = {"ok": True, "fuente": "facturacion", "detalle_fuente": "AWS Cost Explorer", "meses": con,
                          "promedio_usd": round(sum(tot[m] for m in con) / len(con), 2) if con else None,
                          "mes_actual_usd": round(tot.get(actual, 0), 2), "mes_anterior_usd": round(tot.get(cerrados[-1], 0), 2),
                          "desglose": {k: round(sum(por_mes[m].get(k, 0) for m in con) / max(1, len(con)), 2) for k in ("Compute", "Storage", "Red", "Otros")},
                          "top_servicios": [{"servicio": k, "usd": round(v, 2)} for k, v in sorted(serv.items(), key=lambda x: -x[1])[:3]]}
    except Exception as e:  # noqa: BLE001
        out["consumo"] = {"ok": False, "fuente": "sin datos", "falta": f"sin permiso: falta ce:GetCostAndUsage ({type(e).__name__})"}
    return out


# ------------------------------------------------------------------ Azure (preparado; sin probar en el laboratorio)
def azure(suscripcion):
    import requests
    sus = suscripcion or os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    out = {"proveedor": "azure", "cuenta": sus, "calculado": HOY.isoformat(timespec="seconds")}
    t = requests.post(f"https://login.microsoftonline.com/{os.environ.get('AZURE_TENANT', '')}/oauth2/v2.0/token", timeout=30,
                      data={"grant_type": "client_credentials", "client_id": os.environ.get("AZURE_CLIENT_ID", ""),
                            "client_secret": os.environ.get("AZURE_SECRET", ""), "scope": "https://management.azure.com/.default"})
    if t.status_code != 200:
        for k in ("maquinas", "discos", "ips", "snapshots", "firewall", "cuotas", "claves", "recomendaciones"):
            out[k] = {"ok": False, "falta": "la credencial de Azure no obtuvo token"}
        out["consumo"] = {"ok": False, "fuente": "sin datos", "falta": "la credencial de Azure no obtuvo token"}
        return out
    h = {"Authorization": "Bearer " + t.json()["access_token"]}
    base = f"https://management.azure.com/subscriptions/{sus}"
    r = requests.get(f"{base}/providers/Microsoft.Compute/virtualMachines?api-version=2024-03-01&statusOnly=true", headers=h, timeout=60)
    if r.status_code == 200:
        out["maquinas"] = {"ok": True, "lista": [{"nombre": v["name"], "zona": v.get("location", ""), "region": v.get("location", ""),
                                                   "tipo": (v.get("properties", {}).get("hardwareProfile") or {}).get("vmSize", ""),
                                                   "estado": next((s["code"].split("/")[-1].upper() for s in (v.get("properties", {}).get("instanceView") or {}).get("statuses", []) if s["code"].startswith("PowerState")), ""),
                                                   "publicas": []} for v in r.json().get("value", [])]}
        out["regiones"] = {"ok": True, "lista": sorted({m["region"] for m in out["maquinas"]["lista"]})}
    else:
        out["maquinas"] = {"ok": False, "falta": "sin permiso: falta rol Reader en la suscripción"}
    for k in ("discos", "ips", "snapshots", "firewall", "cuotas", "claves", "recomendaciones"):
        out[k] = {"ok": False, "falta": "no implementado para Azure"}
    cerrados, actual = _meses(3)
    ini = f"{cerrados[0][:4]}-{cerrados[0][4:]}-01T00:00:00Z"
    q = {"type": "ActualCost", "timeframe": "Custom", "timePeriod": {"from": ini, "to": HOY.isoformat(timespec="seconds")},
         "dataset": {"granularity": "Monthly", "aggregation": {"c": {"name": "Cost", "function": "Sum"}}, "grouping": [{"type": "Dimension", "name": "ServiceName"}]}}
    r = requests.post(f"{base}/providers/Microsoft.CostManagement/query?api-version=2023-03-01", headers=h, json=q, timeout=120)
    if r.status_code != 200:
        out["consumo"] = {"ok": False, "fuente": "sin datos", "falta": "sin permiso: falta rol Cost Management Reader"}
        return out
    cols = [c["name"] for c in r.json()["properties"]["columns"]]
    por_mes, serv = {}, {}
    for f in r.json()["properties"]["rows"]:
        d = dict(zip(cols, f))
        mes = str(d.get("BillingMonth") or d.get("UsageDate"))[:7].replace("-", "")
        por_mes.setdefault(mes, {}).setdefault(_servicio(d.get("ServiceName")), 0.0)
        por_mes[mes][_servicio(d.get("ServiceName"))] += float(d.get("Cost") or 0)
        if mes in cerrados:
            serv[d.get("ServiceName")] = serv.get(d.get("ServiceName"), 0) + float(d.get("Cost") or 0) / 3
    tot = {m: sum(v.values()) for m, v in por_mes.items()}
    con = [m for m in cerrados if m in tot]
    out["consumo"] = {"ok": True, "fuente": "facturacion", "detalle_fuente": "Azure Cost Management", "meses": con,
                      "promedio_usd": round(sum(tot[m] for m in con) / len(con), 2) if con else None,
                      "mes_actual_usd": round(tot.get(actual, 0), 2), "mes_anterior_usd": round(tot.get(cerrados[-1], 0), 2),
                      "desglose": {k: round(sum(por_mes[m].get(k, 0) for m in con) / max(1, len(con)), 2) for k in ("Compute", "Storage", "Red", "Otros")},
                      "top_servicios": [{"servicio": k, "usd": round(v, 2)} for k, v in sorted(serv.items(), key=lambda x: -x[1])[:3]]}
    return out


def main():
    prov, cuenta, salida = sys.argv[1], sys.argv[2], sys.argv[3]
    tabla = sys.argv[4] if len(sys.argv) > 4 else ""
    try:
        res = {"gcp": lambda: gcp(cuenta, tabla), "aws": lambda: aws(cuenta), "azure": lambda: azure(cuenta)}[prov]()
        res["ok"] = True
    except Exception as e:  # noqa: BLE001  (sin el detalle del entorno: puede traer rutas o secretos)
        res = {"ok": False, "proveedor": prov, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        res["error"] = re.sub(r"(?i)(private_key|secret|password|token)\S*", "[oculto]", res["error"])
    with open(salida, "w") as f:
        json.dump(res, f)
    print(json.dumps({"ok": res["ok"], "proveedor": prov, "fuente": (res.get("consumo") or {}).get("fuente", "")}))


if __name__ == "__main__":
    main()
