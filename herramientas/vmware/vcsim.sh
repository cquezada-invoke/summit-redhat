#!/bin/bash
# VMware vSphere de laboratorio con vcsim (simulador oficial de la API de vCenter, govmomi). Idempotente.
#  · contenedor faceops-vcsim en 192.168.122.1:8989 (red de las VM; AAP llega igual que a cualquier vCenter)
#  · modelo: datacenter «Santiago», clúster «Produccion» con 3 ESXi y VM con nombres y sistemas operativos reales,
#    guardado en /var/lib/faceops-vcsim/modelo (sobrevive reinicios)
#  · la clave del usuario faceops se genera aquí, queda en /var/lib/faceops-vcsim/clave (0600)
#    y en la bóveda (faceops/web/vcsim); nunca se imprime
#  · FaceOps incorpora «VMware · Santiago» (centro de datos Santiago): credencial «VMware vCenter» e inventario
#    con fuente vmware en AAP
# Las VM son simuladas: la API responde como un vCenter real (inventario, encender/apagar, crear, clonar), pero no
# hay sistema operativo dentro, así que no tienen SSH.
set -euo pipefail
V=/var/lib/faceops-vcsim; IMG=docker.io/vmware/vcsim:v0.52.0; GOVC=docker.io/vmware/govc:v0.52.0
ESCUCHA=${VCSIM_ESCUCHA:-192.168.122.1:8989}; USR=faceops   # vcsim no acepta «@dominio» en el usuario
R=$(cd "$(dirname "$0")/../.." && pwd)
paso() { echo "== $*"; }

sudo install -d -m 0700 $V
sudo test -s $V/clave || (umask 077; python3 -c "import secrets;print(secrets.token_urlsafe(18))" | sudo tee $V/clave >/dev/null)

govc() { sudo podman run --rm --network host -e GOVC_URL=https://127.0.0.1:18989/sdk -e GOVC_INSECURE=1 \
  -e GOVC_USERNAME=$USR -e GOVC_PASSWORD="$(sudo cat $V/clave)" -v $V:/data:z $GOVC /govc "$@"; }

if [ -z "$(sudo ls -A $V/modelo 2>/dev/null)" ]; then
  paso "Modelo inicial del vCenter"
  sudo podman rm -f vcsim-armado >/dev/null 2>&1 || true
  sudo podman run -d --name vcsim-armado --network host $IMG -E /dev/null -l 127.0.0.1:18989 -dc 1 -cluster 1 -host 3 -vm 0 \
    -standalone-host 0 -ds 2 -pool 0 -username $USR -password "$(sudo cat $V/clave)" >/dev/null
  sleep 3
  govc object.rename /DC0 Santiago
  govc object.rename /Santiago/host/DC0_C0 Produccion
  i=1; for h in $(govc find /Santiago/host/Produccion -type h); do govc object.rename "$h" "esxi-stgo-0$i.invoke.lab" || true; i=$((i+1)); done
  govc folder.create /Santiago/vm/Produccion /Santiago/vm/Legado >/dev/null
  P=/Santiago/host/Produccion/Resources
  # nombre | carpeta | vCPU | RAM MB | disco | guestId | ds | encendida
  while IFS='|' read -r n f c m d g ds on; do
    govc vm.create -folder /Santiago/vm/$f -pool $P -ds $ds -net "VM Network" -c $c -m $m -disk $d -g $g -on=$on "$n"
    govc vm.change -vm "$n" -e "faceops.origen=vcsim" -annotation "Invoke Lab · simulada en vcsim"
  done <<'L'
srv-erp-app01|Produccion|4|8192|80G|rhel9_64Guest|LocalDS_0|true
srv-erp-app02|Produccion|4|8192|80G|rhel9_64Guest|LocalDS_0|true
srv-erp-bd01|Produccion|8|32768|500G|rhel8_64Guest|LocalDS_1|true
srv-ad-dc02|Produccion|2|4096|60G|windows2019srv_64Guest|LocalDS_0|true
srv-archivos01|Produccion|2|4096|1T|windows2019srv_64Guest|LocalDS_1|true
srv-legado-jboss5|Legado|2|4096|40G|centos6_64Guest|LocalDS_1|true
srv-legado-reportes|Legado|1|2048|40G|rhel6_64Guest|LocalDS_1|false
L
  sudo install -d -m 0700 $V/modelo
  govc object.save -d /data/modelo -f /Santiago >/dev/null 2>&1 || govc object.save -d /data/modelo -f >/dev/null
  sudo podman rm -f vcsim-armado >/dev/null
