#!/bin/bash
# nodo-so quedó registrado al Satellite dc1-sat01 (apagado): se registra directo en Red Hat (CDN) con la llave de activación
# del lab (~/faceops/rhsm-activacion.json). No imprime la organización ni la llave.
IP=192.168.122.12
S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
echo "== comparación: rhsm de nodo-postgres"
IPP=$(sudo virsh domifaddr nodo-postgres 2>/dev/null | awk '/ipv4/ {sub("/.*","",$4); print $4; exit}')
$S ansible@$IPP "grep -E '^\s*(hostname|baseurl)\s*=' /etc/rhsm/rhsm.conf" 2>&1 | head -3
echo "== salida de nodo-so a Red Hat (IPv4)"
$S ansible@$IP 'for u in https://cdn.redhat.com/ https://subscription.rhsm.redhat.com/subscription/; do echo "$u $(curl -4 -s -o /dev/null -w "%{http_code} %{time_total}s" --max-time 20 $u 2>&1) · v6 $(curl -6 -s -o /dev/null -w "%{http_code}" --max-time 8 $u 2>&1)"; done'
OK=$($S ansible@$IP 'curl -4 -s -o /dev/null -w "%{http_code}" --max-time 20 https://subscription.rhsm.redhat.com/subscription/')
echo "rhsm http=$OK"
if [ "$OK" = "000" ]; then echo "SIN SALIDA IPv4 a Red Hat: no se re-registra"; exit 0; fi
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/faceops/rhsm-activacion.json')));print(d['org']);print(d['llave'])" > /tmp/.rh.$$ ; chmod 600 /tmp/.rh.$$
$S ansible@$IP 'read O; read K; set -e
  sudo -n subscription-manager unregister >/dev/null 2>&1 || true
  sudo -n subscription-manager clean >/dev/null 2>&1 || true
  sudo -n subscription-manager config --server.hostname=subscription.rhsm.redhat.com --server.prefix=/subscription --server.port=443 \
       --rhsm.baseurl=https://cdn.redhat.com "--rhsm.repo_ca_cert=%(ca_cert_dir)sredhat-uep.pem" --server.insecure=0
  sudo -n subscription-manager register --org="$O" --activationkey="$K" --force 2>&1 | grep -v -i -E "org|key|id:" | sed "s/[0-9a-f]\{8\}-[0-9a-f-]\{27\}/<uuid>/g" | head -3
  sudo -n subscription-manager repos --enable rhel-9-for-x86_64-baseos-rpms --enable rhel-9-for-x86_64-appstream-rpms 2>&1 | tail -2
  sudo -n dnf clean all -q; echo "makecache: $(sudo -n dnf -q makecache 2>&1 | tail -1)"; echo "repos:"; sudo -n dnf -q repolist --enabled 2>&1 | tail -n +2
  echo "avisos de seguridad disponibles: $(sudo -n dnf -q updateinfo list --security 2>/dev/null | wc -l)"
  echo "estado: $(sudo -n subscription-manager status 2>&1 | grep -E "Overall|Content Access" | tr "\n" " ")"' < /tmp/.rh.$$ 2>&1 | grep -v -i -E "activationkey|password"
rm -f /tmp/.rh.$$
