#!/bin/bash
# Demo 5 · monitoreo: Prometheus + Alertmanager + blackbox_exporter + Grafana en la VM (podman, red del host,
# servicios systemd que sobreviven reinicios), exportador de Artemis, HTTPS en Nginx (certificado vigilado),
# consumidor de la cola pagos.entrada en nodo-tomcat y Grafana publicado en :8090/grafana/. Idempotente.
#   Prometheus 127.0.0.1:9090 · Alertmanager :9093 · blackbox :9115 · Grafana :3001 · exportador Artemis :9410
set -e
D=$(cd "$(dirname "$0")" && pwd); M=/opt/summit-monitoreo; S=~/demo-drift/cicd-secretos.txt
paso(){ echo "   · $*"; }
sudo mkdir -p $M/{prometheus,alertmanager,blackbox,prom-data,am-data,grafana/data,grafana/dashboards} /etc/summit-monitoreo
sudo cp $D/prometheus.yml $D/alertas.yml $M/prometheus/; sudo cp $D/alertmanager.yml $M/alertmanager/; sudo cp $D/blackbox.yml $M/blackbox/
sudo rm -rf $M/grafana/provisioning; sudo cp -r $D/grafana/provisioning $M/grafana/; sudo cp $D/grafana/dashboards/*.json $M/grafana/dashboards/
sudo chown -R 65534:65534 $M/prom-data $M/am-data; sudo chown -R 472:0 $M/grafana
# claves de Grafana (no se imprimen)
grep -q '^grafana_admin_clave=' $S || echo "grafana_admin_clave=$(openssl rand -hex 12)" >> $S
grep -q '^grafana_visitante_clave=' $S || echo "grafana_visitante_clave=Summit-$(openssl rand -hex 3)" >> $S
GA=$(sed -n 's/^grafana_admin_clave=//p' $S)
# exportador de Artemis (credenciales del broker, solo root)
E=$(ssh nodo-tomcat 'sudo podman inspect artemis --format "{{range .Config.Env}}{{println .}}{{end}}"' | grep -E '^ARTEMIS_(USER|PASSWORD)=')
echo "$E" | sudo tee /etc/summit-monitoreo/artemis.env >/dev/null; sudo chmod 600 /etc/summit-monitoreo/artemis.env
sudo cp $D/artemis_exporter.py $M/artemis_exporter.py
unidad(){ sudo tee /etc/systemd/system/$1.service >/dev/null; }
unidad summit-artemis-exporter <<U
[Unit]
Description=Monitoreo Summit · exportador de Artemis para Prometheus
After=network-online.target
[Service]
EnvironmentFile=/etc/summit-monitoreo/artemis.env
User=nobody
ExecStart=/usr/bin/python3 $M/artemis_exporter.py
Restart=always
[Install]
WantedBy=multi-user.target
U
contenedor(){ # nombre imagen "args" "comando"
unidad summit-$1 <<U
[Unit]
Description=Monitoreo Summit · $1
After=network-online.target
[Service]
ExecStartPre=-/usr/bin/podman rm -f summit-$1
ExecStart=/usr/bin/podman run --rm --name summit-$1 --network host $3 $2 $4
ExecStop=/usr/bin/podman stop -t 10 summit-$1
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
U
}
contenedor prometheus docker.io/prom/prometheus:v2.55.1 "-v $M/prometheus:/etc/prometheus:Z -v $M/prom-data:/prometheus:Z" \
  "--config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus --storage.tsdb.retention.time=7d --web.listen-address=127.0.0.1:9090 --web.enable-lifecycle"
contenedor alertmanager docker.io/prom/alertmanager:v0.27.0 "-v $M/alertmanager:/etc/alertmanager:Z -v $M/am-data:/alertmanager:Z" \
  "--config.file=/etc/alertmanager/alertmanager.yml --storage.path=/alertmanager --web.listen-address=127.0.0.1:9093 --cluster.listen-address="
contenedor blackbox docker.io/prom/blackbox-exporter:v0.25.0 "-v $M/blackbox:/config:Z" "--config.file=/config/blackbox.yml --web.listen-address=127.0.0.1:9115"
contenedor grafana docker.io/grafana/grafana:11.3.1 "-v $M/grafana/data:/var/lib/grafana:Z -v $M/grafana/provisioning:/etc/grafana/provisioning:Z -v $M/grafana/dashboards:/var/lib/grafana/dashboards:Z \
  -e GF_SERVER_HTTP_PORT=3001 -e GF_SERVER_ROOT_URL=http://34.176.42.56:8090/grafana/ -e GF_SERVER_SERVE_FROM_SUB_PATH=true \
  -e GF_SECURITY_ADMIN_PASSWORD=$GA -e GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/summit-operacion.json \
  -e GF_USERS_DEFAULT_THEME=light -e GF_ANALYTICS_REPORTING_ENABLED=false -e GF_NEWS_NEWS_FEED_ENABLED=false" ""
sudo chmod 600 /etc/systemd/system/summit-grafana.service
for i in prometheus alertmanager blackbox grafana; do sudo podman pull -q $(grep -o 'docker.io/[^ ]*' /etc/systemd/system/summit-$i.service | head -1) >/dev/null; done
sudo systemctl daemon-reload
for s in summit-artemis-exporter summit-prometheus summit-alertmanager summit-blackbox summit-grafana; do sudo systemctl enable -q $s; sudo systemctl restart $s; done
paso "Prometheus, Alertmanager, blackbox, Grafana y exportador de Artemis activos"

# Nginx (nodo-nginx): HTTPS con certificado propio (lo vigila Prometheus) y Grafana en /grafana/
ssh nodo-nginx 'sudo bash -s' <<'N'
set -e
mkdir -p /etc/pki/nginx/private
if [ ! -f /etc/pki/nginx/summit.crt ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 90 -subj "/CN=app.summit.lab" \
    -keyout /etc/pki/nginx/private/summit.key -out /etc/pki/nginx/summit.crt 2>/dev/null
fi
cat > /etc/nginx/conf.d/summit-tls.conf <<C
# HTTPS de la app (Demo 5 · monitoreo): el certificado lo vigila Prometheus y lo renueva Ansible
server {
    listen 443 ssl;
    server_name _;
    ssl_certificate /etc/pki/nginx/summit.crt;
    ssl_certificate_key /etc/pki/nginx/private/summit.key;
    include /etc/nginx/default.d/*.conf;
}
C
cat > /etc/nginx/default.d/grafana.conf <<'C'
# Grafana del monitoreo de la demo (corre en la VM anfitriona)
location /grafana/ {
    proxy_pass http://192.168.122.1:3001;
    proxy_set_header Host $http_host;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
}
C
nginx -t -q && systemctl reload nginx
N
paso "Nginx: HTTPS en 192.168.122.11:443 y Grafana en http://34.176.42.56:8090/grafana/"

# Consumidor de la cola pagos.entrada (nodo-tomcat): procesa los pagos de a uno (≈10 por segundo)
ssh nodo-tomcat 'sudo bash -s' <<'N'
cat > /etc/systemd/system/consumidor-pagos.service <<'C'
[Unit]
Description=Consumidor de la cola pagos.entrada (Artemis)
After=network-online.target
[Service]
ExecStart=/bin/bash -c 'while true; do podman exec artemis sh -c "/var/lib/artemis-instance/bin/artemis consumer --user \"\$ARTEMIS_USER\" --password \"\$ARTEMIS_PASSWORD\" --url tcp://localhost:61616 --destination queue://pagos.entrada --break-on-null --receive-timeout 5000 --sleep 100 --silent" >/dev/null 2>&1; sleep 2; done'
Restart=always
[Install]
WantedBy=multi-user.target
C
systemctl daemon-reload; systemctl enable -q --now consumidor-pagos
N
paso "nodo-tomcat: consumidor-pagos activo"

# Grafana: usuario de solo lectura
for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:3001/grafana/api/health && break; sleep 2; done
GV=$(sed -n 's/^grafana_visitante_clave=//p' $S)
curl -s -u "admin:$GA" -H 'Content-Type: application/json' http://127.0.0.1:3001/grafana/api/admin/users \
  -d "{\"name\":\"Visitante\",\"login\":\"visitante\",\"password\":\"$GV\"}" >/dev/null || true
paso "Grafana: usuario 'visitante' (solo lectura). Claves: grep grafana ~/demo-drift/cicd-secretos.txt"
