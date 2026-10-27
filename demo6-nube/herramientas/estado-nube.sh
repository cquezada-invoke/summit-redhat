#!/bin/bash
# Demo 6 · estado rápido: servicios, exportador y activación de EDA.
for s in summit-nube-exportador vigia-nube; do printf '%-26s %s\n' "$s" "$(systemctl is-active $s)"; done
curl -s http://127.0.0.1:9420/metrics | grep -E '^nube_(conectada|vms_activas|huerfanos |huerfanos_usd_mes|firewall_peligrosas)'
journalctl -u vigia-nube -n 5 --no-pager -o cat
