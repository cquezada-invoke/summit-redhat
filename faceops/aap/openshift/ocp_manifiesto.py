#!/usr/bin/env python3
"""FaceOps · Habilitar OpenShift: del manifiesto a los archivos del instalador.

Entrada (JSON en argv[1]): {"manifiesto": "<YAML>", "campos": {...del formulario...}, "ips_usadas": [...], "red_lab": "192.168.122"}
Salida (en el directorio actual):
  plan.json            lo que hará el playbook (método, plataforma, nodos y VM a crear, DNS, URLs)
  install-config.yaml  sin pullSecret ni sshKey (los agrega el playbook en el anfitrión, archivo 0600)
  agent-config.yaml    solo para el método agent
  openshift/*.yaml     manifiestos adicionales del usuario

El manifiesto puede ser:
  a) kind: ClusterOpenShift (formato de FaceOps, ver datos/openshift-manifiesto.yml): describe todo en un solo lugar.
  b) un install-config.yaml de OpenShift tal cual (y opcionalmente, separado por ---, un AgentConfig): se respeta como viene.
  c) vacío: se arma con los campos del formulario.
Siempre se puede sobrescribir cualquier parte del install-config con spec.install_config (fusión profunda)."""
import copy
import hashlib
import ipaddress
import json
import os
import re
import sys

import yaml

METODOS = {"agent", "ipi", "upi", "assisted"}
PLATAFORMAS = {"kvm", "baremetal", "vsphere", "gcp", "aws", "azure", "none"}
TOPOLOGIAS = {"sno": (1, 0), "compacto": (3, 0), "ha": (3, 2)}
# qué combinaciones soporta OpenShift (y este playbook)
VALIDAS = {
    "agent": {"kvm", "baremetal", "vsphere", "none"},
    "assisted": {"kvm", "baremetal"},
    "ipi": {"gcp", "aws", "azure", "vsphere"},
    "upi": {"kvm", "baremetal", "vsphere", "gcp", "aws", "azure", "none"},
}
RECURSOS = {  # mínimos de OpenShift para cada rol (vCPU, GB de RAM, GB de disco)
    "sno": {"cpu": 8, "ram_gb": 24, "disco_gb": 120},
    "master": {"cpu": 8, "ram_gb": 16, "disco_gb": 120},
    "worker": {"cpu": 4, "ram_gb": 16, "disco_gb": 120},
}
TIPOS_NUBE = {
    "gcp": {"master": "n2-standard-8", "worker": "n2-standard-4"},
    "aws": {"master": "m6i.2xlarge", "worker": "m6i.xlarge"},
    "azure": {"master": "Standard_D8s_v5", "worker": "Standard_D4s_v5"},
}
NOMBRES_APPS = ["console-openshift-console", "oauth-openshift", "canary-openshift-ingress-canary", "downloads-openshift-console",
                "grafana-openshift-monitoring", "prometheus-k8s-openshift-monitoring", "thanos-querier-openshift-monitoring",
                "alertmanager-main-openshift-monitoring", "default-route-openshift-image-registry"]


class Error(Exception):
    pass


