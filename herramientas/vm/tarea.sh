cd ~/summit-redhat; source herramientas/pruebas/comun.sh >/dev/null 2>&1
IJ=$(ip_de nodo-jboss); IN=$(ip_de nodo-nginx)
echo "== redis en nodo-jboss"
en $IJ 'sudo ss -ltnp | grep -E "redis|6379|637" | head -3; sudo podman ps --format "{{.Names}} {{.Ports}}" 2>/dev/null | grep -i redis; ls /etc/redis* /etc/redis/ 2>/dev/null | head; sudo grep -h -E "^(port|bind|tls-port)" /etc/redis/redis.conf /etc/redis.conf 2>/dev/null | head -4; sudo grep -c "^requirepass" /etc/redis/redis.conf /etc/redis.conf 2>/dev/null; sudo journalctl -u faceops-exportador-redis --no-pager -n 4 2>/dev/null | cut -c1-200'
curl -s --max-time 5 http://$IJ:9121/metrics | grep -E "^redis_up|redis_exporter_last_scrape_error" | head -3
echo "== nginx en nodo-nginx"
en $IN 'sudo ss -ltnp | grep -E ":8081|:9113" ; curl -s -o /dev/null -w "stub %{http_code}\n" http://127.0.0.1:8081/stub_status; sudo nginx -t 2>&1 | tail -2; sudo journalctl -u nginx --no-pager -n 3 | cut -c1-200; sudo ausearch -m avc -ts recent 2>/dev/null | grep -c 8081; rpm -q nginx; nginx -V 2>&1 | grep -o with-http_stub_status_module'
echo "== undertow en nodo-jboss"
curl -s --max-time 5 http://$IJ:9990/metrics | grep -i undertow | head -5
