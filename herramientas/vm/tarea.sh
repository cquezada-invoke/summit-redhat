S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15 cquezada@34.176.114.77"
$S 'bash -s' <<'R'
pkill -TERM -f construir-imagen.py && echo "orquestador detenido (constructora conservada)"
cd ~/demo-gcp; [ -f diag ] || ssh-keygen -q -t ed25519 -N '' -f diag -C diag
VM=$(grep -o 'demo-constructor-[0-9]*' imagen.log | head -1); echo "VM $VM"
python3 - "$VM" <<'PY'
import json, sys, urllib.request, os
vm = sys.argv[1]
t = json.load(urllib.request.urlopen(urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token", headers={"Metadata-Flavor": "Google"})))["access_token"]
A = f"https://compute.googleapis.com/compute/v1/projects/master-ia-320218/zones/southamerica-west1-a/instances/{vm}"
h = lambda m, u, d=None: json.load(urllib.request.urlopen(urllib.request.Request(u, method=m, data=json.dumps(d).encode() if d else None, headers={"Authorization": "Bearer " + t, "Content-Type": "application/json"})))
i = h("GET", A)
items = [x for x in i["metadata"].get("items", []) if x["key"] != "ssh-keys"] + [{"key": "ssh-keys", "value": "diag:" + open(os.path.expanduser("~/demo-gcp/diag.pub")).read().strip()}]
h("POST", A + "/setMetadata", {"fingerprint": i["metadata"]["fingerprint"], "items": items})
print("llave diag agregada; IP interna", i["networkInterfaces"][0]["networkIP"])
open(os.path.expanduser("~/demo-gcp/ip"), "w").write(i["networkInterfaces"][0]["networkIP"])
PY
sleep 40
B="ssh -i diag -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 diag@$(cat ip)"
$B 'sudo bash -s' <<'Q'
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=$PATH:/usr/local/bin
free -g | head -2; uptime
kubectl get pods -A -o wide 2>&1 | cut -c1-150
echo "== eda-manager"; kubectl -n eda logs deploy/eda-server-operator-controller-manager -c eda-manager --tail=25 2>&1 | cut -c1-300
echo "== awx"; kubectl -n awx get awx -o jsonpath='{.items[0].status.conditions}' 2>&1 | head -c 800; echo
curl -s -m5 http://127.0.0.1:30080/api/v2/ping/ | head -c 300; echo
tail -5 /var/log/demo-construccion.log | cut -c1-200
Q
R
