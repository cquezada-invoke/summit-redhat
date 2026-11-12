for p in 9100 9177 9272; do (curl -s -m 3 http://192.168.122.1:$p/metrics | head -c 300 >/dev/null && echo "puerto $p: responde") || echo "puerto $p: no"; done
curl -s -m 3 http://192.168.122.1:9100/metrics | grep -c "^node_systemd_unit_state" 
curl -s -m 3 http://192.168.122.1:9100/metrics | grep -E "^node_systemd_unit_state\{name=\"libvirtd" | head -3
curl -s -m 3 http://192.168.122.1:9100/metrics | grep -E "^node_textfile|libvirt" | head -5
curl -s -m 4 'http://127.0.0.1:9090/api/v1/query?query=up' | python3 -c "import sys,json;d=json.load(sys.stdin);print(sorted({(r['metric'].get('job'),r['metric'].get('instance'),r['metric'].get('faceops_equipo','')) for r in d['data']['result']})[:40])"
systemctl is-active libvirtd virtqemud 2>&1 | head -2
ls /var/lib/node_exporter* /var/lib/prometheus/node-exporter 2>/dev/null | head; ps aux | grep -E "node_exporter|libvirt.*export" | grep -v grep | head -3
