#!/usr/bin/python3
# Exportador simple del estado del ecosistema (corre en la VM anfitriona como root, cada 20 s).
# Escribe métricas en el textfile collector del node_exporter local:
#   summit_eda_activacion{nombre}            1 = running (Event-Driven Ansible)
#   summit_contenedor{nombre,grupo}          1 = contenedor arriba (laboratorio de red y DNS)
#   summit_bgp_establecido{router,vecino}    1 = sesión BGP Established (FRR)
#   summit_firewall_base                     1 = la configuración base del firewall VyOS está activa
#   summit_tekton_listo                      1 = pods de Tekton (tekton-pipelines) en Running
# No imprime claves.
import base64, glob, json, os, re, ssl, subprocess, time, urllib.request

SALIDA = "/var/lib/node_exporter/textfile/summit_ecosistema.prom"
INV = glob.glob("/home/cquezada/aap/*/inventory-demo")
CTX = ssl._create_unverified_context()


def sh(cmd, t=15):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t).stdout
    except Exception:
        return ""


def esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def medir():
    m = []
    # EDA
    try:
        cl = re.search(r"^gateway_admin_password=(.*)$", open(INV[0]).read(), re.M).group(1).strip()
        au = "Basic " + base64.b64encode(f"admin:{cl}".encode()).decode()
        r = urllib.request.Request("https://127.0.0.1/api/eda/v1/activations/?page_size=50", headers={"Authorization": au})
        for a in json.load(urllib.request.urlopen(r, context=CTX, timeout=15))["results"]:
            m.append(f'summit_eda_activacion{{nombre="{esc(a["name"])}"}} {1 if a["status"] == "running" else 0}')
    except Exception:
        m.append('summit_eda_activacion{nombre="(API de EDA)"} 0')
    # Contenedores del laboratorio de red y DNS
    esperados = ["clab-summit-red-isp", "clab-summit-red-rtr", "clab-summit-red-fw", "clab-summit-red-pc-usuario",
                 "clab-summit-red-srv-app", "summit-dns"]
    arriba = set(sh("podman ps --format '{{.Names}}'").split())
    for n in esperados:
        m.append(f'summit_contenedor{{nombre="{n}",grupo="laboratorio de red"}} {1 if n in arriba else 0}')
    # BGP del router de borde
    est = 0
    try:
        d = json.loads(sh("podman exec clab-summit-red-rtr vtysh -c 'show bgp summary json'") or "{}")
        est = 1 if d.get("ipv4Unicast", {}).get("peers", {}).get("10.0.0.1", {}).get("state") == "Established" else 0
    except Exception:
        pass
    m.append(f'summit_bgp_establecido{{router="rtr",vecino="10.0.0.1 (ISP)"}} {est}')
    # Firewall VyOS: base activa
    # Lee el árbol de la configuración activa (sin abrir una sesión de CLI: cada "script-template" deja un
    # unionfs-fuse huérfano y, cada 20 s, llenaba el límite de procesos del contenedor en ~30 min).
    ok = subprocess.run("podman exec clab-summit-red-fw test -d /opt/vyatta/config/active/firewall/group/network-group/red-usuarios",
                        shell=True, capture_output=True, timeout=20).returncode == 0 if "clab-summit-red-fw" in arriba else False
    m.append(f"summit_firewall_base {1 if ok else 0}")
    # Tekton
    pods = sh("KUBECONFIG=/etc/rancher/k3s/k3s.yaml /usr/local/bin/kubectl get pods -n tekton-pipelines --no-headers").splitlines()
    m.append(f"summit_tekton_listo {1 if pods and all(' Running ' in p for p in pods) else 0}")
    return m


while True:
    lineas = ["# Estado del ecosistema de las demos (estado_ecosistema.py)"] + medir()
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    tmp = SALIDA + ".tmp"
    open(tmp, "w").write("\n".join(lineas) + "\n")
    os.replace(tmp, SALIDA)
    time.sleep(20)
