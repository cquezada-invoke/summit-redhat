#!/bin/bash
# Diagnóstico de salida HTTPS de nodo-so (comparado con nodo-postgres). Solo lectura. Sin secretos.
S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
for vm in nodo-so nodo-postgres; do
  ip=$(sudo virsh domifaddr $vm 2>/dev/null | awk '/ipv4/ {sub("/.*","",$4); print $4; exit}')
  echo "===== $vm $ip"
  $S ansible@$ip 'curl -4 -sS -o /dev/null --max-time 15 https://cdn.redhat.com/ 2>&1 | head -2; echo "http: $(curl -4 -s -o /dev/null -w %{http_code} --max-time 15 http://cdn.redhat.com/)";
    echo "tcp443: $(timeout 6 bash -c "</dev/tcp/cdn.redhat.com/443" 2>&1 && echo abierto || echo cerrado)";
    echo "proxy env/curlrc: $(env | grep -ci proxy) $(ls ~/.curlrc /etc/curlrc 2>/dev/null)";
    echo "firewalld: $(sudo -n firewall-cmd --state 2>&1) · zona: $(sudo -n firewall-cmd --get-default-zone 2>&1)"; sudo -n firewall-cmd --direct --get-all-rules 2>&1 | head -5;
    sudo -n nft list ruleset 2>/dev/null | grep -i -E "oif|output|daddr|reject|drop" | grep -v -i "lo\b" | head -12;
    echo "hosts: $(grep -v -E "^#|^$|localhost" /etc/hosts | head -5 | tr "\n" ";")";
    echo "crypto-policy: $(update-crypto-policies --show 2>/dev/null)"; echo "ca bundle: $(ls -la /etc/pki/tls/certs/ca-bundle.crt 2>&1 | cut -c1-80)"' 2>&1 | head -40
done
echo "===== host: reglas para 192.168.122.12"
sudo iptables -S 2>/dev/null | grep -E "192.168.122.12|nodo-so" | head; sudo nft list ruleset 2>/dev/null | grep -n "192.168.122.12" | head
sudo virsh dumpxml nodo-so | grep -i -E "filterref|<interface|mac address|source network" | head
