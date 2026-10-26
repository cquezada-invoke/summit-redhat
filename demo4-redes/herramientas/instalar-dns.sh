#!/bin/bash
# Demo 4 · DNS del laboratorio (dnsmasq) en 172.20.20.30, red de gestión de containerlab. Idempotente.
# AAP lo administra por SSH (host "dns" del inventario "Red · laboratorio"): un archivo por clúster en /etc/dnsmasq.d.
set -e
D=$(cd "$(dirname "$0")/.." && pwd); T=$(mktemp -d); trap 'rm -rf $T' EXIT
cp "$D/lab/dns/Containerfile" $T/ && cp ~/demo4/llave-lab.pub $T/llave.pub
sudo podman build -q -t localhost/dns-summit:1 $T >/dev/null
sudo podman rm -f summit-dns >/dev/null 2>&1 || true
sudo podman run -d --name summit-dns --restart always --network clab-summit --ip 172.20.20.30 localhost/dns-summit:1 >/dev/null
sleep 2
sudo podman exec summit-dns sh -c 'echo "host-record=dns.lab,172.20.20.30" > /etc/dnsmasq.d/00-base.conf; pkill dnsmasq'
sleep 2
echo -n "DNS del laboratorio (172.20.20.30): "; sudo podman exec summit-dns nslookup dns.lab 127.0.0.1 >/dev/null 2>&1 && echo OK || echo FALLA