fi

paso "Servicio faceops-vcsim en $ESCUCHA"
sudo tee /etc/containers/systemd/faceops-vcsim.container >/dev/null <<U
[Unit]
Description=FaceOps · VMware vSphere de laboratorio (vcsim)
After=network-online.target libvirtd.service
[Container]
Image=$IMG
ContainerName=faceops-vcsim
Network=host
Volume=$V/modelo:/modelo:Z
Exec=-E /dev/null -l $ESCUCHA -load /modelo -username $USR -password $(sudo cat $V/clave)
[Service]
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
U
sudo chmod 0600 /etc/containers/systemd/faceops-vcsim.container
sudo systemctl daemon-reload && sudo systemctl restart faceops-vcsim && sleep 4
sudo firewall-cmd -q --zone=libvirt --query-port=8989/tcp || { sudo firewall-cmd -q --zone=libvirt --add-port=8989/tcp; sudo firewall-cmd -q --permanent --zone=libvirt --add-port=8989/tcp; }
sudo podman run --rm --network host -e GOVC_URL=https://$ESCUCHA/sdk -e GOVC_INSECURE=1 -e GOVC_USERNAME=$USR \
  -e GOVC_PASSWORD="$(sudo cat $V/clave)" $GOVC /govc ls -l '/Santiago/vm/*' | sed 's/^/   · /'

paso "Clave en la bóveda (faceops/web/vcsim)"
CLAVE_FILE=$V/clave python3 - <<'PY'
import json, os, subprocess, tempfile, urllib.request
clave = subprocess.run(["sudo", "cat", os.environ["CLAVE_FILE"]], capture_output=True, text=True, check=True).stdout.strip()
llave = subprocess.run(["sudo", "podman", "exec", "faceops", "cat", "/var/lib/faceops/llaves/id_demo"], capture_output=True, text=True, check=True).stdout
fd, kf = tempfile.mkstemp(); os.write(fd, llave.encode()); os.close(fd); os.chmod(kf, 0o600)
try:
    out = subprocess.run(["ssh", "-i", kf, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                          "ansible@192.168.122.210", "sudo cat /root/vault-init.json"], capture_output=True, text=True, check=True).stdout
finally:
    os.remove(kf)
tok = json.loads(out)["root_token"]
r = urllib.request.Request("http://192.168.122.210:8200/v1/faceops/data/web/vcsim", method="POST",
                           data=json.dumps({"data": {"sitio": "VMware vSphere (vcsim) · Santiago", "usuario": "faceops", "clave": clave,
                                                     "url": "https://192.168.122.1:8989/sdk"}}).encode(),
                           headers={"X-Vault-Token": tok, "Content-Type": "application/json"})
urllib.request.urlopen(r, timeout=15).read()
print("   · guardada")
PY

paso "FaceOps: infraestructura «VMware · Santiago»"
cd $R && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 12
sudo podman exec -i -e FACEOPS_VIGIA=0 -e VC_CLAVE="$(sudo cat $V/clave)" faceops python3 - <<'PY'
import os
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app import infraestructuras as I
    cli = q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
    usr = {"usuario": "cquezada", "id": 1}
    x = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre='VMware · Santiago'", cli["id"])
    f = {"tipo": "vmware", "nombre": "VMware · Santiago", "centro_datos": "Centro de datos Santiago", "host": "192.168.122.1:8989",
         "usuario": "faceops", "clave": os.environ["VC_CLAVE"], "datacenter": "Santiago", "carpeta": "/Santiago/vm", "tls": "no"}
    if x:
        I.conectar(usr, cli, x["id"], f)
    else:
        I.incorporar(usr, cli, f)
    x = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre='VMware · Santiago'", cli["id"])
    print("   · prueba de acceso:", *I.probar(usr, cli, x["id"]))
PY
