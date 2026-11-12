#!/usr/bin/python3
"""Construye la IMAGEN BASE de la «Demo de FaceOps» (familia faceops-demo) desde la VM de FaceAdmin, con la cuenta de servicio de esa
VM (servidor de metadatos: sin claves ni gcloud). Solo biblioteca estándar.

    python3 construir-imagen.py [--rama feature/faceops-awx] [--tipo n2-standard-4] [--conservar]

1) VM constructora demo-constructor-<fecha>: Rocky Linux 9 (imagen pública de GCP sin costo de licencia), anidada, tope de 4 h
   (acción DELETE: nunca queda corriendo). Su startup-script clona el repositorio y corre preparar-vm.sh, que incluye la PRUEBA REAL
   de FaceOps en modo awx; si pasa, generaliza y se apaga.
2) Se sigue el avance por el puerto serie (solo las líneas de paso; nunca hay claves en ellas).
3) Imagen faceops-demo-<fecha> en la familia faceops-demo desde el disco; se borra la constructora y se dejan las 2 últimas imágenes."""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

PROY, ZONA = "master-ia-320218", "southamerica-west1-a"
REGION = ZONA.rsplit("-", 1)[0]
API = f"https://compute.googleapis.com/compute/v1/projects/{PROY}"
META = "http://metadata.google.internal/computeMetadata/v1"
REPO = "https://github.com/cquezada-invoke/summit-redhat.git"
FAMILIA = "faceops-demo"
_tok = {"t": "", "v": 0}


def token():
    if time.time() > _tok["v"]:
        r = urllib.request.Request(f"{META}/instance/service-accounts/default/token", headers={"Metadata-Flavor": "Google"})
        d = json.load(urllib.request.urlopen(r, timeout=10))
        _tok.update(t=d["access_token"], v=time.time() + int(d.get("expires_in", 300)) - 60)
    return _tok["t"]


def api(m, ruta, d=None, ok404=False):
    url = ruta if ruta.startswith("http") else f"{API}/{ruta}"
    r = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            b = x.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if ok404 and e.code == 404:
            return None
        raise SystemExit(f"GCP {m} {url.split('?')[0].replace(API, '')} -> {e.code}: {e.read()[:400].decode(errors='replace')}")