def fusion(a, b):
    """Fusión profunda: b manda."""
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = fusion(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def mac_de(cluster, nodo):
    h = hashlib.sha1(f"{cluster}/{nodo}".encode()).hexdigest()
    return f"52:54:00:{h[0:2]}:{h[2:4]}:{h[4:6]}"


def leer_manifiesto(texto):
    docs = [d for d in yaml.safe_load_all(texto or "") if d] if (texto or "").strip() else []
    spec, ic, ac, extra = {}, None, None, []
    for d in docs:
        if not isinstance(d, dict):
            raise Error("El manifiesto debe ser YAML con objetos (clave: valor)")
        k = d.get("kind")
        if k == "ClusterOpenShift":
            spec = dict(d.get("spec") or {})
            spec.setdefault("nombre", (d.get("metadata") or {}).get("name"))
        elif k == "AgentConfig":
            ac = d
        elif "baseDomain" in d or k == "InstallConfig":
            ic = d
        elif k and d.get("apiVersion"):
            extra.append(d)  # un manifiesto de Kubernetes cualquiera: va a openshift/
        else:
            raise Error("No reconozco un documento del manifiesto: use kind: ClusterOpenShift, un install-config o un AgentConfig")
    return spec, ic, ac, extra


def normalizar(spec, campos, ic):
    s = {k: v for k, v in (campos or {}).items() if v not in (None, "", [])}
    s = fusion(s, spec)  # el manifiesto manda sobre el formulario
    if ic:  # install-config entregado tal cual: de él salen nombre, dominio y réplicas
        s.setdefault("nombre", (ic.get("metadata") or {}).get("name"))
        s.setdefault("dominio", ic.get("baseDomain"))
        cp = (ic.get("controlPlane") or {}).get("replicas", 3)
        wk = ((ic.get("compute") or [{}])[0]).get("replicas", 0)
        s.setdefault("topologia", "sno" if cp == 1 else ("compacto" if not wk else "ha"))
        s.setdefault("workers", wk)
        p = next(iter((ic.get("platform") or {"none": {}}).keys()))
        s.setdefault("plataforma", {"baremetal": "baremetal", "none": "none"}.get(p, p))
    s.setdefault("metodo", "agent")
    s.setdefault("plataforma", "kvm")
    s.setdefault("topologia", "sno")
    s.setdefault("dominio", "invoke.lab")
    s.setdefault("version", "4.19")
    s["metodo"], s["plataforma"], s["topologia"] = str(s["metodo"]).lower(), str(s["plataforma"]).lower(), str(s["topologia"]).lower()
    if not re.match(r"^[a-z][a-z0-9-]{1,20}$", str(s.get("nombre") or "")):
        raise Error("Nombre del clúster: minúsculas, números y guiones, 2 a 21 caracteres, empieza con letra")
    if s["metodo"] not in METODOS:
        raise Error(f"Método «{s['metodo']}» desconocido: {', '.join(sorted(METODOS))}")
    if s["plataforma"] not in PLATAFORMAS:
        raise Error(f"Plataforma «{s['plataforma']}» desconocida: {', '.join(sorted(PLATAFORMAS))}")
    if s["topologia"] not in TOPOLOGIAS:
        raise Error(f"Topología «{s['topologia']}» desconocida: sno (1 nodo), compacto (3 nodos) o ha (3 masters + workers)")
    if s["plataforma"] not in VALIDAS[s["metodo"]]:
        raise Error(f"OpenShift no instala con el método «{s['metodo']}» en «{s['plataforma']}». Válidas para ese método: "
                    + ", ".join(sorted(VALIDAS[s["metodo"]])))
    if s["metodo"] == "ipi" and s["topologia"] == "sno" and s["plataforma"] in ("vsphere", "azure"):
        raise Error("SNO con IPI solo está soportado en AWS y GCP; en vSphere use el método agent")
    if not re.match(r"^4\.\d{1,2}(\.\d{1,3})?$", str(s["version"])):
        raise Error("Versión: 4.y o 4.y.z (ej. 4.19 o 4.19.12)")
    m, w = TOPOLOGIAS[s["topologia"]]
    s["masters"] = m
    s["workers"] = int(s.get("workers", w) if s["topologia"] == "ha" else 0)
    if s["topologia"] == "ha" and s["workers"] < 2:
        raise Error("La topología ha necesita al menos 2 workers")
    s["red"] = fusion({"maquinas": "10.0.0.0/16" if s["plataforma"] in ("gcp", "aws", "azure") else "192.168.122.0/24", "pods": "10.128.0.0/14", "host_prefix": 23, "servicios": "172.30.0.0/16",
                       "tipo": "OVNKubernetes"}, s.get("red") or {})
    s["recursos"] = fusion(RECURSOS, s.get("recursos") or {})
    return s


def nodos_kvm(s, usadas, red_lab):
    """Nodos del clúster: los del manifiesto o, en KVM, asignados con IP libre del laboratorio y MAC fija por nombre."""
    pedidos = list(s.get("nodos") or [])
    libres = (f"{red_lab}.{i}" for i in range(51, 100) if f"{red_lab}.{i}" not in usadas)
    roles = ["master"] * s["masters"] + ["worker"] * s["workers"]
    out = []
    for i, rol in enumerate(roles):
        n = dict(pedidos[i]) if i < len(pedidos) else {}
        n.setdefault("rol", rol)
        idx = sum(1 for x in out if x["rol"] == n["rol"])
        n.setdefault("nombre", f"{s['nombre']}-{'m' if n['rol'] == 'master' else 'w'}{idx}")
        base = s["recursos"]["sno" if s["topologia"] == "sno" else n["rol"]]
        for k, v in base.items():
            n.setdefault(k, v)
        if s["plataforma"] == "kvm":
            n.setdefault("mac", mac_de(s["nombre"], n["nombre"]))
            n.setdefault("ip", next(libres))
            n.setdefault("interfaz", "enp1s0")
            n.setdefault("disco", "/dev/vda")
        elif s["plataforma"] == "baremetal" and s["metodo"] in ("agent", "assisted"):
            if not n.get("mac"):
                raise Error(f"Nodo {n['nombre']}: en baremetal cada nodo necesita su MAC (y su IP si no hay DHCP)")
        out.append(n)
    if s["plataforma"] == "kvm" and s["masters"] > 1:  # VIPs de API e ingress: libres, no son de ningún nodo
        ocupadas = usadas | {n["ip"] for n in out}
        libres2 = (f"{red_lab}.{i}" for i in range(51, 100) if f"{red_lab}.{i}" not in ocupadas)
        s.setdefault("vips", {})
        s["vips"].setdefault("api", next(libres2))
        s["vips"].setdefault("ingress", next(libres2))
    return out


def plataforma_ic(s):
    p, nube = s["plataforma"], s.get("nube") or {}
    multi = s["masters"] > 1
    if p in ("kvm", "baremetal"):
        if not multi or s["metodo"] == "upi":
            return {"none": {}}
        v = s.get("vips") or {}
        if not v.get("api") or not v.get("ingress"):
            raise Error("Clúster de 3 o más nodos en baremetal: faltan las VIPs de API e ingress (spec.vips.api / spec.vips.ingress)")
        return {"baremetal": {"apiVIPs": [v["api"]], "ingressVIPs": [v["ingress"]]}}
    if p == "none":
        return {"none": {}}
    if p == "vsphere":
        vs = s.get("vsphere") or {}
        falta = [k for k in ("vcenter", "datacenter", "cluster", "datastore", "red") if not vs.get(k)]
        if falta:
            raise Error("vSphere: faltan en spec.vsphere: " + ", ".join(falta))
        dom = {"name": "fd-1", "region": "region-1", "zone": "zone-1", "server": vs["vcenter"],
               "topology": {"datacenter": vs["datacenter"], "computeCluster": f"/{vs['datacenter']}/host/{vs['cluster']}",
                            "datastore": f"/{vs['datacenter']}/datastore/{vs['datastore']}", "networks": [vs["red"]],
                            **({"folder": vs["carpeta"]} if vs.get("carpeta") else {})}}
        out = {"vcenters": [{"server": vs["vcenter"], "user": "__VSPHERE_USER__", "password": "__VSPHERE_PASSWORD__",
                             "datacenters": [vs["datacenter"]]}], "failureDomains": [dom]}
        if multi or s["metodo"] == "ipi":
            v = s.get("vips") or {}
            if not v.get("api") or not v.get("ingress"):
                raise Error("vSphere: faltan las VIPs de API e ingress (spec.vips)")
            out.update({"apiVIPs": [v["api"]], "ingressVIPs": [v["ingress"]]})
        return {"vsphere": out}
    if not nube.get("region"):
        raise Error(f"{p.upper()}: falta la región (spec.nube.region)")
    if p == "gcp":
        if not nube.get("proyecto"):
            raise Error("GCP: falta el proyecto (spec.nube.proyecto)")
        return {"gcp": {"projectID": nube["proyecto"], "region": nube["region"]}}
    if p == "aws":
        return {"aws": {"region": nube["region"]}}
    if p == "azure":
        if not nube.get("grupo_recursos_dominio"):
            raise Error("Azure: falta el grupo de recursos de la zona DNS (spec.nube.grupo_recursos_dominio)")
        return {"azure": {"region": nube["region"], "baseDomainResourceGroupName": nube["grupo_recursos_dominio"],
                          "cloudName": "AzurePublicCloud"}}
    raise Error(f"plataforma {p}")


def maquinas_nube(s, rol):
    p, nube = s["plataforma"], s.get("nube") or {}
    if p not in TIPOS_NUBE:
        return {}
    tipo = nube.get(f"tipo_{rol}") or TIPOS_NUBE[p][rol]
    zonas = nube.get("zonas") or []
    d = {"type": tipo, **({"zones": zonas} if zonas else {})}
    return {"platform": {p: d}}


def install_config(s, ic_dado):
    if ic_dado:
        ic = copy.deepcopy(ic_dado)
    else:
        r = s["red"]
        ic = {"apiVersion": "v1", "baseDomain": s["dominio"], "metadata": {"name": s["nombre"]},
              "controlPlane": dict({"name": "master", "replicas": s["masters"], "hyperthreading": "Enabled", "architecture": "amd64"},
                                   **maquinas_nube(s, "master")),
              "compute": [dict({"name": "worker", "replicas": s["workers"], "hyperthreading": "Enabled", "architecture": "amd64"},
                               **maquinas_nube(s, "worker"))],
              "networking": {"networkType": r["tipo"], "machineNetwork": [{"cidr": r["maquinas"]}],
                             "clusterNetwork": [{"cidr": r["pods"], "hostPrefix": int(r["host_prefix"])}], "serviceNetwork": [r["servicios"]]},
              "platform": plataforma_ic(s)}
        if s.get("fips"):
            ic["fips"] = True
        if s.get("publicar"):
            ic["publish"] = "Internal" if str(s["publicar"]).lower().startswith("int") else "External"
        px = s.get("proxy") or {}
        if px.get("http") or px.get("https"):
            ic["proxy"] = {k2: v for k, k2 in (("http", "httpProxy"), ("https", "httpsProxy"), ("no_proxy", "noProxy")) if (v := px.get(k))}
        dc = s.get("desconectado") or {}
        if dc.get("registro"):
            fuentes = dc.get("espejos") or [
                {"source": "quay.io/openshift-release-dev/ocp-release", "mirrors": [f"{dc['registro']}/openshift/release-images"]},
                {"source": "quay.io/openshift-release-dev/ocp-v4.0-art-dev", "mirrors": [f"{dc['registro']}/openshift/release"]}]
            ic["imageDigestSources"] = fuentes
        if dc.get("ca") or s.get("ca_adicional"):
            ic["additionalTrustBundle"] = dc.get("ca") or s.get("ca_adicional")
            ic["additionalTrustBundlePolicy"] = "Always"
        if s.get("capacidades"):  # baseline y capacidades opcionales (ej. sin consola, sin marketplace)
            ic["capabilities"] = s["capacidades"]
    ic = fusion(ic, s.get("install_config") or {})
    for k in ("pullSecret", "sshKey"):  # nunca desde el manifiesto: los pone el playbook desde la credencial de AAP
        ic.pop(k, None)
    return ic


def agent_config(s, nodos, ac_dado):
    if ac_dado:
        return ac_dado
    ac = {"apiVersion": "v1beta1", "kind": "AgentConfig", "metadata": {"name": s["nombre"]}, "rendezvousIP": nodos[0].get("ip"),
          "hosts": []}
    if s.get("ntp") or s["plataforma"] == "kvm":
        ac["additionalNTPSources"] = s.get("ntp") or ["192.168.122.1"]
    if not ac["rendezvousIP"]:
        raise Error("Método agent: el primer master necesita IP (spec.nodos[0].ip): es el punto de encuentro de la instalación")
    for n in nodos:
        h = {"hostname": n["nombre"], "role": n["rol"], "interfaces": [{"name": n.get("interfaz", "eno1"), "macAddress": n["mac"]}]}
        if n.get("disco"):
            h["rootDeviceHints"] = {"deviceName": n["disco"]}
        if n.get("ip") and n.get("estatica"):  # red estática con NMState (sin DHCP)
            red = ipaddress.ip_network(s["red"]["maquinas"])
            h["networkConfig"] = {
                "interfaces": [{"name": n.get("interfaz", "eno1"), "type": "ethernet", "state": "up", "mac-address": n["mac"],
                                "ipv4": {"enabled": True, "dhcp": False, "address": [{"ip": n["ip"], "prefix-length": red.prefixlen}]}}],
                "dns-resolver": {"config": {"server": s.get("dns") or [n.get("gateway") or str(next(red.hosts()))]}},
                "routes": {"config": [{"destination": "0.0.0.0/0", "next-hop-address": n.get("gateway") or str(next(red.hosts())),
                                       "next-hop-interface": n.get("interfaz", "eno1")}]}}
        ac["hosts"].append(h)
    return ac


def main():
    entrada = json.load(open(sys.argv[1]))
    spec, ic_dado, ac_dado, extra = leer_manifiesto(entrada.get("manifiesto", ""))
    s = normalizar(spec, entrada.get("campos"), ic_dado)
    usadas = set(entrada.get("ips_usadas") or [])
    nodos = nodos_kvm(s, usadas, entrada.get("red_lab", "192.168.122")) if s["plataforma"] in ("kvm", "baremetal") else []
    ic = install_config(s, ic_dado)
    with open("install-config.yaml", "w") as f:
        yaml.safe_dump(ic, f, sort_keys=False, allow_unicode=True)
    if s["metodo"] == "agent":
        if s["plataforma"] == "vsphere" and not nodos:
            nodos = [dict(n) for n in (s.get("nodos") or [])]
            if len(nodos) != s["masters"] + s["workers"] or not all(n.get("mac") for n in nodos):
                raise Error("Agent en vSphere: declare en spec.nodos cada VM con su MAC e IP (las crea vSphere, no FaceOps)")
        with open("agent-config.yaml", "w") as f:
            yaml.safe_dump(agent_config(s, nodos, ac_dado), f, sort_keys=False, allow_unicode=True)
    os.makedirs("openshift", exist_ok=True)
    for i, d in enumerate(extra + list(s.get("manifiestos_extra") or [])):
        d = yaml.safe_load(d) if isinstance(d, str) else d
        nombre = re.sub(r"[^a-z0-9-]", "-", f"{d.get('kind', 'manifiesto')}-{(d.get('metadata') or {}).get('name', i)}".lower())
        with open(f"openshift/99-faceops-{nombre}.yaml", "w") as f:
            yaml.safe_dump(d, f, sort_keys=False, allow_unicode=True)
    multi = s["masters"] > 1
    vips = s.get("vips") or {}
    ip_api = vips.get("api") if multi else (nodos[0]["ip"] if nodos else None)
    ip_apps = vips.get("ingress") if multi else ip_api
    plan = {"nombre": s["nombre"], "dominio": s["dominio"], "version": str(s["version"]), "metodo": s["metodo"], "plataforma": s["plataforma"],
            "topologia": s["topologia"], "masters": s["masters"], "workers": s["workers"], "nodos": nodos,
            "crear_vms": s["plataforma"] == "kvm" and s["metodo"] in ("agent", "assisted"),
            "dns": ([{"ip": ip_api, "nombres": [f"api.{s['nombre']}.{s['dominio']}", f"api-int.{s['nombre']}.{s['dominio']}"]},
                     {"ip": ip_apps, "nombres": [f"{h}.apps.{s['nombre']}.{s['dominio']}" for h in NOMBRES_APPS]}]
                    if s["plataforma"] == "kvm" and ip_api else []),
            "api": f"https://api.{s['nombre']}.{s['dominio']}:6443", "consola": f"https://console-openshift-console.apps.{s['nombre']}.{s['dominio']}",
            "vips": vips, "nube": s.get("nube") or {}, "ram_total_gb": sum(int(n["ram_gb"]) for n in nodos),
            "cpu_total": sum(int(n["cpu"]) for n in nodos), "disco_total_gb": sum(int(n["disco_gb"]) for n in nodos),
            "extra": len(os.listdir("openshift"))}
    if s["topologia"] == "sno" and ip_api:  # en SNO el nodo es api y apps a la vez: un solo registro DNS
        plan["dns"] = [{"ip": ip_api, "nombres": plan["dns"][0]["nombres"] + plan["dns"][1]["nombres"]}]
    json.dump(plan, open("plan.json", "w"), indent=1, ensure_ascii=False)
    print(json.dumps({k: plan[k] for k in ("nombre", "metodo", "plataforma", "topologia", "masters", "workers", "api", "consola")}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (Error, yaml.YAMLError) as e:
        print(f"MANIFIESTO INVÁLIDO: {e}", file=sys.stderr)
        sys.exit(2)
