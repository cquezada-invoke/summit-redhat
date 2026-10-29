#!/bin/bash
# Crea un JBoss nuevo (p. ej. nodo-jboss-qa .20) como copia de nodo-jboss2 (JBoss EAP 8 ya instalado). Uso: clonar-jboss.sh <nombre> <octeto>
# Lo suma a los mismos inventarios y grupos de AAP que nodo-jboss2. Idempotente.
# Apaga nodo-jboss2 ~1 min para copiar su disco (nodo-jboss sigue atendiendo).
set -e
log(){ echo "$(date +%T) $*"; }
N=${1:?uso: clonar-jboss.sh <nombre> <último octeto IP>}; O=${2:?}; IP=192.168.122.$O; MAC=52:54:00:de:00:$O
if ! sudo virsh dominfo $N >/dev/null 2>&1; then
  sudo virsh net-update default add ip-dhcp-host "<host mac='$MAC' name='$N' ip='$IP'/>" --live --config >/dev/null 2>&1 || true
  grep -q " $N$" /etc/hosts || echo "$IP $N.demo.local $N" | sudo tee -a /etc/hosts >/dev/null
  log "apagando nodo-jboss2 para copiar su disco"
  sudo virsh shutdown nodo-jboss2 >/dev/null
  for i in $(seq 1 60); do sudo virsh domstate nodo-jboss2 | grep -q "shut off" && break; sleep 2; done
  sudo virsh domstate nodo-jboss2 | grep -q "shut off" || { sudo virsh destroy nodo-jboss2; sleep 2; }
  sudo virt-clone --original nodo-jboss2 --name $N --file /var/lib/libvirt/images/$N.qcow2 --mac $MAC >/dev/null
  log "clon listo; encendiendo ambos"
  sudo virsh start nodo-jboss2 >/dev/null; sudo virsh start $N >/dev/null; sudo virsh autostart $N >/dev/null
  ssh-keygen -R $IP >/dev/null 2>&1; ssh-keygen -R $N >/dev/null 2>&1
  for i in $(seq 1 60); do ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no $N true 2>/dev/null && break; sleep 3; done
  log "$N responde por SSH; personalizando"
  ssh -o StrictHostKeyChecking=no $N "sudo bash -s" <<EOS
hostnamectl set-hostname $N.demo.local
rm -f /etc/machine-id && systemd-machine-id-setup >/dev/null
rm -f /etc/ssh/ssh_host_* && ssh-keygen -A >/dev/null
subscription-manager clean >/dev/null 2>&1 || true
systemctl disable --now vigia.service vigia-jboss.service >/dev/null 2>&1 || true
(sleep 2; systemctl restart sshd) >/dev/null 2>&1 &
systemctl restart eap8-standalone
EOS
  ssh-keygen -R $IP >/dev/null 2>&1; ssh-keygen -R $N >/dev/null 2>&1
  ssh nodo-postgres "sudo firewall-cmd -q --permanent --add-rich-rule='rule family=ipv4 source address=$IP/32 port port=5432 protocol=tcp accept' && sudo firewall-cmd -q --reload" || true
else
  log "$N ya existe"
fi
for i in $(seq 1 40); do curl -s -m3 http://$IP:8080/app-jboss/salud | grep -q ok && break; sleep 3; done
log "salud $N: $(curl -s -m3 http://$IP:8080/app-jboss/salud)"

log "AAP: mismos inventarios y grupos que nodo-jboss2"
N=$N IP=$IP python3 - <<'PY'
import base64, glob, json, os, re, ssl, urllib.parse, urllib.request
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
N, IP = os.environ["N"], os.environ["IP"]
CTX = ssl._create_unverified_context(); AUTH = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
def api(m, p, d=None):
    r = urllib.request.Request("https://localhost/api/controller/v2/" + p, method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Content-Type": "application/json", "Authorization": AUTH})
    with urllib.request.urlopen(r, context=CTX, timeout=60) as x:
        b = x.read(); return json.loads(b) if b else {}
for h in api("GET", "hosts/?name=nodo-jboss2&page_size=50")["results"]:
    inv = h["inventory"]; nombre_inv = h["summary_fields"]["inventory"]["name"]
    if nombre_inv == "Drift · Servidores":
        continue  # el control de drift se incorpora aparte (Demo 1)
    vars_ = (h["variables"] or "").replace("192.168.122.18", IP).replace("nodo-jboss2", N)
    ya = api("GET", f"hosts/?name={N}&inventory={inv}")["results"]
    nuevo = ya[0] if ya else api("POST", "hosts/", {"name": N, "inventory": inv, "variables": vars_})
    for g in api("GET", f"hosts/{h['id']}/groups/")["results"]:
        api("POST", f"groups/{g['id']}/hosts/", {"id": nuevo["id"]})
    print(f"   · {nombre_inv}: {N} en {', '.join(x['name'] for x in api('GET', f'hosts/{h[chr(105)+chr(100)]}/groups/')['results']) or 'sin grupos'}")
PY

log "jboss-config: nodos/$N.yml"
SEC=$(grep ^gitea_token= ~/demo-drift/cicd-secretos.txt | cut -d= -f2-)
T=$(mktemp -d); git clone -q "http://invoke:$SEC@127.0.0.1:3000/invoke/jboss-config.git" $T
if [ ! -f $T/nodos/$N.yml ]; then
  printf '# Solo lo que difiere de comun/jboss.yml en este servidor.\nnodo:\n  nombre: %s\n' $N > $T/nodos/$N.yml
  (cd $T && git add -A && git -c user.name='Cristian Quezada' -c user.email=cquezada@invoke.cl commit -qm "$N: JBoss nuevo" && git push -q origin HEAD:main)
fi
rm -rf $T
log "listo"
