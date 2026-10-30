#!/usr/bin/env python3
"""FaceOps · Habilitar OpenShift con el Assisted Installer de console.redhat.com (método «assisted»).
  ocp_assisted.py iso       <dir>   crea el clúster y su infra-env en console.redhat.com y descarga la ISO de descubrimiento
  ocp_assisted.py instalar  <dir>   espera que los nodos se registren, lanza la instalación, espera y baja kubeconfig y clave
En <dir>: plan.json, install-config.yaml (red), .pull-secret, .ssh.pub y .ocm-token (offline token; 0600, se borran al final).
Nada secreto se imprime."""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.openshift.com/api/assisted-install/v2"
SSO = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"


def token(d):
    datos = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": "cloud-services",
                                    "refresh_token": open(f"{d}/.ocm-token").read().strip()}).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request(SSO, data=datos), timeout=60))["access_token"]


def api(d, met, ruta, cuerpo=None, crudo=False):
    req = urllib.request.Request(API + ruta, method=met, data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
                                 headers={"Authorization": "Bearer " + token(d), "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        sys.exit(f"Assisted Installer {met} {ruta.split('?')[0]}: {e.code} {e.read()[:400].decode(errors='replace')}")
    b = r.read()
    return b if crudo else (json.loads(b) if b else {})


def iso(d):
    import yaml
    plan, ic = json.load(open(f"{d}/plan.json")), yaml.safe_load(open(f"{d}/install-config.yaml"))
    ps, ssh = open(f"{d}/.pull-secret").read().strip(), open(f"{d}/.ssh.pub").read().strip()
    red = ic.get("networking") or {}
    ya = [c for c in api(d, "GET", "/clusters") if c["name"] == plan["nombre"] and c.get("base_dns_domain") == plan["dominio"]]
    cuerpo = {"name": plan["nombre"], "openshift_version": plan["version"], "base_dns_domain": plan["dominio"], "pull_secret": ps,
              "ssh_public_key": ssh, "cpu_architecture": "x86_64", "high_availability_mode": "None" if plan["masters"] == 1 else "Full",
              "network_type": red.get("networkType", "OVNKubernetes"),
              "cluster_networks": [{"cidr": c["cidr"], "host_prefix": c["hostPrefix"]} for c in red.get("clusterNetwork", [])],
              "service_networks": [{"cidr": c} for c in red.get("serviceNetwork", [])],
              "machine_networks": [{"cidr": c["cidr"]} for c in red.get("machineNetwork", [])],
              "additional_ntp_source": "192.168.122.1" if plan["plataforma"] == "kvm" else ""}
    if plan["masters"] > 1:
        cuerpo.update({"api_vips": [{"ip": plan["vips"]["api"]}], "ingress_vips": [{"ip": plan["vips"]["ingress"]}]})
    cl = ya[0] if ya else api(d, "POST", "/clusters", cuerpo)
    envs = [e for e in api(d, "GET", f"/infra-envs?cluster_id={cl['id']}")]
    ie = envs[0] if envs else api(d, "POST", "/infra-envs", {"name": f"{plan['nombre']}-faceops", "cluster_id": cl["id"], "pull_secret": ps,
                                                             "ssh_authorized_key": ssh, "cpu_architecture": "x86_64", "image_type": "full-iso",
                                                             "openshift_version": plan["version"]})
    url = api(d, "GET", f"/infra-envs/{ie['id']}/downloads/image-url")["url"]
    urllib.request.urlretrieve(url, f"{d}/discovery.iso")
    json.dump({"cluster_id": cl["id"], "infra_env_id": ie["id"]}, open(f"{d}/assisted.json", "w"))
    print(f"clúster {cl['id']} · ISO de descubrimiento lista")


def instalar(d):
    plan, ids = json.load(open(f"{d}/plan.json")), json.load(open(f"{d}/assisted.json"))
    cid, n = ids["cluster_id"], plan["masters"] + plan["workers"]
    for _ in range(120):  # hasta 60 min a que los nodos arranquen la ISO y queden listos
        hs = api(d, "GET", f"/infra-envs/{ids['infra_env_id']}/hosts")
        listos = [h for h in hs if h.get("status") in ("known", "ready")]
        if len(listos) >= n:
            break
        time.sleep(30)
    else:
        sys.exit(f"Solo {len(listos)} de {n} nodos quedaron listos en el Assisted Installer")
    for _ in range(40):
        st = api(d, "GET", f"/clusters/{cid}")["status"]
        if st == "ready":
            break
        time.sleep(15)
    api(d, "POST", f"/clusters/{cid}/actions/install")
    for _ in range(240):  # hasta 2 h
        c = api(d, "GET", f"/clusters/{cid}")
        if c["status"] == "installed":
            break
        if c["status"] == "error":
            sys.exit("Instalación con error: " + (c.get("status_info") or "")[:400])
        time.sleep(30)
    else:
        sys.exit("La instalación no terminó en 2 horas")
    os.makedirs(f"{d}/auth", mode=0o700, exist_ok=True)
    for f in ("kubeconfig", "kubeadmin-password"):
        open(f"{d}/auth/{f}", "wb").write(api(d, "GET", f"/clusters/{cid}/downloads/credentials?file_name={f}", crudo=True))
        os.chmod(f"{d}/auth/{f}", 0o600)
    print("instalado")


if __name__ == "__main__":
    {"iso": iso, "instalar": instalar}[sys.argv[1]](sys.argv[2])