def esperar_op(op, tope=900):
    ruta = f"zones/{ZONA}/operations/{op['name']}" if op.get("zone") else f"global/operations/{op['name']}"
    for _ in range(tope // 5):
        o = api("GET", ruta)
        if o.get("status") == "DONE":
            if o.get("error"):
                raise SystemExit(f"operación con error: {json.dumps(o['error'])[:400]}")
            return o
        time.sleep(5)
    raise SystemExit("operación sin terminar a tiempo")


ARRANQUE = r"""#!/bin/bash
[ -f /var/lib/demo/construida ] && exit 0
s() { echo "$*"; echo "$*" > /dev/ttyS0; }
s "== CONSTRUCCION inicio $(date +%T)"
dnf -y -q install git >/dev/null 2>&1
rm -rf /opt/prueba/fuente; git clone -q --depth 1 -b "__RAMA__" __REPO__ /opt/prueba/fuente || { s "CONSTRUCCION FALLO (git clone)"; exit 1; }
s "== fuente $(git -C /opt/prueba/fuente log -1 --format=%h)"
if RAMA="__RAMA__" REPO=__REPO__ bash /opt/prueba/fuente/herramientas/demo-gcp/preparar-vm.sh 2>&1 | tee /var/log/demo-construccion.log \
     | grep --line-buffered -E '^== |✓|✗|verificaciones|ERROR|FALL|Traceback|rror:|^    |^(proyecto|AWX:|EDA:|FaceOps)' | while read -r l; do s "$l"; done; [ "${PIPESTATUS[0]}" = 0 ]; then
  s "== generalizar"
  rm -f /etc/ssh/ssh_host_* ; truncate -s0 /etc/machine-id; rm -f /var/lib/demo/credenciales.listo /var/lib/demo/ultimo-uso
  journalctl --rotate >/dev/null 2>&1; journalctl --vacuum-time=1s >/dev/null 2>&1
  install -d /var/lib/demo; touch /var/lib/demo/construida; sync
  s "CONSTRUCCION OK"; sleep 5; poweroff
else
  export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=$PATH:/usr/local/bin
  { echo "== DIAG log"; tail -40 /var/log/demo-construccion.log; echo "== DIAG pods"; kubectl get pods -A 2>&1; for ns in awx eda; do for d in $(kubectl -n $ns get deploy -o name 2>/dev/null); do
      echo "== DIAG $ns $d"; kubectl -n $ns logs $d --all-containers --tail=15 2>&1; done; done; free -m; } 2>&1 \
    | grep -viE 'clave|password|passwd|token|secret' | cut -c1-240 | sed 's/^/DIAG /' > /dev/ttyS0
  sleep 5; s "CONSTRUCCION FALLO"
fi
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rama", default="feature/faceops-awx")
    ap.add_argument("--tipo", default="n2-standard-4")
    ap.add_argument("--disco", type=int, default=60)
    ap.add_argument("--conservar", action="store_true", help="no borrar la constructora si falla (para revisar)")
    a = ap.parse_args()
    t0 = time.time()
    vm = "demo-constructor-" + time.strftime("%Y%m%d%H%M")
    print(f"· constructora {vm} ({a.tipo}, Rocky Linux 9, anidada, {a.disco} GB) · rama {a.rama}", flush=True)
    cuerpo = {
        "name": vm, "machineType": f"zones/{ZONA}/machineTypes/{a.tipo}", "minCpuPlatform": "Intel Cascade Lake",
        "labels": {"faceadmin-demo": "constructor"}, "advancedMachineFeatures": {"enableNestedVirtualization": True},
        "disks": [{"boot": True, "autoDelete": True, "initializeParams": {
            "sourceImage": "projects/rocky-linux-cloud/global/images/family/rocky-linux-9-optimized-gcp",
            "diskSizeGb": str(a.disco), "diskType": f"zones/{ZONA}/diskTypes/pd-balanced"}}],
        "networkInterfaces": [{"network": "global/networks/default", "accessConfigs": [{"name": "externa", "type": "ONE_TO_ONE_NAT"}]}],
        "metadata": {"items": [{"key": "startup-script", "value": ARRANQUE.replace("__RAMA__", a.rama).replace("__REPO__", REPO)},
                               {"key": "serial-port-logging-enable", "value": "true"}]},
        "tags": {"items": ["faceadmin-demo"]},
        "scheduling": {"maxRunDuration": {"seconds": 4 * 3600}, "instanceTerminationAction": "DELETE", "automaticRestart": False,
                       "onHostMaintenance": "MIGRATE"},
    }
    esperar_op(api("POST", f"zones/{ZONA}/instances", cuerpo))
    pos, fin, ok = 0, time.time() + 4 * 3600, None
    try:
        while time.time() < fin and ok is None:
            time.sleep(20)
            s = api("GET", f"zones/{ZONA}/instances/{vm}/serialPort?port=1&start={pos}", ok404=True)
            if s is None:
                print("· la constructora ya no existe", flush=True)
                ok = False
                break
            pos = int(s.get("next", pos))
            for l in s.get("contents", "").splitlines():
                l = l.rstrip()
                # solo lo que escribe el startup-script con s() (sin el prefijo de fecha del journal ni el del kernel)
                if l and not re.match(r"^([A-Z][a-z]{2} +\d|\[ *\d|\x1b|<\d>)", l) and "Cloud Logging" not in l:
                    if not any(k in l.lower() for k in ("clave=", "password=", "token=")):
                        print("  " + l[:300], flush=True)
                    if "CONSTRUCCION OK" in l:
                        ok = True
                    elif "CONSTRUCCION FALLO" in l:
                        ok = False
        if not ok:
            print("· construcción FALLIDA" + (" (tope de tiempo)" if ok is None else ""), flush=True)
            return 1
        for _ in range(60):
            if (api("GET", f"zones/{ZONA}/instances/{vm}") or {}).get("status") == "TERMINATED":
                break
            time.sleep(5)
        else:
            esperar_op(api("POST", f"zones/{ZONA}/instances/{vm}/stop"))
        img = f"{FAMILIA}-" + time.strftime("%Y%m%d-%H%M")
        print(f"· imagen {img} (familia {FAMILIA})", flush=True)
        esperar_op(api("POST", "global/images", {"name": img, "family": FAMILIA, "sourceDisk": f"zones/{ZONA}/disks/{vm}",
                                                 "storageLocations": [REGION], "labels": {"faceadmin-demo": "imagen"},
                                                 "description": f"Demo de FaceOps: Rocky Linux 9 + k3s + AWX 24.6.1 + EDA + FaceOps (modo awx) · {a.rama}"}), 1800)
        im = api("GET", f"global/images/{img}")
        print(f"· imagen lista: {im['name']} · {im['status']} · {im.get('diskSizeGb')} GB · {round(int(im.get('archiveSizeBytes', 0)) / 2**30, 1)} GB comprimida", flush=True)
        imgs = sorted((x for x in (api("GET", f"global/images?filter=family%3D{FAMILIA}") or {}).get("items", [])), key=lambda x: x["creationTimestamp"])
        for x in imgs[:-2]:
            api("DELETE", f"global/images/{x['name']}")
            print(f"· imagen antigua {x['name']} eliminada", flush=True)
        print(f"CONSTRUCCION TERMINADA en {round((time.time() - t0) / 60)} min", flush=True)
        return 0
    finally:
        if ok or not a.conservar:
            if api("GET", f"zones/{ZONA}/instances/{vm}", ok404=True):
                api("DELETE", f"zones/{ZONA}/instances/{vm}")
                print(f"· constructora {vm} eliminada", flush=True)


if __name__ == "__main__":
    sys.exit(main())
