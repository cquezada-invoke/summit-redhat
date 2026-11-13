#!/bin/bash
# BMC simulado del laboratorio (sin hardware real): contenedor liviano con sshd que responde con un prompt tipo iBMC
# («iBMC:/->») y unos pocos comandos de consulta. Escucha en el anfitrión KVM (192.168.122.1:2222), donde lo alcanza la
# consola de FaceOps (Guacamole · faceops-guacd). Su credencial queda en la bóveda (faceops/maquinas/<nodo>-bmc), igual que
# la de las máquinas; la clave se genera aquí, no se imprime y no queda en el disco.
# Uso (en el anfitrión): bash bmc-simulado.sh <nombre-del-nodo-físico | #id> [puerto]
set -euo pipefail
NODO=${1:?nombre del nodo físico o #<id>}; PUERTO=${2:-2222}; CT=faceops-bmc-sim; IMG=localhost/faceops-bmc-sim:1
if [ "${NODO#\#}" != "$NODO" ]; then   # «#97»: el nombre del equipo con ese id en FaceOps
  NODO=$(sudo podman exec -i -e FACEOPS_VIGIA=0 -w /opt/faceops -e PYTHONPATH=/opt/faceops faceops python3 - "${NODO#\#}" 2>/dev/null <<'Q' | tail -1
import sys
from app import create_app
create_app()
from app.bd import q1
print((q1("SELECT nombre FROM equipos WHERE id=?", int(sys.argv[1])) or {}).get("nombre", ""))
Q
); [ -n "$NODO" ] || { echo "no existe ese equipo"; exit 1; }; echo "nodo físico: $NODO"
fi
D=$(mktemp -d); trap 'rm -rf "$D"' EXIT
cat > "$D/ibmc.sh" <<'SH'
#!/bin/bash
# Shell restringido que imita la línea de comandos de un iBMC (xFusion/Huawei). Solo consultas simuladas.
echo "Info: Login from the management network (simulated iBMC · FaceOps lab)."
while true; do
  printf 'iBMC:/->'; IFS= read -r c || exit 0
  case "$c" in
    "") ;;
    exit|quit|logout) exit 0 ;;
    "ipmcget -d version") printf 'iBMC Version:            (U68)3.08.00.36\nBIOS Version:            (U47)2.67\nCPLD Version:            (U6076)1.02\n' ;;
    "ipmcget -d health")  printf 'System in health state\n' ;;
    "ipmcget -d powerstate") printf 'Power state   : On\n' ;;
    "ipmcget -d serialnumber") printf 'Serial number : SIMULADO\n' ;;
    "ipmcget -d ipinfo") printf 'IP Mode       : static\nIP Address    : (simulado)\n' ;;
    help|"?") printf 'Comandos simulados: ipmcget -d version | health | powerstate | serialnumber | ipinfo · exit\n' ;;
    *) printf 'Error: unknown command (iBMC simulado: escribe help)\n' ;;
  esac
done
SH
cat > "$D/Containerfile" <<'CF'
FROM registry.access.redhat.com/ubi9/ubi:latest
RUN dnf -y install openssh-server passwd && dnf clean all && ssh-keygen -A && useradd -m -s /usr/local/bin/ibmc.sh Administrator \
 && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config && rm -f /run/nologin
COPY ibmc.sh /usr/local/bin/ibmc.sh
RUN chmod 755 /usr/local/bin/ibmc.sh && echo /usr/local/bin/ibmc.sh >> /etc/shells
CMD ["/usr/sbin/sshd", "-D", "-e"]
CF
sudo podman build -q -t $IMG "$D" >/dev/null
CLAVE=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)
sudo podman rm -f $CT >/dev/null 2>&1 || true
sudo podman run -d --name $CT --restart=always --cap-add AUDIT_WRITE -p 192.168.122.1:$PUERTO:22 $IMG >/dev/null
for i in $(seq 1 20); do sudo podman exec $CT true 2>/dev/null && break; sleep 1; done
printf 'Administrator:%s\n' "$CLAVE" | sudo podman exec -i $CT chpasswd
# credencial en la bóveda (mismo camino que las demás: faceops/data/maquinas/<nodo>-bmc), con el token raíz del nodo de la bóveda
VAULT=${FACEOPS_VAULT:-http://192.168.122.210:8200}; IPV=$(echo "$VAULT" | sed -E 's#https?://([^:/]+).*#\1#')
K=$(mktemp); sudo podman exec faceops cat /var/lib/faceops/llaves/id_demo > "$K"; chmod 600 "$K"
ROOT=$(ssh -i "$K" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ansible@$IPV "sudo cat /root/vault-init.json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["root_token"])')
rm -f "$K"
python3 - "$VAULT" "$NODO-bmc" "$PUERTO" <<PY
import json, sys, urllib.request
v, n, p = sys.argv[1], sys.argv[2], int(sys.argv[3])
d = {"data": {"usuario": "Administrator", "clave": "$CLAVE", "protocolo": "ssh", "metodo": "clave", "puerto": p, "origen": "BMC simulado del laboratorio"}}
r = urllib.request.Request(f"{v}/v1/faceops/data/maquinas/{n}", method="POST", data=json.dumps(d).encode(), headers={"X-Vault-Token": "$ROOT", "Content-Type": "application/json"})
urllib.request.urlopen(r, timeout=20).read()
print(f"credencial del BMC en la bóveda: maquinas/{n}")
PY
unset CLAVE ROOT
echo "BMC simulado $CT en 192.168.122.1:$PUERTO · $(sudo podman ps --filter name=$CT --format '{{.Status}}')"
